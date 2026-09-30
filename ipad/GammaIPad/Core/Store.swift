import Foundation
import SQLite3

private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

/// A small SQLite connection: one handle, serialized (the sync core's
/// thread, the edit core's and the main thread all use it), and a lock so a
/// check-then-write sequence runs as one.
final class Database {
    private var db: OpaquePointer?
    private let lock = NSRecursiveLock()

    init(path: String) throws {
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(path, &db, flags, nil) == SQLITE_OK else {
            throw JSON.Failure(message: "cannot open the library database")
        }
        try execute("PRAGMA journal_mode = WAL")
        try execute("PRAGMA synchronous = NORMAL")
        try execute("PRAGMA busy_timeout = 5000")
    }

    deinit { sqlite3_close(db) }

    private func message() -> String { String(cString: sqlite3_errmsg(db)) }

    private func prepare(_ sql: String, _ args: [Any?]) throws -> OpaquePointer? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { throw JSON.Failure(message: message()) }
        for (i, arg) in args.enumerated() {
            let n = Int32(i + 1)
            switch arg {
            case nil, is NSNull: sqlite3_bind_null(stmt, n)
            case let v as Int: sqlite3_bind_int64(stmt, n, Int64(v))
            case let v as Int64: sqlite3_bind_int64(stmt, n, v)
            case let v as Bool: sqlite3_bind_int64(stmt, n, v ? 1 : 0)
            case let v as Double: sqlite3_bind_double(stmt, n, v)
            case let v as String: sqlite3_bind_text(stmt, n, v, -1, SQLITE_TRANSIENT)
            default: sqlite3_bind_text(stmt, n, String(describing: arg!), -1, SQLITE_TRANSIENT)
            }
        }
        return stmt
    }

    func execute(_ sql: String, _ args: [Any?] = []) throws {
        lock.lock(); defer { lock.unlock() }
        let stmt = try prepare(sql, args)
        defer { sqlite3_finalize(stmt) }
        let rc = sqlite3_step(stmt)
        guard rc == SQLITE_DONE || rc == SQLITE_ROW else { throw JSON.Failure(message: message()) }
    }

    /// Rows as arrays of String / Int64 / Double / nil.
    func query(_ sql: String, _ args: [Any?] = []) throws -> [[Any?]] {
        lock.lock(); defer { lock.unlock() }
        let stmt = try prepare(sql, args)
        defer { sqlite3_finalize(stmt) }
        var rows: [[Any?]] = []
        while true {
            let rc = sqlite3_step(stmt)
            if rc == SQLITE_DONE { break }
            guard rc == SQLITE_ROW else { throw JSON.Failure(message: message()) }
            var row: [Any?] = []
            for i in 0..<sqlite3_column_count(stmt) {
                switch sqlite3_column_type(stmt, i) {
                case SQLITE_NULL: row.append(nil)
                case SQLITE_INTEGER: row.append(sqlite3_column_int64(stmt, i))
                case SQLITE_FLOAT: row.append(sqlite3_column_double(stmt, i))
                default: row.append(sqlite3_column_text(stmt, i).map { String(cString: $0) } ?? "")
                }
            }
            rows.append(row)
        }
        return rows
    }

    /// The first column of the first row, or nil.
    func scalar(_ sql: String, _ args: [Any?] = []) throws -> Any? {
        guard let row = try query(sql, args).first, let value = row.first else { return nil }
        return value
    }

    func transaction<T>(_ body: () throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        try execute("BEGIN IMMEDIATE")
        do {
            let out = try body()
            try execute("COMMIT")
            return out
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }
}

/// A stored JSON object, or [:].
private func object(_ text: Any?) -> [String: Any] {
    guard let s = text as? String, let v = try? JSON.parse(s), let d = v as? [String: Any] else { return [:] }
    return d
}

/// The replica's library: every page as a snapshot (JSON, the shape of
/// frontend/src/replica/tree.js), its version, whether it was edited here
/// since the last round took it, its sync state (base, remote seq, pending
/// push), the replica's cursor, conflicts and log. The semantics are the
/// host interface's (frontend/src/replica/round.js); the tests' in-memory
/// host (frontend/tests/replica/memoryHost.mjs) is the same thing.
final class Store {
    let db: Database

    init(path: String) throws {
        db = try Database(path: path)
        for sql in [
            """
            CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, snapshot TEXT, version INTEGER NOT NULL DEFAULT 0,
              edited INTEGER NOT NULL DEFAULT 0, tombstone INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '',
              props TEXT NOT NULL DEFAULT '{}', position TEXT NOT NULL DEFAULT '')
            """,
            "CREATE TABLE IF NOT EXISTS blocks (id TEXT PRIMARY KEY, page_id TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS blocks_page ON blocks(page_id)",
            "CREATE TABLE IF NOT EXISTS states (page_id TEXT PRIMARY KEY, state TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            """
            CREATE TABLE IF NOT EXISTS conflicts (id INTEGER PRIMARY KEY AUTOINCREMENT, page_id TEXT, block_id TEXT,
              kind TEXT, body TEXT, at TEXT, resolved INTEGER NOT NULL DEFAULT 0)
            """,
            "CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, page_id TEXT, action TEXT, title TEXT, stats TEXT, at TEXT)",
        ] { try db.execute(sql) }
    }

    // --- pages ------------------------------------------------------------------

    /// `{"snapshot": …, "version": n}` as JSON text, the snapshot as stored.
    func pageJSON(_ id: String) throws -> String {
        var snapshot = "null", version: Int64 = 0
        if let row = try db.query("SELECT snapshot, version FROM pages WHERE id = ?", [id]).first {
            if let text = row[0] as? String { snapshot = text }
            if let v = row[1] as? Int64 { version = v }
        }
        return "{\"snapshot\":\(snapshot),\"version\":\(version)}"
    }

    func snapshot(_ id: String) -> [String: Any]? {
        guard let value = try? db.scalar("SELECT snapshot FROM pages WHERE id = ?", [id]), let text = value as? String else { return nil }
        let page = object(text)
        return page.isEmpty ? nil : page
    }

    func version(_ id: String) throws -> Int64 {
        try db.scalar("SELECT version FROM pages WHERE id = ?", [id]) as? Int64 ?? 0
    }

    /// Write the page unless it changed after `version`: → the new version, or 0.
    func write(_ id: String, snapshot: [String: Any], version: Int64, edit: Bool) throws -> Int64 {
        let text = try JSON.string(snapshot)
        let root = snapshot[id] as? [String: Any] ?? [:]
        let props = try JSON.string(root["props"] ?? [String: Any]())
        return try db.transaction {
            guard try self.version(id) == version else { return 0 }
            let next = version + 1
            try db.execute("""
                INSERT INTO pages (id, snapshot, version, edited, tombstone, title, props, position) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET snapshot = excluded.snapshot, version = excluded.version,
                  edited = CASE WHEN ? THEN excluded.version ELSE pages.edited END,
                  tombstone = CASE WHEN ? THEN 0 ELSE pages.tombstone END,
                  title = excluded.title, props = excluded.props, position = excluded.position
                """, [id, text, next, edit ? next : 0, root.string("content"), props, root.string("position"), edit, edit])
            try db.execute("DELETE FROM blocks WHERE page_id = ?", [id])
            for block in snapshot.keys where block != id {
                try db.execute("INSERT OR REPLACE INTO blocks (id, page_id) VALUES (?, ?)", [block, id])
            }
            return next
        }
    }

    /// The page is gone here: deleted there (`tombstone` false) or here (true,
    /// until the next round carries it).
    func remove(_ id: String, tombstone: Bool) throws {
        try db.transaction {
            try db.execute("""
                INSERT INTO pages (id, snapshot, version, edited, tombstone) VALUES (?, NULL, 1, 0, ?)
                ON CONFLICT(id) DO UPDATE SET snapshot = NULL, version = pages.version + 1, edited = 0, tombstone = excluded.tombstone
                """, [id, tombstone])
            try db.execute("DELETE FROM blocks WHERE page_id = ?", [id])
        }
    }

    func localChanges() throws -> [String: Any] {
        let edited = try db.query("SELECT id FROM pages WHERE edited > 0 AND snapshot IS NOT NULL").compactMap { $0[0] as? String }
        let deleted = try db.query("SELECT id FROM pages WHERE tombstone = 1").compactMap { $0[0] as? String }
        return ["pages": edited, "deleted": deleted]
    }

    func acknowledge(_ id: String, version: Int64) throws {
        try db.execute("UPDATE pages SET edited = 0 WHERE id = ? AND edited <= ?", [id, version])
        try db.execute("UPDATE pages SET tombstone = 0 WHERE id = ? AND snapshot IS NULL", [id])
    }

    var pendingEdits: Int {
        guard let value = try? db.scalar("SELECT COUNT(*) FROM pages WHERE edited > 0 OR tombstone = 1"), let n = value as? Int64 else { return 0 }
        return Int(n)
    }

    func pageOfBlock(_ id: String) throws -> String? {
        try db.scalar("SELECT page_id FROM blocks WHERE id = ?", [id]) as? String
    }

    /// The library: every page here, `{id, content, props, position}`.
    func roots() throws -> [[String: Any]] {
        try db.query("SELECT id, title, props, position FROM pages WHERE snapshot IS NOT NULL").map { row in
            ["id": row[0] as? String ?? "", "content": row[1] as? String ?? "", "props": object(row[2]), "position": row[3] as? String ?? ""]
        }
    }

    /// Every upload name a page here names (storage.upload_refs' grammar).
    func referencedFiles() throws -> [String] {
        let ref = try NSRegularExpression(pattern: "/api/uploads/([0-9A-Za-z_-]+\\.[0-9A-Za-z]{1,12})(?![0-9A-Za-z])")
        let doc = try NSRegularExpression(pattern: "\"doc_id\"\\s*:\\s*\"([0-9A-Za-z_-]{1,128})\"")
        var names = Set<String>()
        for row in try db.query("SELECT snapshot FROM pages WHERE snapshot IS NOT NULL") {
            guard let text = row[0] as? String else { continue }
            let range = NSRange(text.startIndex..., in: text)
            for m in ref.matches(in: text, range: range) { if let r = Range(m.range(at: 1), in: text) { names.insert(String(text[r])) } }
            for m in doc.matches(in: text, range: range) { if let r = Range(m.range(at: 1), in: text) { names.insert("\(text[r]).pdf") } }
        }
        return names.sorted()
    }

    // --- sync state, meta, conflicts, log -----------------------------------------------

    func stateJSON(_ id: String) throws -> String {
        try db.scalar("SELECT state FROM states WHERE page_id = ?", [id]) as? String ?? "null"
    }

    func saveState(_ id: String, _ state: Any?) throws {
        if state == nil || state is NSNull { try db.execute("DELETE FROM states WHERE page_id = ?", [id]); return }
        try db.execute("INSERT OR REPLACE INTO states (page_id, state) VALUES (?, ?)", [id, try JSON.string(state)])
    }

    func meta(_ key: String) throws -> String? {
        try db.scalar("SELECT value FROM meta WHERE key = ?", [key]) as? String
    }

    func setMeta(_ key: String, _ value: String) throws {
        try db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [key, value])
    }

    func addConflict(_ c: [String: Any]) throws {
        // a decision recorded `once` is not recorded again while it is open
        if (c["once"] as? Bool) == true {
            let open = try db.query("SELECT 1 FROM conflicts WHERE page_id = ? AND block_id = ? AND kind = ? AND resolved = 0",
                                    [c.string("page_id"), c.string("block_id"), c.string("kind")])
            if !open.isEmpty { return }
        }
        try db.execute("INSERT INTO conflicts (page_id, block_id, kind, body, at) VALUES (?, ?, ?, ?, ?)",
                       [c.string("page_id"), c.string("block_id"), c.string("kind"), try JSON.string(c), ISO8601DateFormatter().string(from: Date())])
    }

    func openConflicts() throws -> [[String: Any]] {
        try db.query("SELECT id, body FROM conflicts WHERE resolved = 0 ORDER BY id DESC LIMIT 200").map { row in
            var body = object(row[1])
            body["row"] = row[0] as? Int64 ?? 0
            return body
        }
    }

    func resolveConflict(_ row: Int64) throws {
        try db.execute("UPDATE conflicts SET resolved = 1 WHERE id = ?", [row])
    }

    func addNote(_ e: [String: Any]) throws {
        try db.execute("INSERT INTO log (page_id, action, title, stats, at) VALUES (?, ?, ?, ?, ?)",
                       [e.string("page_id"), e.string("action"), e.string("title"), try JSON.string(e["stats"]), e.string("at")])
        try db.execute("DELETE FROM log WHERE id <= (SELECT MAX(id) FROM log) - 500")
    }

    func recentLog() throws -> [[String: Any]] {
        try db.query("SELECT page_id, action, title, at FROM log ORDER BY id DESC LIMIT 100").map {
            ["page_id": $0[0] as? String ?? "", "action": $0[1] as? String ?? "", "title": $0[2] as? String ?? "", "at": $0[3] as? String ?? ""]
        }
    }
}

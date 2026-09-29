import Foundation
import CSQLite

/// Used only inside GammaRepository's actor. The database contains no credentials.
final class SQLiteStore {
    private var db: OpaquePointer?
    private let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    init(url: URL) throws {
        guard sqlite3_open_v2(url.path, &db, SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            throw GammaError.database("Cannot open the local library.")
        }
        sqlite3_busy_timeout(db, 5000)
        try run("PRAGMA journal_mode=WAL")
        try run("PRAGMA synchronous=FULL")
        try run("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS blocks (id TEXT PRIMARY KEY, page_id TEXT NOT NULL, value TEXT NOT NULL)")
        try run("CREATE INDEX IF NOT EXISTS blocks_page ON blocks(page_id)")
        try run("CREATE TABLE IF NOT EXISTS sync_pages (page_id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY AUTOINCREMENT, page_id TEXT NOT NULL, source TEXT NOT NULL, value TEXT NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS deleted_pages (page_id TEXT PRIMARY KEY, at TEXT NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS conflicts (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    }
    deinit { sqlite3_close(db) }
    func run(_ sql: String, _ args: [String] = []) throws { _ = try rows(sql, args) }
    func rows(_ sql: String, _ args: [String] = []) throws -> [[String]] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw failure() }
        defer { sqlite3_finalize(statement) }
        for (i, arg) in args.enumerated() {
            guard sqlite3_bind_text(statement, Int32(i + 1), arg, -1, transient) == SQLITE_OK else { throw failure() }
        }
        var result: [[String]] = []
        while true {
            let status = sqlite3_step(statement)
            if status == SQLITE_DONE { return result }
            guard status == SQLITE_ROW else { throw failure() }
            result.append((0..<sqlite3_column_count(statement)).map { index in
                sqlite3_column_text(statement, index).map { String(cString: $0) } ?? ""
            })
        }
    }
    func transaction<T>(_ body: () throws -> T) throws -> T {
        try run("BEGIN IMMEDIATE")
        do { let value = try body(); try run("COMMIT"); return value }
        catch { try? run("ROLLBACK"); throw error }
    }
    func get<T: Decodable>(_ key: String, as type: T.Type) throws -> T? {
        guard let raw = try rows("SELECT value FROM meta WHERE key=?", [key]).first?.first else { return nil }
        return try GammaJSON.decode(type, raw)
    }
    func set<T: Encodable>(_ key: String, _ value: T) throws {
        try run("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", [key, try GammaJSON.string(value)])
    }
    private func failure() -> GammaError { .database(db.map { String(cString: sqlite3_errmsg($0)) } ?? "SQLite failure") }
}

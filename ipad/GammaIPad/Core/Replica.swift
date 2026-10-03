import Foundation

/// Where the replica follows: a server, the account signed in there, one
/// workspace of it. The token lives in the Keychain under `id`.
struct Connection: Codable, Equatable {
    var id: String
    var server: URL
    var user: String
    var workspace: String
    var workspaceName: String
    var mode: String = "two-way"
}

/// What the last round did (round.js syncRound's status).
struct SyncStatus: Equatable {
    var running = false
    var lastSync: Date?
    var lastError = ""
    var pulled = 0
    var pushed = 0
    var deleted = 0
    var progress = ""
}

/// One workspace of one Gamma server kept on this iPad (docs/dev/ipad.md):
/// the library store, its files, the remote, and the two JavaScript cores
/// that make every decision — the mirror protocol and merges of
/// frontend/src/replica/*, the same code the tests run against a server.
/// This class is the host those cores call (round.js "the HOST"): it only
/// reads and writes, it never decides.
final class Replica {
    let connection: Connection
    let store: Store
    let files: FileStore
    let remote: Remote
    private let syncCore: GammaCore
    private let editCore: GammaCore
    /// Progress while a round runs ("12 of 80"), on whichever thread.
    var onProgress: ((String) -> Void)?

    init(connection: Connection, token: String, directory: URL) throws {
        self.connection = connection
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        store = try Store(path: directory.appendingPathComponent("library.sqlite").path)
        files = try FileStore(directory: directory.appendingPathComponent("uploads", isDirectory: true))
        remote = Remote(base: connection.server, workspace: connection.workspace, token: token)
        let source = try GammaCore.bundledSource()
        syncCore = try GammaCore(name: "sync", source: source)
        editCore = try GammaCore(name: "edit", source: source)
    }

    static func directory(for id: String) -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("Gamma", isDirectory: true).appendingPathComponent(id, isDirectory: true)
    }

    // --- what the app calls -----------------------------------------------------------

    /// One sync round, waited for (call off the main thread).
    func syncRound() throws -> [String: Any] {
        let out = try syncCore.run("syncRound", [], host: host)
        return out as? [String: Any] ?? [:]
    }

    /// An edit (replica/edits.js): `name` and its arguments after the host.
    @discardableResult
    func edit(_ name: String, _ args: [Any?]) throws -> Any? {
        try editCore.run(name, args, host: host)
    }

    /// A pure helper (ink geometry, paper, views).
    func pure(_ name: String, _ args: [Any?]) throws -> Any? {
        try editCore.pure(name, args)
    }

    func view(of pageId: String) -> [String: Any]? {
        guard let snapshot = store.snapshot(pageId) else { return nil }
        return (try? pure("pageView", [snapshot, pageId])) as? [String: Any]
    }

    /// The library's rows (views.js libraryRows): every page with its folders
    /// named as paths and its labels as names, read off the `folders` and
    /// `labels` snapshots — two more snapshots the rounds keep like pages,
    /// either missing until a round brings it.
    func libraryRows() -> [[String: Any]] {
        let trees = ["folders": store.snapshot("folders"), "labels": store.snapshot("labels")].compactMapValues { $0 }
        guard let roots = try? store.roots(), let rows = try? pure("libraryRows", [roots, trees]) as? [[String: Any]] else { return [] }
        return rows
    }

    func inkFile(_ url: String) -> [String: Any]? {
        guard let name = Replica.uploadName(url), let data = files.read(name) else { return nil }
        return JSON.parse(data) as? [String: Any]
    }

    static func uploadName(_ url: String) -> String? {
        guard let range = url.range(of: "/api/uploads/") else { return nil }
        let rest = url[range.upperBound...].prefix { $0 != "?" && $0 != "#" }
        let name = String(rest)
        return FileStore.validName(name) ? name : nil
    }

    // --- the host ------------------------------------------------------------------------

    private func host(_ method: String, _ argsJSON: String) -> String {
        do {
            let args = (try JSON.parse(argsJSON) as? [Any]) ?? []
            return try answer(method, args)
        } catch {
            return "{\"error\":\(JSON.quote(error.localizedDescription))}"
        }
    }

    private func value(_ v: Any?) throws -> String {
        let text = try JSON.string(v)
        return "{\"value\":\(text)}"
    }

    private func answer(_ method: String, _ args: [Any]) throws -> String {
        func str(_ i: Int) -> String { i < args.count ? args[i] as? String ?? "" : "" }
        func int(_ i: Int) -> Int64 { i < args.count ? (args[i] as? NSNumber)?.int64Value ?? 0 : 0 }
        func obj(_ i: Int) -> [String: Any] { i < args.count ? args[i] as? [String: Any] ?? [:] : [:] }
        func list(_ i: Int) -> [String] { i < args.count ? (args[i] as? [Any] ?? []).compactMap { $0 as? String } : [] }
        switch method {
        case "config":
            return try value(["remoteWs": connection.workspace, "user": connection.user, "mode": connection.mode])
        case "request":
            let body: Any? = args.count > 2 ? args[2] : nil
            let answer = remote.json(str(0), str(1), body: body)
            let parsed: Any = JSON.parse(answer.body) ?? (String(data: answer.body, encoding: .utf8) ?? "")
            return try value(["status": answer.status, "body": parsed])
        case "getMeta":
            let meta = try store.meta("replica") ?? "null"
            return "{\"value\":\(meta)}"
        case "setMeta":
            try store.setMeta("replica", try JSON.string(args.first))
            return try value(nil)
        case "localChanges":
            return try value(try store.localChanges())
        case "acknowledge":
            try store.acknowledge(str(0), version: int(1))
            return try value(nil)
        case "page":
            let page = try store.pageJSON(str(0))
            return "{\"value\":\(page)}"
        case "writePage", "writeEdit":
            let v = try store.write(str(0), snapshot: obj(1), version: int(2), edit: method == "writeEdit")
            return try value(v)
        case "removePage":
            try store.remove(str(0), tombstone: false)
            return try value(nil)
        case "deleteHere":
            try store.remove(str(0), tombstone: true)
            return try value(nil)
        case "state":
            let state = try store.stateJSON(str(0))
            return "{\"value\":\(state)}"
        case "saveState":
            try store.saveState(str(0), args.count > 1 ? args[1] : nil)
            return try value(nil)
        case "pageOfBlock":
            return try value(try store.pageOfBlock(str(0)))
        case "readText":
            let text = files.read(str(0)).flatMap { String(data: $0, encoding: .utf8) }
            return try value(text)
        case "storeText":
            return try value(try files.store(Data(str(0).utf8), ext: str(1)))
        case "pullFiles":
            return try value(try pullFiles(list(0)))
        case "pushFiles":
            return try value(try pushFiles(list(0)))
        case "referencedFiles":
            return try value(try store.referencedFiles())
        case "conflict":
            try store.addConflict(obj(0))
            return try value(nil)
        case "note":
            try store.addNote(obj(0))
            return try value(nil)
        case "progress":
            let p = obj(0)
            onProgress?("\(p.int("done") ?? 0) / \(p.int("total") ?? 0)")
            return try value(nil)
        default:
            throw JSON.Failure(message: "the host has no \(method)")
        }
    }

    /// The names this iPad lacks, fetched; a file is kept only when it is
    /// what its name says (a captive portal's page never becomes a PDF).
    private func pullFiles(_ names: [String]) throws -> Int {
        var n = 0
        for name in names where FileStore.validName(name) && !files.has(name) {
            let answer = remote.download(name)
            if answer.status == 404 { continue } // the remote lost it too
            guard answer.status == 200 else { throw JSON.Failure(message: "fetching \(name): \(answer.status)") }
            guard FileStore.matches(name: name, data: answer.body) else { continue }
            try files.write(name, answer.body)
            n += 1
        }
        return n
    }

    /// The names the remote lacks, uploaded; the remote must store each
    /// under the same name (content-addressed on both sides).
    private func pushFiles(_ names: [String]) throws -> Int {
        var n = 0
        for name in names {
            guard let data = files.read(name) else { continue }
            if remote.head(name) == 200 { continue }
            let answer = remote.upload(name, data)
            guard answer.status == 200 || answer.status == 201 else {
                throw JSON.Failure(message: "uploading \(name): \(answer.status)")
            }
            let out = JSON.parse(answer.body) as? [String: Any] ?? [:]
            let stored = out.string("source_url").isEmpty ? out.string("url") : out.string("source_url")
            guard stored.hasSuffix("/\(name)") else { throw JSON.Failure(message: "the remote stored \(name) as \(stored)") }
            n += 1
        }
        return n
    }
}

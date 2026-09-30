import XCTest
@testable import Gamma

/// The Swift half of the replica: the store's page semantics (the host
/// interface frontend/src/replica/round.js names, the same rules as the
/// tests' in-memory host), file naming, and the JavaScript core as the app
/// loads it. The sync rules themselves are the shared JavaScript's and are
/// tested against a real server by the web suite (frontend/tests/e2e,
/// group `replica`).
final class StoreTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    private func page(_ title: String) -> [String: Any] {
        ["p1": ["parent": "root", "position": "a0", "content": title, "props": ["folder": "x"]],
         "b1": ["parent": "p1", "position": "a0", "content": "note", "props": [:]]]
    }

    func testWritesAreVersionedAndEditsAreMarkedUntilAcknowledged() throws {
        let store = try Store(path: dir.appendingPathComponent("s.sqlite").path)
        XCTAssertEqual(try store.version("p1"), 0)
        let v1 = try store.write("p1", snapshot: page("One"), version: 0, edit: true)
        XCTAssertEqual(v1, 1)
        XCTAssertEqual(try store.write("p1", snapshot: page("Two"), version: 0, edit: false), 0, "a stale version is refused")
        XCTAssertEqual(try store.localChanges()["pages"] as? [String], ["p1"])
        let v2 = try store.write("p1", snapshot: page("Two"), version: v1, edit: false)
        XCTAssertEqual(v2, 2)
        XCTAssertEqual(try store.localChanges()["pages"] as? [String], ["p1"], "a sync write is no edit made here")
        try store.acknowledge("p1", version: v2)
        XCTAssertEqual(try store.localChanges()["pages"] as? [String], [])
        XCTAssertEqual(try store.pageOfBlock("b1"), "p1")
        XCTAssertEqual(try store.roots().first?["content"] as? String, "Two")
        let json = try JSON.parse(try store.pageJSON("p1")) as? [String: Any]
        XCTAssertEqual(json?.int("version"), 2)
    }

    func testAnEditAfterTheAcknowledgedVersionStaysMarked() throws {
        let store = try Store(path: dir.appendingPathComponent("s.sqlite").path)
        let v1 = try store.write("p1", snapshot: page("One"), version: 0, edit: true)
        let v2 = try store.write("p1", snapshot: page("Two"), version: v1, edit: true)
        try store.acknowledge("p1", version: v1)
        XCTAssertEqual(try store.localChanges()["pages"] as? [String], ["p1"])
        try store.acknowledge("p1", version: v2)
        XCTAssertEqual(try store.localChanges()["pages"] as? [String], [])
    }

    func testAPageDeletedHereIsATombstoneUntilCarried() throws {
        let store = try Store(path: dir.appendingPathComponent("s.sqlite").path)
        _ = try store.write("p1", snapshot: page("One"), version: 0, edit: true)
        try store.remove("p1", tombstone: true)
        XCTAssertNil(store.snapshot("p1"))
        XCTAssertEqual(try store.localChanges()["deleted"] as? [String], ["p1"])
        XCTAssertNil(try store.pageOfBlock("b1"))
        try store.acknowledge("p1", version: 0)
        XCTAssertEqual(try store.localChanges()["deleted"] as? [String], [])
        try store.remove("p2", tombstone: false)
        XCTAssertEqual(try store.localChanges()["deleted"] as? [String], [], "deleted there: nothing to carry")
    }

    func testReferencedFilesFollowTheServersGrammar() throws {
        let store = try Store(path: dir.appendingPathComponent("s.sqlite").path)
        var p = page("One")
        p["p1"] = ["parent": "root", "position": "a0", "content": "One", "props": ["doc_id": "abc123"]]
        p["b1"] = ["parent": "p1", "position": "a0", "content": "see ![x](/api/uploads/ffee.png).",
                   "props": ["ink_url": "/api/uploads/0011.ink"]]
        _ = try store.write("p1", snapshot: p, version: 0, edit: false)
        XCTAssertEqual(try store.referencedFiles(), ["0011.ink", "abc123.pdf", "ffee.png"])
    }
}

final class FileStoreTests: XCTestCase {
    func testNamesAreTheServersHashNames() throws {
        let data = Data("{\"format\":\"gamma-ink\"}".utf8)
        XCTAssertEqual(FileStore.digest(data).count, 24)
        let name = FileStore.digest(data) + ".ink"
        XCTAssertTrue(FileStore.matches(name: name, data: data))
        XCTAssertFalse(FileStore.matches(name: name, data: Data("tampered".utf8)))
        XCTAssertTrue(FileStore.matches(name: "anything.pdf", data: Data("%PDF-1.4".utf8)))
        XCTAssertFalse(FileStore.matches(name: "anything.pdf", data: Data("<html>".utf8)), "a captive portal's page is no PDF")
        XCTAssertFalse(FileStore.validName("../escape.ink"))
    }
}

final class CoreTests: XCTestCase {
    func testTheBundledCoreComputesWhatTheWebAppComputes() throws {
        let core = try GammaCore(name: "test", source: GammaCore.bundledSource())
        let stroke = try core.pure("encodeStroke", [["id": "s1", "ch": "xypt", "t0": 5,
            "samples": [["x": 10, "y": 20, "p": 0.5, "t": 0], ["x": 30, "y": 25, "p": 0.7, "t": 8]]]]) as? [String: Any]
        XCTAssertEqual((stroke?["pts"] as? [NSNumber])?.map(\.intValue), [1000, 2000, 500, 0, 2000, 500, 700, 8])
        let vp = try core.pure("viewportTransform", [[0, 0, 612, 792], 0]) as? [String: Any]
        XCTAssertEqual((vp?["transform"] as? [NSNumber])?.map(\.doubleValue), [1, 0, 0, -1, 0, 792])
        let lines = try core.pure("paperLines", [["width": 200, "height": 150, "pattern": "ruled", "spacing": 50]]) as? [String: Any]
        XCTAssertEqual((lines?["lines"] as? [Any])?.count, 2)
    }

    func testEditsRunThroughTheHostAndLandInTheStore() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let connection = Connection(id: "t", server: URL(string: "https://example.invalid")!, user: "u", workspace: "w", workspaceName: "W")
        let replica = try Replica(connection: connection, token: "gamma_test", directory: dir)
        let book = try XCTUnwrap(try replica.edit("createNotebook", [["title": "On the iPad"]]) as? String)
        let sheet = try XCTUnwrap(try replica.edit("addSheet", [book]) as? String)
        let view = try XCTUnwrap(replica.view(of: book))
        XCTAssertEqual(view.string("kind"), "notebook")
        XCTAssertEqual(view.dict("notebook").array("sheets").count, 2)
        let file = try replica.pure("newCanvasInk", [595.28, 841.89])
        let stroke = try replica.pure("encodeStroke", [["id": "k1", "samples": [["x": 5, "y": 5], ["x": 9, "y": 9]]]])
        let ink = try replica.pure("appendStroke", [file, stroke])
        let url = try XCTUnwrap(try replica.edit("saveInk", [book, ["blockId": "g1", "ink": ink, "parent": sheet]]) as? String)
        XCTAssertTrue(url.hasPrefix("/api/uploads/") && url.hasSuffix(".ink"))
        XCTAssertEqual(replica.inkFile(url)?.array("strokes").count, 1)
        XCTAssertEqual(try replica.store.localChanges()["pages"] as? [String], [book], "an edit made here waits for a round")
    }
}

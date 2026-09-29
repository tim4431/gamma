import XCTest
@testable import GammaCore

/// A protocol-level origin: it has no knowledge of the replica database. Tests can
/// lose an acknowledgement after a commit, or edit while a request is in flight.
actor TestOrigin: GammaTransport {
    var pages: [String: GammaSnapshot]
    var seq: [String: Int] = [:]
    var forgottenResponses: [String: JSONValue] = [:]
    var failNextAcknowledgement = false
    var postCount = 0
    var beforePost: (@Sendable () async throws -> Void)?
    init(_ pages: [String: GammaSnapshot]) { self.pages = pages; for id in pages.keys { seq[id] = 1 } }
    func edit(page: String, block: String, text: String) { pages[page]?[block]?.content = text; seq[page, default: 0] += 1 }
    func text(page: String, block: String) -> String? { pages[page]?[block]?.content }
    func loseNextAnswer() { failNextAcknowledgement = true }
    func forgetReplayCache() { forgottenResponses = [:] }
    func onNextPost(_ body: @escaping @Sendable () async throws -> Void) { beforePost = body }
    func move(block: String, from: String, to: String, text: String) {
        guard var value = pages[from]?.removeValue(forKey: block) else { return }
        value.parent = to; value.content = text; pages[to]?[block] = value
        seq[from, default: 0] += 1; seq[to, default: 0] += 1
    }
    func response(_ value: JSONValue, status: Int = 200) throws -> GammaHTTPResponse { .init(status: status, data: try GammaJSON.data(value)) }
    func request(_ request: URLRequest) async throws -> GammaHTTPResponse {
        let path = request.url!.path, method = request.httpMethod ?? "GET"
        if path == "/api/sync/whoami" {
            return try response(.object(["user": .string("alice"), "workspace": .object(["id": .string("lab")]), "role": .string("owner"), "scope": .string("write"), "capabilities": .object(["ink_versions": .array([.number(1), .number(2)]), "ink_base_props": .bool(true), "notebooks": .number(1), "audio": .number(1)])]))
        }
        if path == "/api/sync/changes" {
            return try response(.object(["pages": .array(pages.keys.sorted().map { .object(["id": .string($0), "seq": .number(Double(seq[$0] ?? 0))]) }), "deleted": .array([]), "cursor": .string("2026-09-28T00:00:00Z"), "more": .bool(false)]))
        }
        if path.hasSuffix("/subtree") {
            let id = path.split(separator: "/")[2].description
            guard let tree = pages[id], tree[id] != nil else { return try response(.object(["detail": .string("missing")]), status: 404) }
            func nested(_ block: GammaBlock) throws -> JSONValue {
                var value = try JSONDecoder().decode(JSONValue.self, from: GammaJSON.data(block)).object!
                value["children"] = .array(try GammaTree.children(tree, of: block.id).map(nested))
                return .object(value)
            }
            return try response(.object(["block": try nested(tree[id]!), "seq": .number(Double(seq[id] ?? 0))]))
        }
        if path == "/api/pages", method == "POST" {
            let value = try JSONDecoder().decode(JSONValue.self, from: request.httpBody!)
            let id = value["id"]!.string!
            if pages[id] != nil { return try response(.object(["detail": .string("exists")]), status: 409) }
            let root = GammaBlock(id: id, parent: "root", content: value["title"]?.string ?? "", properties: value["properties"]?.object ?? [:])
            pages[id] = [id: root]; seq[id] = 0
            return try response(try JSONDecoder().decode(JSONValue.self, from: GammaJSON.data(root)), status: 201)
        }
        if path.hasSuffix("/ops"), method == "POST" {
            let id = path.split(separator: "/")[2].description
            guard let before = pages[id] else { return try response(.object(["detail": .string("missing")]), status: 404) }
            let value = try JSONDecoder().decode(JSONValue.self, from: request.httpBody!)
            let batch = value["batch"]!.string!
            if let replay = forgottenResponses[batch] { return try response(replay) }
            let ops = try JSONDecoder().decode([GammaOperation].self, from: GammaJSON.data(value["ops"]!))
            for op in ops where op.op == "insert" {
                if pages.contains(where: { $0.key != id && $0.value[op.id] != nil }) { return try response(.object(["detail": .string("block \(op.id) is outside this page")]), status: 403) }
            }
            if let callback = beforePost { beforePost = nil; try await callback() }
            do { pages[id] = try GammaTree.apply(ops, to: pages[id] ?? before, pageID: id) }
            catch GammaError.propertyChanged(let block) { return try response(.object(["detail": .string("changed"), "conflict": .string("property_changed"), "id": .string(block)]), status: 409) }
            seq[id, default: 0] += 1; postCount += 1
            let result: JSONValue = .object(["seq": .number(Double(seq[id]!)), "ops": try JSONDecoder().decode(JSONValue.self, from: GammaJSON.data(ops))])
            forgottenResponses[batch] = result
            if failNextAcknowledgement { failNextAcknowledgement = false; throw URLError(.networkConnectionLost) }
            return try response(result)
        }
        if method == "DELETE", path.hasPrefix("/api/blocks/") {
            let id = path.split(separator: "/").last!.description
            pages.removeValue(forKey: id)
            return try response(.object(["ok": .bool(true)]))
        }
        return try response(.object(["detail": .string("unexpected request: \(method) \(path)")]), status: 404)
    }
}

final class MirrorRecoveryTests: XCTestCase {
    func initial(page: String = "page", note: String = "note", text: String = "hello") -> GammaSnapshot {
        [page: GammaBlock(id: page, parent: "root", content: page), note: GammaBlock(id: note, parent: page, content: text)]
    }
    func repository(_ origin: TestOrigin) async throws -> GammaRepository {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let value = try GammaRepository(directory: directory, transport: origin)
        try await value.configureMirror(origin: URL(string: "https://gamma.invalid")!, account: "alice", workspaceID: "lab", token: "test")
        _ = try await value.sync()
        return value
    }
    func testUntouchedCloneNeverWritesBack() async throws {
        let origin = TestOrigin(["page": initial()])
        let repo = try await repository(origin)
        _ = try await repo.sync()
        let writes = await origin.postCount
        XCTAssertEqual(writes, 0)
    }
    func testLostAcknowledgementAndServerRestartDoNotDoubleText() async throws {
        let origin = TestOrigin(["page": initial()])
        let repo = try await repository(origin)
        try await repo.apply(pageID: "page", ops: [.init(op: "set", id: "note", content: "hello!", base: "hello")])
        await origin.loseNextAnswer()
        let interrupted = try await repo.sync()
        XCTAssertNotNil(interrupted.lastError)
        await origin.forgetReplayCache()
        await origin.edit(page: "page", block: "note", text: "hello! later")
        let directory = await repo.directory
        let reopened = try GammaRepository(directory: directory, transport: origin)
        await reopened.setMirrorToken("test")
        let settled = try await reopened.sync()
        XCTAssertNil(settled.lastError)
        let document = try await reopened.document(id: "page")
        XCTAssertEqual(document.blocks.first { $0.id == "note" }?.content, "hello! later")
        let remoteText = await origin.text(page: "page", block: "note")
        XCTAssertEqual(remoteText, "hello! later")
    }
    func testTypingDuringPushSurvivesAndPushesNextRound() async throws {
        let origin = TestOrigin(["page": initial()])
        let repo = try await repository(origin)
        try await repo.apply(pageID: "page", ops: [.init(op: "set", id: "note", content: "hello first", base: "hello")])
        await origin.onNextPost {
            try await repo.apply(pageID: "page", ops: [.init(op: "set", id: "note", content: "hello first second", base: "hello first")])
        }
        let first = try await repo.sync()
        XCTAssertTrue(first.pending)
        let document = try await repo.document(id: "page")
        XCTAssertEqual(document.blocks.first { $0.id == "note" }?.content, "hello first second")
        _ = try await repo.sync()
        let remoteText = await origin.text(page: "page", block: "note")
        XCTAssertEqual(remoteText, "hello first second")
    }
    func testCrossPageMoveCarriesLocalAndRemoteText() async throws {
        let origin = TestOrigin(["a-page": initial(page: "a-page", text: "alpha beta"), "z-page": ["z-page": GammaBlock(id: "z-page", parent: "root")]])
        let repo = try await repository(origin)
        try await repo.apply(pageID: "a-page", ops: [.init(op: "set", id: "note", content: "ALPHA beta", base: "alpha beta")])
        await origin.move(block: "note", from: "a-page", to: "z-page", text: "alpha BETA")
        _ = try await repo.sync()
        _ = try await repo.sync()
        let document = try await repo.document(id: "z-page")
        XCTAssertEqual(document.blocks.first { $0.id == "note" }?.content, "ALPHA BETA")
        let source = try await repo.document(id: "a-page")
        XCTAssertNil(source.blocks.first { $0.id == "note" })
    }
}

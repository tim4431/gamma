import XCTest
@testable import GammaCore

final class GammaCoreTests: XCTestCase {
    func fixture() throws -> JSONValue {
        let url = Bundle.module.url(forResource: "mirror-native-v1", withExtension: "json", subdirectory: "Fixtures")!
        return try JSONDecoder().decode(JSONValue.self, from: Data(contentsOf: url))
    }
    func testSharedTextAndAssetFixtures() throws {
        let value = try fixture()
        for item in value["text"]!.array! {
            let merged = GammaTextMerge.merge(base: item["base"]!.string!, ours: item["ours"]!.string!, theirs: item["theirs"]!.string!)
            XCTAssertEqual(merged.text, item["result"]!.string!, item["name"]!.string!)
            XCTAssertEqual(JSONValue.bool(merged.clean), item["clean"]!)
        }
        for item in value["assets"]!.array! { XCTAssertEqual(GammaAssets.digest(Data(item["text"]!.string!.utf8)), item["sha24"]!.string!) }
    }
    func testPositionAppendUsesValidFractionalKeys() {
        XCTAssertEqual(GammaTree.nextPosition(after: nil), "a0")
        XCTAssertEqual(GammaTree.nextPosition(after: "a0"), "a1")
        XCTAssertEqual(GammaTree.nextPosition(after: "az"), "b00")
        XCTAssertEqual(GammaTree.nextPosition(after: "Zz"), "a0")
    }
    func testIndependentEditsAndInkCollisionPreserveBothFiles() {
        let root = GammaBlock(id: "page", parent: "root")
        let note = GammaBlock(id: "ink", parent: "page", content: "caption", properties: ["ink_url": .string("/api/uploads/aaaaaaaaaaaaaaaaaaaaaaaa.ink")])
        let base = [root.id: root, note.id: note]
        var local = base, remote = base
        local["ink"]!.properties["ink_url"] = .string("/api/uploads/bbbbbbbbbbbbbbbbbbbbbbbb.ink")
        remote["ink"]!.properties["ink_url"] = .string("/api/uploads/cccccccccccccccccccccccc.ink")
        let result = GammaReconciler.reconcile(pageID: "page", base: base, local: local, remote: remote)
        XCTAssertEqual(result.snapshot["ink"]!.properties["ink_url"], remote["ink"]!.properties["ink_url"])
        let variants = result.snapshot.values.filter { $0.properties["ink_conflict"] != nil }
        XCTAssertEqual(variants.count, 1)
        XCTAssertEqual(variants[0].properties["ink_url"], local["ink"]!.properties["ink_url"])
        XCTAssertEqual(variants[0].content, "caption")
    }
    func testLocalDurabilityAndEmptyInkKeepsCaption() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let repository = try GammaRepository(directory: directory)
        let document = try await repository.createNotebook(title: "Notebook")
        let sheet = document.blocks.first { $0.properties["type"]?.string == "notebook-sheet" }!
        try await repository.apply(pageID: document.id, ops: [.init(op: "insert", id: "ink", parent: sheet.id, content: "Keep caption")])
        let ink: JSONValue = .object(["format": .string("gamma-ink"), "version": .number(2), "space": .object(["kind": .string("notebook-page"), "sheet_id": .string(sheet.id), "width": .number(600), "height": .number(800)]), "strokes": .array([])])
        _ = try await repository.saveInk(pageID: document.id, blockID: "ink", parentID: sheet.id, ink: GammaJSON.data(ink))
        let reopened = try GammaRepository(directory: directory)
        let saved = try await reopened.document(id: document.id)
        XCTAssertEqual(saved.blocks.first { $0.id == "ink" }?.content, "Keep caption")
        XCTAssertNotNil(saved.blocks.first { $0.id == "ink" }?.properties["ink_url"])
    }
}

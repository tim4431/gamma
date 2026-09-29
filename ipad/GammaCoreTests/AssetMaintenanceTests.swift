import XCTest
@testable import GammaCore

final class AssetMaintenanceTests: XCTestCase {
    func repository() throws -> GammaRepository {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        return try GammaRepository(directory: directory)
    }

    func age(_ url: URL) throws {
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-8 * 24 * 60 * 60)], ofItemAtPath: url.path)
    }

    func testCollectionRetainsEveryRecoveryRootAndYoungAssets() async throws {
        let repo = try repository()
        var references: [String: String] = [:], files: [String: URL] = [:]
        for key in ["current", "base", "pending", "conflict", "journal", "young", "reused", "unused"] {
            let reference = try await repo.storeAsset(data: Data(key.utf8), extension: "bin")
            let file = try await repo.assetURL(reference: reference)
            references[key] = reference; files[key] = file
            if key != "young" { try age(file) }
        }
        let pdf = try await repo.storeAsset(data: Data("%PDF-old-root".utf8), extension: "pdf")
        let pdfFile = try await repo.assetURL(reference: pdf)
        try age(pdfFile)
        let docID = pdfFile.deletingPathExtension().lastPathComponent
        _ = try await repo.createPage(title: "Current", properties: ["attachment": .string(references["current"]!), "doc_id": .string(docID)])
        let base = GammaBlock(id: "checkpoint", parent: "root", properties: ["attachment": .string(references["base"]!)])
        let pending = GammaOperation(op: "set", id: base.id, props: ["attachment": .string(references["pending"]!)])
        try await repo.saveCheckpoint(base.id, PageCheckpoint(base: [base.id: base], pending: [PendingBatch(id: "batch", ops: [pending], attempted: true)]))
        try await repo.record([GammaConflict(pageID: base.id, blockID: base.id, kind: "ink", mine: references["conflict"]!)])
        try await repo.journal(base.id, source: "local", ops: [.init(op: "set", id: base.id, base: references["journal"]!)])
        _ = try await repo.storeAsset(data: Data("reused".utf8), extension: "bin")

        let directory = await repo.assets
        for name in ["notes.txt", ".partial-unfinished"] {
            let file = directory.appendingPathComponent(name)
            try Data("unknown".utf8).write(to: file); try age(file)
        }
        let removed = try await repo.maintainAssets()
        XCTAssertEqual(removed, 1)
        XCTAssertFalse(FileManager.default.fileExists(atPath: files["unused"]!.path))
        for (key, file) in files where key != "unused" {
            XCTAssertTrue(FileManager.default.fileExists(atPath: file.path), key)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: pdfFile.path))
        for name in ["notes.txt", ".partial-unfinished"] {
            XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent(name).path))
        }
    }

    func testUnreadableCheckpointPreventsAnyDeletion() async throws {
        let repo = try repository()
        let reference = try await repo.storeAsset(data: Data("unused".utf8), extension: "bin")
        let file = try await repo.assetURL(reference: reference)
        try age(file)
        try await repo.installUnreadableCheckpoint()
        do { _ = try await repo.maintainAssets(); XCTFail("An unreadable retention root must stop collection") }
        catch { XCTAssertTrue(FileManager.default.fileExists(atPath: file.path)) }
    }

    func testLargeBacklogUsesBoundedPassesWithoutWaitingAnotherDay() async throws {
        let repo = try repository()
        for index in 0..<201 {
            let reference = try await repo.storeAsset(data: Data("revision-\(index)".utf8), extension: "bin")
            try age(try await repo.assetURL(reference: reference))
        }
        let first = try await repo.maintainAssets()
        let firstTimestamp = try await repo.maintenanceTimestamp()
        XCTAssertEqual(first, 200)
        XCTAssertEqual(firstTimestamp, 0)
        let second = try await repo.maintainAssets()
        let secondTimestamp = try await repo.maintenanceTimestamp()
        XCTAssertEqual(second, 1)
        XCTAssertGreaterThan(secondTimestamp, 0)
    }

    func testSuccessfulSyncCollectsAndMaintenanceRejectsActiveRound() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let root = GammaBlock(id: "page", parent: "root", content: "hello")
        let origin = TestOrigin([root.id: [root.id: root]])
        let repo = try GammaRepository(directory: directory, transport: origin)
        let reference = try await repo.storeAsset(data: Data("unused".utf8), extension: "bin")
        let file = try await repo.assetURL(reference: reference)
        try age(file)
        try await repo.configureMirror(origin: URL(string: "https://gamma.invalid")!, account: "alice", workspaceID: "lab", token: "test")
        let synced = try await repo.sync()
        XCTAssertNil(synced.lastError)
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        try await repo.apply(pageID: root.id, ops: [.init(op: "set", id: root.id, content: "hello!", base: "hello")])
        await origin.onNextPost {
            do { _ = try await repo.maintainAssets(); XCTFail("Collection must not run during a request") }
            catch GammaError.busy { }
        }
        let edited = try await repo.sync()
        XCTAssertNil(edited.lastError)
    }
}

private extension GammaRepository {
    func maintenanceTimestamp() throws -> Double { try db.get("asset_maintenance_at", as: Double.self) ?? 0 }
    func installUnreadableCheckpoint() throws {
        try db.run("INSERT INTO sync_pages(page_id,value) VALUES (?,?)", ["broken", "{broken"])
    }
}

import XCTest
@testable import Gamma

/// The files the system opens in the app (Shell/OpenedFiles.swift): held as
/// copies until the page is done with them, listed, and read back in chunks
/// that put the file together again. The page's half, which reads them and
/// decides what each becomes, is the `ipad` browser group's.
final class OpenedFilesTests: XCTestCase {
    /// A store over folders of its own, removed after the test.
    @MainActor
    private func store() throws -> (root: URL, opened: OpenedFiles) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("OpenedFilesTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return (root, OpenedFiles(folder: root.appendingPathComponent("Opened"), inbox: root.appendingPathComponent("Inbox")))
    }

    private func file(_ name: String, _ data: Data, in root: URL, folder: String = "Source") throws -> URL {
        let dir = root.appendingPathComponent(folder)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent(name)
        try data.write(to: url)
        return url
    }

    /// The file as the page reads it: chunk after chunk from offset 0 until
    /// it has `size` bytes or a chunk comes back empty.
    @MainActor
    private func readBack(_ opened: OpenedFiles, _ id: String, size: Int) throws -> Data {
        var out = Data()
        while out.count < size {
            let reply = try XCTUnwrap(opened.chunk(id: id, offset: out.count))
            let data = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(reply["data"] as? String)))
            if data.isEmpty { break }
            out.append(data)
        }
        return out
    }

    @MainActor
    func testAnOpenedFileIsListedAndReadsBackWhole() async throws {
        let (root, opened) = try store()
        // Over two chunks and a part, so the offsets carry on from each other.
        let bytes = Data((0..<(OpenedFiles.chunkBytes * 2 + 1234)).map { UInt8(truncatingIfNeeded: $0 &* 31) })
        let source = try file("Paper (draft).pdf", bytes, in: root)
        var arrived = 0
        opened.onArrival = { arrived += 1 }
        let taken = await opened.take(source)
        XCTAssertTrue(taken)
        XCTAssertEqual(arrived, 1, "the page is told")
        let held = try XCTUnwrap(opened.files.first)
        XCTAssertEqual(held.name, "Paper (draft).pdf")
        XCTAssertEqual(held.size, bytes.count)
        XCTAssertEqual(held.type, "application/pdf")
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path), "a file opened from elsewhere is copied, not taken")
        let listed = try XCTUnwrap(opened.listing["files"] as? [[String: Any]])
        XCTAssertEqual(listed.count, 1)
        XCTAssertEqual(listed[0]["id"] as? String, held.id)
        XCTAssertEqual(listed[0]["name"] as? String, held.name)
        XCTAssertEqual(listed[0]["size"] as? Int, bytes.count)
        XCTAssertEqual(listed[0]["type"] as? String, "application/pdf")
        XCTAssertEqual(try readBack(opened, held.id, size: held.size), bytes)
        XCTAssertEqual(opened.chunk(id: held.id, offset: held.size)?["data"] as? String, "", "the end reads as an empty chunk")
        XCTAssertNil(opened.chunk(id: held.id, offset: held.size + 1), "past the end")
        XCTAssertNil(opened.chunk(id: held.id, offset: -1))
        XCTAssertNil(opened.chunk(id: "no-such-file", offset: 0))
    }

    @MainActor
    func testDoneDropsTheCopyAndNothingElse() async throws {
        let (root, opened) = try store()
        let first = try file("a.md", Data("# A".utf8), in: root)
        let second = try file("b.note", Data("PK".utf8), in: root)
        _ = await opened.take(first)
        _ = await opened.take(second)
        XCTAssertEqual(opened.files.map(\.name), ["a.md", "b.note"], "in the order they came")
        let copy = try XCTUnwrap(opened.files.first).url
        opened.done(id: opened.files[0].id)
        XCTAssertEqual(opened.files.map(\.name), ["b.note"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: copy.path), "the copy goes")
        XCTAssertTrue(FileManager.default.fileExists(atPath: first.path), "the original stays")
        opened.done(id: "no-such-file")
        XCTAssertEqual(opened.files.count, 1)
    }

    @MainActor
    func testAFileInTheInboxIsMovedOut() async throws {
        let (root, opened) = try store()
        let inboxed = try file("mail.pdf", Data("%PDF-1.4".utf8), in: root, folder: "Inbox")
        let taken = await opened.take(inboxed)
        XCTAssertTrue(taken)
        XCTAssertFalse(FileManager.default.fileExists(atPath: inboxed.path), "the Inbox Files shows keeps no copy")
        let held = try XCTUnwrap(opened.files.first)
        XCTAssertEqual(try readBack(opened, held.id, size: held.size), Data("%PDF-1.4".utf8))
    }

    @MainActor
    func testAFolderOrAMissingFileIsNotTaken() async throws {
        let (root, opened) = try store()
        let folder = root.appendingPathComponent("Bundle.nbn", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let tookFolder = await opened.take(folder)
        let tookMissing = await opened.take(root.appendingPathComponent("gone.pdf"))
        let tookWeb = await opened.take(try XCTUnwrap(URL(string: "https://example.org/a.pdf")))
        XCTAssertFalse(tookFolder)
        XCTAssertFalse(tookMissing)
        XCTAssertFalse(tookWeb)
        XCTAssertTrue(opened.files.isEmpty)
    }

    @MainActor
    func testAnEarlierRunsCopiesGoAtLaunch() async throws {
        let (root, opened) = try store()
        _ = await opened.take(try file("left.pdf", Data("%PDF".utf8), in: root))
        XCTAssertTrue(FileManager.default.fileExists(atPath: opened.folder.path))
        let again = OpenedFiles(folder: opened.folder, inbox: opened.inbox)
        XCTAssertTrue(again.files.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: opened.folder.path))
    }
}

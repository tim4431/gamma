import XCTest
import PencilKit
@testable import GammaIPad

@MainActor
final class GammaInkTests: XCTestCase {
    private let space = GammaSpace(page: 2, width: 612, height: 792)
    private func sample(_ id: String = "stroke") -> GammaStroke {
        GammaStroke(id: id, tool: "pen", brush: "monoline", color: "#1d4ed8", size: 2, opacity: 1,
                    pen: true, t0: 100, ch: "xyptaz", pts: [1000, 2000, 200, 0, 45, 90, 3000, 0, 800, 16, 50, 100])
    }
    func testUneditedImportPreservesEveryChannelAndID() throws {
        let ink = GammaInk(space: space, strokes: [sample(), sample("second")])
        let codec = GammaInkCodec()
        let drawing = try codec.drawing(from: ink)
        XCTAssertEqual(try codec.export(drawing, space: space), ink)
        // The binary recovery archive must not cause a second smoothing pass.
        let recovered = try PKDrawing(data: drawing.dataRepresentation())
        XCTAssertEqual(try codec.export(recovered, space: space), ink)
    }
    func testNewNativeBrushesExportEditableOpenStrokes() throws {
        for type in [PKInkingTool.InkType.pen, .monoline, .marker] {
            let points = [PKStrokePoint(location: CGPoint(x: 10, y: 20), timeOffset: 0,
                            size: CGSize(width: 2, height: 2), opacity: 1, force: 0.2, azimuth: 0.5, altitude: 1),
                          PKStrokePoint(location: CGPoint(x: 50, y: 25), timeOffset: 0.1,
                            size: CGSize(width: 3, height: 3), opacity: 1, force: 0.8, azimuth: 0.6, altitude: 1)]
            let stroke = PKStroke(ink: PKInk(type, color: .blue),
                path: PKStrokePath(controlPoints: points, creationDate: Date(timeIntervalSince1970: 100)))
            let codec = GammaInkCodec()
            let ink = try codec.export(PKDrawing(strokes: [stroke]), space: space)
            let output = try XCTUnwrap(ink.strokes.first)
            XCTAssertEqual(output.tool, type == .marker ? "highlighter" : "pen")
            XCTAssertEqual(output.brush, type == .monoline ? "monoline" : nil)
            XCTAssertEqual(output.ch, "xyptaz")
            XCTAssertGreaterThan(output.pts.count, 12, "The curve is interpolated, not just its control polygon")
            XCTAssertEqual(Double(output.pts[0]), 1000, accuracy: 5)
            XCTAssertEqual(Double(output.pts[1]), 2000, accuracy: 5)
            XCTAssertEqual(try codec.export(PKDrawing(strokes: [stroke]), space: space), ink)
        }
    }
    func testUnsupportedBrushCannotSilentlyBecomeAPen() throws {
        let point = PKStrokePoint(location: .zero, timeOffset: 0, size: CGSize(width: 2, height: 2),
                                  opacity: 1, force: 0.5, azimuth: 0, altitude: 1)
        let stroke = PKStroke(ink: PKInk(.pencil, color: .black),
                              path: PKStrokePath(controlPoints: [point], creationDate: Date()))
        XCTAssertThrowsError(try GammaInkCodec().export(PKDrawing(strokes: [stroke]), space: space))
    }
    func testLegacyDefaultsAndBadChannels() throws {
        let json = #"{"id":"old","ch":"xy","pts":[100,200]}"#.data(using: .utf8)!
        let stroke = try JSONDecoder().decode(GammaStroke.self, from: json)
        XCTAssertEqual(stroke.tool, "pen")
        XCTAssertNil(stroke.brush)
        var ink = GammaInk(space: space, strokes: [stroke])
        XCTAssertNoThrow(try GammaInkCodec().drawing(from: ink))
        ink.strokes[0].ch = "xxy"
        XCTAssertThrowsError(try ink.validate())
    }
    func testServerAddressesRejectCredentialsAndCrossOriginPorts() {
        XCTAssertNotNil(ServerAddress.parse("https://gamma.example/"))
        XCTAssertNil(ServerAddress.parse("http://gamma.example"))
        XCTAssertNil(ServerAddress.parse("https://user:password@gamma.example"))
        XCTAssertNil(ServerAddress.parse("https://gamma.example/api"))
        XCTAssertFalse(ServerAddress.sameOrigin(URL(string: "https://gamma.example")!, URL(string: "https://gamma.example:444")!))
        XCTAssertTrue(ServerAddress.sameOrigin(URL(string: "https://gamma.example")!, URL(string: "https://gamma.example:443/path")!))
    }
    func testDraftIsolationAndCorruptionAreExplicit() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try InkDraftStore(directory: directory)
        var request = InkRequest(requestId: "request", user: "alice", workspace: "lab", pageId: "paper",
            document: "/api/uploads/paper.pdf", blockId: "ink", parentId: "paper", expectedURL: nil,
            existing: false, ink: GammaInk(space: space, strokes: [sample()]), background: [], image: "data:image/png;base64,")
        let key = try request.draftKey(origin: "https://gamma.example/")
        request.user = "bob"
        XCTAssertNotEqual(try request.draftKey(origin: "https://gamma.example/"), key)
        request.user = "alice"; request.workspace = "other"
        XCTAssertNotEqual(try request.draftKey(origin: "https://gamma.example/"), key)
        XCTAssertNotEqual(try request.draftKey(origin: "https://other.example/"), key)
        let draft = InkDraft(key: key, blockId: "ink", expectedURL: nil, baseInk: request.ink, drawing: Data([1, 2]))
        try store.write(draft)
        XCTAssertEqual(try store.read(key)?.blockId, "ink")
        try Data("corrupt".utf8).write(to: directory.appendingPathComponent(key + ".json"))
        XCTAssertThrowsError(try store.read(key))
        XCTAssertThrowsError(try store.read("../escape"))
    }
}

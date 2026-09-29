import XCTest
import UIKit
@testable import GammaIPad

@MainActor
final class InkEngineTests: XCTestCase {
    func fixture() -> [String: Any] {
        ["id": "stable", "tool": "pen", "color": "#1d4ed8", "size": 2, "opacity": 1,
         "pen": true, "t0": 1000, "ch": "xyptaz", "pts": [1000, 2000, 200, 0, 45, 90, 3000, 0, 800, 16, 50, 100]]
    }
    func testSharedCodecAndTransformPreserveTimingIdentity() throws {
        let engine = try InkEngine(), stroke = fixture()
        var ink = try engine.object("newNotebookInk", ["sheet1", 612, 792]); ink["strokes"] = [stroke]
        let translated = try engine.object("translateStrokes", [ink, ["stable"], 12, 8])
        let changed = try XCTUnwrap((translated["strokes"] as? [[String: Any]])?.first)
        XCTAssertEqual(changed["id"] as? String, "stable")
        XCTAssertEqual(changed["t0"] as? Int, 1000)
        let originalPoints = try XCTUnwrap(stroke["pts"] as? [Int]), changedPoints = try XCTUnwrap(changed["pts"] as? [Int])
        XCTAssertEqual(Array(changedPoints.dropFirst(2)), Array(originalPoints.dropFirst(2)))
        XCTAssertEqual(changedPoints[0], originalPoints[0] + 1200)
        XCTAssertEqual(changedPoints[1], originalPoints[1] + 800)
        XCTAssertEqual(ink["version"] as? Int, 2)
    }
    func testSharedOutlineRendersNonemptyNativeImage() throws {
        let engine = try InkEngine()
        let shape = try engine.object("geometry", [fixture()])
        XCTAssertGreaterThan((shape["points"] as? [[Double]])?.count ?? 0, 3)
        var failure: Error?
        let image = UIGraphicsImageRenderer(size: CGSize(width: 100, height: 100)).image { renderer in
            do { try engine.draw(fixture(), in: renderer.cgContext) } catch { failure = error }
        }
        XCTAssertNil(failure)
        let data = try XCTUnwrap(image.cgImage?.dataProvider?.data)
        let bytes = CFDataGetBytePtr(data)!
        XCTAssertTrue((0..<CFDataGetLength(data)).contains { bytes[$0] != 0 })
        let attachment = XCTAttachment(image: image); attachment.name = "Native canonical Gamma ink"; attachment.lifetime = .keepAlways; add(attachment)
    }
    func testUntimedImportStaysUntimedWhenRestyled() throws {
        let engine = try InkEngine()
        var ink = try engine.object("newInk", [1, 612, 792])
        ink["strokes"] = [["id": "old", "tool": "pen", "color": "#000000", "size": 2, "opacity": 1, "pen": false, "ch": "xy", "pts": [100, 200, 300, 400]]]
        let changed = try engine.object("restyleStrokes", [ink, ["old"], ["color": "#ff0000"]])
        let stroke = try XCTUnwrap((changed["strokes"] as? [[String: Any]])?.first)
        XCTAssertNil(stroke["t0"]); XCTAssertEqual(stroke["ch"] as? String, "xy"); XCTAssertEqual(stroke["pen"] as? Bool, false)
    }
}

import UIKit
import XCTest
@testable import Gamma

/// The host contract's Swift half (Shell/Bridge.swift): what the web posts
/// decodes as docs/dev/ipad.md "The host contract" says, and what the shell
/// sends encodes the same way. The web half is tested in
/// frontend/tests/host.test.mjs and the `ipad` browser group.
final class BridgeTests: XCTestCase {
    private func json(_ message: HostMessage) throws -> [String: Any] {
        let data = try JSONEncoder().encode(message)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testAClaimDecodesWithItsTool() throws {
        let body: [String: Any] = ["type": "claim", "pointerId": 7, "at": ["x": 120.5, "y": 300],
                                   "tool": ["kind": "highlighter", "color": "#fde047", "size": 18, "opacity": 0.35]]
        XCTAssertEqual(try WebMessage(body: body),
                       .claim(pointerId: 7, at: CGPoint(x: 120.5, y: 300),
                              tool: InkTool(kind: .highlighter, color: "#fde047", size: 18, opacity: 0.35)))
    }

    func testAToolWithoutOpacityIsOpaque() throws {
        let body: [String: Any] = ["type": "claim", "pointerId": 1, "at": ["x": 0, "y": 0],
                                   "tool": ["kind": "pen", "color": "#111111", "size": 2]]
        guard case let .claim(_, _, tool) = try WebMessage(body: body) else { return XCTFail("not a claim") }
        XCTAssertEqual(tool.opacity, 1)
    }

    func testThemeAndServersDecode() throws {
        XCTAssertEqual(try WebMessage(body: ["type": "theme", "bg": "#1e1e1e", "scheme": "dark"]),
                       .theme(bg: "#1e1e1e", scheme: .dark))
        XCTAssertEqual(try WebMessage(body: ["type": "servers"]), .servers)
    }

    func testAnythingElseIsRefused() {
        XCTAssertThrowsError(try WebMessage(body: ["type": "open-page"]), "an unknown type")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1, "at": ["x": 0, "y": 0]]),
                             "a claim without its tool")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1,
                                                   "tool": ["kind": "pen", "color": "#000", "size": 1]]),
                             "a claim without its place")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1, "at": ["x": 0, "y": 0],
                                                   "tool": ["kind": "eraser", "color": "#000", "size": 1]]), "not a drawing tool")
        XCTAssertThrowsError(try WebMessage(body: ["type": "theme", "bg": "#fff", "scheme": "sepia"]), "an unknown scheme")
        XCTAssertThrowsError(try WebMessage(body: "claim"), "not an object")
    }

    func testAStrokeEncodesItsSamples() throws {
        let out = try json(.stroke(pointerId: 3, samples: [[10, 20.5, 0.4, 0], [11, 21, 0.5, 4.2]]))
        XCTAssertEqual(out["type"] as? String, "stroke")
        XCTAssertEqual(out["pointerId"] as? Int, 3)
        XCTAssertEqual(out["samples"] as? [[Double]], [[10, 20.5, 0.4, 0], [11, 21, 0.5, 4.2]])
    }

    func testAPencilGestureCarriesItsAction() throws {
        let out = try json(.pencil(gesture: .squeeze, action: "showInkAttributes"))
        XCTAssertEqual(out["type"] as? String, "pencil")
        XCTAssertEqual(out["gesture"] as? String, "squeeze")
        XCTAssertEqual(out["action"] as? String, "showInkAttributes")
        XCTAssertEqual(Set(out.keys), ["type", "gesture", "action"])
    }

    func testTheUserScriptDeclaresTheHost() {
        let script = HostInfo(version: "0.2.0").userScript
        XCTAssertTrue(script.contains(#"{"ink":true,"pencil":true,"platform":"ipad","servers":true,"version":"0.2.0"}"#))
        XCTAssertTrue(script.contains(#"setAttribute("data-host", "ipad")"#))
        XCTAssertTrue(script.contains("queue.push({ msg, resolve, reject })"), "the stub queues until host.js drains it")
    }
}

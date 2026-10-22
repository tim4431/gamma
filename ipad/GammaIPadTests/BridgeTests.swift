import UIKit
import XCTest
@testable import Gamma

/// The host contract's Swift half (Shell/Bridge.swift): what the web posts
/// decodes as docs/dev/ipad.md "The host contract" says, and what the shell
/// sends encodes the same way. The web half is tested in
/// frontend/tests/host.test.mjs, frontend/tests/hostInk.test.mjs and the
/// `ipad` browser group.
final class BridgeTests: XCTestCase {
    private struct NotAClaim: Error {}

    private func json(_ message: HostMessage) throws -> [String: Any] {
        let data = try JSONEncoder().encode(message)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func tool(_ keys: [String: Any]) throws -> InkTool {
        var spec: [String: Any] = ["kind": "pen", "color": "#1f4fd8", "size": 3]
        spec.merge(keys) { _, new in new }
        let body: [String: Any] = ["type": "claim", "pointerId": 61, "at": ["x": 212.5, "y": 480], "tool": spec]
        guard case let .claim(_, _, decoded) = try WebMessage(body: body) else { throw NotAClaim() }
        return decoded
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

    func testAToolWithoutItsLookDrawsThePlainLine() throws {
        let plain = try tool([:])
        XCTAssertEqual(plain.brush, .pen)
        XCTAssertTrue(plain.pressure)
        XCTAssertNil(plain.dash)
        XCTAssertFalse(plain.multiply)
        XCTAssertEqual(plain.stabilize, 0)
        XCTAssertNil(plain.snap)
    }

    func testAClaimCarriesThePresetsLook() throws {
        // the claim hostInk.test.mjs makes for a dashed Brush preset, plus a blend and a snap
        let look = try tool(["opacity": 1, "brush": "brush", "pressure": false, "dash": [6, 6], "blend": "multiply",
                             "stabilize": 2, "snap": ["x0": 10, "y0": 20, "angle": 15]])
        XCTAssertEqual(look, InkTool(kind: .pen, color: "#1f4fd8", size: 3, opacity: 1, brush: .brush, pressure: false,
                                     dash: [6, 6], multiply: true, stabilize: 2,
                                     snap: InkTool.Snap(x0: 10, y0: 20, angle: 15)))
        XCTAssertEqual(try tool(["brush": "monoline"]).brush, .monoline)
        XCTAssertEqual(try tool(["brush": "pencil"]).brush, .pencil)
        XCTAssertEqual(try tool(["brush": "pen"]).brush, .pen)
        XCTAssertEqual(try tool(["dash": [0.3, 6]]).dash, [0.3, 6], "dotted: a short on, a long off")
    }

    func testALookTheShellCannotReadIsDroppedNotTheClaim() throws {
        XCTAssertEqual(try tool(["kind": "eraser"]).kind, .pen, "a kind but the highlighter draws as a pen")
        XCTAssertEqual(try tool(["kind": "calligraphy"]).kind, .pen)
        XCTAssertEqual(try tool(["brush": "charcoal"]).brush, .pen, "an unknown brush is the plain Pen")
        XCTAssertEqual(try tool(["brush": 7]).brush, .pen)
        XCTAssertTrue(try tool(["pressure": "no"]).pressure)
        XCTAssertNil(try tool(["dash": [6]]).dash, "a dash is two lengths")
        XCTAssertNil(try tool(["dash": [6, 6, 6]]).dash)
        XCTAssertNil(try tool(["dash": [6, 0]]).dash, "both above 0")
        XCTAssertNil(try tool(["dash": [-6, 6]]).dash)
        XCTAssertNil(try tool(["dash": "dashed"]).dash)
        XCTAssertFalse(try tool(["blend": "screen"]).multiply)
        XCTAssertFalse(try tool(["blend": true]).multiply)
        XCTAssertEqual(try tool(["stabilize": 9]).stabilize, 4, "held to the levels there are")
        XCTAssertEqual(try tool(["stabilize": -2]).stabilize, 0)
        XCTAssertEqual(try tool(["stabilize": 2.6]).stabilize, 3, "rounded")
        XCTAssertEqual(try tool(["stabilize": "max"]).stabilize, 0)
        XCTAssertNil(try tool(["snap": ["x0": 1, "y0": "top", "angle": 0]]).snap)
        XCTAssertNil(try tool(["snap": 5]).snap)
        XCTAssertEqual(try tool(["grain": ["angle": 30], "thinning": 0.5]), try tool([:]), "keys the shell does not know are ignored")
        XCTAssertEqual(try tool(["nib": ["angle": 30]]).nib, 45, "a nib that is no number is the default")
        XCTAssertEqual(try tool(["nib": "steep"]).nib, 45)
        XCTAssertFalse(try tool(["nibRoll": "yes"]).nibRoll)
    }

    func testACalligraphyPensClaimCarriesItsNib() throws {
        let cal = try tool(["brush": "calligraphy", "nib": 150, "nibRoll": true])
        XCTAssertEqual(cal, InkTool(kind: .pen, color: "#1f4fd8", size: 3, brush: .calligraphy, nib: 150, nibRoll: true))
        let plain = try tool(["brush": "calligraphy"])
        XCTAssertEqual(plain.nib, 45, "a claim without a nib: 45")
        XCTAssertFalse(plain.nibRoll, "and no roll")
        XCTAssertEqual(try tool(["nib": 200.4]).nib, 20, "turned into 0…179")
        XCTAssertEqual(try tool(["nib": -30]).nib, 150)
        // the live nib: the claim's, less the first sample's clockwise roll, mod 180
        XCTAssertEqual(cal.liveNib(roll: 30), 120)
        XCTAssertEqual(cal.liveNib(roll: 200), 130)
        XCTAssertEqual(cal.liveNib(roll: nil), 150, "a sample without a roll keeps the nib")
        XCTAssertEqual(try tool(["brush": "calligraphy", "nib": 150]).liveNib(roll: 30), 150, "Lock angle: no roll")
    }

    func testThemeAndServersDecode() throws {
        XCTAssertEqual(try WebMessage(body: ["type": "theme", "bg": "#1e1e1e", "scheme": "dark"]),
                       .theme(bg: "#1e1e1e", scheme: .dark))
        XCTAssertEqual(try WebMessage(body: ["type": "servers"]), .servers)
    }

    func testAHapticDecodesWithItsPlace() throws {
        let snap: [String: Any] = ["type": "haptic", "kind": "snap", "at": ["x": 300, "y": 412.5]]
        XCTAssertEqual(try WebMessage(body: snap), .haptic(kind: .snap, at: CGPoint(x: 300, y: 412.5)))
        let select: [String: Any] = ["type": "haptic", "kind": "select", "at": ["x": 1, "y": 2]]
        XCTAssertEqual(try WebMessage(body: select), .haptic(kind: .select, at: CGPoint(x: 1, y: 2)))
        XCTAssertEqual(try WebMessage(body: ["type": "haptic", "kind": "tool"]), .haptic(kind: .tool, at: nil),
                       "a tool switch has no place")
        XCTAssertEqual(try WebMessage(body: ["type": "haptic", "kind": "buzz", "at": "here"]), .haptic(kind: .tool, at: nil),
                       "an unknown kind is a tick all the same")
    }

    func testAnythingElseIsRefused() {
        XCTAssertThrowsError(try WebMessage(body: ["type": "open-page"]), "an unknown type")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1, "at": ["x": 0, "y": 0]]),
                             "a claim without its tool")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1,
                                                   "tool": ["kind": "pen", "color": "#000", "size": 1]]),
                             "a claim without its place")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1, "at": ["x": 0, "y": 0],
                                                   "tool": ["color": "#000", "size": 1]]), "a tool without its kind")
        XCTAssertThrowsError(try WebMessage(body: ["type": "claim", "pointerId": 1, "at": ["x": 0, "y": 0],
                                                   "tool": ["kind": "pen", "color": "#000"]]), "a tool without its size")
        XCTAssertThrowsError(try WebMessage(body: ["type": "theme", "bg": "#fff", "scheme": "sepia"]), "an unknown scheme")
        XCTAssertThrowsError(try WebMessage(body: "claim"), "not an object")
    }

    func testAStrokeEncodesItsSamples() throws {
        let out = try json(.stroke(pointerId: 3, samples: [[10, 20.5, 0.4, 0], [11, 21, 0.5, 4.2]]))
        XCTAssertEqual(out["type"] as? String, "stroke")
        XCTAssertEqual(out["pointerId"] as? Int, 3)
        XCTAssertEqual(out["samples"] as? [[Double]], [[10, 20.5, 0.4, 0], [11, 21, 0.5, 4.2]])
        XCTAssertEqual(Set(out.keys), ["type", "pointerId", "samples"], "no shape drawn: no hold named")
    }

    func testAStrokeNamesTheHoldWhoseShapeItWasDrawnAs() throws {
        let out = try json(.stroke(pointerId: 61, samples: [[10, 20, 0.5, 0], [40, 20, 0.5, 300]], held: 22))
        XCTAssertEqual(out["held"] as? Int, 22)
        XCTAssertEqual(Set(out.keys), ["type", "pointerId", "samples", "held"])
    }

    func testAHoldEncodesTheSamplesSoFar() throws {
        let samples: [[Double]] = [[10, 20, 0.4, 0, 60, 135], [40, 20, 0.5, 300, 58.5, 134]]
        let out = try json(.hold(pointerId: 61, samples: samples))
        XCTAssertEqual(out["type"] as? String, "hold")
        XCTAssertEqual(out["pointerId"] as? Int, 61)
        XCTAssertEqual(out["samples"] as? [[Double]], samples)
        XCTAssertEqual(Set(out.keys), ["type", "pointerId", "samples"])
    }

    func testAPencilGestureCarriesItsAction() throws {
        let out = try json(.pencil(gesture: .squeeze, action: "showInkAttributes"))
        XCTAssertEqual(out["type"] as? String, "pencil")
        XCTAssertEqual(out["gesture"] as? String, "squeeze")
        XCTAssertEqual(out["action"] as? String, "showInkAttributes")
        XCTAssertEqual(Set(out.keys), ["type", "gesture", "action"], "no phase: a double tap, or an older shell's squeeze")
    }

    func testASqueezesEndSaysItEnded() throws {
        let out = try json(.pencil(gesture: .squeeze, action: "switchEraser", phase: .ended))
        XCTAssertEqual(out["type"] as? String, "pencil")
        XCTAssertEqual(out["gesture"] as? String, "squeeze")
        XCTAssertEqual(out["phase"] as? String, "ended")
        XCTAssertEqual(Set(out.keys), ["type", "gesture", "action", "phase"])
    }

    func testASqueezesStartAndCancelHaveATypeOfTheirOwn() throws {
        let began = try json(.squeeze(phase: .began, action: "switchEraser"))
        XCTAssertEqual(began["type"] as? String, "squeeze")
        XCTAssertEqual(began["phase"] as? String, "began")
        XCTAssertEqual(began["action"] as? String, "switchEraser")
        XCTAssertEqual(Set(began.keys), ["type", "phase", "action"])
        XCTAssertEqual(try json(.squeeze(phase: .cancelled, action: "ignore"))["phase"] as? String, "cancelled")
    }

    func testTheHoldsRepliesAreRead() {
        let params: [String: Double] = ["x1": 10, "y1": 20, "x2": 210.5, "y2": 20]
        let line: [String: Any] = ["kind": "line", "params": params]
        XCTAssertEqual(HoldReply(reply: ["shape": line]), .shape(HeldShape(kind: .line, params: params)))
        XCTAssertEqual(HoldReply(reply: ["cancel": true]), .cancel)
        let both: [String: Any] = ["cancel": true, "shape": line]
        XCTAssertEqual(HoldReply(reply: both), .cancel, "a cancel wins")
        let notCancelled: [String: Any] = ["cancel": false, "shape": line]
        XCTAssertEqual(HoldReply(reply: notCancelled), .shape(HeldShape(kind: .line, params: params)))
        XCTAssertEqual(HoldReply(reply: [String: Any]()), .nothing)
        XCTAssertEqual(HoldReply(reply: nil), .nothing, "not delivered")
        XCTAssertEqual(HoldReply(reply: "shape"), .nothing)
        let shapes: [(String, [String: Any], String)] = [
            ("star", ["cx": 0, "cy": 0, "r": 4], "a kind the shell cannot draw"),
            ("line", ["x1": 10, "y1": 20], "params it cannot be drawn from"),
            ("circle", ["cx": 0, "cy": "middle", "r": 4], "a param that is not a number"),
            ("polygon", ["cx": 0, "cy": 0, "r": 4, "n": 2], "a polygon has three sides at least"),
        ]
        for (kind, values, why) in shapes {
            let shape: [String: Any] = ["kind": kind, "params": values]
            XCTAssertEqual(HoldReply(reply: ["shape": shape]), .nothing, why)
        }
        let rect: [String: Any] = ["kind": "rect", "params": ["cx": 50, "cy": 60, "w": 40, "h": 20]]
        XCTAssertEqual(HoldReply(reply: ["shape": rect]),
                       .shape(HeldShape(kind: .rect, params: ["cx": 50, "cy": 60, "w": 40, "h": 20])),
                       "an angle left out reads as 0, as shapes.js reads it")
    }

    func testTheUserScriptDeclaresTheHost() {
        let script = HostInfo(version: "0.2.0").userScript
        XCTAssertTrue(script.contains(
            #"{"haptics":true,"hold":true,"ink":true,"inkLooks":["pressure","dash","blend","stabilize","pencil","calligraphy","snap"],"pencil":true,"pencilPhases":true,"pencilRoll":true,"platform":"ipad","servers":true,"version":"0.2.0"}"#
        ), script)
        XCTAssertTrue(script.contains(#"setAttribute("data-host", "ipad")"#))
        XCTAssertTrue(script.contains("queue.push({ msg, resolve, reject })"), "the stub queues until host.js drains it")
    }
}

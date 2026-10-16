import UIKit
import XCTest
@testable import Gamma

/// The Pencil layer's pure parts (Pencil/): which touch a claim takes, the
/// samples a stroke hands back, and the gesture actions' names. The
/// recognizer's life among WebKit's own recognizers needs a device
/// (docs/dev/ipad.md "Device checks").
final class ClaimQueueTests: XCTestCase {
    private let here = CGPoint(x: 100, y: 200)
    private let there = CGPoint(x: 300, y: 260)

    func testAClaimTakesTheTouchThatIsDownWhereItsPointerWent() {
        var queue = ClaimQueue<Int>()
        queue.began(1, at: here, time: 10)
        XCTAssertEqual(queue.claim(near: CGPoint(x: 101, y: 199), at: 10.01), 1)
        XCTAssertNil(queue.claim(near: here, at: 10.02), "one claim, one touch")
    }

    func testAClaimWithNoTouchThereTakesNothing() {
        var queue = ClaimQueue<Int>()
        XCTAssertNil(queue.claim(near: here, at: 1))
        queue.began(1, at: here, time: 2)
        XCTAssertNil(queue.claim(near: there, at: 2.01), "a touch elsewhere is not this pointer's")
        XCTAssertEqual(queue.claim(near: here, at: 2.02), 1)
    }

    func testALateClaimNeverTakesAFinishedTouchNorTheNextOne() {
        var queue = ClaimQueue<Int>()
        queue.began(1, at: here, time: 0)
        queue.ended(1, at: 0.04) // a dot, done before its claim arrived
        queue.began(2, at: here, time: 0.1) // the next stroke, at the same spot
        XCTAssertNil(queue.claim(near: here, at: 0.12), "the dot's claim: the page drew it")
        XCTAssertEqual(queue.claim(near: here, at: 0.13), 2, "the next stroke's own claim")
    }

    func testAnUnclaimedTapElsewhereNeverStandsInTheWay() {
        var queue = ClaimQueue<Int>(grace: 0.5)
        queue.began(1, at: there, time: 0)
        queue.ended(1, at: 0.05) // a tap on a button: never claimed
        queue.began(2, at: here, time: 0.2)
        XCTAssertEqual(queue.claim(near: here, at: 0.21), 2)
    }

    func testStrokesWrittenRightAfterAnUnclaimedTapAreAllTaken() {
        var queue = ClaimQueue<Int>(grace: 0.5)
        queue.began(1, at: there, time: 0)
        queue.ended(1, at: 0.05)
        var time = 0.2
        for (touch, x) in [(2, 100.0), (3, 130.0), (4, 160.0)] {
            let down = CGPoint(x: x, y: 200)
            queue.began(touch, at: down, time: time)
            XCTAssertEqual(queue.claim(near: down, at: time + 0.01), touch, "no chain of missed claims")
            queue.ended(touch, at: time + 0.3)
            time += 0.4
        }
    }

    func testAnUnclaimedTapIsForgottenAfterTheGrace() {
        var queue = ClaimQueue<Int>(grace: 0.5)
        queue.began(1, at: here, time: 0)
        queue.ended(1, at: 0.05)
        queue.began(2, at: here, time: 2)
        XCTAssertEqual(queue.claim(near: here, at: 2.01), 2)
    }

    func testAReusedIdentityEndsOnlyTheTouchThatIsDown() {
        var queue = ClaimQueue<Int>(grace: 0.5)
        queue.began(1, at: there, time: 0)
        queue.ended(1, at: 0.05)
        queue.began(1, at: here, time: 0.2) // the same key, a new touch
        queue.ended(1, at: 0.3)
        XCTAssertNil(queue.claim(near: here, at: 0.31), "the new touch is over, not still down")
    }

    func testEndedTouchesDoNotPileUp() {
        var queue = ClaimQueue<Int>(grace: 0.5)
        for i in 0..<100 {
            queue.began(i, at: here, time: Double(i))
            queue.ended(i, at: Double(i) + 0.1)
        }
        queue.began(100, at: here, time: 200)
        XCTAssertEqual(queue.count, 1, "a touch going down forgets the long-ended ones")
        XCTAssertEqual(queue.claim(near: here, at: 200.01), 100)
    }

    func testTouchesUIKitForgotAreNotClaimable() {
        var queue = ClaimQueue<Int>()
        queue.began(1, at: here, time: 0)
        queue.endAll(at: 0.1)
        XCTAssertNil(queue.claim(near: here, at: 0.2))
        XCTAssertTrue(queue.isEmpty)
    }
}

final class StrokeRecorderTests: XCTestCase {
    func testSamplesAreXYPressureAndMillisecondsFromTheFirst() {
        var stroke = StrokeRecorder()
        stroke.add(x: 10, y: 20, force: 2, maximumForce: 4, time: 100)
        stroke.add(x: 12, y: 21, force: 3, maximumForce: 4, time: 100.008)
        XCTAssertEqual(stroke.samples.count, 2)
        XCTAssertEqual(stroke.samples[0], [10, 20, 0.5, 0])
        XCTAssertEqual(stroke.samples[1][2], 0.75)
        XCTAssertEqual(stroke.samples[1][3], 8, accuracy: 1e-6)
        XCTAssertEqual(stroke.points, [CGPoint(x: 10, y: 20), CGPoint(x: 12, y: 21)])
    }

    func testTheLiftKeepsTheLastPressure() {
        var stroke = StrokeRecorder()
        stroke.add(x: 0, y: 0, force: 1, maximumForce: 4, time: 0)
        stroke.add(x: 1, y: 0, force: 0, maximumForce: 4, time: 0.01)
        XCTAssertEqual(stroke.samples[1][2], 0.25)
    }

    func testTimeNeverGoesBackAndForceIsCapped() {
        var stroke = StrokeRecorder()
        stroke.add(x: 0, y: 0, force: 9, maximumForce: 4, time: 1)
        stroke.add(x: 1, y: 0, force: 1, maximumForce: 4, time: 0.99)
        XCTAssertEqual(stroke.samples[0][2], 1)
        XCTAssertEqual(stroke.samples[1][3], 0)
    }

    func testATouchWithoutForceDrawsAtHalfPressure() {
        var stroke = StrokeRecorder()
        stroke.add(x: 0, y: 0, force: 0, maximumForce: 0, time: 0)
        XCTAssertEqual(stroke.samples[0][2], 0.5)
    }
}

final class PencilActionTests: XCTestCase {
    func testTheSystemsPreferredActionsKeepTheirNames() {
        XCTAssertEqual(PencilAction.name(.switchEraser), "switchEraser")
        XCTAssertEqual(PencilAction.name(.switchPrevious), "switchPrevious")
        XCTAssertEqual(PencilAction.name(.showColorPalette), "showColorPalette")
        XCTAssertEqual(PencilAction.name(.showInkAttributes), "showInkAttributes")
        XCTAssertEqual(PencilAction.name(.ignore), "ignore")
    }

    func testTheSqueezesActionsHaveTheirWebNames() throws {
        guard #available(iOS 17.5, *) else { throw XCTSkip("iPadOS 17.5 actions") }
        XCTAssertEqual(PencilAction.name(.showContextualPalette), "showInkAttributes")
        XCTAssertEqual(PencilAction.name(.runSystemShortcut), "runSystemShortcut")
    }
}

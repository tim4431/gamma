import UIKit
import XCTest
@testable import Gamma

/// The Pencil layer's pure parts (Pencil/): which touch a claim takes, the
/// samples a stroke hands back with their tilt, roll and stillness anchor,
/// when a hold is asked, the lazy brush, the ribbon, the held shape and how
/// it follows the pen, the ruler's edge (RulerSnap), where the Pencil
/// hovered for a gesture, the gesture actions' names, and the feedback each
/// `haptic` kind plays (HapticFeel). The recognizer's life among WebKit's
/// own recognizers, and how each feedback feels, need a device
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

    func testThePencilsTiltComesInDegrees() {
        var stroke = StrokeRecorder()
        stroke.add(x: 10, y: 20, force: 2, maximumForce: 4, time: 0, altitude: Double.pi / 4, azimuth: Double.pi / 2)
        let first = stroke.samples[0]
        XCTAssertEqual(first.count, 6, "[x, y, p, t, a, z]")
        XCTAssertEqual(Array(first.prefix(4)), [10, 20, 0.5, 0])
        XCTAssertEqual(first[4], 45, accuracy: 1e-9, "the altitude")
        XCTAssertEqual(first[5], 90, accuracy: 1e-9, "the azimuth")
        stroke.add(x: 11, y: 20, force: 2, maximumForce: 4, time: 0.004, altitude: 0.5)
        XCTAssertEqual(stroke.samples[1].count, 4, "without both angles, four values, as an older shell sends")
    }

    func testThePencilsRollComesSeventhInDegreesClockwise() {
        var stroke = StrokeRecorder()
        stroke.add(x: 10, y: 20, force: 2, maximumForce: 4, time: 0, altitude: Double.pi / 4, azimuth: 0, roll: Double.pi / 6)
        XCTAssertEqual(stroke.samples[0].count, 7, "[x, y, p, t, a, z, r]")
        XCTAssertEqual(stroke.samples[0][6], 30, accuracy: 1e-9, "the roll in degrees")
        stroke.add(x: 11, y: 20, force: 2, maximumForce: 4, time: 0.004, altitude: Double.pi / 4, azimuth: 0, roll: -Double.pi / 2)
        XCTAssertEqual(stroke.samples[1][6], 270, accuracy: 1e-9, "into 0…360")
        stroke.add(x: 12, y: 20, force: 2, maximumForce: 4, time: 0.008, altitude: Double.pi / 4, azimuth: 0)
        XCTAssertEqual(stroke.samples[2].count, 6, "no roll reported: six values")
        stroke.add(x: 13, y: 20, force: 2, maximumForce: 4, time: 0.012, roll: 1)
        XCTAssertEqual(stroke.samples[3].count, 4, "a roll without the tilt is not written: the seventh value follows the sixth")
    }

    func testTheStillnessAnchorMovesOnlyPastTheSlop() {
        var stroke = StrokeRecorder()
        XCTAssertEqual(PencilCapture.holdSlop, 3)
        let xs: [Double] = [0, 1, 2, 3, 3.5, 3.2, 6.6, 7]
        for (i, x) in xs.enumerated() {
            let time: Double = 10 + Double(i) * 0.01
            stroke.add(x: x, y: 0, force: 1, maximumForce: 2, time: time)
            // 3 is not past the slop from 0; 3.5 is, and becomes the anchor; 6.6 is 3.1 from it
            let anchor: Int = i < 4 ? 0 : i < 6 ? 4 : 6
            XCTAssertEqual(stroke.stillSince, anchor, "after x = \(x)")
        }
        XCTAssertEqual(stroke.stillTime, 10.06, accuracy: 1e-9, "the anchor's own time")
    }

    func testTheSlopIsADistanceFromTheAnchor() {
        var stroke = StrokeRecorder()
        stroke.add(x: 0, y: 0, force: 1, maximumForce: 2, time: 0)
        stroke.add(x: 2.2, y: 2.2, force: 1, maximumForce: 2, time: 0.01) // 3.11 away, though under 3 on each axis
        XCTAssertEqual(stroke.stillSince, 1)
        // creeping 0.9 pt a sample from there: each within 3 of the one before, not of the anchor
        for i in 1...4 {
            let x: Double = 2.2 + 0.9 * Double(i)
            let time: Double = 0.01 + Double(i) * 0.01
            stroke.add(x: x, y: 2.2, force: 1, maximumForce: 2, time: time)
        }
        XCTAssertEqual(stroke.stillSince, 5, "the fourth step is 3.6 from the anchor")
    }
}

/// When the hold is asked, with the time given (the touches' clock), as
/// PencilCapture's timer asks HoldWatch.
final class HoldWatchTests: XCTestCase {
    /// A stroke that moves 4 pt a sample from time 100 every 4 ms to x = 36
    /// (each sample a new anchor, the last at 100.036), then rests there,
    /// within a point, for `rest` more samples.
    private func stroke(rest: Int) -> StrokeRecorder {
        var stroke = StrokeRecorder()
        for i in 0..<10 {
            let x: Double = Double(i) * 4
            let time: Double = 100 + Double(i) * 0.004
            stroke.add(x: x, y: 50, force: 1, maximumForce: 2, time: time)
        }
        for i in 0..<rest {
            let x: Double = 36 + (i.isMultiple(of: 2) ? 0.5 : -0.5)
            let time: Double = 100.04 + Double(i) * 0.004
            stroke.add(x: x, y: 50, force: 1, maximumForce: 2, time: time)
        }
        return stroke
    }

    func testTheHoldIsDueTheDelayAfterTheAnchorsSample() throws {
        let watch = HoldWatch()
        let due = try XCTUnwrap(watch.due(stroke(rest: 10)))
        XCTAssertEqual(due.anchor, 9)
        XCTAssertEqual(due.time, 100.036 + PencilCapture.holdDelay, accuracy: 1e-9)
        XCTAssertEqual(PencilCapture.holdDelay, 0.6)
    }

    func testItAsksOnceWhenDue() {
        var watch = HoldWatch()
        let recorder = stroke(rest: 10)
        XCTAssertFalse(watch.fire(recorder, now: 100.5), "not yet")
        XCTAssertTrue(watch.fire(recorder, now: 100.636))
        XCTAssertNil(watch.due(recorder), "one hold at a time")
        XCTAssertFalse(watch.fire(recorder, now: 101), "and no second one while it is asked")
    }

    func testTheAskSaysWhatAShapeFollowsFrom() throws {
        var watch = HoldWatch()
        let recorder = stroke(rest: 10)
        XCTAssertTrue(watch.fire(recorder, now: 100.7))
        let ask = try XCTUnwrap(watch.pending)
        XCTAssertEqual(ask.anchor, 9)
        XCTAssertEqual(ask.n, 20, "every sample so far")
        XCTAssertEqual(ask.from, CGPoint(x: 35.5, y: 50), "the last one, where the pen stands")
        XCTAssertEqual(ask.tSnap, recorder.samples[9][3] + 600, accuracy: 1e-9, "the anchor's t + 600 ms")
    }

    func testAnAnswerOfNothingWaitsForTheAnchorToMove() throws {
        var watch = HoldWatch()
        var recorder = stroke(rest: 10)
        XCTAssertTrue(watch.fire(recorder, now: 100.7))
        XCTAssertEqual(watch.answered(.nothing)?.anchor, 9)
        XCTAssertNil(watch.due(recorder), "this still spell has been asked")
        XCTAssertFalse(watch.fire(recorder, now: 102))
        recorder.add(x: 60, y: 50, force: 1, maximumForce: 2, time: 101)
        let due = try XCTUnwrap(watch.due(recorder), "the pen moved on: its next stop can ask again")
        XCTAssertEqual(due.anchor, 20)
        XCTAssertEqual(due.time, 101.6, accuracy: 1e-9)
        XCTAssertTrue(watch.fire(recorder, now: 101.6))
    }

    func testAShapeOrACancelEndsTheHolds() {
        let shape = HeldShape(kind: .line, params: ["x1": 0, "y1": 50, "x2": 36, "y2": 50])
        for reply in [HoldReply.shape(shape), .cancel] {
            var watch = HoldWatch()
            var recorder = stroke(rest: 10)
            XCTAssertTrue(watch.fire(recorder, now: 100.7))
            XCTAssertNotNil(watch.answered(reply))
            XCTAssertTrue(watch.settled)
            recorder.add(x: 80, y: 50, force: 1, maximumForce: 2, time: 101)
            XCTAssertNil(watch.due(recorder), "no more holds after \(reply)")
            XCTAssertFalse(watch.fire(recorder, now: 103))
        }
    }

    func testAnAnswerNobodyWaitsForChangesNothing() {
        var watch = HoldWatch()
        let recorder = stroke(rest: 10)
        XCTAssertNil(watch.answered(.cancel), "nothing was asked (an answer after the lift)")
        XCTAssertFalse(watch.settled)
        XCTAssertNotNil(watch.due(recorder))
        XCTAssertTrue(watch.fire(recorder, now: 100.7))
        XCTAssertNotNil(watch.answered(.nothing))
        XCTAssertNil(watch.answered(.cancel), "a second answer")
        XCTAssertFalse(watch.settled)
    }

    func testAnAnchorThatMovedWhileAskingIsDueAtOnce() throws {
        var watch = HoldWatch()
        var recorder = stroke(rest: 10)
        XCTAssertTrue(watch.fire(recorder, now: 100.7))
        recorder.add(x: 50, y: 50, force: 1, maximumForce: 2, time: 100.71) // moved on while the page answered
        XCTAssertNil(watch.due(recorder), "still asking")
        XCTAssertNotNil(watch.answered(.nothing))
        let due = try XCTUnwrap(watch.due(recorder))
        XCTAssertEqual(due.anchor, 20)
        XCTAssertEqual(due.time, 101.31, accuracy: 1e-9)
    }

    func testAStillPenFromTheDownIsAskedToo() throws {
        // the paste hold: the page answers it from the samples
        var stroke = StrokeRecorder()
        for i in 0..<50 {
            let x: Double = 200 + (i.isMultiple(of: 3) ? 0.4 : 0)
            let time: Double = 5 + Double(i) * 0.008
            stroke.add(x: x, y: 300, force: 1, maximumForce: 2, time: time)
        }
        let due = try XCTUnwrap(HoldWatch().due(stroke))
        XCTAssertEqual(due.anchor, 0)
        XCTAssertEqual(due.time, 5.6, accuracy: 1e-9)
    }
}

final class LazyBrushTests: XCTestCase {
    private let line: [[Double]] = (0...12).map { (i: Int) -> [Double] in
        let p: Double = 0.1 * Double(i % 10)
        let t: Double = Double(i) * 4
        return [Double(i), 10, p, t]
    }

    func testNoSmoothingGivesTheSamplesBack() {
        XCTAssertEqual(LazyBrush.smooth(line, level: 0), line)
        XCTAssertEqual(LazyBrush.smooth(line, level: 9), line, "a level there is no string for")
        XCTAssertEqual(LazyBrush.smooth([], level: 2), [])
    }

    func testTheBrushTrailsThePenByItsString() {
        XCTAssertEqual(LazyBrush.radii, [0, 3, 6, 10, 16])
        let out = LazyBrush.smooth(line, level: 1) // a 3 pt string
        // the first where it is; x = 1, 2, 3 are within the string; then 3 short of each
        XCTAssertEqual(out.count, 10)
        XCTAssertEqual(out[0], line[0])
        for (k, sample) in out.dropFirst().enumerated() {
            let source = line[k + 4]
            XCTAssertEqual(sample[0], source[0] - 3, accuracy: 1e-9)
            XCTAssertEqual(sample[1], 10, accuracy: 1e-9)
            XCTAssertEqual(Array(sample.dropFirst(2)), Array(source.dropFirst(2)), "pressure and time ride along")
        }
    }

    func testAShakeWithinTheStringDrawsNothing() {
        let shake: [[Double]] = [[0, 0, 0.5, 0], [2, 1, 0.5, 4], [-1, 2, 0.5, 8], [1, -2, 0.5, 12]]
        XCTAssertEqual(LazyBrush.smooth(shake, level: 2), [[0, 0, 0.5, 0]])
    }
}

final class RibbonTests: XCTestCase {
    func testOnePointIsADisc() {
        let path = LiveStrokeView.ribbon([CGPoint(x: 10, y: 20)], radii: [2])
        let box = path.boundingBoxOfPath
        XCTAssertEqual(box.minX, 8, accuracy: 1e-6)
        XCTAssertEqual(box.maxX, 12, accuracy: 1e-6)
        XCTAssertEqual(box.minY, 18, accuracy: 1e-6)
        XCTAssertEqual(box.maxY, 22, accuracy: 1e-6)
        XCTAssertTrue(path.contains(CGPoint(x: 11, y: 21)))
        XCTAssertFalse(path.contains(CGPoint(x: 11.8, y: 21.8)))
    }

    func testTheWidthChangesAlongIt() {
        let path = LiveStrokeView.ribbon([CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)], radii: [1, 3])
        XCTAssertTrue(path.contains(CGPoint(x: 5, y: 1.5)), "between the two: 2 wide either side at the middle")
        XCTAssertFalse(path.contains(CGPoint(x: 5, y: 2.5)))
        XCTAssertTrue(path.contains(CGPoint(x: 12.5, y: 0)), "the end's disc")
        XCTAssertFalse(path.contains(CGPoint(x: -1.5, y: 0)))
    }

    func testOverlapsAddUpUnderTheNonZeroRule() {
        // Where a disc and a quadrilateral overlap, both wound one way, the
        // point is filled; wound against each other, it would be a hole.
        let segments: [(CGPoint, CGPoint)] = [
            (CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)), (CGPoint(x: 10, y: 0), CGPoint(x: 0, y: 0)),
            (CGPoint(x: 0, y: 0), CGPoint(x: 0, y: 10)), (CGPoint(x: 3, y: 7), CGPoint(x: -4, y: -2)),
        ]
        for (a, b) in segments {
            let path = LiveStrokeView.ribbon([a, b], radii: [2, 2])
            let middleX: CGFloat = (a.x + b.x) / 2, middleY: CGFloat = (a.y + b.y) / 2
            let nearX: CGFloat = a.x + (b.x - a.x) * 0.05, nearY: CGFloat = a.y + (b.y - a.y) * 0.05
            let points: [CGPoint] = [a, b, CGPoint(x: middleX, y: middleY), CGPoint(x: nearX, y: nearY)]
            for point in points {
                XCTAssertTrue(path.contains(point, using: .winding), "\(point) on \(a) → \(b)")
            }
        }
    }

    func testRepeatedPointsAndNoPoints() {
        XCTAssertTrue(LiveStrokeView.ribbon([], radii: []).isEmpty)
        let path = LiveStrokeView.ribbon([CGPoint(x: 5, y: 5), CGPoint(x: 5, y: 5)], radii: [1, 1])
        XCTAssertTrue(path.contains(CGPoint(x: 5, y: 5.5)))
    }
}

/// HeldShape and ShapeHold, by hand (the expectations are shapes.js
/// refitShape's for the same input) and against every case of
/// tests/shared/inkhold.json, which shapes.test.mjs runs too.
final class HeldShapeTests: XCTestCase {
    private func assertParams(_ got: [String: Double]?, _ want: [String: Double]?, _ note: String,
                              file: StaticString = #filePath, line: UInt = #line) {
        guard let want else {
            XCTAssertNil(got, "\(note): the shape is dropped", file: file, line: line)
            return
        }
        guard let got else {
            XCTFail("\(note): dropped, where \(want) was expected", file: file, line: line)
            return
        }
        XCTAssertEqual(Set(got.keys), Set(want.keys), note, file: file, line: line)
        for (key, value) in want {
            let mine = got[key] ?? .nan
            if key == "n" {
                XCTAssertEqual(mine, value, "\(note): n", file: file, line: line)
            } else {
                XCTAssertEqual(mine, value, accuracy: 0.011, "\(note): \(key)", file: file, line: line)
            }
        }
    }

    private func refit(_ kind: HeldShape.Kind, _ params: [String: Double], from: (Double, Double),
                       to pointer: (Double, Double)) -> [String: Double] {
        HeldShape(kind: kind, params: params)
            .refit(pointer: CGPoint(x: pointer.0, y: pointer.1), from: CGPoint(x: from.0, y: from.1)).params
    }

    func testALineOrAnArrowTakesThePenAsItsEnd() {
        assertParams(refit(.line, ["x1": 0, "y1": 0, "x2": 100, "y2": 0], from: (100, 0), to: (110.004, 5.006)),
                     ["x1": 0, "y1": 0, "x2": 110, "y2": 5.01], "a line")
        assertParams(refit(.arrow, ["x1": 0, "y1": 0, "x2": 100, "y2": 0, "head": 20], from: (100, 0), to: (50, 50)),
                     ["x1": 0, "y1": 0, "x2": 50, "y2": 50, "head": 20], "an arrow keeps its head")
    }

    func testAClosedShapeScalesAndTurnsAboutItsCentre() {
        assertParams(refit(.circle, ["cx": 50, "cy": 50, "r": 50], from: (100, 50), to: (110, 50)),
                     ["cx": 50, "cy": 50, "r": 60], "a circle grows")
        assertParams(refit(.circle, ["cx": 50, "cy": 50, "r": 50], from: (50, 50), to: (70, 50)),
                     ["cx": 50, "cy": 50, "r": 50], "from the centre itself: no scale, no turn")
        assertParams(refit(.rect, ["cx": 0, "cy": 0, "w": 40, "h": 20, "angle": 0], from: (20, 0), to: (0, 20)),
                     ["cx": 0, "cy": 0, "w": 20, "h": 40, "angle": 0], "a quarter turn swaps the sides")
        assertParams(refit(.rect, ["cx": 0, "cy": 0, "w": 40, "h": 20, "angle": 0], from: (20, 0), to: (20, 2)),
                     ["cx": 0, "cy": 0, "w": 40.2, "h": 20.1, "angle": 5.71], "a slight turn and growth")
        assertParams(refit(.rect, ["cx": 10, "cy": 10, "w": 40, "h": 20, "angle": 30], from: (30, 10), to: (10, 40)),
                     ["cx": 10, "cy": 10, "w": 30, "h": 60, "angle": 30], "turned past 45°")
        assertParams(refit(.ellipse, ["cx": 0, "cy": 0, "rx": 30, "ry": 10, "angle": 170], from: (30, 0), to: (0, 30)),
                     ["cx": 0, "cy": 0, "rx": 30, "ry": 10, "angle": 80], "the angle in [0°, 180°)")
        assertParams(refit(.ellipse, ["cx": 0, "cy": 0, "rx": 30, "ry": 10, "angle": 0], from: (0, 10), to: (0, 40)),
                     ["cx": 0, "cy": 0, "rx": 120, "ry": 40, "angle": 0], "four times as far out")
        assertParams(refit(.polygon, ["cx": 0, "cy": 0, "r": 10, "n": 5, "angle": 10], from: (10, 0), to: (0, -10)),
                     ["cx": 0, "cy": 0, "r": 10, "n": 5, "angle": 64], "the angle in [0, 360 / n)")
        assertParams(refit(.triangle, ["x1": 0, "y1": 0, "x2": 30, "y2": 0, "x3": 0, "y3": 30], from: (30, 0), to: (40, -10)),
                     ["x1": -8, "y1": -4, "x2": 40, "y2": -10, "x3": -2, "y3": 44], "a triangle about its centroid")
    }

    func testTheHoldFollowsThePenOnceItLeavesTheSnap() {
        let snap = HeldShape(kind: .line, params: ["x1": 0, "y1": 0, "x2": 100, "y2": 0])
        let hold = ShapeHold(snap: snap, from: CGPoint(x: 100, y: 0), n: 2, tSnap: 600)
        let held: [[Double]] = [[0, 0, 0.5, 0], [100, 0, 0.5, 400]]
        // the shape once `more` samples followed the two the hold carried
        func current(after more: [[Double]]) -> HeldShape? {
            let samples: [[Double]] = held + more
            return hold.current(samples)
        }
        XCTAssertEqual(hold.current(held), snap, "nothing since the snap")
        XCTAssertEqual(current(after: [[103, 0, 0.5, 620], [104, 0, 0.5, 700]]), snap, "within the refit slop")
        XCTAssertEqual(current(after: [[110, 0, 0.5, 800]])?.params["x2"], 110, "6 or more out: refit")
        XCTAssertEqual(current(after: [[110, 0, 0.5, 800], [100.5, 0, 0.5, 850]])?.params["x2"], 100.5,
                       "once out, it follows the pen back")
        XCTAssertNil(current(after: [[113, 0, 0.5, 700]]), "far and fast right after the snap: freehand")
        XCTAssertEqual(current(after: [[113, 0, 0.5, 760]])?.params["x2"], 113, "as far after the window: a refit")
        XCTAssertNil(current(after: [[113, 0, 0.5, 700], [100, 0, 0.5, 900]]), "dropped for the rest of the stroke")
        let past: [[Double]] = [[150, 0, 0.5, 610]]
        XCTAssertEqual(ShapeHold(snap: snap, from: CGPoint(x: 100, y: 0), n: 3, tSnap: 600).current(held + past),
                       snap, "the samples the hold carried are not followed")
    }

    func testAShapesOutline() {
        let circle = HeldShape(kind: .circle, params: ["cx": 50, "cy": 60, "r": 10]).path.boundingBoxOfPath
        XCTAssertEqual(circle.minX, 40, accuracy: 1e-6)
        XCTAssertEqual(circle.maxY, 70, accuracy: 1e-6)
        let rect = HeldShape(kind: .rect, params: ["cx": 0, "cy": 0, "w": 40, "h": 20, "angle": 90]).path.boundingBoxOfPath
        XCTAssertEqual(rect.width, 20, accuracy: 1e-6, "turned a quarter: as wide as it was tall")
        XCTAssertEqual(rect.height, 40, accuracy: 1e-6)
        let ellipse = HeldShape(kind: .ellipse, params: ["cx": 0, "cy": 0, "rx": 30, "ry": 10, "angle": 90]).path.boundingBoxOfPath
        XCTAssertEqual(ellipse.height, 60, accuracy: 1e-3)
        let line = HeldShape(kind: .line, params: ["x1": 0, "y1": 0, "x2": 100, "y2": 50]).path.boundingBoxOfPath
        XCTAssertEqual(line.size, CGSize(width: 100, height: 50))
        let arrow = HeldShape(kind: .arrow, params: ["x1": 0, "y1": 0, "x2": 100, "y2": 0, "head": 20]).path.boundingBoxOfPath
        XCTAssertEqual(arrow.minY, -10, accuracy: 1e-6, "the barbs at 30° to the shaft")
        XCTAssertEqual(arrow.maxY, 10, accuracy: 1e-6)
        XCTAssertFalse(HeldShape(kind: .polygon, params: ["cx": 0, "cy": 0, "r": 10, "n": 5, "angle": 0]).path.isEmpty)
    }

    func testEveryCaseOfTheSharedFixture() throws {
        let fixture = try SharedFixture.load("inkhold", as: HoldFixture.self)
        XCTAssertFalse(fixture.still.isEmpty)
        XCTAssertFalse(fixture.follow.isEmpty)
        XCTAssertFalse(fixture.refit.isEmpty)
        for row in fixture.still {
            var recorder = StrokeRecorder()
            for sample in row.samples {
                recorder.add(x: sample.host[0], y: sample.host[1], force: 1, maximumForce: 2, time: sample.host[3] / 1000)
            }
            XCTAssertEqual(recorder.stillSince, row.stillSince, row.note)
        }
        for row in fixture.follow {
            let kind = try XCTUnwrap(HeldShape.Kind(rawValue: row.hold.kind), row.note)
            let hold = ShapeHold(snap: HeldShape(kind: kind, params: row.hold.params), from: try HoldFixture.point(row.hold.from),
                                 n: row.hold.n, tSnap: row.hold.tSnap)
            assertParams(hold.current(row.samples.map(\.host))?.params, row.expect, row.note)
        }
        for row in fixture.refit {
            let kind = try XCTUnwrap(HeldShape.Kind(rawValue: row.kind), row.note)
            let shape = HeldShape(kind: kind, params: row.params)
            let got = shape.refit(pointer: try HoldFixture.point(row.pointer), from: try HoldFixture.point(row.from))
            assertParams(got.params, row.expect, row.note)
        }
    }
}

/// tests/shared/inkhold.json, in viewport CSS px.
private struct HoldFixture: Decodable {
    struct Still: Decodable {
        let note: String
        let samples: [FixtureSample]
        let stillSince: Int
    }

    struct Hold: Decodable {
        let kind: String
        let params: [String: Double]
        let from: [Double]
        let n: Int
        let tSnap: Double
    }

    struct Follow: Decodable {
        let note: String
        let hold: Hold
        let samples: [FixtureSample]
        let expect: [String: Double]?
    }

    struct Refit: Decodable {
        let note: String
        let kind: String
        let params: [String: Double]
        let from: [Double]
        let pointer: [Double]
        let expect: [String: Double]
    }

    let still: [Still]
    let follow: [Follow]
    let refit: [Refit]

    struct NotAPoint: Error {}

    static func point(_ values: [Double]) throws -> CGPoint {
        guard values.count >= 2 else { throw NotAPoint() }
        return CGPoint(x: values[0], y: values[1])
    }
}

/// A fixture's sample, an array (`[x, y]`, `[x, y, t]`, or the host's `[x,
/// y, p, t, …]`) or an object (`{x, y, p?, t?}`), as the host sends it:
/// `[x, y, p, t, …]`.
struct FixtureSample: Decodable {
    let host: [Double]

    private enum Keys: String, CodingKey { case x, y, p, t }

    init(from decoder: Decoder) throws {
        if var list = try? decoder.unkeyedContainer() {
            var values: [Double] = []
            while !list.isAtEnd {
                if try list.decodeNil() {
                    values.append(.nan)
                } else {
                    values.append(try list.decode(Double.self))
                }
            }
            switch values.count {
            case 0, 1: throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "not a sample"))
            case 2: host = values + ([0.5, 0] as [Double])
            case 3: host = [values[0], values[1], 0.5, values[2]]
            default: host = values
            }
            return
        }
        let c = try decoder.container(keyedBy: Keys.self)
        let x: Double = try c.decode(Double.self, forKey: .x)
        let y: Double = try c.decode(Double.self, forKey: .y)
        let p: Double = try c.decodeIfPresent(Double.self, forKey: .p) ?? 0.5
        let t: Double = try c.decodeIfPresent(Double.self, forKey: .t) ?? 0
        host = [x, y, p, t]
    }
}

/// A file of the repository's tests/shared, which the test bundle carries
/// as a resource (project.yml), so the Swift ports are held to the rows the
/// web's tests read (the server's read inkwidth.json too).
enum SharedFixture {
    private final class Token {}

    static func load<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
        let url = try XCTUnwrap(Bundle(for: Token.self).url(forResource: name, withExtension: "json"),
                                "tests/shared/\(name).json is not in the test bundle (project.yml)")
        return try JSONDecoder().decode(T.self, from: Data(contentsOf: url))
    }
}

final class GestureHoverTests: XCTestCase {
    func testAGesturesHoverIsKeptForTheTickThatFollows() {
        var hover = GestureHover()
        XCTAssertNil(hover.location(now: 0), "no gesture yet")
        hover.gesture(at: CGPoint(x: 120, y: 340.5), time: 100)
        XCTAssertEqual(hover.location(now: 100.05), CGPoint(x: 120, y: 340.5))
        XCTAssertEqual(hover.location(now: 100 + GestureHover.lifetime), CGPoint(x: 120, y: 340.5))
        XCTAssertNil(hover.location(now: 101.5), "a tick long after is no gesture's")
    }

    func testAGestureAwayFromTheScreenHasNoPlace() {
        var hover = GestureHover()
        hover.gesture(at: CGPoint(x: 1, y: 2), time: 10)
        hover.gesture(at: nil, time: 11)
        XCTAssertNil(hover.location(now: 11), "the Pencil did not hover: the plain tick, not the last gesture's place")
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

/// The feedback each `haptic` kind plays (PencilHaptics): Apple's for its event.
final class HapticFeelTests: XCTestCase {
    func testEachKindPlaysAFeedbackOfItsOwn() {
        XCTAssertEqual(HapticFeel(.snap, placed: true), .pathCompleted, "a held shape: the canvas's shape recognition")
        XCTAssertEqual(HapticFeel(.select, placed: true), .selectionChanged, "a pen lasso's selection")
        XCTAssertEqual(HapticFeel(.tool, placed: true), .lightImpact, "a gesture's tool switch")
        XCTAssertEqual(Set([HapticKind.snap, .select, .tool].map { HapticFeel($0, placed: true) }).count, 3)
    }

    func testWithoutAPlaceASnapIsTheLightImpact() {
        // the canvas generator plays only at a place; the other two have a call without one
        XCTAssertEqual(HapticFeel(.snap, placed: false), .lightImpact)
        XCTAssertEqual(HapticFeel(.select, placed: false), .selectionChanged)
        XCTAssertEqual(HapticFeel(.tool, placed: false), .lightImpact)
    }
}

/// The ruler's edge (the claim's `snap`): the web's ink/ruler.js onRail and
/// railLine, the cases of frontend/tests/ruler.test.mjs in view coordinates.
final class RulerSnapTests: XCTestCase {
    func testAPointIsProjectedOntoTheEdge() {
        let level = InkTool.Snap(x0: 100, y0: 200, angle: 0)
        XCTAssertEqual(RulerSnap.point(CGPoint(x: 130, y: 190), onto: level), CGPoint(x: 130, y: 200))
        // 90°: the edge runs up the screen (counterclockwise as seen, y down)
        let upright = RulerSnap.point(CGPoint(x: 80, y: 20), onto: InkTool.Snap(x0: 50, y0: 50, angle: 90))
        XCTAssertEqual(upright.x, 50, accuracy: 1e-9)
        XCTAssertEqual(upright.y, 20, accuracy: 1e-9)
        // 30°: along (cos 30°, −sin 30°) from the origin, 100 cos 30° out
        let tilted = RulerSnap.point(CGPoint(x: 100, y: 0), onto: InkTool.Snap(x0: 0, y0: 0, angle: 30))
        XCTAssertEqual(tilted.x, 75, accuracy: 1e-9)
        XCTAssertEqual(tilted.y, -43.30127, accuracy: 1e-5)
    }

    func testTheLineRunsBetweenTheOutermostProjectionsFromTheEndNearerTheFirst() {
        let level = InkTool.Snap(x0: 100, y0: 200, angle: 0)
        let wobbly: [CGPoint] = [CGPoint(x: 150, y: 195), CGPoint(x: 260, y: 205), CGPoint(x: 330, y: 190), CGPoint(x: 290, y: 199)]
        XCTAssertEqual(RulerSnap.project(wobbly, onto: level), [CGPoint(x: 150, y: 200), CGPoint(x: 330, y: 200)],
                       "it went back, but the line reaches the farthest point")
        XCTAssertEqual(RulerSnap.project([CGPoint(x: 450, y: 190), CGPoint(x: 300, y: 185), CGPoint(x: 160, y: 205)], onto: level),
                       [CGPoint(x: 450, y: 200), CGPoint(x: 160, y: 200)], "drawn right to left, it starts at the right")
        XCTAssertEqual(RulerSnap.project([CGPoint(x: 120, y: 190)], onto: level), [CGPoint(x: 120, y: 200), CGPoint(x: 120, y: 200)],
                       "a dot of the edge")
        XCTAssertEqual(RulerSnap.project([], onto: level), [])
    }

    func testTheEdgeTheClaimCarriesIsDrawnAndDeclared() throws {
        XCTAssertTrue(HostInfo(version: "0").inkLooks.contains("snap"), "the live line draws a stroke along the ruler")
        let claim = try JSONDecoder().decode(InkTool.self, from: Data(##"{"kind":"pen","color":"#222222","size":2,"snap":{"x0":10.5,"y0":20,"angle":30}}"##.utf8))
        XCTAssertEqual(claim.snap, InkTool.Snap(x0: 10.5, y0: 20, angle: 30))
    }
}

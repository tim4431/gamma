import UIKit
import UIKit.UIGestureRecognizerSubclass

/// Apple Pencil strokes, taken natively once the web claims them
/// (docs/dev/ipad.md "The pen"). It sits on the web view's container, so it
/// sees every touch the web view gets, and it never gets in the way of the
/// page until a claim: it recognizes alongside every other recognizer and
/// delays nothing. A Pencil touch is recorded from its first sample while
/// the page's ink layer decides what the touch is; when the layer posts
/// `claim` (a pen or highlighter stroke, not a tap, the eraser or the lasso),
/// the recognizer begins, which prevents WebKit's own recognizers still
/// deciding (its touch events, pans, long presses) and cancels the web
/// view's touch; draws the live stroke; and hands the samples back at the
/// lift. The page drops its own draft on the claim's `{taken: true}`, so it
/// does not depend on a `pointercancel` arriving. With no claim the page
/// handled the touch, as in Safari, and nothing here touched it.
///
/// While a claimed stroke is captured, fingers on the screen (the palm) are
/// swallowed too: the page must not move under the Pencil.
final class PencilCapture: UIGestureRecognizer {
    /// The web view: samples are in its points, which are the page's CSS px.
    weak var surface: UIView?
    /// The stroke in progress: its points so far plus the predicted ones.
    var onPreview: ((_ points: [CGPoint], _ tool: InkTool) -> Void)?
    /// The claimed stroke ended: the web's pointer id and its samples.
    var onStroke: ((_ pointerId: Int, _ samples: [[Double]]) -> Void)?
    /// The claimed stroke was cancelled by the system: nothing to save, but
    /// the web's claim for `pointerId` must still end.
    var onAbandon: ((_ pointerId: Int) -> Void)?

    private struct Capture {
        let touch: ObjectIdentifier
        let pointerId: Int
        let tool: InkTool
    }

    private var claims = ClaimQueue<ObjectIdentifier>()
    private var recordings: [ObjectIdentifier: StrokeRecorder] = [:]
    private var capture: Capture?

    override init(target: Any?, action: Selector?) {
        super.init(target: target, action: action)
        allowedTouchTypes = [UITouch.TouchType.pencil, .direct].map { NSNumber(value: $0.rawValue) }
        cancelsTouchesInView = true
        delaysTouchesBegan = false
        delaysTouchesEnded = false
    }

    /// The web's claim for its `pointerId`, which went down at `point` (the
    /// surface's points): true when that Pencil touch was still down to take
    /// (ClaimQueue says which).
    func claim(pointerId: Int, at point: CGPoint, tool: InkTool) -> Bool {
        guard capture == nil, state == .possible,
              let touch = claims.claim(near: point, at: ProcessInfo.processInfo.systemUptime),
              recordings[touch] != nil else { return false }
        capture = Capture(touch: touch, pointerId: pointerId, tool: tool)
        state = .began // cancels the web view's touch
        preview(predicted: [])
        return true
    }

    // Simultaneous until claimed, exclusive after: preventing only matters
    // once this recognizer leaves .possible, which only a claim does, so a
    // page that claims nothing is untouched. Nothing of WebKit's stops it.
    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool { true }
    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool { false }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        for touch in touches {
            guard touch.type == .pencil else {
                // A finger is the page's, unless a stroke is being captured (a palm).
                if capture == nil { ignore(touch, for: event) }
                continue
            }
            let key = ObjectIdentifier(touch)
            var recorder = StrokeRecorder()
            record(touch, into: &recorder)
            recordings[key] = recorder
            claims.began(key, at: recorder.points[0], time: touch.timestamp)
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        for touch in touches where touch.type == .pencil {
            let key = ObjectIdentifier(touch)
            guard var recorder = recordings[key] else { continue }
            // every sample the Pencil measured since the last frame (240 Hz)
            for sample in event.coalescedTouches(for: touch) ?? [touch] { record(sample, into: &recorder) }
            recordings[key] = recorder
            if capture?.touch == key {
                state = .changed
                preview(predicted: event.predictedTouches(for: touch) ?? [])
            }
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        for touch in touches where touch.type == .pencil {
            let key = ObjectIdentifier(touch)
            guard var recorder = recordings.removeValue(forKey: key) else { continue }
            for sample in event.coalescedTouches(for: touch) ?? [touch] { record(sample, into: &recorder) }
            if let capture, capture.touch == key {
                self.capture = nil
                state = .ended
                onStroke?(capture.pointerId, recorder.samples)
            } else {
                claims.ended(key, at: touch.timestamp) // a claim for it now comes too late
            }
        }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        for touch in touches where touch.type == .pencil {
            let key = ObjectIdentifier(touch)
            recordings.removeValue(forKey: key)
            if let capture, capture.touch == key {
                self.capture = nil
                state = .cancelled
                onAbandon?(capture.pointerId)
            } else {
                claims.ended(key, at: touch.timestamp)
            }
        }
    }

    override func reset() {
        super.reset()
        recordings.removeAll()
        if let capture {
            self.capture = nil
            onAbandon?(capture.pointerId)
        }
        // touches UIKit stopped telling us about are over: never claimable
        claims.endAll(at: ProcessInfo.processInfo.systemUptime)
    }

    private func record(_ touch: UITouch, into recorder: inout StrokeRecorder) {
        let point = touch.preciseLocation(in: surface ?? view)
        recorder.add(x: Double(point.x), y: Double(point.y), force: Double(touch.force),
                     maximumForce: Double(touch.maximumPossibleForce), time: touch.timestamp)
    }

    private func preview(predicted: [UITouch]) {
        guard let capture, let recorder = recordings[capture.touch] else { return }
        let ahead = predicted.map { $0.preciseLocation(in: surface ?? view) }
        onPreview?(recorder.points + ahead, capture.tool)
    }
}

/// Which Pencil touch a claim is for. The web names its pointer, which the
/// native side cannot see, so a claim says where its pointer went down and
/// takes the touch that began there: the oldest one within `reach` points
/// of that place, so two touches at one spot still go in order. A claim
/// whose touch already ended (a dot, finished by the page's own `pointerup`
/// before the claim arrived) takes nothing, and an ended touch is
/// remembered for `grace` seconds so a late claim cannot grab the next
/// touch begun at the same spot instead. Matching by place keeps a miss
/// local: a touch nobody claimed (a tap on a button, the eraser, the lasso)
/// only stands in the way of a claim for that same spot, and not of every
/// stroke written after it.
struct ClaimQueue<Touch: Hashable> {
    private struct Entry {
        let touch: Touch
        let at: CGPoint
        var ended: TimeInterval?
    }

    let grace: TimeInterval
    let reach: CGFloat
    private var entries: [Entry] = []

    init(grace: TimeInterval = 0.5, reach: CGFloat = 10) {
        self.grace = grace
        self.reach = reach
    }

    var isEmpty: Bool { entries.isEmpty }
    var count: Int { entries.count }

    /// A Pencil touch went down at `point`. Touches that ended long enough
    /// ago go now, so the queue stays as short as the touches in play.
    mutating func began(_ touch: Touch, at point: CGPoint, time: TimeInterval) {
        forget(before: time)
        entries.append(Entry(touch: touch, at: point, ended: nil))
    }

    /// Only a touch still down can end: a touch's identity can be reused by
    /// a later touch once the first is gone.
    mutating func ended(_ touch: Touch, at time: TimeInterval) {
        guard let i = entries.firstIndex(where: { $0.touch == touch && $0.ended == nil }) else { return }
        entries[i].ended = time
    }

    mutating func endAll(at time: TimeInterval) {
        for i in entries.indices where entries[i].ended == nil { entries[i].ended = time }
    }

    /// The touch a claim for a pointer down at `point`, arriving at `now`,
    /// takes, or nil.
    mutating func claim(near point: CGPoint, at now: TimeInterval) -> Touch? {
        forget(before: now)
        let reach = self.reach
        guard let i = entries.firstIndex(where: { hypot(Double($0.at.x - point.x), Double($0.at.y - point.y)) <= Double(reach) }) else {
            return nil
        }
        let entry = entries.remove(at: i)
        return entry.ended == nil ? entry.touch : nil
    }

    private mutating func forget(before now: TimeInterval) {
        let grace = self.grace // not `self` inside the closure: entries is being modified
        entries.removeAll { entry in entry.ended.map { now - $0 > grace } ?? false }
    }
}

/// One stroke's samples as the web's ink layer takes them: `[x, y, p, t]`,
/// p the force over the maximum (0…1), t in ms since the first sample and
/// never going back. The lift reports no force, so a zero keeps the last
/// pressure, as the web's own sampling does.
struct StrokeRecorder {
    private(set) var samples: [[Double]] = []
    private(set) var points: [CGPoint] = []
    private var start: TimeInterval?

    mutating func add(x: Double, y: Double, force: Double, maximumForce: Double, time: TimeInterval) {
        let begin = start ?? time
        start = begin
        let measured = maximumForce > 0 ? force / maximumForce : 0.5
        let p = measured > 0 ? min(1, measured) : (samples.last?[2] ?? 0.5)
        let t = max(samples.last?[3] ?? 0, (time - begin) * 1000)
        samples.append([x, y, p, t])
        points.append(CGPoint(x: x, y: y))
    }
}

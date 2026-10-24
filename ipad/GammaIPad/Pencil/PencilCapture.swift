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
///
/// The one thing decided here about the pen is that it held still: UIKit
/// sends no touches while the Pencil rests, and the page sees nothing of a
/// captured stroke before its lift, so a timer fires `holdDelay` after the
/// stillness anchor's sample (StrokeRecorder) and the page is asked (`hold`,
/// HoldWatch). Its answer comes back through `held`.
final class PencilCapture: UIGestureRecognizer {
    /// How long the captured Pencil stays within `holdSlop` of its stillness
    /// anchor before the page is asked about a hold (shapes.js HOLD_MS).
    nonisolated static var holdDelay: TimeInterval { 0.6 }
    /// How far, in points (= CSS px), a sample may land from the anchor and
    /// the Pencil still count as still (shapes.js HOLD_MOVE).
    nonisolated static var holdSlop: Double { 3 }

    /// The web view: samples are in its points, which are the page's CSS px.
    weak var surface: UIView?
    /// The stroke in progress: its samples so far, the predicted points, the
    /// shape the page snapped it to (nil while it is freehand), its tool.
    var onPreview: ((_ samples: [[Double]], _ predicted: [CGPoint], _ shape: HeldShape?, _ tool: InkTool) -> Void)?
    /// The claimed stroke ended: the web's pointer id, its samples, and the
    /// `n` of the hold whose shape the line was drawn as (nil: none was),
    /// the one hold the page may commit a shape for.
    var onStroke: ((_ pointerId: Int, _ samples: [[Double]], _ held: Int?) -> Void)?
    /// The claimed stroke was cancelled by the system: nothing to save, but
    /// the web's claim for `pointerId` must still end.
    var onAbandon: ((_ pointerId: Int) -> Void)?
    /// The captured Pencil held still: ask the page (`hold`) with the
    /// samples so far, and hand its answer to `held`.
    var onHold: ((_ pointerId: Int, _ samples: [[Double]]) -> Void)?
    /// The page took the hold (*Paste here*): the line goes now, and the
    /// touch stays captured, swallowing palms, until its lift.
    var onDrop: (() -> Void)?

    private struct Capture {
        let touch: ObjectIdentifier
        let pointerId: Int
        let tool: InkTool
        var watch = HoldWatch()
        /// The stillness anchor the hold's timer is set for (nil: not set).
        var armed: Int? = nil
        /// The shape the page snapped the stroke to, as it follows the pen.
        var shape: ShapeHold? = nil
        /// The page took the hold: no line, and the lift hands back no samples.
        var dropped = false
    }

    private var claims = ClaimQueue<ObjectIdentifier>()
    private var recordings: [ObjectIdentifier: StrokeRecorder] = [:]
    private var capture: Capture?
    /// The hold's timer, one per capture, in the run loop's common modes (it
    /// fires while a scroll view tracks too). Repeating, so it stays valid
    /// to be set again; `fireDate` is moved to the next hold's time, or far
    /// off, and each firing asks at most once (HoldWatch).
    private var holdTimer: Timer?

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
        armHold()
        return true
    }

    /// The page's answer to the hold asked for `pointerId`. A shape is drawn
    /// at once (the pen is still: no touch will come to draw it) and follows
    /// the pen from then on; a cancel takes the line away; nothing lets the
    /// next still spell ask again. An answer for a stroke that has ended, or
    /// that asked nothing, changes nothing: that lift sent the freehand line
    /// and named no hold, which the page commits as it is.
    func held(_ reply: HoldReply, pointerId: Int) {
        guard var capture, capture.pointerId == pointerId else { return }
        guard let ask = capture.watch.answered(reply) else { return }
        switch reply {
        case let .shape(shape):
            capture.shape = ShapeHold(snap: shape, from: ask.from, n: ask.n, tSnap: ask.tSnap)
        case .cancel:
            capture.dropped = true
        case .nothing:
            break
        }
        self.capture = capture
        if capture.dropped {
            onDrop?()
        } else {
            preview(predicted: [])
        }
        armHold()
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
            // A Pencil on the bar or the launcher is theirs: it never enters
            // the claim queue, where a tap there would stand in the way of a
            // claim for a stroke begun at the same place just after.
            if let surface, let touched = touch.view, !touched.isDescendant(of: surface) {
                ignore(touch, for: event)
                continue
            }
            let key = ObjectIdentifier(touch)
            var recorder = StrokeRecorder()
            record(touch, into: &recorder)
            recordings[key] = recorder
            let first: [Double] = recorder.samples[0]
            claims.began(key, at: CGPoint(x: first[0], y: first[1]), time: touch.timestamp)
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
                armHold()
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
                stopHold()
                state = .ended
                // The line drawn once more from exactly the samples handed
                // back, without the prediction: a held shape is then the one
                // the page commits (shapes.js followHold over these samples),
                // for the hold named here. A lift that came before the page
                // answered the hold has no shape, and names none.
                if !capture.dropped {
                    onPreview?(recorder.samples, [], capture.shape?.current(recorder.samples), capture.tool)
                }
                onStroke?(capture.pointerId, capture.dropped ? [] : recorder.samples, capture.dropped ? nil : capture.shape?.n)
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
                stopHold()
                state = .cancelled
                onAbandon?(capture.pointerId)
            } else {
                claims.ended(key, at: touch.timestamp)
            }
        }
    }

    override func reset() {
        super.reset()
        stopHold()
        recordings.removeAll()
        if let capture {
            self.capture = nil
            onAbandon?(capture.pointerId)
        }
        // touches UIKit stopped telling us about are over: never claimable
        claims.endAll(at: ProcessInfo.processInfo.systemUptime)
    }

    private func record(_ touch: UITouch, into recorder: inout StrokeRecorder) {
        let space = surface ?? view
        let point = touch.preciseLocation(in: space)
        // the barrel roll (an Apple Pencil Pro), which turns a calligraphy nib
        var roll: Double? = nil
        if #available(iOS 17.5, *) { roll = Double(touch.rollAngle) }
        recorder.add(x: Double(point.x), y: Double(point.y), force: Double(touch.force),
                     maximumForce: Double(touch.maximumPossibleForce), time: touch.timestamp,
                     altitude: Double(touch.altitudeAngle), azimuth: Double(touch.azimuthAngle(in: space)), roll: roll)
    }

    private func preview(predicted: [UITouch]) {
        guard let capture, !capture.dropped, let recorder = recordings[capture.touch] else { return }
        let ahead = predicted.map { $0.preciseLocation(in: surface ?? view) }
        onPreview?(recorder.samples, ahead, capture.shape?.current(recorder.samples), capture.tool)
    }

    // --- the hold ------------------------------------------------------------------

    /// Sets the hold's timer for the captured stroke's still spell
    /// (HoldWatch.due), or puts it far off when there is none to ask; it is
    /// moved only when the anchor it is set for changes.
    private func armHold() {
        guard var capture else { return }
        let next = recordings[capture.touch].flatMap { capture.watch.due($0) }
        guard next?.anchor != capture.armed else { return }
        capture.armed = next?.anchor
        self.capture = capture
        guard let due = next else {
            holdTimer?.fireDate = .distantFuture
            return
        }
        // the touches' clock (system uptime) to the timer's (the date)
        let date = Date(timeIntervalSinceNow: due.time - ProcessInfo.processInfo.systemUptime)
        if let holdTimer {
            holdTimer.fireDate = date
        } else {
            let timer = Timer(fireAt: date, interval: 60, target: self, selector: #selector(holdTimerFired(_:)),
                              userInfo: nil, repeats: true)
            RunLoop.main.add(timer, forMode: .common)
            holdTimer = timer
        }
    }

    @objc private func holdTimerFired(_ timer: Timer) {
        timer.fireDate = .distantFuture
        guard var capture, let recorder = recordings[capture.touch] else { return }
        capture.armed = nil
        let asks = capture.watch.fire(recorder, now: ProcessInfo.processInfo.systemUptime)
        self.capture = capture
        if asks {
            onHold?(capture.pointerId, recorder.samples)
        } else {
            armHold() // not due on the touches' clock yet: set again
        }
    }

    private func stopHold() {
        holdTimer?.invalidate()
        holdTimer = nil
    }
}

/// When a captured stroke asks the page about a hold (docs/dev/ipad.md "The
/// pen", step 6). Pure: the time is the caller's, on the touches' clock
/// (seconds of system uptime; PencilCapture passes the uptime, a test its
/// own numbers), so the timer's rules are tested without a run loop. The
/// hold is due `PencilCapture.holdDelay` after the stillness anchor's sample;
/// one is asked at a time; after an answer of nothing the next one waits for
/// the anchor to move; after a shape or a cancel the stroke asks no more; and
/// an answer nobody is waiting for changes nothing.
struct HoldWatch: Equatable {
    /// A hold sent, with what a shape the page answers with follows from.
    struct Ask: Equatable {
        /// The stillness anchor the hold was asked for.
        let anchor: Int
        /// The number of samples the hold carried.
        let n: Int
        /// The last of them, where the pen stood.
        let from: CGPoint
        /// The snap's time on the samples' clock: the anchor's t + the delay, in ms.
        let tSnap: Double
    }

    /// The hold asked and not answered yet.
    private(set) var pending: Ask?
    /// The page answered with a shape or took the hold: no more holds.
    private(set) var settled = false
    /// The anchor of the last hold answered with nothing.
    private var spent: Int?

    /// The anchor of the recorder's present still spell and when its hold
    /// is due, or nil when there is none to ask: one is pending, the stroke
    /// has settled, this spell was answered already, or there are no samples.
    func due(_ recorder: StrokeRecorder) -> (anchor: Int, time: TimeInterval)? {
        guard pending == nil, !settled, !recorder.samples.isEmpty, recorder.stillSince != spent else { return nil }
        return (recorder.stillSince, recorder.stillTime + PencilCapture.holdDelay)
    }

    /// The timer fired at `now`: true when the hold is to be sent, which
    /// then waits for its answer; false when it is not due yet (a few
    /// milliseconds early are allowed, the timer's clock being the date) or
    /// there is none to ask.
    mutating func fire(_ recorder: StrokeRecorder, now: TimeInterval) -> Bool {
        guard let next = due(recorder), now >= next.time - 0.005 else { return false }
        let samples = recorder.samples
        let last = samples[samples.count - 1]
        let from: CGPoint = CGPoint(x: last[0], y: last[1])
        let tSnap: Double = samples[next.anchor][3] + PencilCapture.holdDelay * 1000
        pending = Ask(anchor: next.anchor, n: samples.count, from: from, tSnap: tSnap)
        return true
    }

    /// The page's answer to the pending hold: the hold it answers, or nil
    /// when none was pending (an answer after the lift, or a second one).
    mutating func answered(_ reply: HoldReply) -> Ask? {
        guard let ask = pending else { return nil }
        pending = nil
        switch reply {
        case .shape, .cancel: settled = true
        case .nothing: spent = ask.anchor
        }
        return ask
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
        guard let i = entries.firstIndex(where: { (candidate: Entry) -> Bool in
            let dx: Double = Double(candidate.at.x - point.x)
            let dy: Double = Double(candidate.at.y - point.y)
            return hypot(dx, dy) <= Double(reach)
        }) else {
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
/// never going back, then, when the Pencil reports its tilt, `a` and `z`,
/// its altitude and azimuth in degrees (the web converts them back to
/// radians, as WebKit gives a page's pointer events), and after them, when
/// it reports its barrel roll (iPadOS 17.5), `r`, in degrees in [0, 360),
/// clockwise as seen (PointerEvent.twist's convention: the page turns a
/// calligraphy nib by the first sample's). The lift reports no force, so a
/// zero keeps the last pressure, as the web's own sampling does.
///
/// It also keeps the stroke's stillness anchor (shapes.js stillSince): the
/// latest sample that landed more than `PencilCapture.holdSlop` from the
/// anchor before it, at first the first sample, so every sample since the
/// anchor is within the slop of it.
struct StrokeRecorder {
    private(set) var samples: [[Double]] = []
    /// The stillness anchor's index in `samples`.
    private(set) var stillSince = 0
    /// The anchor sample's time, on the touches' clock (seconds of system uptime).
    private(set) var stillTime: TimeInterval = 0
    private var start: TimeInterval?

    /// `altitude`, `azimuth` and `roll` in radians, as UITouch gives them; a
    /// sample without the two angles has four values, one without the roll six.
    mutating func add(x: Double, y: Double, force: Double, maximumForce: Double, time: TimeInterval,
                      altitude: Double? = nil, azimuth: Double? = nil, roll: Double? = nil) {
        let begin = start ?? time
        start = begin
        let measured = maximumForce > 0 ? force / maximumForce : 0.5
        let p = measured > 0 ? min(1, measured) : (samples.last?[2] ?? 0.5)
        let t = max(samples.last?[3] ?? 0, (time - begin) * 1000)
        var sample: [Double] = [x, y, p, t]
        if let altitude, let azimuth {
            let altitudeDegrees: Double = altitude * 180 / .pi
            let azimuthDegrees: Double = azimuth * 180 / .pi
            sample += [altitudeDegrees, azimuthDegrees]
            if let roll, roll.isFinite {
                let degrees: Double = roll * 180 / .pi
                sample.append(mod(degrees, 360))
            }
        }
        if samples.isEmpty {
            stillSince = 0
            stillTime = time
        } else {
            let anchor = samples[stillSince]
            if hypot(x - anchor[0], y - anchor[1]) > PencilCapture.holdSlop {
                stillSince = samples.count
                stillTime = time
            }
        }
        samples.append(sample)
    }
}

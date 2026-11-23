import UIKit

/// The touch report (docs/research/ipad-pencil-pro-freeze.md): what the
/// shell knew about touches when the bar's Reload was pressed. On an iPad
/// Air with an Apple Pencil Pro the page stops answering every tap, the
/// Pencil's and a finger's, from the Pencil's first contact, while the
/// Pencil's hover still reaches it and the bar still works; no device here
/// does that. The report says which recognizers took the last touches and
/// in what state, and which recognizers and interactions every view holds,
/// so a stuck one shows by name. It is appended to `touch-report.log`
/// beside the local server's log, which the launcher's *Show log file*
/// opens in Files, at each Reload; nothing is sent anywhere.
@MainActor
enum TouchReport {
    /// The latest touches' lines, oldest first (`PencilCapture.onTouch`).
    static var recent = TouchLog()

    private static let clock: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "HH:mm:ss.SSS"
        return format
    }()

    private static let day: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "yyyy-MM-dd HH:mm:ss ZZZZZ"
        return format
    }()

    /// A touch's begin, end or cancel as `PencilCapture` saw it, or its
    /// reset (no touch), noted with `page`, the page's web view, to tell
    /// the page's touches from the chrome's.
    static func note(_ phase: String, _ touch: UITouch?, page: UIView?, capture: String) {
        recent.add(line(phase, touch, page: page, capture: capture, at: Date()))
    }

    /// When, what, where, and each recognizer UIKit gave the touch, in its
    /// state; for a reset, the capture's own state instead.
    static func line(_ phase: String, _ touch: UITouch?, page: UIView?, capture: String, at date: Date) -> String {
        var words = [clock.string(from: date), phase]
        guard let touch else { return (words + [capture]).joined(separator: " ") }
        words.append(kind(touch.type))
        let place = touch.view.map { view in page.map { view.isDescendant(of: $0) ? "page" : "chrome" } ?? "?" } ?? "none"
        words.append("\(place):\(name(of: touch.view))")
        let point = touch.location(in: nil)
        words.append("at \(Int(point.x)),\(Int(point.y))")
        if touch.type == .pencil {
            words.append(String(format: "force %.2f/%.2f roll %.0f", touch.force, touch.maximumPossibleForce,
                                touch.rollAngle * 180 / .pi))
            let expecting = touch.estimatedPropertiesExpectingUpdates
            if !expecting.isEmpty { words.append("expecting \(expecting.rawValue)") }
        }
        let recognizers = (touch.gestureRecognizers ?? []).map { "\(name(of: $0)):\(name($0.state))" }
        words.append("→ " + (recognizers.isEmpty ? "no recognizer" : counted(recognizers)))
        return words.joined(separator: " ")
    }

    /// Equal items once each, in the order they first come, with their
    /// count when it is more than one: WebKit gives a touch eight
    /// `WKDeferringGestureRecognizer`s.
    static func counted(_ items: [String]) -> String {
        var order: [String] = []
        var counts: [String: Int] = [:]
        for item in items {
            if counts[item] == nil { order.append(item) }
            counts[item, default: 0] += 1
        }
        return order.map { item in
            let count = counts[item] ?? 1
            return count > 1 ? "\(item)×\(count)" : item
        }.joined(separator: ", ")
    }

    /// The whole report: the device, the Pencil's settings, the capture,
    /// every recognizer and interaction under `root`, and the latest touches.
    static func report(root: UIView, capture: String, at date: Date) -> [String] {
        var machine = utsname()
        uname(&machine)
        let model = withUnsafeBytes(of: &machine.machine) { bytes in
            String(decoding: bytes.prefix { $0 != 0 }, as: UTF8.self)
        }
        let screen = ((root as? UIWindow) ?? root.window)?.windowScene?.screen
        let size = screen.map { "\(Int($0.bounds.width))×\(Int($0.bounds.height)) pt at \($0.maximumFramesPerSecond) Hz" } ?? "no screen"
        return [
            "=== Touch report, \(day.string(from: date))",
            "Gamma \(HostInfo.current.version), iPadOS \(UIDevice.current.systemVersion), \(model), \(size)",
            "Apple Pencil: only draws \(UIPencilInteraction.prefersPencilOnlyDrawing), "
                + "double tap \(PencilAction.name(UIPencilInteraction.preferredTapAction)), "
                + "squeeze \(PencilAction.name(UIPencilInteraction.preferredSqueezeAction)), "
                + "hover preview \(UIPencilInteraction.prefersHoverToolPreview)",
            "Native pieces: \(NativePieces.current.summary)",
            "Pencil capture: \(capture)",
            "Recognizers and interactions:",
        ] + tree(root) + ["Latest touches, oldest first:"] + recent.lines
    }

    /// Every view under `root` that holds recognizers or interactions,
    /// indented by its depth, with them; at most `limit` lines.
    static func tree(_ root: UIView, limit: Int = 800) -> [String] {
        var lines: [String] = []
        func walk(_ view: UIView, _ depth: Int) {
            guard lines.count < limit else { return }
            let recognizers = view.gestureRecognizers ?? []
            if !recognizers.isEmpty || !view.interactions.isEmpty {
                let pad = String(repeating: "  ", count: depth)
                var title = "\(pad)\(name(of: view))"
                if view.isHidden { title += " hidden" }
                if !view.isUserInteractionEnabled { title += " untouchable" }
                lines.append(title)
                lines += recognizers.map { "\(pad)  \(describe($0))" }
                if !view.interactions.isEmpty {
                    lines.append("\(pad)  interactions: " + view.interactions.map { name(of: $0) }.joined(separator: ", "))
                }
            }
            for child in view.subviews { walk(child, depth + 1) }
        }
        walk(root, 0)
        if lines.count >= limit { lines.append("… cut at \(limit) lines") }
        return lines
    }

    /// A recognizer as the tree lists it: its class and state, and what
    /// is unusual about it.
    static func describe(_ recognizer: UIGestureRecognizer) -> String {
        var words = [name(of: recognizer), name(recognizer.state)]
        if !recognizer.isEnabled { words.append("disabled") }
        if recognizer.numberOfTouches > 0 { words.append("touches \(recognizer.numberOfTouches)") }
        if recognizer.delaysTouchesBegan { words.append("delaysBegan") }
        return words.joined(separator: " ")
    }

    static func name(_ state: UIGestureRecognizer.State) -> String {
        switch state {
        case .possible: return "possible"
        case .began: return "began"
        case .changed: return "changed"
        case .ended: return "ended"
        case .cancelled: return "cancelled"
        case .failed: return "failed"
        @unknown default: return "state\(state.rawValue)"
        }
    }

    private static func kind(_ type: UITouch.TouchType) -> String {
        switch type {
        case .direct: return "finger"
        case .pencil: return "pencil"
        case .indirect: return "indirect"
        case .indirectPointer: return "pointer"
        @unknown default: return "type\(type.rawValue)"
        }
    }

    private static func name(of object: Any?) -> String {
        object.map { String(describing: type(of: $0)) } ?? "none"
    }

    /// Appends `lines` and a blank line to `file`, making its folder. A file
    /// grown past `cap` bytes first keeps only its newest half, from a line's
    /// start.
    nonisolated static func append(_ lines: [String], to file: URL, cap: Int = 1024 * 1024) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        var data = (try? Data(contentsOf: file)) ?? Data()
        if data.count > cap {
            let tail = data.suffix(cap / 2)
            let start = tail.firstIndex(of: UInt8(ascii: "\n")).map { tail.index(after: $0) } ?? tail.startIndex
            data = Data(tail[start...])
        }
        data.append(Data((lines.joined(separator: "\n") + "\n\n").utf8))
        try data.write(to: file, options: .atomic)
    }
}

/// The latest `capacity` lines, oldest first. Pure, so it is tested.
struct TouchLog {
    let capacity: Int
    private(set) var lines: [String] = []

    init(capacity: Int = 100) {
        self.capacity = capacity
    }

    mutating func add(_ line: String) {
        lines.append(line)
        if lines.count > capacity { lines.removeFirst(lines.count - capacity) }
    }
}

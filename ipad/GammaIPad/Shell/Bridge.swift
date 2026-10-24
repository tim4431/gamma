import UIKit
import WebKit

// The one place that knows the protocol between the web app and the shell.
// The web half is frontend/src/shared/lib/host.js; both are described in
// docs/dev/ipad.md "The host contract". The vocabulary is kept small on
// purpose: a web feature reaches the iPad without touching it, and a new
// message is added here and in host.js together, with a flag in HostInfo
// saying what the web may rely on (`version` only names the build).

/// What the shell declares as `window.gammaNative` before the bundle runs.
struct HostInfo: Encodable, Equatable {
    var platform = "ipad"
    /// The app's version (CFBundleShortVersionString).
    var version: String
    /// The host captures claimed Pencil strokes.
    var ink = true
    /// What of the claim's look the live line draws (LiveStrokeView): the
    /// width from the Pencil's force by the brush, the dash, a highlighter
    /// multiplied onto the page, the smoothing, the Pencil brush's width
    /// from the tilt, the Calligraphy pen's width from the travel against
    /// its nib, and a stroke along the ruler's edge (the claim's `snap`,
    /// RulerSnap). A look not listed is drawn as the plain line, and the
    /// page claims the stroke all the same.
    var inkLooks = ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"]
    /// The host sends `hold` when a captured Pencil stays still (PencilCapture).
    var hold = true
    /// The host forwards the Pencil's double tap and squeeze.
    var pencil = true
    /// A squeeze's start and cancel come too (`squeeze`), and its end says
    /// so (`pencil` with `phase`).
    var pencilPhases = true
    /// The `stroke` and `hold` samples carry the Pencil's barrel roll, a
    /// seventh value (UITouch.rollAngle, iPadOS 17.5: an Apple Pencil Pro),
    /// which turns a calligraphy pen's nib; the web shows its Lock angle.
    var pencilRoll: Bool = {
        if #available(iOS 17.5, *) { return true }
        return false
    }()
    /// The host plays `haptic` (PencilHaptics).
    var haptics = true
    /// The host has a server picker (the web shows a "Server…" row).
    var servers = true
    /// The host holds the files the system opens in the app (OpenedFiles)
    /// for the page to take: `files`, `fileData` and `fileDone`, with
    /// `filesOpened` when one comes.
    var openFiles = true

    static var current: HostInfo {
        HostInfo(version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0")
    }

    /// The document-start script. `receive` is a stub that queues whatever
    /// the shell sends before host.js is loaded: host.js replaces `receive`
    /// and drains `queue` (each entry `{msg, resolve, reject}`), so nothing
    /// the shell sends early is lost and every send's promise settles.
    var userScript: String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        let info = (try? encoder.encode(self)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        let host = (try? encoder.encode(platform)).flatMap { String(data: $0, encoding: .utf8) } ?? "\"ipad\""
        return """
        (() => {
          const queue = [];
          window.gammaNative = Object.assign(\(info), {
            queue,
            receive: (msg) => new Promise((resolve, reject) => { queue.push({ msg, resolve, reject }); }),
          });
          document.documentElement.setAttribute("data-host", \(host));
        })();
        """
    }
}

/// The tool a claimed stroke draws with, as the web's ink layer has it, in
/// its preset's look (frontend/src/ink/hostInk.js claimTool; docs/dev/ipad.md
/// "The host contract"). `kind`, `color` and `size` are required. Each look
/// key is left out at its default and read leniently: a value the shell
/// cannot read is dropped, never the claim, so a newer page's claim still
/// draws here, as its plain line at the least.
struct InkTool: Decodable, Equatable {
    /// Anything but a highlighter draws as a pen.
    enum Kind: String { case pen, highlighter }
    /// A pen's brush (finishStroke.js BRUSHES; InkWidth): absent, or a brush
    /// the shell does not know, is the plain Pen.
    enum Brush: String { case pen, monoline, brush, pencil, calligraphy }
    /// The ruler's edge the stroke runs along (RulerSnap): a point of it,
    /// (x0, y0) in viewport CSS px, and its angle in degrees counterclockwise
    /// as seen, the edge running along (cos angle, −sin angle). A snap the
    /// shell cannot read is dropped, never the claim.
    struct Snap: Decodable, Equatable {
        var x0: Double
        var y0: Double
        var angle: Double
    }

    var kind: Kind
    /// "#rrggbb".
    var color: String
    /// The line's width on screen, in CSS px (= points: the viewport is at scale 1).
    var size: Double
    var opacity: Double
    var brush: Brush
    /// The stroke reads the Pencil's pressure (false: *Pressure-sensitive
    /// strokes* off, and only a Brush, by its speed law, and a Calligraphy
    /// pen, by its travel against the nib, still vary).
    var pressure: Bool
    /// The dash, `[on, off]` in CSS px (ink.js DASH × size × zoom); nil for a solid line.
    var dash: [Double]?
    /// Multiplied onto the page (a highlighter's `blend: "multiply"`).
    var multiply: Bool
    /// The smoothing, 0 (none) to 4: the lazy brush's string (LazyBrush.radii).
    var stabilize: Int
    var snap: Snap?
    /// A calligraphy pen's nib, degrees 0…179 counterclockwise as seen (the
    /// edge the vector (cos nib, −sin nib) in the view's y-down space), 45
    /// when the claim names none.
    var nib: Int
    /// The nib turns with the Pencil's roll at the stroke's first sample
    /// (the preset's Lock angle off): `liveNib`.
    var nibRoll: Bool

    init(kind: Kind, color: String, size: Double, opacity: Double = 1, brush: Brush = .pen, pressure: Bool = true,
         dash: [Double]? = nil, multiply: Bool = false, stabilize: Int = 0, snap: Snap? = nil, nib: Int = 45, nibRoll: Bool = false) {
        self.kind = kind
        self.color = color
        self.size = size
        self.opacity = opacity
        self.brush = brush
        self.pressure = pressure
        self.dash = dash
        self.multiply = multiply
        self.stabilize = stabilize
        self.snap = snap
        self.nib = nib
        self.nibRoll = nibRoll
    }

    /// The nib the live line draws with: the claim's, turned by `roll` (the
    /// stroke's first sample's, degrees clockwise as seen) when it follows
    /// the roll, (nib − roll) mod 180 — the page's strokeNib.
    func liveNib(roll: Double?) -> Double {
        guard nibRoll, let roll, roll.isFinite else { return Double(nib) }
        let turned: Double = (Double(nib) - roll).rounded()
        return mod(turned, 180)
    }

    private enum Keys: String, CodingKey { case kind, color, size, opacity, brush, pressure, dash, blend, stabilize, snap, nib, nibRoll }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        let kind = try c.decode(String.self, forKey: .kind)
        // The look: each key on its own, through `try?`, so a bad value is dropped.
        let brush = (try? c.decodeIfPresent(String.self, forKey: .brush)).flatMap { Brush(rawValue: $0) } ?? .pen
        let pressure = (try? c.decodeIfPresent(Bool.self, forKey: .pressure)) ?? true
        // A dash is two lengths, both finite and above 0.
        let dash = (try? c.decodeIfPresent([Double].self, forKey: .dash)).flatMap { pattern -> [Double]? in
            let usable = pattern.count == 2 && pattern.allSatisfy { $0.isFinite && $0 > 0 }
            return usable ? pattern : nil
        }
        let blend = try? c.decodeIfPresent(String.self, forKey: .blend)
        // A level is a number, rounded and held to 0…4.
        let level = (try? c.decodeIfPresent(Double.self, forKey: .stabilize)).flatMap { value -> Int? in
            guard value.isFinite else { return nil }
            let clamped: Double = min(4, max(0, value.rounded()))
            return Int(clamped)
        }
        // A nib is a number, rounded and turned into 0…179 (on the Int, where
        // % and + 180 are exact: `mod` is for Doubles).
        let nib = (try? c.decodeIfPresent(Double.self, forKey: .nib)).flatMap { value -> Int? in
            guard value.isFinite, abs(value) < 1e6 else { return nil }
            let degrees = Int(value.rounded()) % 180
            return degrees < 0 ? degrees + 180 : degrees
        }
        let nibRoll = (try? c.decodeIfPresent(Bool.self, forKey: .nibRoll)) ?? false
        self.init(kind: kind == "highlighter" ? .highlighter : .pen,
                  color: try c.decode(String.self, forKey: .color),
                  size: try c.decode(Double.self, forKey: .size),
                  opacity: try c.decodeIfPresent(Double.self, forKey: .opacity) ?? 1,
                  brush: brush,
                  pressure: pressure,
                  dash: dash,
                  multiply: blend == "multiply",
                  stabilize: level ?? 0,
                  snap: try? c.decodeIfPresent(Snap.self, forKey: .snap),
                  nib: nib ?? 45,
                  nibRoll: nibRoll)
    }
}

/// What a `haptic` is for (PencilHaptics plays each as its own feedback,
/// HapticFeel).
enum HapticKind: String {
    /// A held stroke snapped to a shape.
    case snap
    /// A pen lasso selected strokes.
    case select
    /// A Pencil gesture switched the tool; also a kind the shell does not know.
    case tool
}

/// Web → native: `window.webkit.messageHandlers.gamma.postMessage(msg)`.
enum WebMessage: Equatable {
    /// This Pencil touch is a stroke: take it. Replied `{taken}`. `at` is
    /// where the pointer went down (viewport CSS px = the web view's points),
    /// which tells the shell which touch the page means (ClaimQueue).
    case claim(pointerId: Int, at: CGPoint, tool: InkTool)
    /// Paint the window behind the web view and pick the status bar style.
    case theme(bg: String, scheme: Scheme)
    /// Show the server picker.
    case servers
    /// Play the kind's feedback (PencilHaptics), sent once per event. `at`
    /// is where, in viewport CSS px, when the page says.
    case haptic(kind: HapticKind, at: CGPoint?)
    /// The files the system opened in the app that the shell still holds
    /// (OpenedFiles): replied `{files: [{id, name, size, type}]}`.
    case files
    /// Up to `OpenedFiles.chunkBytes` of a held file from `offset`: replied
    /// `{data}` in base64, "" at its end, `{}` when the shell holds no such
    /// file.
    case fileData(id: String, offset: Int)
    /// The page is done with a held file: the shell drops its copy.
    case fileDone(id: String)

    enum Scheme: String, Decodable { case light, dark }
}

extension WebMessage: Decodable {
    private enum Keys: String, CodingKey { case type, pointerId, at, tool, bg, scheme, kind, id, offset }
    private struct Point: Decodable { let x, y: Double }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "claim":
            let at = try c.decode(Point.self, forKey: .at)
            self = .claim(pointerId: try c.decode(Int.self, forKey: .pointerId), at: CGPoint(x: at.x, y: at.y),
                          tool: try c.decode(InkTool.self, forKey: .tool))
        case "theme":
            self = .theme(bg: try c.decode(String.self, forKey: .bg), scheme: try c.decode(Scheme.self, forKey: .scheme))
        case "servers":
            self = .servers
        case "haptic":
            let kind = (try? c.decodeIfPresent(String.self, forKey: .kind)).flatMap { HapticKind(rawValue: $0) } ?? .tool
            let at: CGPoint? = (try? c.decodeIfPresent(Point.self, forKey: .at)).map { (p: Point) -> CGPoint in CGPoint(x: p.x, y: p.y) }
            self = .haptic(kind: kind, at: at)
        case "files":
            self = .files
        case "fileData":
            self = .fileData(id: try c.decode(String.self, forKey: .id), offset: try c.decode(Int.self, forKey: .offset))
        case "fileDone":
            self = .fileDone(id: try c.decode(String.self, forKey: .id))
        default:
            throw DecodingError.dataCorruptedError(forKey: .type, in: c, debugDescription: "unknown message \(type)")
        }
    }

    /// A message as WebKit hands it over (`WKScriptMessage.body`: the
    /// posted object as Foundation types), through the same Codable path.
    init(body: Any) throws {
        self = try decodeWebValue(WebMessage.self, from: body)
    }
}

/// A value as WebKit hands it over (a posted message's body, or what a
/// promise resolved with: Foundation types) decoded as `type`, through its
/// Codable path. JSONSerialization raises an Objective-C exception (not a
/// Swift error) on anything it cannot serialize, so that is checked first.
private func decodeWebValue<T: Decodable>(_ type: T.Type, from value: Any) throws -> T {
    guard JSONSerialization.isValidJSONObject(value) else {
        throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "not a JSON object"))
    }
    let data: Data = try JSONSerialization.data(withJSONObject: value)
    return try JSONDecoder().decode(type, from: data)
}

enum PencilGesture: String, Encodable { case tap, squeeze }

/// A squeeze's phase (iPadOS 17.5, an Apple Pencil Pro): its start and its
/// cancel come as `squeeze`, its end as `pencil` (PencilGestures).
enum SqueezePhase: String, Encodable { case began, ended, cancelled }

/// Native → web: `window.gammaNative.receive(msg)`, awaited.
enum HostMessage: Equatable {
    /// The finished stroke: `[x, y, p, t, a, z, r?]` per sample (StrokeRecorder),
    /// x and y in CSS px of the viewport, p = force / maximum force, t = ms
    /// from the first sample, a and z the Pencil's altitude and azimuth in
    /// degrees, r its barrel roll in degrees clockwise as seen (`pencilRoll`
    /// only). No samples: the system cancelled the touch, or the page took
    /// the hold. `held` is the `n` of the hold whose shape the line was drawn
    /// as (ShapeHold), nil when none was: the page commits a shape only for
    /// that hold, so a lift that came before the page's answer stays the
    /// freehand line it was drawn as. The web resolves once the stroke is
    /// painted.
    case stroke(pointerId: Int, samples: [[Double]], held: Int? = nil)
    /// The captured Pencil has held still (PencilCapture): the samples so
    /// far, as `stroke` sends them. Asked (`Bridge.ask`): the page answers at
    /// once with a shape, a cancel or nothing (HoldReply).
    case hold(pointerId: Int, samples: [[Double]])
    /// A Pencil gesture with the system's preferred action (PencilGestures):
    /// a double tap, or a squeeze's end, which says `phase: "ended"` (a
    /// squeeze without it, from an older shell, is a quick squeeze).
    case pencil(gesture: PencilGesture, action: String, phase: SqueezePhase? = nil)
    /// A squeeze's start (.began) or its cancel (.cancelled, in place of its
    /// `pencil`): a type of its own, so a page that predates it ignores both.
    case squeeze(phase: SqueezePhase, action: String)
    /// The system opened a file in the app (OpenedFiles): the page asks for
    /// what is held (`files`) when it can take it. A page that is not ready,
    /// or predates it, ignores it, and a ready page asks on its own.
    case filesOpened
}

extension HostMessage: Encodable {
    private enum Keys: String, CodingKey { case type, pointerId, samples, held, gesture, action, phase }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        switch self {
        case let .stroke(pointerId, samples, held):
            try c.encode("stroke", forKey: .type)
            try c.encode(pointerId, forKey: .pointerId)
            try c.encode(samples, forKey: .samples)
            try c.encodeIfPresent(held, forKey: .held)
        case let .hold(pointerId, samples):
            try c.encode("hold", forKey: .type)
            try c.encode(pointerId, forKey: .pointerId)
            try c.encode(samples, forKey: .samples)
        case let .pencil(gesture, action, phase):
            try c.encode("pencil", forKey: .type)
            try c.encode(gesture, forKey: .gesture)
            try c.encode(action, forKey: .action)
            try c.encodeIfPresent(phase, forKey: .phase)
        case let .squeeze(phase, action):
            try c.encode("squeeze", forKey: .type)
            try c.encode(phase, forKey: .phase)
            try c.encode(action, forKey: .action)
        case .filesOpened:
            try c.encode("filesOpened", forKey: .type)
        }
    }
}

/// The page's answer to `hold`, as `Bridge.ask` hands it over (the value
/// its promise resolved with, as Foundation types): `{shape: {kind, params}}`
/// to snap to that shape (HeldShape, viewport CSS px), `{cancel: true}` when
/// the page took the hold (*Paste here*), and anything else, `{}` included,
/// nothing. A cancel wins over a shape; a shape of an unknown kind, or with
/// params it cannot be drawn from, is nothing.
enum HoldReply: Equatable {
    case shape(HeldShape)
    case cancel
    case nothing

    init(reply: Any?) {
        guard let reply = reply as? [String: Any] else {
            self = .nothing
            return
        }
        if let cancel = reply["cancel"] as? Bool, cancel {
            self = .cancel
            return
        }
        guard let body = reply["shape"], let shape = try? decodeWebValue(HeldShape.self, from: body) else {
            self = .nothing
            return
        }
        self = .shape(shape)
    }
}

/// The handler WebKit calls (handler name `gamma`, page content world) and
/// the way back (`send`, and `ask` when the shell needs the answer). Messages
/// are taken only from the server's own main frame: nothing else is loaded
/// in the shell's web view, and nothing else should be able to claim the
/// Pencil.
@MainActor
final class Bridge: NSObject, WKScriptMessageHandlerWithReply {
    static let handlerName = "gamma"

    weak var webView: WKWebView?
    /// The origin messages are taken from (the server shown).
    var origin: Origin?
    /// Answers one message; the result is the reply's value.
    var onMessage: ((WebMessage) -> [String: Any])?

    func install(in controller: WKUserContentController, info: HostInfo) {
        controller.addUserScript(WKUserScript(source: info.userScript, injectionTime: .atDocumentStart,
                                              forMainFrameOnly: true, in: .page))
        controller.addScriptMessageHandler(self, contentWorld: .page, name: Self.handlerName)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.frameInfo.isMainFrame, let origin, Origin(message.frameInfo.securityOrigin) == origin else {
            replyHandler(nil, "Only the Gamma server's page talks to the app.")
            return
        }
        do {
            let parsed = try WebMessage(body: message.body)
            replyHandler(onMessage?(parsed) ?? [:], nil)
        } catch {
            replyHandler(nil, "Gamma for iPad does not understand this message.")
        }
    }

    /// Delivers `message` and waits for the web's promise. False when it
    /// could not be delivered or the web rejected it. The page's value is
    /// not handed back (`return true`): WebKit may fail to convert it.
    @discardableResult
    func send(_ message: HostMessage) async -> Bool {
        let result: Any? = await deliver(message, running: "await window.gammaNative.receive(JSON.parse(json)); return true;")
        return result != nil
    }

    /// Delivers `message` and hands back what the web's promise resolved
    /// with, as Foundation types (`{}` when it resolved with nothing, as a
    /// page without a listener for the type does), or nil when it could not
    /// be delivered or the web rejected it.
    func ask(_ message: HostMessage) async -> Any? {
        await deliver(message, running: "return (await window.gammaNative.receive(JSON.parse(json))) ?? {};")
    }

    /// Runs `body` in the page with `message` as its `json`: what `body`
    /// returned, or nil when the message could not be encoded or
    /// delivered, or `body` threw (the web rejected the message).
    private func deliver(_ message: HostMessage, running body: String) async -> Any? {
        guard let webView, let data = try? JSONEncoder().encode(message),
              let json = String(data: data, encoding: .utf8) else { return nil }
        return try? await webView.callAsync(body, arguments: ["json": json], in: .page)
    }
}

extension WKWebView {
    /// `callAsyncJavaScript` as an async call. The body returns a value on
    /// every path: WebKit hands an undefined result over as nil.
    func callAsync(_ body: String, arguments: [String: Any] = [:], in world: WKContentWorld) async throws -> Any {
        try await withCheckedThrowingContinuation { continuation in
            callAsyncJavaScript(body, arguments: arguments, in: nil, in: world) { result in
                continuation.resume(with: result)
            }
        }
    }
}

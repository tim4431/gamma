import UIKit
import WebKit

// The one place that knows the protocol between the web app and the shell.
// The web half is frontend/src/shared/lib/host.js; both are described in
// docs/dev/ipad.md "The host contract". The vocabulary is kept small on
// purpose: a web feature reaches the iPad without touching it, and a new
// message is added here and in host.js together, with `version` telling the
// web which host it runs under.

/// What the shell declares as `window.gammaNative` before the bundle runs.
struct HostInfo: Encodable, Equatable {
    var platform = "ipad"
    /// The app's version (CFBundleShortVersionString).
    var version: String
    /// The host captures claimed Pencil strokes.
    var ink = true
    /// The host forwards the Pencil's double tap and squeeze.
    var pencil = true
    /// The host has a server picker (the web shows a "Server…" row).
    var servers = true

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

/// The tool a claimed stroke draws with, as the web's ink layer has it.
struct InkTool: Decodable, Equatable {
    enum Kind: String, Decodable { case pen, highlighter }
    var kind: Kind
    /// "#rrggbb".
    var color: String
    /// The line's width on screen, in CSS px (= points: the viewport is at scale 1).
    var size: Double
    var opacity: Double

    init(kind: Kind, color: String, size: Double, opacity: Double = 1) {
        self.kind = kind
        self.color = color
        self.size = size
        self.opacity = opacity
    }

    private enum Keys: String, CodingKey { case kind, color, size, opacity }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        self.init(kind: try c.decode(Kind.self, forKey: .kind),
                  color: try c.decode(String.self, forKey: .color),
                  size: try c.decode(Double.self, forKey: .size),
                  opacity: try c.decodeIfPresent(Double.self, forKey: .opacity) ?? 1)
    }
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

    enum Scheme: String, Decodable { case light, dark }
}

extension WebMessage: Decodable {
    private enum Keys: String, CodingKey { case type, pointerId, at, tool, bg, scheme }
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
        default:
            throw DecodingError.dataCorruptedError(forKey: .type, in: c, debugDescription: "unknown message \(type)")
        }
    }

    /// A message as WebKit hands it over (`WKScriptMessage.body`: the
    /// posted object as Foundation types), through the same Codable path.
    init(body: Any) throws {
        // JSONSerialization raises an Objective-C exception (not a Swift
        // error) on anything it cannot serialize, so this is checked first.
        guard JSONSerialization.isValidJSONObject(body) else {
            throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "not a message object"))
        }
        self = try JSONDecoder().decode(WebMessage.self, from: JSONSerialization.data(withJSONObject: body))
    }
}

enum PencilGesture: String, Encodable { case tap, squeeze }

/// Native → web: `window.gammaNative.receive(msg)`, awaited.
enum HostMessage: Equatable {
    /// The finished stroke: `[x, y, p, t]` per sample, x and y in CSS px of
    /// the viewport, p = force / maximum force, t = ms from the first sample.
    /// The web resolves once the stroke is painted.
    case stroke(pointerId: Int, samples: [[Double]])
    /// A Pencil gesture with the system's preferred action (PencilGestures).
    case pencil(gesture: PencilGesture, action: String)
}

extension HostMessage: Encodable {
    private enum Keys: String, CodingKey { case type, pointerId, samples, gesture, action }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        switch self {
        case let .stroke(pointerId, samples):
            try c.encode("stroke", forKey: .type)
            try c.encode(pointerId, forKey: .pointerId)
            try c.encode(samples, forKey: .samples)
        case let .pencil(gesture, action):
            try c.encode("pencil", forKey: .type)
            try c.encode(gesture, forKey: .gesture)
            try c.encode(action, forKey: .action)
        }
    }
}

/// The handler WebKit calls (handler name `gamma`, page content world) and
/// the way back (`send`). Messages are taken only from the server's own main
/// frame: nothing else is loaded in the shell's web view, and nothing else
/// should be able to claim the Pencil.
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
    /// could not be delivered or the web rejected it.
    @discardableResult
    func send(_ message: HostMessage) async -> Bool {
        guard let webView, let data = try? JSONEncoder().encode(message),
              let json = String(data: data, encoding: .utf8) else { return false }
        do {
            _ = try await webView.callAsync("await window.gammaNative.receive(JSON.parse(json)); return true;",
                                            arguments: ["json": json], in: .page)
            return true
        } catch {
            return false
        }
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

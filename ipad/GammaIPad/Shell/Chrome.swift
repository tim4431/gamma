import UIKit
import WebKit

/// The desktop app's own chrome on the iPad: its shell bar and its launcher
/// (desktop/ui/bar.html and launcher.html, copied into the bundle's
/// `chrome` folder by project.yml), each in a web view of its own over the
/// page, and the `gammaShell` they call, answered here as Electron's
/// preload answers it (desktop/preload.js; docs/dev/ipad.md "The chrome").
/// Both views share one configuration, which loads nothing but the
/// bundled pages; the page's web view (WebShell) never sees any of it.
@MainActor
final class Chrome: NSObject, WKScriptMessageHandlerWithReply, WKNavigationDelegate {
    static let folder = (Bundle.main.resourceURL ?? Bundle.main.bundleURL).appendingPathComponent("chrome", isDirectory: true)
    static let barURL = folder.appendingPathComponent("bar.html")

    /// Answers one call by its method's name: the reply's value (JSON
    /// values only), or a throw, whose message the page's promise rejects with.
    var onCall: (@MainActor (_ method: String, _ args: [Any]) async throws -> Any?)?
    /// A page's process ended, just before the page loads again.
    var onTerminate: ((WKWebView) -> Void)?

    /// Made on the first view, with the calls' script and their handler.
    /// Lazy rather than set in an init: overriding NSObject's init from a
    /// main-actor class is a mismatch the compiler warns about.
    private lazy var configuration: WKWebViewConfiguration = {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.addUserScript(WKUserScript(source: Self.script, injectionTime: .atDocumentStart,
                                                                       forMainFrameOnly: true, in: .page))
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "shell")
        return configuration
    }()

    /// The calls the pages make on the iPad (ShellController.call): the
    /// desktop's gammaShell without what the iPad does not have.
    static let script = """
    (() => {
      const listeners = new Set();
      const post = (method, args) => window.webkit.messageHandlers.shell.postMessage({ method, args });
      const shell = {
        platform: "ipad",
        onState(cb) { listeners.add(cb); return () => listeners.delete(cb); },
        _state(s) { for (const cb of listeners) cb(s); },
      };
      for (const m of ["state", "list", "open", "openWorkspace", "launcher", "reload", "barExpand", "addRemote",
                       "rename", "setUrl", "remove", "revealData", "revealLog", "copyAddress"]) {
        shell[m] = (...args) => post(m, args);
      }
      window.gammaShell = shell;
    })();
    """

    /// The launcher, with the failure it shows (ShellFailure) as the
    /// desktop passes it. Every character but the unreserved ones is
    /// percent-encoded: URLComponents' own query encoding keeps `+`, which
    /// the page's URLSearchParams reads as a space.
    static func launcherURL(error: String?) -> URL {
        let page = folder.appendingPathComponent("launcher.html")
        guard let error, var parts = URLComponents(url: page, resolvingAgainstBaseURL: false) else { return page }
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        parts.percentEncodedQuery = "error=" + (error.addingPercentEncoding(withAllowedCharacters: unreserved) ?? "")
        return parts.url ?? page
    }

    /// A web view for one of the pages, set up as WebShell sets up its own:
    /// the bar see-through around its strip and open menu, which scrolls in
    /// its own box; the launcher in the theme's colour until it paints.
    func makeView(bar: Bool) -> WKWebView {
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.isOpaque = false
        view.backgroundColor = bar ? .clear : (WebShell.savedBackground ?? .systemBackground)
        view.scrollView.backgroundColor = .clear
        view.scrollView.bounces = false
        view.scrollView.isScrollEnabled = !bar
        view.scrollView.contentInsetAdjustmentBehavior = .never // the bar pads by env(safe-area-inset-top) itself
        view.allowsLinkPreview = false
        #if DEBUG
        view.isInspectable = true // Safari's Web Inspector from a Mac
        #endif
        view.navigationDelegate = self
        return view
    }

    /// Hands the state to a page's `onState` listeners. A page not loaded
    /// yet has none, and asks for the state itself once it runs.
    static func push(_ state: [String: Any], to view: WKWebView) {
        view.callAsyncJavaScript("window.gammaShell?._state(state); return true;", arguments: ["state": state],
                                 in: nil, in: .page) { _ in }
    }

    /// Only the bundled pages call: nothing else is loaded with this
    /// configuration, and a frame inside one is not the page.
    static func accepts(_ url: URL?, mainFrame: Bool) -> Bool {
        mainFrame && url?.isFileURL == true
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard Self.accepts(message.frameInfo.request.url, mainFrame: message.frameInfo.isMainFrame),
              let body = message.body as? [String: Any], let method = body["method"] as? String else {
            replyHandler(nil, "Only Gamma's own pages talk to the app.")
            return
        }
        let args = body["args"] as? [Any] ?? []
        let call = onCall
        Task {
            do {
                let value: Any? = try await call?(method, args)
                replyHandler(value, nil)
            } catch {
                replyHandler(nil, error.localizedDescription)
            }
        }
    }

    /// The system ended a page's process: the page loads again and asks for the state.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        onTerminate?(webView)
        webView.reload()
    }
}

/// A refusal with the sentence shown for it: a chrome call's, which the
/// page's promise rejects with, or the Gamma Cloud sign-in's (CloudSignIn).
struct Refusal: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? { message }
}

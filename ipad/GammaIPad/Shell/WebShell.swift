import UIKit
import WebKit

/// The one web view the app shows, set up once and kept for the app's life:
/// the server's web app in it, the host declared before the bundle runs
/// (Bridge), and the delegates that keep the window the web app's
/// (Navigation, CloudSignIn). Under it, the page lays out as in Safari on an
/// iPad: the desktop content mode on every model (an iPad mini would get the
/// phone's otherwise), one CSS pixel to one point, and a document that never
/// scrolls or bounces (the web app scrolls its own panes).
@MainActor
final class WebShell {
    let webView: WKWebView
    let bridge: Bridge
    let navigation: Navigation
    let cloudSignIn: CloudSignIn
    private(set) var server: URL?
    /// The local server's seeded account while the local server is shown
    /// (AppModel.login), nil for any other server.
    var login: LocalLogin?
    /// A sign-in is due after the next finished load: set by each open,
    /// retry and recovery, as the desktop signs in once per open
    /// (desktop/main.js openServerNow). A person who signs out stays out
    /// until the server is opened again.
    private var loginDue = false
    private var loading: NSKeyValueObservation?

    private static let backgroundKey = "gamma.theme.background"

    init() {
        let configuration = WKWebViewConfiguration()
        // The default store keeps the server's session cookie (a year) across launches.
        configuration.websiteDataStore = .default()
        configuration.defaultWebpagePreferences.preferredContentMode = .desktop
        configuration.allowsInlineMediaPlayback = true // the recordings' player
        let bridge = Bridge()
        bridge.install(in: configuration.userContentController, info: .current)

        let webView = WKWebView(frame: .zero, configuration: configuration)
        // Transparent until the page paints: the container shows the theme's colour.
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.bounces = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never // the page reads the safe areas itself
        webView.allowsLinkPreview = false // the web app has its own long-press menus
        #if DEBUG
        webView.isInspectable = true // Safari's Web Inspector from a Mac
        #endif

        self.webView = webView
        self.bridge = bridge
        navigation = Navigation()
        cloudSignIn = CloudSignIn()

        webView.navigationDelegate = navigation
        webView.uiDelegate = navigation
        bridge.webView = webView
        cloudSignIn.webView = webView
        navigation.onCloudSignIn = { [weak self] url in self?.cloudSignIn.start(url) }
        navigation.onTerminate = { [weak self] in self?.recover() }
        cloudSignIn.report = { [weak self] message in self?.navigation.alert(message) }
        // A main-frame load has finished when the web view stops loading
        // (KVO on WKWebView comes on the main thread).
        loading = webView.observe(\.isLoading, options: [.new]) { [weak self] _, change in
            guard change.newValue == false else { return }
            MainActor.assumeIsolated { self?.signInIfDue() }
        }
    }

    /// Shows `server`; nothing happens when it is the one shown.
    func open(_ server: URL) {
        guard server != self.server else { return }
        self.server = server
        let origin = Origin(server)
        bridge.origin = origin
        navigation.server = origin
        loginDue = true
        webView.load(URLRequest(url: server))
    }

    /// After a failed load: the server's address again.
    func retry() {
        guard let server else { return }
        loginDue = true
        webView.load(URLRequest(url: server))
    }

    /// The page's process was ended by the system: the page reloads, and the
    /// web app restores itself as after any reload.
    private func recover() {
        loginDue = true
        if webView.url == nil { retry() } else { webView.reload() }
    }

    // --- the local server's silent sign-in --------------------------------------------------

    /// The desktop's autoLoginScript (desktop/main.js): when the page's
    /// session is anonymous, sign in with the seeded account and reload, so
    /// the session cookie lands in the web view's own store. Run as a
    /// function body with the account as arguments (no quoting), in the
    /// app's own content world, so the page's scripts never see the password.
    static let autoLoginScript = """
    const session = await fetch("/api/session", { credentials: "same-origin" });
    if (session.ok) {
      const answer = await session.json().catch(() => null);
      if (answer && answer.user) return "already";
    }
    const login = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
    if (login.ok) {
      location.reload();
      return "logged-in";
    }
    return "login-failed:" + login.status;
    """

    /// Only on the local server's own page: nothing is run in a remote one.
    private func signInIfDue() {
        guard loginDue, let login, let server, let page = webView.url,
              let origin = Origin(server), Origin(page) == origin else { return }
        loginDue = false
        let webView = self.webView
        Task {
            _ = try? await webView.callAsync(Self.autoLoginScript,
                                             arguments: ["username": login.username, "password": login.password],
                                             in: .defaultClient)
        }
    }

    /// The theme's background behind and under the page, kept for the next
    /// launch so the app opens in the colour it closed in. Nil when `hex`
    /// is no colour.
    func paint(_ hex: String) -> UIColor? {
        guard let color = UIColor(cssHex: hex) else { return nil }
        webView.underPageBackgroundColor = color
        UserDefaults.standard.set(hex, forKey: Self.backgroundKey)
        return color
    }

    static var savedBackground: UIColor? {
        UserDefaults.standard.string(forKey: backgroundKey).flatMap { UIColor(cssHex: $0) }
    }
}

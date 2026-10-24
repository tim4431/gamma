import ObjectiveC
import UIKit
import WebKit

/// The web view of the server's page, set up once and kept for the app's
/// life (the chrome over it has views of its own, Chrome): the server's web
/// app in it, the host declared before the bundle runs (Bridge), and the
/// delegates that keep the window the web app's (Navigation, CloudSignIn).
/// Under it, the page lays out as in Safari on an iPad: the desktop content
/// mode on every model (an iPad mini would get the phone's otherwise), one
/// CSS pixel to one point, and a document that never scrolls or bounces
/// (the web app scrolls its own panes).
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
    /// What the page's server answered `/api/session` (its user, default
    /// workspace and workspaces: the bar's switcher), nil while no one is
    /// signed in or it is not known.
    private(set) var session: [String: Any]?
    /// The page's theme (`<html data-theme>`), which the chrome pages paint
    /// in, kept for the next launch like the background; "" until a page
    /// has reported one.
    private(set) var themeName: String
    /// `session` or `themeName` changed (the controller hands the chrome its state).
    var onChange: (() -> Void)?
    private var readingSession = false
    private var loading: NSKeyValueObservation?
    private var address: NSKeyValueObservation?

    private static let backgroundKey = "gamma.theme.background"
    private static let themeNameKey = "gamma.theme.name"

    init() {
        themeName = UserDefaults.standard.string(forKey: Self.themeNameKey) ?? ""
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
        Self.hideKeyboardAccessoryBar(of: webView)

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
            MainActor.assumeIsolated {
                self?.signInIfDue()
                self?.readSession()
            }
        }
        // The address changes without a load too: the web app writes the
        // workspace (?ws=) with replaceState, and signs in in place.
        address = webView.observe(\.url, options: [.new]) { [weak self] _, _ in
            MainActor.assumeIsolated { self?.readSession() }
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
        session = nil
        webView.load(URLRequest(url: server))
    }

    /// The server's web app on one of its workspaces, as the desktop's
    /// switcher opens one (`?ws=`).
    func openWorkspace(_ id: String) {
        guard let server, var parts = URLComponents(url: server, resolvingAgainstBaseURL: false) else { return }
        parts.path = "/"
        parts.queryItems = [URLQueryItem(name: "ws", value: id)]
        guard let url = parts.url else { return }
        webView.load(URLRequest(url: url))
    }

    /// After a failed load: the server's address again.
    func retry() {
        guard let server else { return }
        loginDue = true
        webView.load(URLRequest(url: server))
    }

    /// The page again while it is on the shown server, else the server's
    /// address (none has loaded, or a switch to it failed).
    func reload() {
        if let page = webView.url, let server, Origin(page) == Origin(server) { webView.reload() } else { retry() }
    }

    /// The page's process was ended by the system: the page reloads, and the
    /// web app restores itself as after any reload.
    private func recover() {
        loginDue = true
        Self.hideKeyboardAccessoryBar(of: webView) // a new process may bring a new content view
        reload()
    }

    // --- the session, for the bar's switcher --------------------------------------------------

    /// The desktop's refreshGamma (desktop/main.js): a public API call with
    /// the page's own cookie, run in the app's content world, so nothing is
    /// put into the page's.
    static let sessionScript = """
    const answer = await fetch("/api/session", { credentials: "same-origin", cache: "no-store" });
    const session = answer.ok ? await answer.json().catch(() => null) : null;
    if (!session || !session.user) return null;
    return { user: session.user, default_workspace: session.default_workspace ?? null, workspaces: session.workspaces || [] };
    """

    /// Reads the session while the page is on the shown server and not
    /// loading (a load's new address comes before its document: a read then
    /// runs in the old one), one read at a time; the change is announced
    /// whatever it is (`onChange`).
    func readSession() {
        guard !readingSession, !webView.isLoading, let server, let page = webView.url,
              let origin = Origin(server), Origin(page) == origin else { return }
        readingSession = true
        let webView = self.webView
        Task {
            let answer = try? await webView.callAsync(Self.sessionScript, in: .defaultClient)
            readingSession = false
            session = answer as? [String: Any]
            onChange?()
        }
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
    /// launch so the app opens in the colour it closed in, and its name for
    /// the chrome. Nil when `hex` is no colour.
    func paint(_ hex: String, name: String) -> UIColor? {
        if name != themeName {
            themeName = name
            UserDefaults.standard.set(name, forKey: Self.themeNameKey)
            onChange?()
        }
        guard let color = UIColor(cssHex: hex) else { return nil }
        webView.underPageBackgroundColor = color
        UserDefaults.standard.set(hex, forKey: Self.backgroundKey)
        return color
    }

    static var savedBackground: UIColor? {
        UserDefaults.standard.string(forKey: backgroundKey).flatMap { UIColor(cssHex: $0) }
    }

    // --- the keyboard's accessory bar ------------------------------------------------------

    /// WebKit's content view shows an accessory bar (‹ › Done) over the
    /// keyboard, which Safari does not, and the web app's editing bar
    /// (editor/EditBar.jsx) would sit on it. The content view is given a
    /// subclass made at run time whose `inputAccessoryView` is nil, the
    /// technique Capacitor and Cordova ship. False when the content view was
    /// not found (another WebKit); a second call on the same view is nothing.
    @discardableResult
    static func hideKeyboardAccessoryBar(of webView: WKWebView) -> Bool {
        let prefix = "GammaNoAccessory_"
        // Once given the subclass, the content view's class name starts with the prefix.
        guard let content = webView.scrollView.subviews.first(where: {
            let name = NSStringFromClass(type(of: $0))
            return name.hasPrefix("WKContent") || name.hasPrefix(prefix + "WKContent")
        }) else {
            return false
        }
        let base: AnyClass = type(of: content)
        let baseName = NSStringFromClass(base)
        if baseName.hasPrefix(prefix) { return true }
        let name = prefix + baseName
        let subclass: AnyClass
        if let existing = NSClassFromString(name) {
            subclass = existing
        } else {
            guard let made = objc_allocateClassPair(base, name, 0),
                  let method = class_getInstanceMethod(NoAccessoryView.self, #selector(getter: NoAccessoryView.inputAccessoryView)) else {
                return false
            }
            class_addMethod(made, #selector(getter: UIResponder.inputAccessoryView),
                            method_getImplementation(method), method_getTypeEncoding(method))
            objc_registerClassPair(made)
            subclass = made
        }
        object_setClass(content, subclass)
        return true
    }
}

/// The one method the content view's subclass gets (`hideKeyboardAccessoryBar`).
private final class NoAccessoryView: NSObject {
    @objc var inputAccessoryView: UIView? { nil }
}

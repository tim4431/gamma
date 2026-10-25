import SwiftUI
import UIKit
import WebKit

/// The whole window, made once: the controller is kept, and a new server, a
/// retry or the launcher reaches it here, which also hands the chrome its
/// state whenever the model or the local server changes.
struct ShellView: UIViewControllerRepresentable {
    @ObservedObject var model: AppModel
    @ObservedObject var local: LocalServer

    func makeUIViewController(context: Context) -> ShellController {
        let controller = ShellController(model: model)
        updateUIViewController(controller, context: context)
        return controller
    }

    func updateUIViewController(_ controller: ShellController, context: Context) {
        let server = model.shellURL
        controller.show(server, login: server.flatMap { model.login(for: $0) }, attempt: model.connects)
        controller.showLauncher(model.launcher)
        controller.placeBar(shown: model.barShown, hidden: model.barHidden)
        controller.pushState()
    }
}

/// The container, bottom to top: the web app's web view with the live
/// stroke over it, the launcher while it is up, and the bar (Chrome) unless
/// the person hid it (AppModel.barShown), with the Pencil capture and
/// gestures on the container itself, so they see every touch the web view
/// gets. It answers the web's messages and the chrome's calls; none of it
/// knows what the page shows. Safari views, alerts and the share sheet are
/// presented from here.
final class ShellController: UIViewController {
    private let model: AppModel
    private let shell: WebShell
    private let chrome: Chrome
    private let bar: WKWebView
    private var launcherView: WKWebView?
    private var launcher: AppModel.Launcher?
    /// The bar's bottom edge: under its strip, or at the window's bottom
    /// while a dropdown is open (`expandBar`). Made with the view.
    private var barStrip: NSLayoutConstraint?
    private var barFull: NSLayoutConstraint?
    /// The frame the page's web view, the live stroke and the launcher
    /// share: from under the bar's strip, or from under the status bar while
    /// the bar is away (`placeBar`), to the window's bottom. Its top is made
    /// with the view.
    private let content = UILayoutGuide()
    private var contentTop: NSLayoutConstraint?
    /// The bar as it is placed, and the person's choice as the page was last
    /// told it (`shellBar`).
    private var barShown: Bool
    private var barHidden: Bool
    private let live: LiveStrokeView
    private let capture: PencilCapture
    private let gestures: PencilGestures
    private var attempt = 0
    private var scheme: WebMessage.Scheme?
    private var sceneStyle: (any UITraitChangeRegistration)?

    /// The bar's strip below the status bar: bar.html's `.bar` on the iPad,
    /// 44 px under its env(safe-area-inset-top) padding.
    private static let barHeight: CGFloat = 44
    /// How long the bar takes to fade away or back.
    private static let barFade: TimeInterval = 0.2
    /// How long a finished stroke's line waits for the web's paint before it
    /// goes anyway (a page that never answers leaves nothing behind).
    private static let paintTimeout: UInt64 = 3_000_000_000

    init(model: AppModel) {
        self.model = model
        barShown = model.barShown
        barHidden = model.barHidden
        shell = WebShell()
        let chrome = Chrome()
        self.chrome = chrome
        bar = chrome.makeView(bar: true)
        live = LiveStrokeView(frame: .zero)
        capture = PencilCapture(target: nil, action: nil)
        gestures = PencilGestures()
        super.init(nibName: nil, bundle: nil)
        // Wired before the first load starts (`show` can come before the view).
        shell.navigation.presenter = self
        shell.navigation.onLoad = { [weak self] failure in
            guard let self else { return }
            if let failure { self.model.loadFailed(failure) } else { self.model.pageLoaded() }
        }
        shell.onChange = { [weak self] in self?.pushState() }
        shell.bridge.onMessage = { [weak self] message in self?.answer(message) ?? [:] }
        chrome.onCall = { [weak self] method, args in try await self?.call(method, args) }
        // A bar reloaded after its process ended has no dropdown open.
        chrome.onTerminate = { [weak self] view in
            guard let self, view === self.bar else { return }
            self.expandBar(false)
        }
        bar.loadFileURL(Chrome.barURL, allowingReadAccessTo: Chrome.folder)
        capture.onPreview = { [weak self] samples, predicted, shape, tool in
            self?.live.show(samples, predicted: predicted, shape: shape, tool: tool)
        }
        capture.onStroke = { [weak self] pointerId, samples, held in
            self?.deliver(pointerId: pointerId, samples: samples, held: held)
        }
        capture.onAbandon = { [weak self] pointerId in self?.abandon(pointerId: pointerId) }
        capture.onHold = { [weak self] pointerId, samples in self?.hold(pointerId: pointerId, samples: samples) }
        capture.onDrop = { [weak self] in self?.live.cancel() }
        gestures.onGesture = { [weak self] message in
            guard let self else { return }
            Task { await self.shell.bridge.send(message) }
        }
        // A file opened while the page is up: the page asks for it when it
        // can take it. One opened before the page loaded is asked for at
        // its start, so this may reach no one.
        model.opened.onArrival = { [weak self] in
            guard let self else { return }
            Task { await self.shell.bridge.send(.filesOpened) }
        }
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override func loadView() {
        let container = UIView()
        container.backgroundColor = WebShell.savedBackground ?? .systemBackground
        // The web view starts below the bar's strip, or below the status bar
        // while the bar is away, as the page expects (it pads only the
        // bottom: app.css `html[data-shell]`), and runs under the home
        // indicator. The strip above it is then the window's own colour, the
        // page's topbar's (the `theme` message). The live stroke keeps
        // exactly the web view's frame: the samples and the preview are both
        // in its points.
        container.addLayoutGuide(content)
        let top = content.topAnchor.constraint(equalTo: container.safeAreaLayoutGuide.topAnchor,
                                               constant: barShown ? Self.barHeight : 0)
        contentTop = top
        NSLayoutConstraint.activate([
            top,
            content.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            content.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            content.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        for child in [shell.webView, live] as [UIView] {
            container.addSubview(child)
            fillContent(child)
        }
        // The bar from the window's top: it paints the strip under the
        // status bar itself.
        bar.translatesAutoresizingMaskIntoConstraints = false
        bar.isHidden = !barShown
        bar.alpha = barShown ? 1 : 0
        container.addSubview(bar)
        let strip = bar.bottomAnchor.constraint(equalTo: container.safeAreaLayoutGuide.topAnchor, constant: Self.barHeight)
        barStrip = strip
        barFull = bar.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        NSLayoutConstraint.activate([
            bar.topAnchor.constraint(equalTo: container.topAnchor),
            bar.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            bar.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            strip,
        ])
        capture.surface = shell.webView
        gestures.surface = shell.webView
        container.addGestureRecognizer(capture)
        container.addInteraction(gestures.interaction)
        view = container
    }

    /// The web view's frame, which the live stroke and the launcher share.
    private func fillContent(_ child: UIView) {
        child.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            child.topAnchor.constraint(equalTo: content.topAnchor),
            child.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            child.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            child.bottomAnchor.constraint(equalTo: content.bottomAnchor),
        ])
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        applyScheme()
        becomeFirstResponder() // the shortcuts work before any web view has focus
    }

    override var canBecomeFirstResponder: Bool { true }

    override var keyCommands: [UIKeyCommand]? {
        [UIKeyCommand(title: "All Servers", action: #selector(toggleLauncher), input: "l", modifierFlags: [.command, .shift]),
         UIKeyCommand(title: "Reload", action: #selector(reloadPage), input: "r", modifierFlags: .command),
         UIKeyCommand(title: model.barHidden ? "Show Server Bar" : "Hide Server Bar", action: #selector(toggleBar),
                      input: "b", modifierFlags: [.command, .shift])]
    }

    func show(_ server: URL?, login: LocalLogin?, attempt: Int) {
        shell.login = login
        if let server, server != shell.server {
            shell.open(server)
        } else if attempt != self.attempt {
            shell.retry()
        }
        self.attempt = attempt
    }

    /// The launcher over the page, each showing a fresh load of it, or none.
    func showLauncher(_ launcher: AppModel.Launcher?) {
        guard launcher != self.launcher else { return }
        self.launcher = launcher
        guard let launcher else {
            // A tap in the launcher made its content view first responder:
            // take it back, so the key commands still have a chain.
            let focused = launcherView?.scrollView.subviews.contains(where: \.isFirstResponder) == true
            launcherView?.removeFromSuperview()
            launcherView = nil
            if focused { becomeFirstResponder() }
            return
        }
        let page = launcherView ?? addLauncherView()
        page.loadFileURL(Chrome.launcherURL(error: launcher.error), allowingReadAccessTo: Chrome.folder)
    }

    private func addLauncherView() -> WKWebView {
        let page = chrome.makeView(bar: false)
        view.insertSubview(page, belowSubview: bar)
        fillContent(page)
        // Laid out at once, so a bar coming back with it (`placeBar`) moves
        // it down with the page rather than growing it from nothing.
        view.layoutIfNeeded()
        launcherView = page
        return page
    }

    /// The state to every chrome page: when the model or the local server
    /// changes (ShellView), and when the page's session or theme does
    /// (WebShell).
    func pushState() {
        let state = self.state()
        for page in [bar, launcherView].compactMap({ $0 }) { Chrome.push(state, to: page) }
    }

    private func state(sizeBytes: Int64? = nil) -> [String: Any] {
        ShellState.make(version: HostInfo.current.version, theme: shell.themeName, servers: model.saved,
                        reachable: model.reachable, local: model.local.state, slowStart: model.local.slowStart,
                        login: model.local.login, session: shell.session, page: shell.webView.url,
                        barHideable: !model.barNeeded, sizeBytes: sizeBytes)
    }

    /// The bar over the whole window while a dropdown is open, so its menu
    /// has room and a tap beside it closes it (bar.html), else its strip.
    private func expandBar(_ on: Bool) {
        guard let barStrip, let barFull else { return }
        let (old, new) = on ? (barStrip, barFull) : (barFull, barStrip)
        old.isActive = false
        new.isActive = true
    }

    /// An open dropdown closes in the bar's page too (bar.html's Escape), so
    /// its next tap opens it again, and the view goes back to its strip,
    /// also for a bar whose page has not loaded.
    private func closeBarMenu() {
        bar.evaluateJavaScript("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))", completionHandler: nil)
        expandBar(false)
    }

    /// The bar up or away (AppModel.barShown): the content's top moves with
    /// it as the bar fades, after an open dropdown closes. The person's
    /// choice goes to the page when it changes (`shellBar`), for its View
    /// menu's check; a page that loads asks for it itself.
    func placeBar(shown: Bool, hidden: Bool) {
        if hidden != barHidden {
            barHidden = hidden
            Task { await shell.bridge.send(.shellBar(shown: !hidden)) }
        }
        guard shown != barShown else { return }
        barShown = shown
        guard let contentTop else { return } // the view is not made yet: loadView places it
        if !shown { closeBarMenu() }
        contentTop.constant = shown ? Self.barHeight : 0
        guard view.window != nil, !UIAccessibility.isReduceMotionEnabled else {
            bar.alpha = shown ? 1 : 0
            bar.isHidden = !shown
            view.layoutIfNeeded()
            return
        }
        bar.isHidden = false
        UIView.animate(withDuration: Self.barFade, delay: 0, options: [.beginFromCurrentState]) {
            self.bar.alpha = shown ? 1 : 0
            self.view.layoutIfNeeded()
        } completion: { _ in
            // Hidden once faded, so the page takes every touch; the latest
            // placing decides when two cross.
            self.bar.isHidden = !self.barShown
        }
    }

    @objc private func toggleLauncher() {
        closeBarMenu()
        model.toggleLauncher()
    }

    @objc private func toggleBar() {
        model.setBarHidden(!model.barHidden)
    }

    @objc private func reloadPage() {
        shell.reload()
    }

    // --- the chrome's calls (Chrome) ----------------------------------------------------

    /// One `gammaShell` call, answered as desktop/main.js answers it, with
    /// what the iPad has: the reply's value, or a throw the page shows.
    private func call(_ method: String, _ args: [Any]) async throws -> Any? {
        func arg<T>(_ index: Int) throws -> T {
            guard index < args.count, let value = args[index] as? T else {
                throw Refusal("Gamma for iPad cannot read this call to \(method).")
            }
            return value
        }
        // A listed server, by its id.
        func server(_ index: Int) throws -> Server {
            let id: String = try arg(index)
            switch Server(id: id) {
            case .local?:
                return .local
            case let .remote(url)? where model.remotes.contains(url):
                return .remote(url)
            default:
                throw Refusal("\(id) is not a listed server.")
            }
        }
        switch method {
        case "state":
            model.probe(model.remotes)
            shell.readSession()
            return state()
        case "list":
            model.probe(model.remotes)
            let size = await model.local.dataSize()
            return state(sizeBytes: size)
        case "open":
            try model.open(server(0))
        case "openWorkspace":
            expandBar(false)
            try shell.openWorkspace(arg(0))
        case "launcher":
            toggleLauncher()
        case "reload":
            reloadPage()
        case "barExpand":
            try expandBar(arg(0))
        case "hideBar":
            model.setBarHidden(true)
        case "addRemote":
            let name: String = try arg(0)
            guard let url = try ServerAddress.parse(arg(1)) else { throw Refusal("That is not a web address.") }
            guard !model.remotes.contains(url) else { throw Refusal("\(url.absoluteString) is already listed") }
            model.add(url)
            model.rename(.remote(url), to: name)
        case "rename":
            try model.rename(server(0), to: arg(1))
        case "setUrl":
            guard case let .remote(old) = try server(0) else { throw Refusal("This iPad's address is its own.") }
            guard let new = try ServerAddress.parse(arg(1)) else { throw Refusal("That is not a web address.") }
            model.replace(old, with: new)
        case "remove":
            guard case let .remote(url) = try server(0) else { throw Refusal("This iPad cannot be removed.") }
            model.remove(url)
        case "revealData":
            reveal(model.local.dataDirectory)
        case "revealLog":
            reveal(model.local.logFile.deletingLastPathComponent())
        case "copyAddress":
            guard let address = model.local.url?.absoluteString else { return nil }
            UIPasteboard.general.string = address
            return address
        default:
            throw Refusal("Gamma for iPad has no \(method).")
        }
        return nil
    }

    /// A folder of This iPad's in the Files app, made first if it is missing.
    private func reveal(_ folder: URL) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        guard let path = folder.path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              let url = URL(string: "shareddocuments://" + path) else { return }
        UIApplication.shared.open(url)
    }

    // --- the web's messages (Bridge) ----------------------------------------------------

    private func answer(_ message: WebMessage) -> [String: Any] {
        switch message {
        case let .claim(pointerId, at, tool):
            return ["taken": capture.claim(pointerId: pointerId, at: at, tool: tool)]
        case let .theme(bg, scheme, name):
            if let color = shell.paint(bg, name: name ?? scheme.rawValue) { view.backgroundColor = color }
            self.scheme = scheme
            applyScheme()
            return [:]
        case let .haptic(kind, at):
            // A gesture's tick comes without a place: where the Pencil hovered for it.
            let place: CGPoint? = at ?? (kind == .tool ? gestures.recentHover : nil)
            PencilHaptics.play(kind, at: place, in: shell.webView)
            return [:]
        case .files:
            return model.opened.listing
        case let .fileData(id, offset):
            return model.opened.chunk(id: id, offset: offset) ?? [:]
        case let .fileDone(id):
            model.opened.done(id: id)
            return [:]
        case let .shellBar(shown):
            if let shown { model.setBarHidden(!shown) }
            return ["shown": !model.barHidden]
        }
    }

    /// The captured Pencil held still: the page answers at once, from the
    /// samples (a shape to draw, the hold taken for *Paste here*, or
    /// nothing), and the capture follows its answer.
    private func hold(pointerId: Int, samples: [[Double]]) {
        Task {
            let reply = await shell.bridge.ask(.hold(pointerId: pointerId, samples: samples))
            capture.held(HoldReply(reply: reply), pointerId: pointerId)
        }
    }

    /// The lift: the samples go to the web, with the hold whose shape the
    /// line was drawn as, and the line stays until the web has painted the
    /// stroke itself.
    private func deliver(pointerId: Int, samples: [[Double]], held: Int?) {
        let line = live.finish()
        Task {
            await shell.bridge.send(.stroke(pointerId: pointerId, samples: samples, held: held))
            if let line { live.remove(line) }
        }
        Task {
            try? await Task.sleep(nanoseconds: Self.paintTimeout)
            if let line { live.remove(line) }
        }
    }

    /// The system cancelled a claimed stroke: its line goes, and the web hears
    /// of a stroke with no samples, which ends its claim and saves nothing.
    private func abandon(pointerId: Int) {
        live.cancel()
        Task { await shell.bridge.send(.stroke(pointerId: pointerId, samples: [])) }
    }

    /// The status bar follows the page's theme, which can differ from the
    /// system's: the window takes the page's scheme, and the web view is held
    /// at the system's, so a page on the "system" theme still reads the
    /// system's preference and not the window's.
    private func applyScheme() {
        guard let window = view.window, let scene = window.windowScene else { return }
        shell.webView.overrideUserInterfaceStyle = scene.traitCollection.userInterfaceStyle
        if let scheme { window.overrideUserInterfaceStyle = scheme == .dark ? .dark : .light }
        if sceneStyle == nil {
            sceneStyle = scene.registerForTraitChanges([UITraitUserInterfaceStyle.self]) { [weak self] (_: UIWindowScene, _: UITraitCollection) in
                self?.applyScheme()
            }
        }
    }
}

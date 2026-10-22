import SwiftUI
import UIKit

/// The whole window: the web app, with the Pencil layer over it, and a
/// native page of its own only when the server cannot be reached (the web
/// app cannot say so itself before it has loaded).
struct ShellView: View {
    let server: URL
    /// The local server's seeded account when `server` is the local one (WebShell signs in with it).
    let login: LocalLogin?
    @EnvironmentObject private var model: AppModel
    @State private var failure: String?
    @State private var attempt = 0

    var body: some View {
        ZStack {
            // A pick in the server picker counts as a retry too: choosing the
            // server that just failed must load it again.
            ShellHost(server: server, login: login, attempt: attempt + model.connects,
                      onServers: { model.choosingServer = true },
                      onLoad: { failure = $0 })
                // Full-screen, so the theme's colour paints the strip under the
                // status bar; the web view itself starts below it (loadView).
                .ignoresSafeArea()
            if let failure {
                ContentUnavailableView {
                    Label("Gamma cannot reach \(server.host ?? server.absoluteString)", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(failure)
                } actions: {
                    Button("Try again") { attempt += 1 }
                        .buttonStyle(.borderedProminent)
                    Button("Choose a server") { model.choosingServer = true }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color(uiColor: .systemBackground))
            }
        }
    }
}

/// Hosts the controller, so Safari views, alerts and the share sheet have a
/// presenter. It is made once: a new server or a retry reaches the same
/// web view through `show`.
private struct ShellHost: UIViewControllerRepresentable {
    let server: URL
    let login: LocalLogin?
    let attempt: Int
    let onServers: () -> Void
    let onLoad: (String?) -> Void

    func makeUIViewController(context: Context) -> ShellController {
        let controller = ShellController()
        updateUIViewController(controller, context: context)
        return controller
    }

    func updateUIViewController(_ controller: ShellController, context: Context) {
        controller.onServers = onServers
        controller.onLoad = onLoad
        controller.show(server, login: login, attempt: attempt)
    }
}

/// The container: the web view, the live stroke over it, and the Pencil
/// capture and gestures on the container itself, so they see every touch
/// the web view gets. It answers the web's messages; none of it knows what
/// the page shows.
final class ShellController: UIViewController {
    var onServers: (() -> Void)?
    var onLoad: ((String?) -> Void)?

    private let shell: WebShell
    private let live: LiveStrokeView
    private let capture: PencilCapture
    private let gestures: PencilGestures
    private var attempt = 0
    private var scheme: WebMessage.Scheme?
    private var sceneStyle: (any UITraitChangeRegistration)?

    /// How long a finished stroke's line waits for the web's paint before it
    /// goes anyway (a page that never answers leaves nothing behind).
    private static let paintTimeout: UInt64 = 3_000_000_000

    init() {
        shell = WebShell()
        live = LiveStrokeView(frame: .zero)
        capture = PencilCapture(target: nil, action: nil)
        gestures = PencilGestures()
        super.init(nibName: nil, bundle: nil)
        // Wired before the first load starts (`show` can come before the view).
        shell.navigation.presenter = self
        shell.navigation.onLoad = { [weak self] failure in self?.onLoad?(failure) }
        shell.bridge.onMessage = { [weak self] message in self?.answer(message) ?? [:] }
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
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override func loadView() {
        let container = UIView()
        container.backgroundColor = WebShell.savedBackground ?? .systemBackground
        // The web view starts below the status bar, as the page expects (it
        // pads only the bottom: app.css `html[data-shell]`), and runs under
        // the home indicator. The live stroke keeps exactly the web view's
        // frame: the samples and the preview are both in its points.
        for child in [shell.webView, live] as [UIView] {
            child.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(child)
            NSLayoutConstraint.activate([
                child.topAnchor.constraint(equalTo: container.safeAreaLayoutGuide.topAnchor),
                child.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                child.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                child.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            ])
        }
        capture.surface = shell.webView
        gestures.surface = shell.webView
        container.addGestureRecognizer(capture)
        container.addInteraction(gestures.interaction)
        view = container
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        applyScheme()
    }

    func show(_ server: URL, login: LocalLogin?, attempt: Int) {
        shell.login = login
        if server != shell.server {
            shell.open(server)
        } else if attempt != self.attempt {
            shell.retry()
        }
        self.attempt = attempt
    }

    // --- the web's messages (Bridge) ----------------------------------------------------

    private func answer(_ message: WebMessage) -> [String: Any] {
        switch message {
        case let .claim(pointerId, at, tool):
            return ["taken": capture.claim(pointerId: pointerId, at: at, tool: tool)]
        case let .theme(bg, scheme):
            if let color = shell.paint(bg) { view.backgroundColor = color }
            self.scheme = scheme
            applyScheme()
            return [:]
        case .servers:
            onServers?()
            return [:]
        case let .haptic(kind, at):
            // A gesture's tick comes without a place: where the Pencil hovered for it.
            PencilHaptics.play(kind, at: at ?? (kind == .tool ? gestures.recentHover : nil), in: shell.webView)
            return [:]
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

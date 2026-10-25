import SwiftUI

/// A Gamma server the shell can show: the one inside the app ("This iPad",
/// LocalServer) or another one at its address.
enum Server: Hashable, Codable {
    case local
    case remote(URL)

    /// The id the chrome pages name it by, as the desktop's registry has
    /// ids: "local", or a remote's origin (ServerAddress.parse).
    var id: String {
        switch self {
        case .local: return "local"
        case let .remote(url): return url.absoluteString
        }
    }

    init?(id: String) {
        if id == "local" {
            self = .local
        } else if let url = URL(string: id) {
            self = .remote(url)
        } else {
            return nil
        }
    }
}

/// The app's one piece of state: which server the shell shows, the servers
/// the chrome lists (Chrome: the desktop's bar and launcher), as the
/// desktop's registry has them (desktop/lib/registry.js), whether the
/// launcher is up, and whether the bar is. The session is the web view's cookie and the library is
/// the server's: the shell never holds a token (docs/dev/ipad.md).
@MainActor
final class AppModel: ObservableObject {
    /// One showing of the launcher: each is a fresh page load, so a failure
    /// repeated with the same text still loads it again. `error` is the
    /// failure it shows (ShellFailure), as the desktop's `?error=`.
    struct Launcher: Equatable {
        var error: String?
        let id = UUID()
    }

    /// The server chosen last; nil until the first one is opened.
    @Published private(set) var current: Server?
    /// The other servers, in the order they were added.
    @Published private(set) var remotes: [URL]
    /// The names given to servers, by id; a server without one is named by
    /// SavedServers.name.
    @Published private(set) var names: [String: String]
    /// The address the shell shows. It changes only to another address, so
    /// the shell's web view, made once, stays while the local server starts.
    @Published private(set) var shellURL: URL? = nil
    /// How many times a server was opened, counted so the shell reloads even
    /// when it is the server already shown (a retry after an outage).
    @Published private(set) var connects = 0
    /// The remotes' last /api/health answers (the chrome's dots).
    @Published private(set) var reachable: [URL: Bool] = [:]
    /// The shell's last main-frame load failed with this message; cleared
    /// when a page arrives.
    @Published private(set) var loadError: String? = nil
    /// The launcher over the page, nil when it is closed.
    @Published private(set) var launcher: Launcher? = nil
    /// The bar hidden by the person (its button, ⌘⇧B, the page's View
    /// menu), kept between launches. It still shows while it is needed
    /// (`barShown`).
    @Published private(set) var barHidden: Bool

    let local: LocalServer
    /// The files the system opened in the app, held for the page (the shell
    /// answers its `files` messages from here, whichever server it shows).
    let opened: OpenedFiles
    private var probed: [URL: Date] = [:]
    private let defaults: UserDefaults

    /// A dot stays as it is this long before the chrome asks again, as the
    /// desktop caches its remotes' health.
    private static let probeInterval: TimeInterval = 30
    /// Where `barHidden` is kept, beside the servers (SavedServers.key).
    static let barHiddenKey = "gamma.barHidden"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        local = LocalServer(defaults: defaults)
        opened = OpenedFiles()
        let saved = SavedServers.load(from: defaults)
        current = saved.current
        remotes = saved.remotes
        names = saved.names ?? [:]
        barHidden = defaults.bool(forKey: Self.barHiddenKey)
        if case let .remote(url) = saved.current { shellURL = url }
        // The first launch: the launcher, over an empty shell.
        if saved.current == nil { launcher = Launcher() }
        self.local.onState = { [weak self] state in self?.localChanged(state) }
        if saved.current == .local { self.local.start() }
    }

    /// What is kept between launches, as it stands.
    var saved: SavedServers {
        SavedServers(current: current, remotes: remotes, names: names.isEmpty ? nil : names)
    }

    /// The server already shown, loaded and up, only closes the launcher.
    /// Any other is shown, the same one again too (a retry). This iPad not
    /// running is started, or started again after a failure, and the
    /// launcher stays up until it runs (localChanged).
    func open(_ server: Server) {
        let running = server != .local || local.url != nil
        if server == current, loadError == nil, running {
            launcher = nil
            return
        }
        current = server
        save()
        switch server {
        case let .remote(url):
            show(url)
        case .local:
            if let url = local.url {
                show(url)
            } else {
                local.restart()
            }
        }
    }

    /// The bar shows unless the person hid it, and while it is needed.
    var barShown: Bool { !barHidden || barNeeded }

    /// The bar is needed while the launcher is up, which its mark closes,
    /// and while This iPad starts, which its status says: the page under it
    /// is empty meanwhile. Its hide button is offered only when it is not.
    var barNeeded: Bool { launcher != nil || (current == .local && local.state == .starting) }

    /// The bar's hide button, ⌘⇧B and the page's View menu.
    func setBarHidden(_ hidden: Bool) {
        guard hidden != barHidden else { return }
        barHidden = hidden
        defaults.set(hidden, forKey: Self.barHiddenKey)
    }

    /// The mark, *All servers…* and ⌘⇧L: a launcher that is up without an
    /// error closes; otherwise a fresh one opens.
    func toggleLauncher() {
        launcher = (launcher != nil && launcher?.error == nil) ? nil : Launcher()
    }

    /// The shell's main-frame load failed: the launcher shows why, with Try
    /// again (`open`), as the desktop sends a failed open to its launcher.
    func loadFailed(_ message: String) {
        loadError = message
        guard let current, let url = shellURL else { return }
        launcher = Launcher(error: ShellFailure.unreachable(url, server: current.id, detail: message).json)
    }

    /// A page arrived: a load error is over, and the launcher showing it closes.
    func pageLoaded() {
        guard loadError != nil else { return }
        loadError = nil
        if launcher?.error != nil { launcher = nil }
    }

    /// The seeded account for `url` when it is the local server's, so the
    /// shell signs in there and nowhere else.
    func login(for url: URL) -> LocalLogin? {
        url == local.url ? local.login : nil
    }

    /// Adds a server to the end of the list without opening it.
    func add(_ url: URL) {
        guard !remotes.contains(url) else { return }
        remotes.append(url)
        save()
        probe([url], force: true)
    }

    /// A server's address edited. The one shown loads its new address, and
    /// its name goes with it.
    func replace(_ old: URL, with new: URL) {
        guard old != new, let index = remotes.firstIndex(of: old) else { return }
        if remotes.contains(new) {
            remotes.remove(at: index)
        } else {
            remotes[index] = new
        }
        if let name = names.removeValue(forKey: Server.remote(old).id) { names[Server.remote(new).id] = name }
        if current == .remote(old) {
            current = .remote(new)
            shellURL = new
        }
        save()
        probe([new], force: true)
    }

    /// Takes a server off the list. The current one is unchosen, as on the
    /// desktop: its page stays under the launcher until another is opened,
    /// and the next launch shows the launcher.
    func remove(_ url: URL) {
        remotes.removeAll { $0 == url }
        names[Server.remote(url).id] = nil
        reachable[url] = nil
        probed[url] = nil
        if current == .remote(url) { current = nil }
        save()
    }

    /// Names a server; a blank name gives it back its own (SavedServers.name).
    func rename(_ server: Server, to name: String) {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        names[server.id] = name.isEmpty ? nil : name
        save()
    }

    /// Asks each server's /api/health, unless it answered within the last
    /// `probeInterval`.
    func probe(_ urls: [URL], force: Bool = false) {
        let now = Date()
        for url in urls {
            if !force, let last = probed[url], now.timeIntervalSince(last) < Self.probeInterval { continue }
            probed[url] = now
            Task { reachable[url] = await Health.probe(url, timeout: 3) }
        }
    }

    /// This iPad, while it is the server chosen: shown once it runs, which
    /// closes the launcher; a failed start goes to the launcher with the
    /// end of its log.
    private func localChanged(_ state: LocalServer.State) {
        guard current == .local else { return }
        switch state {
        case let .running(url):
            shellURL = url
            launcher = nil
        case let .failed(message):
            launcher = Launcher(error: ShellFailure.localFailed(message, log: local.logTail()).json)
        case .starting, .stopped:
            break
        }
    }

    private func show(_ url: URL) {
        launcher = nil
        shellURL = url
        connects += 1
    }

    private func save() {
        saved.save(to: defaults)
    }
}

/// What is kept between launches, as JSON under one key. `names` is
/// optional, so a build from before names reads this and this reads its.
struct SavedServers: Codable, Equatable {
    var current: Server?
    var remotes: [URL]
    var names: [String: String]?

    static let key = "gamma.servers"

    static func load(from defaults: UserDefaults) -> SavedServers {
        defaults.data(forKey: key).flatMap { try? JSONDecoder().decode(SavedServers.self, from: $0) }
            ?? SavedServers(current: nil, remotes: [])
    }

    func save(to defaults: UserDefaults) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.key)
    }

    /// A server's name: the one given it, else "This iPad", or a remote's
    /// host with its port when it has one.
    func name(of server: Server) -> String {
        if let name = names?[server.id] { return name }
        switch server {
        case .local:
            return "This iPad"
        case let .remote(url):
            let host = url.host ?? url.absoluteString
            return url.port.map { "\(host):\($0)" } ?? host
        }
    }
}

/// What the address field accepts. Pure, so the tests cover it.
enum ServerAddress {
    /// The server's origin from what a person types or pastes: a bare host
    /// means https, and a page link keeps only its origin. The web app is
    /// served at the root, and the Gamma Cloud sign-in works by origin
    /// (CloudSignIn), so a path would only get in the way.
    static func parse(_ text: String) -> URL? {
        var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "https://" + text }
        guard let parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(), scheme == "https" || scheme == "http",
              let host = parts.host, !host.isEmpty else { return nil }
        var origin = URLComponents()
        origin.scheme = scheme
        origin.host = host.lowercased()
        origin.port = parts.port
        return origin.url
    }
}

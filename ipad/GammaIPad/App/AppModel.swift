import SwiftUI

/// A Gamma server the shell can show: the one inside the app ("This iPad",
/// LocalServer) or another one at its address.
enum Server: Hashable, Codable {
    case local
    case remote(URL)
}

/// The app's one piece of state: which server the shell shows and the
/// servers in the picker (ServersView), as the desktop's registry has them
/// (desktop/lib/registry.js). The session is the web view's cookie and the
/// library is the server's: the shell never holds a token (docs/dev/ipad.md).
@MainActor
final class AppModel: ObservableObject {
    /// The server chosen last; nil until the first pick.
    @Published private(set) var current: Server?
    /// The other servers, newest first.
    @Published private(set) var remotes: [URL]
    /// The address the shell shows. It changes only to another address, so
    /// the shell's web view, made once, stays while the local server starts.
    @Published private(set) var shellURL: URL? = nil
    /// The server picker is up (the web app's "Server…" row asked for it).
    @Published var choosingServer = false
    /// How many times a server was picked, counted so the shell reloads even
    /// when the pick is the server already shown (a retry after an outage).
    @Published private(set) var connects = 0
    /// The remotes' last /api/health answers (the picker's dots).
    @Published private(set) var reachable: [URL: Bool] = [:]

    let local: LocalServer
    /// The files the system opened in the app, held for the page (the shell
    /// answers its `files` messages from here, whichever server it shows).
    let opened: OpenedFiles
    private var probed: [URL: Date] = [:]
    private let defaults: UserDefaults

    /// A dot stays as it is this long before the picker asks again, as the
    /// desktop caches its remotes' health.
    private static let probeInterval: TimeInterval = 30

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        local = LocalServer(defaults: defaults)
        opened = OpenedFiles()
        let saved = SavedServers.load(from: defaults)
        current = saved.current
        remotes = saved.remotes
        if case let .remote(url) = saved.current { shellURL = url }
        self.local.onState = { [weak self] state in self?.localChanged(state) }
        if saved.current == .local { self.local.start() }
    }

    func open(_ server: Server) {
        current = server
        choosingServer = false
        switch server {
        case let .remote(url):
            if !remotes.contains(url) { remotes = ServerAddress.remember(url, in: remotes) }
            shellURL = url
            connects += 1
        case .local:
            // A server already up opens now; else it is shown once it answers
            // (localChanged), and the page under the start view stays as it is.
            if let url = local.url {
                shellURL = url
                connects += 1
            } else {
                local.start()
            }
        }
        save()
    }

    /// The seeded account for `url` when it is the local server's, so the
    /// shell signs in there and nowhere else.
    func login(for url: URL) -> LocalLogin? {
        url == local.url ? local.login : nil
    }

    /// Adds a server to the list without opening it.
    func add(_ url: URL) {
        guard !remotes.contains(url) else { return }
        remotes = ServerAddress.remember(url, in: remotes)
        save()
        probe([url], force: true)
    }

    /// A server's address edited. The one shown loads its new address.
    func replace(_ old: URL, with new: URL) {
        guard old != new, let index = remotes.firstIndex(of: old) else { return }
        if remotes.contains(new) {
            remotes.remove(at: index)
        } else {
            remotes[index] = new
        }
        if current == .remote(old) {
            current = .remote(new)
            shellURL = new
        }
        save()
        probe([new], force: true)
    }

    /// Takes a server off the list. The page shown stays until another pick.
    func remove(_ url: URL) {
        remotes.removeAll { $0 == url }
        reachable[url] = nil
        probed[url] = nil
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

    private func localChanged(_ state: LocalServer.State) {
        if case let .running(url) = state, current == .local { shellURL = url }
    }

    private func save() {
        SavedServers(current: current, remotes: remotes).save(to: defaults)
    }
}

/// What is kept between launches, as JSON under one key. Phase 1 kept one
/// address and a recent list; those become the current server and the
/// remotes the first time this reads them.
struct SavedServers: Codable, Equatable {
    var current: Server?
    var remotes: [URL]

    static let key = "gamma.servers"
    private static let legacyServerKey = "gamma.server"
    private static let legacyRecentKey = "gamma.recentServers"

    static func load(from defaults: UserDefaults) -> SavedServers {
        if let data = defaults.data(forKey: key),
           let saved = try? JSONDecoder().decode(SavedServers.self, from: data) {
            return saved
        }
        let server = defaults.string(forKey: legacyServerKey).flatMap(ServerAddress.parse)
        let recent = (defaults.stringArray(forKey: legacyRecentKey) ?? []).compactMap(ServerAddress.parse)
        return SavedServers(current: server.map(Server.remote), remotes: recent)
    }

    func save(to defaults: UserDefaults) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.key)
    }
}

/// What the address field accepts. Pure, so the tests cover it.
enum ServerAddress {
    /// The list is kept by hand (add, edit, remove); the cap only keeps a
    /// forgotten one from growing without end.
    static let recentLimit = 20

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

    /// `url` first, without duplicates, at most `recentLimit` long.
    static func remember(_ url: URL, in list: [URL]) -> [URL] {
        Array(([url] + list.filter { $0 != url }).prefix(recentLimit))
    }
}

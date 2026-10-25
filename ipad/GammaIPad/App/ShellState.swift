import Foundation

/// What the chrome pages (desktop/ui/bar.html and launcher.html, Chrome)
/// read of the shell, built as the desktop builds its barState and
/// fullState (desktop/main.js), with only what the iPad has: no `keeping`,
/// `update` or `settings`, so the pages hide the sync button, the update
/// pill and the Settings block. One key is the iPad's own, `barHideable`,
/// which shows the bar's hide button. Pure, over plain values, so the tests
/// cover it; every value in it is one JSON can carry, as WebKit hands it to
/// the page.
enum ShellState {
    /// This iPad's data folder as the Files app shows Documents/Gamma/local.
    static let dataDir = "On My iPad › Gamma › Gamma › local"
    /// What the credentials show when this iPad keeps no password for the
    /// account (LocalServer.start: a data directory from elsewhere).
    static let noPassword = "This iPad keeps no password for this account. A password the server made itself is in its log."

    /// `session` is what the page's server answered `/api/session` (WebShell),
    /// read only while `page`, the shell's address, is on the chosen server;
    /// `sizeBytes` is This iPad's data folder, which only the launcher asks for;
    /// `barHideable` is whether a tap on the bar's hide button would hide it
    /// now (AppModel.barNeeded).
    static func make(version: String, theme: String, servers: SavedServers, reachable: [URL: Bool],
                     local: LocalServer.State, slowStart: Bool, login: LocalLogin?,
                     session: [String: Any]?, page: URL?, barHideable: Bool, sizeBytes: Int64? = nil) -> [String: Any] {
        var localURL: URL? = nil
        if case let .running(url) = local { localURL = url }
        var state: [String: Any] = ["version": version, "theme": theme, "current": NSNull(), "busy": NSNull(),
                                    "gamma": NSNull(), "barHideable": barHideable]

        var shown: URL? = nil
        if let current = servers.current {
            state["current"] = ["id": current.id, "name": servers.name(of: current),
                                "type": current == .local ? "local" : "remote"]
            state["lastOpened"] = current.id
            switch current {
            case .local: shown = localURL
            case let .remote(url): shown = url
            }
        }
        if servers.current == .local, local == .starting {
            let name = servers.name(of: .local)
            state["busy"] = slowStart
                ? "Still starting \(name)… the first start after an install or an update can take a few minutes"
                : "Starting \(name)…"
        }
        // The workspaces of the server the page is on, as it sent them, and
        // the one the page shows (its ?ws=), else the account's default.
        if let session, let user = session["user"], let page, let shown, let origin = Origin(shown),
           Origin(page) == origin {
            var gamma: [String: Any] = ["user": user, "list": session["workspaces"] ?? [Any]()]
            let ws = URLComponents(url: page, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "ws" })?.value
            if let ws {
                gamma["current"] = ws
            } else {
                gamma["current"] = session["default_workspace"]
            }
            state["gamma"] = gamma
        }

        var thisIPad: [String: Any] = ["id": Server.local.id, "name": servers.name(of: .local), "type": "local",
                                       "running": localURL != nil, "dataDir": dataDir,
                                       "adminUser": login?.username ?? LocalLogin.account,
                                       "adminPassword": login?.password ?? noPassword]
        thisIPad["url"] = localURL?.absoluteString
        thisIPad["sizeBytes"] = sizeBytes.map { NSNumber(value: $0) }
        let others = servers.remotes.map { url -> [String: Any] in
            var entry: [String: Any] = ["id": Server.remote(url).id, "name": servers.name(of: .remote(url)),
                                        "type": "remote", "url": url.absoluteString]
            entry["reachable"] = reachable[url]
            return entry
        }
        state["servers"] = [thisIPad] + others
        return state
    }
}

/// A server that would not open, as the launcher shows it (launcher.html
/// showFailure, loaded with `?error=`), in the shape the desktop sends.
struct ShellFailure: Encodable {
    let summary: String
    let detail: String
    var log: String?
    let action = "retry"
    let server: String

    /// The shell's page could not load from the server at `url`.
    static func unreachable(_ url: URL, server: String, detail: String) -> ShellFailure {
        ShellFailure(summary: "Gamma cannot reach \(url.host ?? url.absoluteString)", detail: detail, server: server)
    }

    /// This iPad's server did not start, with the end of its log.
    static func localFailed(_ detail: String, log: String) -> ShellFailure {
        ShellFailure(summary: "Gamma could not start on this iPad", detail: detail, log: log.isEmpty ? nil : log,
                     server: Server.local.id)
    }

    var json: String {
        (try? JSONEncoder().encode(self)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
    }
}

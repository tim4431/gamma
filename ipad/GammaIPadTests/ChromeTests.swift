import XCTest
@testable import Gamma

/// The desktop's chrome on the iPad without a web view: who may call it
/// (Chrome), the launcher's failure, the state the pages read (ShellState),
/// and the rules of opening a server and of the launcher (AppModel).
final class ChromeTests: XCTestCase {
    @MainActor
    func testOnlyTheBundledPagesCall() throws {
        let page = Chrome.folder.appendingPathComponent("bar.html")
        XCTAssertTrue(Chrome.accepts(page, mainFrame: true))
        XCTAssertFalse(Chrome.accepts(page, mainFrame: false), "a frame inside the page")
        XCTAssertFalse(Chrome.accepts(URL(string: "https://gamma.example.org/"), mainFrame: true), "a server's page")
        XCTAssertFalse(Chrome.accepts(nil, mainFrame: true))
    }

    @MainActor
    func testTheLauncherCarriesTheFailureAsTheDesktopSendsIt() throws {
        XCTAssertEqual(Chrome.launcherURL(error: nil), Chrome.folder.appendingPathComponent("launcher.html"))
        let json = ShellFailure.localFailed("port 50123 is in use", log: "C++ & friends = \"trouble\"\nline 2 · ü").json
        let url = Chrome.launcherURL(error: json)
        XCTAssertTrue(url.isFileURL)
        XCTAssertEqual(url.lastPathComponent, "launcher.html")
        let parts = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertFalse(try XCTUnwrap(parts.percentEncodedQuery).contains("+"), "a + would read as a space")
        let error = try XCTUnwrap(parts.queryItems?.first(where: { $0.name == "error" })?.value)
        XCTAssertEqual(error, json)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(error.utf8)) as? [String: Any])
        XCTAssertEqual(payload["summary"] as? String, "Gamma could not start on this iPad")
        XCTAssertEqual(payload["detail"] as? String, "port 50123 is in use")
        XCTAssertEqual(payload["log"] as? String, "C++ & friends = \"trouble\"\nline 2 · ü")
        XCTAssertEqual(payload["action"] as? String, "retry")
        XCTAssertEqual(payload["server"] as? String, "local")

        let away = try XCTUnwrap(URL(string: "http://nas.local:9001"))
        let unreachable = try XCTUnwrap(JSONSerialization.jsonObject(
            with: Data(ShellFailure.unreachable(away, server: away.absoluteString, detail: "offline").json.utf8)) as? [String: Any])
        XCTAssertEqual(unreachable["summary"] as? String, "Gamma cannot reach nas.local")
        XCTAssertEqual(unreachable["server"] as? String, "http://nas.local:9001")
        XCTAssertNil(unreachable["log"], "no log for a server elsewhere")
    }
}

final class ShellStateTests: XCTestCase {
    private let a = URL(string: "https://a.example")!
    private let nas = URL(string: "http://nas.local:9001")!
    private let address = URL(string: "http://127.0.0.1:50123")!
    private let session: [String: Any] = [
        "user": ["username": "admin"],
        "default_workspace": "w1",
        "workspaces": [["id": "w1", "name": "Personal"], ["id": "w2", "name": "Lab"]],
    ]

    private func state(current: Server? = .local, remotes: [URL] = [], names: [String: String]? = nil,
                       reachable: [URL: Bool] = [:], local: LocalServer.State = .stopped, slowStart: Bool = false,
                       login: LocalLogin? = nil, session: [String: Any]? = nil, page: URL? = nil,
                       sizeBytes: Int64? = nil) -> [String: Any] {
        ShellState.make(version: "0.2.0", theme: "gamma-dark",
                        servers: SavedServers(current: current, remotes: remotes, names: names), reachable: reachable,
                        local: local, slowStart: slowStart, login: login, session: session, page: page,
                        sizeBytes: sizeBytes)
    }

    private func servers(_ state: [String: Any]) throws -> [[String: Any]] {
        try XCTUnwrap(state["servers"] as? [[String: Any]])
    }

    func testThisIPadComesFirstThenTheRemotesByTheirIds() throws {
        let built = state(current: .remote(nas), remotes: [a, nas], names: [nas.absoluteString: "NAS"],
                          reachable: [a: false])
        let list = try servers(built)
        XCTAssertEqual(list.map { $0["id"] as? String }, ["local", "https://a.example", "http://nas.local:9001"])
        XCTAssertEqual(list.map { $0["name"] as? String }, ["This iPad", "a.example", "NAS"])
        XCTAssertEqual(list.map { $0["type"] as? String }, ["local", "remote", "remote"])
        XCTAssertEqual(list[1]["url"] as? String, "https://a.example")
        XCTAssertEqual(list[1]["reachable"] as? Bool, false)
        XCTAssertNil(list[2]["reachable"], "not asked yet")
        let current = try XCTUnwrap(built["current"] as? [String: Any])
        XCTAssertEqual(current["id"] as? String, "http://nas.local:9001")
        XCTAssertEqual(current["name"] as? String, "NAS")
        XCTAssertEqual(current["type"] as? String, "remote")
        XCTAssertEqual(built["lastOpened"] as? String, "http://nas.local:9001")
        XCTAssertEqual(built["version"] as? String, "0.2.0")
        XCTAssertEqual(built["theme"] as? String, "gamma-dark")
    }

    func testAServerWithoutANameIsNamedByWhatItIs() {
        let saved = SavedServers(current: nil, remotes: [a, nas], names: ["local": "Studio"])
        XCTAssertEqual(saved.name(of: .local), "Studio")
        XCTAssertEqual(saved.name(of: .remote(a)), "a.example")
        XCTAssertEqual(saved.name(of: .remote(nas)), "nas.local:9001", "with its port")
        XCTAssertEqual(SavedServers(current: nil, remotes: []).name(of: .local), "This iPad")
        XCTAssertTrue(state(current: nil)["current"] is NSNull, "nothing chosen yet")
        XCTAssertNil(state(current: nil)["lastOpened"])
    }

    func testThisIPadsCardHasItsAccountAndFolder() throws {
        let stopped = try servers(state())[0]
        XCTAssertEqual(stopped["running"] as? Bool, false)
        XCTAssertNil(stopped["url"], "no address until it runs")
        XCTAssertNil(stopped["sizeBytes"], "only the launcher asks for the size")
        XCTAssertEqual(stopped["dataDir"] as? String, "On My iPad › Gamma › Gamma › local")
        XCTAssertEqual(stopped["adminUser"] as? String, "admin")
        XCTAssertEqual(stopped["adminPassword"] as? String, ShellState.noPassword, "no password kept for it")

        let running = try servers(state(local: .running(address), login: LocalLogin(username: "admin", password: "abcdefghijkl"),
                                        sizeBytes: 123_456))[0]
        XCTAssertEqual(running["running"] as? Bool, true)
        XCTAssertEqual(running["url"] as? String, "http://127.0.0.1:50123")
        XCTAssertEqual(running["sizeBytes"] as? Int, 123_456)
        XCTAssertEqual(running["adminPassword"] as? String, "abcdefghijkl")
    }

    func testTheBarSaysThisIPadIsStarting() {
        XCTAssertEqual(state(local: .starting)["busy"] as? String, "Starting This iPad…")
        XCTAssertEqual(state(local: .starting, slowStart: true)["busy"] as? String,
                       "Still starting This iPad… the first start after an install or an update can take a few minutes")
        XCTAssertEqual(state(names: ["local": "Studio"], local: .starting)["busy"] as? String, "Starting Studio…")
        XCTAssertTrue(state(local: .running(address))["busy"] is NSNull)
        XCTAssertTrue(state(current: .remote(a), remotes: [a], local: .starting)["busy"] is NSNull,
                      "This iPad starting while another server is chosen")
    }

    func testTheWorkspacesComeFromTheSessionOfThePagesServer() throws {
        let page = address.appendingPathComponent("p/abc")
        let gamma = try XCTUnwrap(state(local: .running(address), session: session, page: page)["gamma"] as? [String: Any])
        XCTAssertEqual(gamma["current"] as? String, "w1", "the account's default")
        XCTAssertEqual((gamma["list"] as? [[String: Any]])?.compactMap { $0["id"] as? String }, ["w1", "w2"])
        XCTAssertEqual((gamma["user"] as? [String: Any])?["username"] as? String, "admin")

        let onLab = try XCTUnwrap(URL(string: "http://127.0.0.1:50123/?ws=w2"))
        let shown = try XCTUnwrap(state(local: .running(address), session: session, page: onLab)["gamma"] as? [String: Any])
        XCTAssertEqual(shown["current"] as? String, "w2", "the page's ?ws=")

        XCTAssertTrue(state(local: .running(address), page: page)["gamma"] is NSNull, "no one signed in")
        XCTAssertTrue(state(local: .starting, session: session, page: page)["gamma"] is NSNull,
                      "the page is not on the chosen server")
        XCTAssertTrue(state(current: .remote(a), remotes: [a], local: .running(address), session: session, page: page)["gamma"] is NSNull,
                      "nor on another server's")
    }

    func testOnlyWhatTheIPadHasAndAllOfItJSON() throws {
        let keys = Set(state().keys)
        XCTAssertEqual(keys, ["version", "theme", "current", "busy", "gamma", "servers", "lastOpened"],
                       "no keeping, update or settings: the pages hide what reads them")
        let full = state(current: .local, remotes: [a, nas], names: ["local": "Studio"], reachable: [a: true],
                         local: .running(address), login: LocalLogin(username: "admin", password: "abcdefghijkl"),
                         session: session, page: address, sizeBytes: 9_876_543_210)
        XCTAssertTrue(JSONSerialization.isValidJSONObject(full))
        XCTAssertNoThrow(try JSONSerialization.data(withJSONObject: full))
    }
}

/// Opening, retrying and the launcher (AppModel), on servers that are never
/// asked for anything but their health: This iPad is never opened here.
final class LauncherTests: XCTestCase {
    private let a = URL(string: "https://a.example")!
    private let b = URL(string: "https://b.example")!

    @MainActor
    private func makeModel(_ saved: SavedServers) throws -> (AppModel, UserDefaults) {
        let suite = "gamma.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        addTeardownBlock { defaults.removePersistentDomain(forName: suite) }
        saved.save(to: defaults)
        return (AppModel(defaults: defaults), defaults)
    }

    @MainActor
    func testTheFirstLaunchShowsTheLauncher() throws {
        let (first, _) = try makeModel(SavedServers(current: nil, remotes: []))
        XCTAssertNotNil(first.launcher)
        XCTAssertNil(first.launcher?.error)
        XCTAssertNil(first.shellURL)
        let (again, _) = try makeModel(SavedServers(current: .remote(a), remotes: [a]))
        XCTAssertNil(again.launcher, "a relaunch reopens the last server")
        XCTAssertEqual(again.shellURL, a)
    }

    @MainActor
    func testTheLauncherToggles() throws {
        let (model, _) = try makeModel(SavedServers(current: .remote(a), remotes: [a]))
        model.toggleLauncher()
        XCTAssertNotNil(model.launcher)
        model.toggleLauncher()
        XCTAssertNil(model.launcher)
        model.loadFailed("The Internet connection appears to be offline.")
        let failed = try XCTUnwrap(model.launcher)
        XCTAssertNotNil(failed.error)
        model.toggleLauncher()
        XCTAssertNotNil(model.launcher, "one showing an error is not closed but opened afresh")
        XCTAssertNil(model.launcher?.error)
        XCTAssertNotEqual(AppModel.Launcher(error: "x"), AppModel.Launcher(error: "x"), "each showing is a load of its own")
    }

    @MainActor
    func testOpeningTheServerShownOnlyClosesTheLauncher() throws {
        let (model, defaults) = try makeModel(SavedServers(current: .remote(a), remotes: [a, b]))
        model.toggleLauncher()
        model.open(.remote(a))
        XCTAssertNil(model.launcher)
        XCTAssertEqual(model.connects, 0, "no reload")
        model.open(.remote(b))
        XCTAssertEqual(model.current, .remote(b))
        XCTAssertEqual(model.shellURL, b)
        XCTAssertEqual(model.connects, 1)
        XCTAssertEqual(SavedServers.load(from: defaults).current, .remote(b))
    }

    @MainActor
    func testALoadErrorGoesToTheLauncherAndOpeningRetries() throws {
        let (model, _) = try makeModel(SavedServers(current: .remote(a), remotes: [a]))
        model.loadFailed("The Internet connection appears to be offline.")
        XCTAssertEqual(model.loadError, "The Internet connection appears to be offline.")
        let error = try XCTUnwrap(model.launcher?.error)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(error.utf8)) as? [String: Any])
        XCTAssertEqual(payload["summary"] as? String, "Gamma cannot reach a.example")
        XCTAssertEqual(payload["server"] as? String, "https://a.example")
        model.open(.remote(a))
        XCTAssertNil(model.launcher)
        XCTAssertEqual(model.connects, 1, "the same server, loaded again")
        model.loadFailed("Still offline.")
        model.pageLoaded()
        XCTAssertNil(model.loadError)
        XCTAssertNil(model.launcher, "a page arriving closes the launcher showing its error")
        model.toggleLauncher()
        model.pageLoaded()
        XCTAssertNotNil(model.launcher, "and leaves one opened by hand")
    }

    @MainActor
    func testServersAreAddedAtTheEndAndKeepTheirNames() throws {
        let (model, defaults) = try makeModel(SavedServers(current: .remote(a), remotes: [a]))
        model.add(b)
        XCTAssertEqual(model.remotes, [a, b])
        model.rename(.remote(b), to: "  Lab  ")
        XCTAssertEqual(model.names, ["https://b.example": "Lab"])
        let c = try XCTUnwrap(URL(string: "http://nas.local:9001"))
        model.replace(b, with: c)
        XCTAssertEqual(model.remotes, [a, c])
        XCTAssertEqual(model.names, ["http://nas.local:9001": "Lab"], "the name goes with the new address")
        model.rename(.local, to: "Studio")
        XCTAssertEqual(SavedServers.load(from: defaults).name(of: .local), "Studio")
        model.rename(.local, to: " ")
        XCTAssertEqual(model.saved.name(of: .local), "This iPad", "a blank name gives back its own")
        model.remove(c)
        XCTAssertEqual(model.remotes, [a])
        XCTAssertTrue(model.names.isEmpty)
        XCTAssertNil(SavedServers.load(from: defaults).names)
        model.open(.remote(b))
        XCTAssertEqual(model.remotes, [a], "opening never lists a server")
    }

    @MainActor
    func testRemovingTheCurrentServerUnchoosesIt() throws {
        let (model, defaults) = try makeModel(SavedServers(current: .remote(a), remotes: [a, b]))
        model.remove(b)
        XCTAssertEqual(model.current, .remote(a))
        model.remove(a)
        XCTAssertNil(SavedServers.load(from: defaults).current, "the next launch shows the launcher")
    }
}

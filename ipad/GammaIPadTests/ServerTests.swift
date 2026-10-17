import XCTest
@testable import Gamma

/// The local server's rules that need no interpreter: the port, the
/// environment, the health answer, the account, the bundle layout, and the
/// servers list kept between launches. LocalServerStartTests, at the end,
/// runs the interpreter itself.
final class LocalPortsTests: XCTestCase {
    func testTheRememberedPortWhileItIsFree() {
        XCTAssertEqual(LocalPorts.pick(remembered: 50123, from: 0, isFree: { _ in true }), 50123)
    }

    func testATakenOrMissingPortGivesOneFromTheDynamicRange() {
        let picked = LocalPorts.pick(remembered: 50123, from: 7, isFree: { $0 != 50123 })
        XCTAssertEqual(picked, LocalPorts.first + 7, "the search starts at the random offset")
        XCTAssertEqual(LocalPorts.pick(remembered: 0, from: 0, isFree: { _ in true }), LocalPorts.first)
        XCTAssertEqual(LocalPorts.pick(remembered: 70000, from: 0, isFree: { _ in true }), LocalPorts.first,
                       "a remembered value that is no port is ignored")
    }

    func testTheSearchWrapsAroundTheRange() {
        let last = LocalPorts.first + LocalPorts.count - 1
        let picked = LocalPorts.pick(remembered: 0, from: LocalPorts.count - 1, isFree: { $0 == last || $0 == LocalPorts.first })
        XCTAssertEqual(picked, last)
        let wrapped = LocalPorts.pick(remembered: 0, from: LocalPorts.count - 1, isFree: { $0 == LocalPorts.first })
        XCTAssertEqual(wrapped, LocalPorts.first)
    }

    func testNoFreePortIsNil() {
        XCTAssertNil(LocalPorts.pick(remembered: 50123, from: 0, isFree: { _ in false }))
    }

    func testTheSystemHandsOutAPortAndZeroIsNone() throws {
        let port = try XCTUnwrap(LocalPorts.anyFree())
        XCTAssertTrue((1..<65536).contains(port))
        XCTAssertFalse(LocalPorts.isFree(0))
    }
}

final class LocalEnvironmentTests: XCTestCase {
    private func environment(seed: LocalLogin?) -> [String: String] {
        LocalEnvironment.make(dataDirectory: "/data", staticDirectory: "/bundle/python/frontend_dist",
                              version: "0.2.0", port: 50123, seed: seed)
    }

    func testTheServersEnvironment() {
        let env = environment(seed: nil)
        XCTAssertEqual(env["GAMMA_DATA_DIR"], "/data")
        XCTAssertEqual(env["GAMMA_STATIC_DIR"], "/bundle/python/frontend_dist")
        XCTAssertEqual(env["GAMMA_VERSION"], "0.2.0")
        XCTAssertEqual(env["GAMMA_PORT"], "50123")
        XCTAssertEqual(env["GAMMA_CLOUD_DEFAULT_ISSUER"], "https://account.gammapdf.com")
        XCTAssertNil(env["GAMMA_ADMIN_USER"])
        XCTAssertNil(env["GAMMA_ADMIN_PASSWORD"])
    }

    func testTheSeedOnlyForAnEmptyDataDirectory() {
        let env = environment(seed: LocalLogin(username: "admin", password: "abcdefghijkl"))
        XCTAssertEqual(env["GAMMA_ADMIN_USER"], "admin")
        XCTAssertEqual(env["GAMMA_ADMIN_PASSWORD"], "abcdefghijkl")
    }

    func testThePasswordIsTwelveBase64URLCharacters() throws {
        let password = try XCTUnwrap(LocalLogin.makePassword())
        XCTAssertEqual(password.count, 12)
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        XCTAssertTrue(password.unicodeScalars.allSatisfy(allowed.contains))
        XCTAssertEqual(Data([0xfb, 0xff, 0xbf]).base64URL, "-_-_")
    }

    func testThePythonPathsInTheBundle() throws {
        let layout = PythonRuntime.Layout(resources: URL(fileURLWithPath: "/App.app", isDirectory: true))
        XCTAssertEqual(layout.home, "/App.app/python")
        XCTAssertEqual(layout.searchPaths, [
            "/App.app/python/lib/python3.13",
            "/App.app/python/lib/python3.13/lib-dynload",
            "/App.app/python/app",
            "/App.app/python/app_packages",
        ])
    }
}

final class HealthTests: XCTestCase {
    func testUpIsOKOrAServerThatCannotUpgradeItsData() {
        XCTAssertTrue(Health.isUp(status: 200, body: Data("{\"ok\":true}".utf8)))
        XCTAssertTrue(Health.isUp(status: 503, body: Data("{\"error\":\"data_directory_not_upgradable\",\"build\":\"x\"}".utf8)))
    }

    func testAnythingElseIsDown() {
        XCTAssertFalse(Health.isUp(status: 503, body: Data("{\"error\":\"busy\"}".utf8)))
        XCTAssertFalse(Health.isUp(status: 503, body: Data("<html>".utf8)))
        XCTAssertFalse(Health.isUp(status: 500, body: Data()))
        XCTAssertFalse(Health.isUp(status: 404, body: Data()))
    }
}

final class ServerStateTests: XCTestCase {
    func testTheSupervisorsFileIsReadAndAnythingElseIsNone() throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("server-\(UUID().uuidString).state")
        defer { try? FileManager.default.removeItem(at: file) }
        XCTAssertNil(ServerState.read(file), "no file yet")
        try Data("{\"state\": \"stopped\", \"detail\": \"the server could not listen on its port\", \"at\": \"2026-10-16T10:00:00+00:00\"}".utf8).write(to: file)
        XCTAssertEqual(ServerState.read(file), ServerState(state: "stopped", detail: "the server could not listen on its port"))
        try Data("{\"state\": \"start".utf8).write(to: file)
        XCTAssertNil(ServerState.read(file), "half a write (the supervisor renames a whole file into place, so this is not expected)")
    }
}

final class SavedServersTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite = ""

    override func setUp() {
        super.setUp()
        suite = "gamma.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testTheListRoundTrips() throws {
        let a = try XCTUnwrap(URL(string: "https://a.example")), b = try XCTUnwrap(URL(string: "http://nas.local:9001"))
        for current in [Server.local, .remote(a), nil] as [Server?] {
            let saved = SavedServers(current: current, remotes: [a, b])
            saved.save(to: defaults)
            XCTAssertEqual(SavedServers.load(from: defaults), saved)
        }
    }

    func testPhaseOnesConnectionBecomesTheCurrentRemote() throws {
        defaults.set("https://gamma.example.org", forKey: "gamma.server")
        defaults.set(["https://gamma.example.org", "nas.local:8080"], forKey: "gamma.recentServers")
        let saved = SavedServers.load(from: defaults)
        XCTAssertEqual(saved.current, .remote(try XCTUnwrap(URL(string: "https://gamma.example.org"))))
        XCTAssertEqual(saved.remotes.map(\.absoluteString), ["https://gamma.example.org", "https://nas.local:8080"])
    }

    func testNothingSavedIsNoServer() {
        XCTAssertEqual(SavedServers.load(from: defaults), SavedServers(current: nil, remotes: []))
    }
}

/// The embedded server for real, in the test host (the app): the bundle's
/// Python, its wheels and the backend start and answer /api/health. CI has
/// no other check of the bundle, so a wrong layout, a broken framework
/// conversion, a missing wheel or an import error fails here instead of on
/// an iPad. The interpreter starts once per process, so this is one test.
final class LocalServerStartTests: XCTestCase {
    @MainActor
    func testTheEmbeddedServerStartsAndAnswers() async throws {
        let resources = try XCTUnwrap(Bundle.main.resourceURL)
        // What the build puts in the bundle (project.yml, install_python),
        // checked first so an unprepared build says what is missing.
        for path in ["python/app/gamma_ios.py", "python/app/gamma", "python/app_packages",
                     "python/frontend_dist/index.html", "python/lib/python3.13/os.py",
                     "python/lib/python3.13/lib-dynload"] {
            guard FileManager.default.fileExists(atPath: resources.appendingPathComponent(path).path) else {
                XCTFail("\(path) is not in the app bundle: run bash ipad/scripts/build-python.sh before building")
                return
            }
        }
        // A simulator whose last pick was This iPad started the server with
        // the app, and a second interpreter cannot start.
        try XCTSkipIf(PythonRuntime.started, "the app started its own server already (its last pick was This iPad)")

        let suite = "gamma.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let server = LocalServer(defaults: defaults)
        server.start()

        // Longer than LocalServer's slow mark: a cold simulator on a CI
        // runner imports the whole server.
        let deadline = Date().addingTimeInterval(180)
        wait: while Date() < deadline {
            switch server.state {
            case .running:
                break wait
            case let .failed(message):
                XCTFail("\(message)\n\(server.logTail())")
                return
            case .starting, .stopped:
                try await Task.sleep(nanoseconds: 500_000_000)
            }
        }
        let url = try XCTUnwrap(server.url, "the server did not answer in 3 minutes\n\(server.logTail())")
        let (body, response) = try await URLSession.shared.data(from: url.appendingPathComponent("api/health"))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        XCTAssertTrue(Health.isUp(status: status, body: body), "/api/health answered \(status)")
    }
}

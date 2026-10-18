import XCTest
@testable import Gamma

/// The local server's rules that need no interpreter: the port, the
/// environment, the health answer, the account, the bundle layout, and the
/// servers list kept between launches. LocalServerStartTests, at the end,
/// runs the interpreter itself and reads a PDF through its server.
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
/// Python, its wheels and the backend start and answer /api/health, and a
/// PDF uploaded to it is read through PyMuPDF. CI has no other check of the
/// bundle, so a wrong layout, a broken framework conversion, a missing
/// wheel or an import error fails here instead of on an iPad. /api/health
/// proves the start but not the reader: a server without PyMuPDF starts
/// too (pdf_text falls back to PyPDF2, and the manifest's page count comes
/// through that as well). A page picture proves it: render_page answers
/// nothing without PyMuPDF, and page-image answers 404. The server is
/// started with an account the test knows: the Keychain's, as the
/// app's, or one the test seeds itself where the Keychain refuses
/// (LocalServer's seed): the test host on CI is unsigned
/// (CODE_SIGNING_ALLOWED=NO in ipad.yml) and has no Keychain
/// entitlement, so the app's own path, a password made for the
/// Keychain, gets nothing there. A fresh data directory thus has a
/// password the test knows, and only a used one without a password in
/// the Keychain makes the PDF part skip: a directory this test seeded
/// on an unsigned host is one until deleted. The interpreter starts
/// once per process, so this is one test.
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
        // The account a fresh data directory is seeded with where the
        // Keychain refuses the one LocalServer makes for it (LocalServer's
        // seed): the unsigned test host on CI. Signed by Xcode, the test host
        // keeps the app's path, a password in the Keychain, so the app on
        // that simulator signs in silently and the next run of this test
        // signs in again. A fixed password rather than a random one: it lives
        // for one run on a throwaway simulator, on a server that listens on
        // loopback only, and an unsigned host's simulator left with this
        // directory has an account whose password is written here instead of
        // nowhere (the server's log says only "(from GAMMA_ADMIN_PASSWORD)").
        let seed = LocalLogin(username: LocalLogin.account, password: "start-test-pw")
        let server = LocalServer(defaults: defaults, seed: seed)
        // Whether this start seeds the account (LocalServer.start): a data
        // directory without users.db gets the Keychain's account, or the seed
        // where the Keychain refuses, and the server's login is it. One with
        // accounts keeps them, and the login is the Keychain's password for
        // them, or nil.
        let fresh = !FileManager.default.fileExists(atPath: server.dataDirectory.appendingPathComponent("users.db").path)
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
        let up = Health.isUp(status: status, body: body)
        XCTAssertTrue(up, "/api/health answered \(status)")
        // Up by the desktop's rule, but standing aside: a data directory
        // this build cannot upgrade (a newer build ran on this simulator,
        // usually). That server answers every /api call with the same 503
        // (gamma/app.py _blocked_app), so nothing below can run.
        if up, status == 503 {
            throw XCTSkip("the data directory cannot be upgraded by this build (data_directory_not_upgradable), so every API call answers 503 and the PDF part cannot run: delete the simulator's Documents/Gamma/local to run it")
        }

        // The PDF part signs in as the seeded account: a fresh directory's is
        // one this test knows. An older directory (a developer's simulator
        // that ran the app, or this test on an unsigned host) keeps its
        // accounts, and without the password stored for them nothing here can
        // sign in.
        guard let login = server.login else {
            if fresh {
                XCTFail("a fresh data directory is seeded with an account this test knows (the Keychain's, or the seed where the Keychain refuses), but the server has none\n\(server.logTail())")
                return
            }
            throw XCTSkip("the data directory has its accounts already and the Keychain holds no password for them, so the PDF part cannot sign in: delete the simulator's Documents/Gamma/local to run it")
        }
        // Every call below works in the account's default workspace (no
        // X-Gamma-Workspace header, docs/dev/api.md "Auth model"), and
        // URLSession.shared carries the session cookie the login set.
        let api = url.appendingPathComponent("api")
        let signedIn = try await post(api.appendingPathComponent("login"),
                                      json: ["username": login.username, "password": login.password])
        guard signedIn.status == 200 else {
            fail("POST /api/login", signedIn, server)
            return
        }
        let uploaded = try await post(api.appendingPathComponent("uploads"), pdf: Self.onePagePdf(), filename: "start-test.pdf")
        guard uploaded.status == 200, let docID = uploaded.json["doc_id"] as? String, !docID.isEmpty else {
            fail("POST /api/uploads", uploaded, server)
            return
        }
        // The page that carries the PDF, which page-image is asked by.
        let page = try await post(api.appendingPathComponent("blocks/by-doc/\(docID)"),
                                  json: ["default_title": "The start test's PDF"])
        guard page.status == 200, let pageID = page.json["id"] as? String, !pageID.isEmpty else {
            fail("POST /api/blocks/by-doc", page, server)
            return
        }
        // The manifest: the walk over the pages (pdf_meta), in PDF points.
        let manifest = try await get(api.appendingPathComponent("pdf-info/\(docID)"))
        let dims = (manifest.json["dims"] as? [[NSNumber]])?.map { $0.map(\.doubleValue) } ?? []
        guard manifest.status == 200, (manifest.json["pages"] as? Int) == 1, dims == [[612, 792]] else {
            fail("GET /api/pdf-info (the manifest walk)", manifest, server)
            return
        }
        // The picture: MuPDF rendered the page. Without PyMuPDF the server
        // answers 404 here, whatever the calls above said. The page is one
        // line of text on white: 40 KB as JPEG, 19 KB as the server's own
        // PNG when Pillow is missing.
        let pictureURL = try XCTUnwrap(URL(string: "\(api.absoluteString)/ai/page-image/\(pageID)?page=1"))
        let picture = try await get(pictureURL)
        guard picture.status == 200, picture.type.hasPrefix("image/"), picture.body.count > 2048 else {
            fail("GET /api/ai/page-image (MuPDF rendering the page)", picture, server)
            return
        }
    }

    /// What a call answered: the status, the Content-Type and the body.
    private struct Answer {
        let status: Int
        let type: String
        let body: Data

        /// The body as a JSON object, or empty.
        var json: [String: Any] {
            (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
        }

        /// The body for a failure message: its head, or a picture's size.
        var text: String {
            type.hasPrefix("image/") ? "\(body.count) bytes of \(type)" : String(decoding: body.prefix(300), as: UTF8.self)
        }
    }

    /// A call that did not answer as expected: what it answered and the end
    /// of the server's log, so a CI failure says which call and what the
    /// server logged.
    @MainActor
    private func fail(_ call: String, _ answer: Answer, _ server: LocalServer,
                      file: StaticString = #filePath, line: UInt = #line) {
        XCTFail("\(call) answered \(answer.status): \(answer.text)\n\(server.logTail())", file: file, line: line)
    }

    private func send(_ request: URLRequest) async throws -> Answer {
        let (body, response) = try await URLSession.shared.data(for: request)
        let http = response as? HTTPURLResponse
        return Answer(status: http?.statusCode ?? 0,
                      type: http?.value(forHTTPHeaderField: "Content-Type") ?? "",
                      body: body)
    }

    private func get(_ url: URL) async throws -> Answer {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        return try await send(request)
    }

    /// A POST with a JSON body, as the web app's fetch wrapper sends one.
    private func post(_ url: URL, json: [String: Any]) async throws -> Answer {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        return try await send(request)
    }

    /// A PDF as a browser's file form sends one: multipart/form-data with
    /// the one field the upload route takes (routers/uploads.py, `file`).
    private func post(_ url: URL, pdf: Data, filename: String) async throws -> Answer {
        let boundary = "gamma-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: application/pdf\r\n\r\n".utf8))
        body.append(pdf)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        return try await send(request)
    }

    /// A one-page PDF with a line of Helvetica text, the shape of the
    /// browser suite's makePdf (frontend/tests/e2e/harness.mjs): US Letter,
    /// one content stream, a correct xref. ASCII throughout, so the offsets
    /// are character counts.
    private static func onePagePdf() -> Data {
        let content = "BT\n/F1 20 Tf\n72 720 Td\n26 TL\n(Gamma on this iPad reads PDFs through MuPDF.) Tj T*\nET"
        let objects = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Length \(content.utf8.count) >>\nstream\n\(content)\nendstream",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>",
        ]
        var out = "%PDF-1.4\n"
        var offsets: [Int] = []
        for (index, object) in objects.enumerated() {
            offsets.append(out.utf8.count)
            out += "\(index + 1) 0 obj\n\(object)\nendobj\n"
        }
        let xref = out.utf8.count
        out += "xref\n0 \(objects.count + 1)\n0000000000 65535 f \n"
        for offset in offsets {
            let digits = String(offset)
            out += String(repeating: "0", count: 10 - digits.count) + digits + " 00000 n \n"
        }
        out += "trailer\n<< /Size \(objects.count + 1) /Root 1 0 R >>\nstartxref\n\(xref)\n%%EOF\n"
        return Data(out.utf8)
    }
}

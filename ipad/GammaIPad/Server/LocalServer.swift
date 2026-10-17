import Darwin
import Foundation
import Security

/// The local server: Gamma's own backend inside the app on
/// 127.0.0.1:<port>, as the desktop shell runs one beside it
/// (desktop/lib/sidecar.js, docs/dev/ipad.md "The local server"). It owns
/// nothing the web app shows: it picks the port and the data directory,
/// seeds the first account, starts the interpreter (PythonRuntime) and says
/// when the server answers. Everything else, clones of remote workspaces
/// included, is the web app's, as on the desktop.
@MainActor
final class LocalServer: ObservableObject {
    enum State: Equatable {
        case stopped
        case starting
        case running(URL)
        case failed(String)
    }

    @Published private(set) var state: State = .stopped {
        didSet { onState?(state) }
    }
    /// AppModel follows the state from here (the shell opens the address once it answers).
    var onState: (@MainActor (State) -> Void)?
    /// The seeded account the shell signs in with (WebShell), once started.
    /// Nil when this iPad does not know the account's password (`start`).
    private(set) var login: LocalLogin?
    /// Still starting after a minute. The interpreter keeps going and the
    /// server is used once it answers; the pages say it can take a while.
    @Published private(set) var slowStart = false

    /// <Documents>/Gamma/local: the server's GAMMA_DATA_DIR. In Documents so
    /// the Files app reaches it (On My iPad > Gamma, UIFileSharingEnabled in
    /// project.yml), with its log, as the desktop shows its data folder.
    let dataDirectory: URL
    private let defaults: UserDefaults
    private static let portKey = "gamma.local.port"
    private var rechecking = false
    /// The address the interpreter was started for; `restart` keeps it.
    private var address: URL?

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        dataDirectory = documents.appendingPathComponent("Gamma/local", isDirectory: true)
    }

    /// The server's own output (gamma_ios.py writes stdout and stderr here).
    var logFile: URL {
        dataDirectory.appendingPathComponent("logs/server.log", isDirectory: false)
    }

    /// The supervisor's state (gamma_ios.py STATE_FILE, `ServerState`).
    var stateFile: URL {
        dataDirectory.appendingPathComponent("logs/server.state", isDirectory: false)
    }

    var url: URL? {
        if case let .running(url) = state { return url }
        return nil
    }

    /// Starts the server unless it was started already. The interpreter runs
    /// once per launch (PythonRuntime); the server in it can be run again
    /// (`restart`).
    func start() {
        guard state == .stopped else { return }
        state = .starting
        let files = FileManager.default
        do {
            try files.createDirectory(at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        } catch {
            state = .failed("Gamma cannot make its data folder: \(error.localizedDescription)")
            return
        }
        try? files.removeItem(at: stateFile) // the last launch's; this start writes its own
        guard let resources = Bundle.main.resourceURL,
              files.fileExists(atPath: resources.appendingPathComponent("python/app/gamma_ios.py").path),
              files.fileExists(atPath: resources.appendingPathComponent("python/frontend_dist/index.html").path) else {
            state = .failed("This build of Gamma has no server in it (ipad/scripts/build-python.sh did not run).")
            return
        }
        let remembered = defaults.integer(forKey: Self.portKey)
        guard let port = LocalPorts.pick(remembered: remembered, from: Int.random(in: 0..<LocalPorts.count),
                                         isFree: LocalPorts.isFree) ?? LocalPorts.anyFree() else {
            state = .failed("Gamma found no free port on this iPad.")
            return
        }
        defaults.set(port, forKey: Self.portKey)

        // The first start on an empty data directory seeds this account (the
        // server's own seed otherwise, with a password only its log knows).
        // Afterwards the server keeps the accounts it has and ignores the
        // seed, so only a password stored for it is any use: a data directory
        // without one (restored from a backup without the Keychain, or a
        // Keychain that refused the first write) gets no silent sign-in and
        // the person signs in on the page.
        let fresh = !files.fileExists(atPath: dataDirectory.appendingPathComponent("users.db").path)
        let login = fresh ? LocalLogin.stored() : LocalLogin.existing()
        self.login = login
        let environment = LocalEnvironment.make(
            dataDirectory: dataDirectory.path,
            staticDirectory: resources.appendingPathComponent("python/frontend_dist", isDirectory: true).path,
            version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            port: port,
            seed: fresh ? login : nil)

        let layout = PythonRuntime.Layout(resources: resources)
        let started = PythonRuntime.start(environment: environment, layout: layout, log: logFile) { [weak self] reason in
            self?.state = .failed(reason)
        }
        guard started, let url = URL(string: "http://127.0.0.1:\(port)") else {
            state = .failed("The server already ran in this session. Close Gamma and open it again.")
            return
        }
        address = url
        Task { await waitUntilUp(url) }
    }

    /// The desktop's rule (sidecar.js start), every 250 ms, but with no
    /// deadline: giving up would only hide a server that answers later. A
    /// start that takes long (the first one after an install, on an older
    /// iPad) is marked slow after a minute and keeps being polled, every
    /// second from then on. It fails when the supervisor says the server
    /// stopped before it answered (`ServerState`: a port it could not
    /// listen on, say), or on the runtime's own exit (`onExit`).
    private func waitUntilUp(_ url: URL) async {
        let slowAfter = Date().addingTimeInterval(60)
        while state == .starting {
            if await Health.probe(url, timeout: 1.5) {
                if state == .starting {
                    slowStart = false
                    state = .running(url)
                }
                return
            }
            if let written = ServerState.read(stateFile), written.state == "stopped" {
                if state == .starting {
                    slowStart = false
                    state = .failed(written.detail.isEmpty ? "The server stopped before it answered." : written.detail)
                }
                return
            }
            if !slowStart, Date() > slowAfter { slowStart = true }
            try? await Task.sleep(nanoseconds: slowStart ? 1_000_000_000 : 250_000_000)
        }
    }

    /// Asked when the app is active again (RootView). iOS can reclaim a
    /// suspended app's listening sockets, notably while the screen is locked
    /// (Apple TN2277). A server that no longer answers is run again in the
    /// same interpreter (`restart`), behind the start page for the moment
    /// that takes, rather than left behind a page that cannot load.
    func recheck() {
        guard case let .running(url) = state, !rechecking else { return }
        rechecking = true
        Task {
            defer { rechecking = false }
            // A few tries: the server's thread is just waking up with the app.
            for attempt in 0..<3 {
                if attempt > 0 { try? await Task.sleep(nanoseconds: 1_000_000_000) }
                if await Health.probe(url, timeout: 2) { return }
            }
            if state == .running(url) { restart() }
        }
    }

    /// The server again: after a failed start (the pages' Try again) or when
    /// it stopped answering (`recheck`). With the interpreter up,
    /// `gamma_ios.restart` stops a server still running and runs a new one
    /// on the same port, so the address, and what the web app keeps per
    /// origin, stay. A start that failed before the interpreter (no free
    /// port, no data folder) is tried from the beginning; one the
    /// interpreter itself could not survive stays failed until the app is
    /// opened again.
    func restart() {
        guard state != .starting else { return }
        guard let url = address else {
            state = .stopped
            start()
            return
        }
        state = .starting
        slowStart = false
        Task {
            let asked = await Task.detached { PythonRuntime.restart() }.value
            guard asked else {
                if state == .starting {
                    state = .failed("Python is not running in this app. Close Gamma and open it again.")
                }
                return
            }
            await waitUntilUp(url)
        }
    }

    /// The end of the server's log, for a failed start.
    func logTail(maxBytes: UInt64 = 4096) -> String {
        guard let handle = try? FileHandle(forReadingFrom: logFile) else { return "" }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        try? handle.seek(toOffset: size > maxBytes ? size - maxBytes : 0)
        let data = (try? handle.readToEnd()) ?? Data()
        return String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The data directory's size on disk, counted off the main thread.
    func dataSize() async -> Int64 {
        let folder = dataDirectory
        return await Task.detached(priority: .utility) { LocalServer.size(of: folder) }.value
    }

    nonisolated static func size(of folder: URL) -> Int64 {
        let keys: [URLResourceKey] = [.isRegularFileKey, .totalFileAllocatedSizeKey, .fileAllocatedSizeKey]
        guard let walk = FileManager.default.enumerator(at: folder, includingPropertiesForKeys: keys) else { return 0 }
        var total: Int64 = 0
        while let file = walk.nextObject() as? URL {
            guard let values = try? file.resourceValues(forKeys: Set(keys)), values.isRegularFile == true else { continue }
            total += Int64(values.totalFileAllocatedSize ?? values.fileAllocatedSize ?? 0)
        }
        return total
    }
}

/// The local server's one account, made once: "admin" and a random
/// password kept in the Keychain. The shell signs in with it silently
/// (WebShell), as the desktop does for its local servers.
struct LocalLogin: Equatable {
    let username: String
    let password: String

    static let account = "admin"
    private static let keychainAccount = "local-server.admin-password"

    /// For a first start: the stored account, or a new one stored now. Nil
    /// only when the Keychain refuses it: the server then seeds its own, and
    /// the person signs in on the page with what its log says.
    static func stored() -> LocalLogin? {
        if let login = existing() { return login }
        guard let password = makePassword(), Keychain.set(password, account: keychainAccount) else { return nil }
        return LocalLogin(username: account, password: password)
    }

    /// For a data directory that has its accounts already: the stored
    /// account, never a new one, which that server would not know.
    static func existing() -> LocalLogin? {
        Keychain.string(account: keychainAccount).map { LocalLogin(username: account, password: $0) }
    }

    /// 12 base64url characters from 9 random bytes, like the desktop's
    /// (desktop/lib/registry.js newPassword) and the server's own seed.
    static func makePassword() -> String? {
        var bytes = [UInt8](repeating: 0, count: 9)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { return nil }
        return Data(bytes).base64URL
    }
}

extension Data {
    /// base64url without padding (RFC 4648 §5): the seeded password's and
    /// the PKCE verifier's alphabet (CloudSignIn).
    var base64URL: String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// The server's environment (gamma/config.py reads it at import). Pure, so
/// the tests cover it.
enum LocalEnvironment {
    static let cloudIssuer = "https://account.gammapdf.com"

    static func make(dataDirectory: String, staticDirectory: String, version: String, port: Int,
                     seed: LocalLogin?) -> [String: String] {
        var environment = [
            "GAMMA_DATA_DIR": dataDirectory,
            // The built web app the server serves at its root.
            "GAMMA_STATIC_DIR": staticDirectory,
            // The admin dashboard reports the app the server ships in (gamma/version.py).
            "GAMMA_VERSION": version,
            // Gamma Cloud sign-in is on until the admin picks another account
            // server or none, as on the desktop (gamma/cloud_auth.py).
            "GAMMA_CLOUD_DEFAULT_ISSUER": cloudIssuer,
            // Read by ipad/python/gamma_ios.py.
            "GAMMA_PORT": String(port),
        ]
        // Only for the first start: the server seeds this account on an empty
        // data directory and ignores these afterwards.
        if let seed {
            environment["GAMMA_ADMIN_USER"] = seed.username
            environment["GAMMA_ADMIN_PASSWORD"] = seed.password
        }
        return environment
    }
}

/// The port rule of desktop/lib/sidecar.js pickPort: the port the server
/// had last time while it is free, so the address (and what the web app
/// keeps per origin) stays the same across launches; else a free one from
/// the IANA dynamic range, from a random start.
enum LocalPorts {
    static let first = 49152
    static let count = 16384

    static func pick(remembered: Int, from start: Int, isFree: (Int) -> Bool) -> Int? {
        if remembered > 0, remembered < 65536, isFree(remembered) { return remembered }
        for i in 0..<count {
            let port = first + (start + i) % count
            if isFree(port) { return port }
        }
        return nil
    }

    /// Whether uvicorn could listen on 127.0.0.1:`port` now: a bind with the
    /// SO_REUSEADDR uvicorn sets too, so a port whose last connections are
    /// still in TIME_WAIT (the app's previous run, just ended) counts as
    /// free and keeps its address, while one another socket listens on
    /// does not (Darwin refuses a second bind to a listening address).
    static func isFree(_ port: Int) -> Bool {
        port > 0 && bind(port: port) != nil
    }

    /// Any port the system hands out, the desktop's last resort.
    static func anyFree() -> Int? {
        bind(port: 0)
    }

    /// Binds a throwaway socket to 127.0.0.1:`port` and answers the port it
    /// got (the system's pick for 0), or nil.
    private static func bind(port: Int) -> Int? {
        guard (0..<65536).contains(port) else { return nil }
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(UInt16(port).bigEndian)
        address.sin_addr = in_addr(s_addr: UInt32(0x7F00_0001).bigEndian) // 127.0.0.1
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let bound = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { generic -> Bool in
                guard Darwin.bind(fd, generic, length) == 0 else { return false }
                return getsockname(fd, generic, &length) == 0
            }
        }
        return bound ? Int(UInt16(bigEndian: address.sin_port)) : nil
    }
}

/// What the supervisor in gamma_ios.py last wrote to `logs/server.state`:
/// "starting", or "stopped" with why (a port it could not listen on, a
/// server that ended). Read while the app waits for /api/health, so a start
/// that failed is reported, with Try again, instead of polled for ever.
struct ServerState: Decodable, Equatable {
    let state: String
    let detail: String

    static func read(_ file: URL) -> ServerState? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        return try? JSONDecoder().decode(ServerState.self, from: data)
    }
}

/// Whether a Gamma server is up, by the desktop's rule (sidecar.js healthy):
/// /api/health answers 200, or 503 with `data_directory_not_upgradable`,
/// a server that is up and shows its own guidance on every page.
enum Health {
    private struct Answer: Decodable { let error: String? }

    static func isUp(status: Int, body: Data) -> Bool {
        if (200..<300).contains(status) { return true }
        guard status == 503 else { return false }
        return (try? JSONDecoder().decode(Answer.self, from: body))?.error == "data_directory_not_upgradable"
    }

    static func probe(_ server: URL, timeout: TimeInterval) async -> Bool {
        var request = URLRequest(url: server.appendingPathComponent("api/health"))
        request.timeoutInterval = timeout
        request.cachePolicy = .reloadIgnoringLocalCacheData
        guard let answer = try? await URLSession.shared.data(for: request),
              let http = answer.1 as? HTTPURLResponse else { return false }
        return isUp(status: http.statusCode, body: answer.0)
    }
}

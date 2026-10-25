import AuthenticationServices
import CryptoKit
import Network
import WebKit

// Gamma Cloud sign-in from the iPad: the desktop app's flow
// (desktop/lib/cloudSignIn.js, desktop/main.js signInWithCloud; the server
// side is backend/gamma/cloud_auth.py and routers/cloud_auth.py), so the
// server needs nothing new. Google refuses to sign in inside an embedded web
// view, so the page's own start (the login page's button, Settings' Link)
// is taken over: the shell listens on a free loopback port, asks the server
// for a sign-in that reports there (app-start, with the S256 challenge of a
// verifier only the shell keeps), and opens the account server in a system
// browser sheet (ASWebAuthenticationSession), where Google's sign-in works.
// The server sends that browser to http://127.0.0.1:<port>/cloud-signin
// with a one-time result; the listener keeps it and redirects the sheet to
// gamma-ipad://cloud-signin, which closes it. The web view then redeems the
// result with the verifier (app-claim), so the session cookie lands where
// the web app runs. Only this device reaches that loopback address (RFC 8252
// §7.3), so a sign-in someone else started never hands its result to them.

/// The contract with the server, as pure functions (tested).
enum CloudWire {
    static let startPath = "/api/auth/cloud/start"
    static let appStartPath = "/api/auth/cloud/app-start"
    static let claimPath = "/api/auth/cloud/app-claim"
    static let returnPath = "/cloud-signin"
    static let callbackScheme = "gamma-ipad"
    /// What tells the server the app's own window is claiming, not some page
    /// (routers/cloud_auth.py APP_CLAIM_HEADER).
    static let claimHeader = "X-Gamma-Desktop"

    /// app-start's body: what the page's start asked for, and where to report.
    struct AppStart: Encodable, Equatable {
        let next: String
        let link: Bool
        let returnTo: String
        let challenge: String

        enum CodingKeys: String, CodingKey { case next, link, returnTo = "return_to", challenge }

        /// From the page's start URL (`/api/auth/cloud/start?next=&link=1`).
        init(start: URL, returnTo: String, challenge: String) {
            let query = URLComponents(url: start, resolvingAgainstBaseURL: true)?.queryItems ?? []
            let value = { (name: String) in query.first { $0.name == name }?.value ?? "" }
            next = value("next").isEmpty ? "/" : value("next")
            link = !value("link").isEmpty
            self.returnTo = returnTo
            self.challenge = challenge
        }
    }

    /// The account server's authorize URL from app-start's answer; nil when
    /// the server is older than the app's sign-in (no app-start route).
    static func authorizeURL(status: Int, text: String) throws -> URL? {
        if status == 404 || status == 405 { return nil }
        let json = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
        if (200..<300).contains(status), let url = (json?["url"] as? String).flatMap({ URL(string: $0) }) {
            return url
        }
        throw Refusal((json?["detail"] as? String) ?? "HTTP \(status)")
    }

    /// The web view's request that redeems a result: a form post, as the
    /// desktop's window sends it.
    static func claimRequest(origin: Origin, result: String, verifier: String) -> URLRequest? {
        guard let url = URL(string: origin.string + claimPath) else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("claim", forHTTPHeaderField: claimHeader)
        request.httpBody = formBody([("result", result), ("verifier", verifier)])
        return request
    }

    static func formBody(_ fields: [(String, String)]) -> Data {
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        let pairs = fields.map { name, value in
            "\(name)=\(value.addingPercentEncoding(withAllowedCharacters: unreserved) ?? "")"
        }
        return Data(pairs.joined(separator: "&").utf8)
    }

    /// What the browser brought back to the loopback address: its request's
    /// head ("GET /cloud-signin?result=… HTTP/1.1"), or nil for any other
    /// request (a favicon, a probe).
    static func loopbackAnswer(_ head: String) -> CloudAnswer? {
        let line = head.components(separatedBy: "\r\n").first ?? ""
        let parts = line.split(separator: " ")
        guard parts.count >= 2, parts[0] == "GET" else { return nil }
        // The server's urlencode writes a space as "+".
        let target = String(parts[1]).replacingOccurrences(of: "+", with: "%20")
        guard let url = URLComponents(string: "http://127.0.0.1" + target), url.path == returnPath else { return nil }
        let value = { (name: String) in url.queryItems?.first { $0.name == name }?.value ?? "" }
        if !value("result").isEmpty { return .result(value("result")) }
        if !value("error").isEmpty { return .error(value("error")) }
        return nil
    }

    /// The loopback's answer to the browser: on to the custom scheme, which
    /// ends the sheet, or nothing here.
    static func loopbackResponse(for answer: CloudAnswer?) -> Data {
        let head = answer == nil
            ? "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            : "HTTP/1.1 302 Found\r\nLocation: \(callbackScheme)://cloud-signin\r\nCache-Control: no-store\r\n"
                + "Content-Length: 0\r\nConnection: close\r\n\r\n"
        return Data(head.utf8)
    }
}

enum CloudAnswer: Equatable {
    case result(String)
    case error(String)
}

/// RFC 7636's S256: a random verifier only the shell keeps, and its
/// challenge, which the server stores with the pending sign-in.
struct PKCE {
    let verifier: String
    var challenge: String { Self.challenge(for: verifier) }

    init(verifier: String = PKCE.newVerifier()) {
        self.verifier = verifier
    }

    /// 32 random bytes: 43 characters, as the server's challenge check expects.
    static func newVerifier() -> String {
        SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }.base64URL
    }

    static func challenge(for verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
    }
}

/// The loopback address the browser reports to: one listener on 127.0.0.1
/// for one sign-in. Its callbacks run on the main queue.
@MainActor
final class LoopbackReturn {
    private(set) var answer: CloudAnswer?
    private var listener: NWListener?
    private var ready: CheckedContinuation<String, Error>?

    /// Listens on a free port; the return address for app-start.
    func start() async throws -> String {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: .any)
        let listener = try NWListener(using: parameters)
        self.listener = listener
        listener.newConnectionHandler = { [weak self] connection in
            MainActor.assumeIsolated {
                if let self { self.serve(connection) }
            }
        }
        listener.stateUpdateHandler = { [weak self] state in
            MainActor.assumeIsolated {
                if let self { self.changed(to: state) }
            }
        }
        return try await withCheckedThrowingContinuation { continuation in
            ready = continuation
            listener.start(queue: .main)
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
        finish(.failure(CancellationError()))
    }

    private func changed(to state: NWListener.State) {
        switch state {
        case .ready:
            guard let port = listener?.port?.rawValue else { return }
            finish(.success("http://127.0.0.1:\(port)\(CloudWire.returnPath)"))
        case .failed(let error), .waiting(let error):
            // A listener left waiting (no loopback to bind) would never be
            // ready: the sign-in fails now instead of hanging.
            finish(.failure(error))
        case .cancelled:
            finish(.failure(CancellationError()))
        default:
            break
        }
    }

    private func finish(_ result: Result<String, Error>) {
        ready?.resume(with: result)
        ready = nil
    }

    /// One request: the answer is kept before the reply goes out, so it is
    /// there by the time the redirect closes the sheet.
    private func serve(_ connection: NWConnection) {
        connection.start(queue: .main)
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16 * 1024) { [weak self] data, _, _, _ in
            MainActor.assumeIsolated {
                let answer = CloudWire.loopbackAnswer(data.map { String(decoding: $0, as: UTF8.self) } ?? "")
                if let self, let answer, self.answer == nil { self.answer = answer }
                connection.send(content: CloudWire.loopbackResponse(for: answer),
                                completion: .contentProcessed { _ in connection.cancel() })
            }
        }
    }
}

/// The flow itself, one sign-in at a time (the sheet is modal anyway).
@MainActor
final class CloudSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    weak var webView: WKWebView?
    /// Tells the person when the sign-in cannot finish.
    var report: ((String) -> Void)?

    private var running = false
    private var session: ASWebAuthenticationSession?
    private var sheetClosed: CheckedContinuation<Void, Never>?

    /// Takes over the page's start (`/api/auth/cloud/start?…`).
    func start(_ startURL: URL) {
        guard !running else { return }
        running = true
        Task {
            defer { running = false }
            await run(startURL)
        }
    }

    private func run(_ startURL: URL) async {
        guard let webView, let origin = Origin(startURL) else { return }
        let pkce = PKCE()
        let loopback = LoopbackReturn()
        defer { loopback.stop() }
        do {
            let returnTo = try await loopback.start()
            let body = CloudWire.AppStart(start: startURL, returnTo: returnTo, challenge: pkce.challenge)
            guard let authorize = try await appStart(body, origin: origin, in: webView) else {
                report?("This server is older than the app. Update it to sign in with Gamma Cloud here, or sign in with a password.")
                return
            }
            await showSheet(authorize)
            switch loopback.answer {
            case .result(let result)?:
                if let claim = CloudWire.claimRequest(origin: origin, result: result, verifier: pkce.verifier) {
                    webView.load(claim) // sets the session cookie and goes on to `next`
                }
            case .error(let message)?:
                report?("Gamma Cloud sign-in did not finish: \(message)")
            case nil:
                break // the sheet was closed before the sign-in finished
            }
        } catch {
            report?("Gamma Cloud sign-in: \(error.localizedDescription)")
        }
    }

    /// app-start from inside the page, so the request carries the page's
    /// session (a Link needs it) exactly as the web app's own fetches do.
    /// It runs in the app's own content world, out of the page's reach.
    private func appStart(_ body: CloudWire.AppStart, origin: Origin, in webView: WKWebView) async throws -> URL? {
        let json = try String(decoding: JSONEncoder().encode(body), as: UTF8.self)
        let value = try await webView.callAsync("""
            const r = await fetch(url, { method: "POST", credentials: "include", cache: "no-store",
                                         headers: { "Content-Type": "application/json" }, body });
            return { status: r.status, text: await r.text() };
            """, arguments: ["url": origin.string + CloudWire.appStartPath, "body": json], in: .defaultClient)
        let reply = value as? [String: Any]
        return try CloudWire.authorizeURL(status: (reply?["status"] as? NSNumber)?.intValue ?? 0,
                                          text: reply?["text"] as? String ?? "")
    }

    /// The system browser sheet, until it closes: by the loopback's redirect
    /// to the custom scheme, or by the person.
    private func showSheet(_ url: URL) async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            sheetClosed = continuation
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: CloudWire.callbackScheme) { [weak self] _, _ in
                Task { @MainActor in self?.closeSheet() }
            }
            session.presentationContextProvider = self
            // Not ephemeral: the browser's own Google or GitHub session can be used.
            session.prefersEphemeralWebBrowserSession = false
            self.session = session
            if !session.start() { closeSheet() }
        }
    }

    private func closeSheet() {
        session = nil
        sheetClosed?.resume()
        sheetClosed = nil
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        webView?.window ?? ASPresentationAnchor()
    }
}

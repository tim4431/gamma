import Foundation

/// The Gamma server the replica follows, over its public API with the
/// replica's write token (`Authorization: Bearer`, the workspace in
/// `X-Gamma-Workspace`) — the calls a desktop mirror makes (docs/dev/mirror.md).
/// The sync core calls these from its own thread and waits for the answer;
/// nothing here runs on the main thread.
final class Remote {
    struct Answer {
        let status: Int
        let body: Data
    }

    let base: URL
    let workspace: String
    private let token: String
    private let session: URLSession

    init(base: URL, workspace: String, token: String) {
        self.base = base
        self.workspace = workspace
        self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 30 * 60
        config.httpCookieAcceptPolicy = .never
        config.httpShouldSetCookies = false
        session = URLSession(configuration: config)
    }

    func url(_ path: String) -> URL {
        URL(string: path, relativeTo: base)!.absoluteURL
    }

    private func request(_ method: String, _ path: String, contentType: String? = nil, body: Data? = nil) -> URLRequest {
        var r = URLRequest(url: url(path))
        r.httpMethod = method
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.setValue(workspace, forHTTPHeaderField: "X-Gamma-Workspace")
        r.setValue("Gamma-iPad/1", forHTTPHeaderField: "User-Agent")
        if let contentType { r.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        r.httpBody = body
        return r
    }

    /// One request, waited for. Status 0 is a request that never got an
    /// answer (no network, a dropped link).
    private func send(_ req: URLRequest) -> Answer {
        let done = DispatchSemaphore(value: 0)
        var answer = Answer(status: 0, body: Data())
        let task = session.dataTask(with: req) { data, response, error in
            if let http = response as? HTTPURLResponse, error == nil {
                let body = data ?? Data()
                // a body cut short by a dropped link is no answer (sync_engine's Remote.get_bytes)
                let expected = http.expectedContentLength
                if req.httpMethod != "HEAD", expected > 0, Int64(body.count) < expected {
                    answer = Answer(status: 0, body: Data("the answer was cut short".utf8))
                } else {
                    answer = Answer(status: http.statusCode, body: body)
                }
            } else {
                answer = Answer(status: 0, body: Data((error?.localizedDescription ?? "no answer").utf8))
            }
            done.signal()
        }
        task.resume()
        done.wait()
        return answer
    }

    func json(_ method: String, _ path: String, body: Any?) -> Answer {
        var data: Data?
        if let body, !(body is NSNull) { data = try? JSONSerialization.data(withJSONObject: body, options: [.withoutEscapingSlashes]) }
        return send(request(method, path, contentType: data == nil ? nil : "application/json", body: data))
    }

    func download(_ name: String) -> Answer { send(request("GET", "/api/uploads/\(name)")) }

    func head(_ name: String) -> Int { send(request("HEAD", "/api/uploads/\(name)")).status }

    /// A file upload (multipart `file`): a PDF to /api/uploads, anything else
    /// to /api/upload-file — the routes a mirror pushes files by.
    func upload(_ name: String, _ data: Data) -> Answer {
        let boundary = "gamma-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let path = name.lowercased().hasSuffix(".pdf") ? "/api/uploads" : "/api/upload-file"
        return send(request("POST", path, contentType: "multipart/form-data; boundary=\(boundary)", body: body))
    }
}

/// The server before the replica exists: sign-in happens in a web view, so
/// these calls carry the web session's cookie (`session`), not a token.
enum ServerSetup {
    struct Workspace: Identifiable, Hashable {
        let id: String
        let name: String
        let role: String
        let personal: Bool
    }

    struct Session {
        let user: String
        let workspaces: [Workspace]
    }

    private static func call(_ base: URL, _ path: String, cookie: String, method: String = "GET", workspace: String? = nil,
                             body: [String: Any]? = nil) async throws -> (Int, Any?) {
        var r = URLRequest(url: URL(string: path, relativeTo: base)!.absoluteURL)
        r.httpMethod = method
        r.setValue("session=\(cookie)", forHTTPHeaderField: "Cookie")
        r.setValue("Gamma-iPad/1", forHTTPHeaderField: "User-Agent")
        if let workspace { r.setValue(workspace, forHTTPHeaderField: "X-Gamma-Workspace") }
        if let body {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: r)
        return ((response as? HTTPURLResponse)?.statusCode ?? 0, JSON.parse(data))
    }

    /// Who the web session is and the workspaces it can open; nil while signed out.
    static func session(base: URL, cookie: String) async throws -> Session? {
        let (status, body) = try await call(base, "/api/session", cookie: cookie)
        guard status == 200, let s = body as? [String: Any], let user = s["user"] as? String, !user.isEmpty else { return nil }
        let list = (s["workspaces"] as? [[String: Any]] ?? []).map {
            Workspace(id: $0.string("id"), name: $0.string("name"), role: $0.string("role"), personal: ($0["personal"] as? Bool) ?? false)
        }
        return Session(user: user, workspaces: list)
    }

    /// A write token on `workspace` for this iPad (the one credential it keeps).
    static func mintToken(base: URL, cookie: String, workspace: String, device: String) async throws -> String {
        let (status, body) = try await call(base, "/api/integrations/tokens", cookie: cookie, method: "POST", workspace: workspace,
                                            body: ["name": "iPad: \(device)", "scope": "write", "expires_in_days": 365])
        guard status == 201 || status == 200, let token = (body as? [String: Any])?["token"] as? String else {
            let detail = (body as? [String: Any])?["detail"] as? String ?? "the server answered \(status)"
            throw JSON.Failure(message: detail)
        }
        return token
    }
}

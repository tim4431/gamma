import Foundation

public struct GammaHTTPResponse: Sendable {
    public var status: Int
    public var data: Data
    public var headers: [String: String]
    public init(status: Int, data: Data = Data(), headers: [String: String] = [:]) { self.status = status; self.data = data; self.headers = headers }
}

public protocol GammaTransport: Sendable {
    func request(_ request: URLRequest) async throws -> GammaHTTPResponse
}

public final class GammaURLSessionTransport: NSObject, GammaTransport, URLSessionTaskDelegate, @unchecked Sendable {
    public override init() { super.init() }
    public func request(_ request: URLRequest) async throws -> GammaHTTPResponse {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 600
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw GammaError.invalid("The server returned a non-HTTP response.") }
        var headers: [String: String] = [:]
        for (key, value) in http.allHeaderFields { headers[String(describing: key).lowercased()] = String(describing: value) }
        if request.httpMethod != "HEAD", headers["content-encoding"] == nil,
           let length = headers["content-length"].flatMap(Int.init), length != data.count {
            throw GammaError.invalid("The network transfer was interrupted.")
        }
        return GammaHTTPResponse(status: http.statusCode, data: data, headers: headers)
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                           newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let original = task.originalRequest?.url, let next = request.url,
              original.scheme == next.scheme, original.host == next.host, original.port == next.port else { completionHandler(nil); return }
        completionHandler(request)
    }
}

public struct GammaRemoteError: Error, LocalizedError, Sendable {
    public var status: Int
    public var body: JSONValue?
    public var errorDescription: String? { body?["detail"]?.string ?? "Gamma server returned HTTP \(status)." }
}

struct GammaRemote: Sendable {
    let mirror: GammaMirror
    let token: String
    let transport: any GammaTransport
    var capabilities: [String: JSONValue] = [:]
    func requireCapabilities(for ops: [GammaOperation]) throws {
        for op in ops {
            let props = op.props ?? [:]
            if (props["ink_url"] != nil || op.baseProps?["ink_url"] != nil), capabilities["ink_base_props"] != .bool(true) {
                throw GammaError.invalid("Upgrade the origin server before syncing handwriting. Your edits remain saved on this iPad.")
            }
            if props["notebook"] != nil || props["type"]?.string == "notebook-sheet" || props["sheet_id"] != nil || props["paper"] != nil {
                guard (capabilities["notebooks"]?.number ?? 0) >= 1 else { throw GammaError.invalid("Upgrade the origin server before syncing notebooks. Your notebook remains on this iPad.") }
            }
            if props["type"]?.string == "audio" || props["audio_segments"] != nil || props["audio_events"] != nil {
                guard (capabilities["audio"]?.number ?? 0) >= 1 else { throw GammaError.invalid("Upgrade the origin server before syncing audio notes. Your recording remains on this iPad.") }
            }
        }
    }
    func call(_ method: String, _ path: String, body: Data? = nil, contentType: String? = nil, allowed: Set<Int> = [200, 201]) async throws -> GammaHTTPResponse {
        guard let url = URL(string: mirror.origin + path) else { throw GammaError.invalid("Invalid server address.") }
        var request = URLRequest(url: url)
        request.httpMethod = method; request.httpBody = body
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue(mirror.workspaceID, forHTTPHeaderField: "X-Gamma-Workspace")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Gamma-iPad/1", forHTTPHeaderField: "User-Agent")
        if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        let response = try await transport.request(request)
        guard allowed.contains(response.status) else { throw GammaRemoteError(status: response.status, body: try? JSONDecoder().decode(JSONValue.self, from: response.data)) }
        return response
    }
    func json(_ method: String, _ path: String, body: JSONValue? = nil, allowed: Set<Int> = [200, 201]) async throws -> JSONValue {
        let response = try await call(method, path, body: try body.map(GammaJSON.data), contentType: body == nil ? nil : "application/json", allowed: allowed)
        if response.data.isEmpty { return .null }
        return try JSONDecoder().decode(JSONValue.self, from: response.data)
    }
    func tree(_ pageID: String) async throws -> (GammaSnapshot?, Int) {
        let response = try await call("GET", "/api/blocks/\(pageID)/subtree", allowed: [200, 404])
        if response.status == 404 { return (nil, 0) }
        let value = try JSONDecoder().decode(JSONValue.self, from: response.data)
        var snapshot: GammaSnapshot = [:]
        func visit(_ node: JSONValue) throws {
            guard let id = node["id"]?.string else { throw GammaError.invalid("Malformed page from the server.") }
            snapshot[id] = GammaBlock(id: id, parent: node["parent_id"]?.string ?? "root", position: node["position"]?.string ?? "a0", content: node["content"]?.string ?? "", properties: node["properties"]?.object ?? [:])
            for child in node["children"]?.array ?? [] { try visit(child) }
        }
        try visit(value["block"] ?? value)
        return (snapshot, Int(value["seq"]?.number ?? 0))
    }
    func upload(name: String, data: Data) async throws {
        let boundary = "Gamma" + GammaID.make()
        var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8)
        body.append(data); body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let response = try await call("POST", name.hasSuffix(".pdf") ? "/api/uploads" : "/api/upload-file", body: body, contentType: "multipart/form-data; boundary=\(boundary)")
        let value = try JSONDecoder().decode(JSONValue.self, from: response.data)
        let url = value["source_url"]?.string ?? value["url"]?.string ?? ""
        guard url.hasSuffix("/" + name) else { throw GammaError.invalid("The origin assigned a different asset name; sync paused to avoid a broken reference.") }
    }
}

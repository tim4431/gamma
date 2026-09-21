import UIKit
import WebKit

enum ServerAddress {
    static func parse(_ text: String) -> URL? {
        guard var parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path == "" || parts.path == "/" else { return nil }
        parts.scheme = "https"; parts.host = host.lowercased(); parts.path = "/"
        return parts.url
    }
    static func sameOrigin(_ left: URL, _ right: URL) -> Bool {
        left.scheme?.lowercased() == right.scheme?.lowercased() && left.host?.lowercased() == right.host?.lowercased()
            && (left.port ?? 443) == (right.port ?? 443)
    }
}

@MainActor
private final class WeakInkHandler: NSObject, WKScriptMessageHandlerWithReply {
    weak var owner: WorkspaceController?
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard let owner else { replyHandler(nil, "Workspace closed"); return }
        owner.receive(message, reply: replyHandler)
    }
}

@MainActor
final class WorkspaceController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var web: WKWebView!
    private var server: URL?
    private var editor: InkEditorController?
    private var openReply: ((Any?, String?) -> Void)?
    private var downloadURL: URL?

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Gamma"
        view.backgroundColor = .systemBackground
        let handler = WeakInkHandler(); handler.owner = self
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.addScriptMessageHandler(handler, contentWorld: .page, name: "gammaInk")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self; web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web)
        NSLayoutConstraint.activate([web.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor), web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            web.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor)])
        navigationItem.leftBarButtonItem = UIBarButtonItem(title: "Server", style: .plain, target: self, action: #selector(chooseServer))
        navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .refresh, target: self, action: #selector(reload))
        if let saved = UserDefaults.standard.string(forKey: "gamma.server"), let url = ServerAddress.parse(saved) {
            server = url; web.load(URLRequest(url: url))
        }
    }
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        if server == nil && presentedViewController == nil { chooseServer() }
    }
    @objc private func reload() { if editor == nil { web.reload() } }
    @objc private func chooseServer() {
        guard editor == nil else { return }
        let alert = UIAlertController(title: "Connect to Gamma", message: "Enter your Gamma server's HTTPS address. Sign in using the usual Gamma login.", preferredStyle: .alert)
        alert.addTextField { field in
            field.placeholder = "https://gamma.example.com"; field.text = self.server?.absoluteString
            field.keyboardType = .URL; field.autocapitalizationType = .none; field.autocorrectionType = .no
        }
        if server != nil { alert.addAction(UIAlertAction(title: "Cancel", style: .cancel)) }
        alert.addAction(UIAlertAction(title: "Connect", style: .default) { [weak self, weak alert] _ in
            guard let self else { return }
            guard let url = ServerAddress.parse(alert?.textFields?.first?.text ?? "") else {
                self.showError("Use an HTTPS server address without a path, password, query or fragment.")
                return
            }
            self.server = url
            UserDefaults.standard.set(url.absoluteString, forKey: "gamma.server")
            self.web.load(URLRequest(url: url))
        })
        present(alert, animated: true)
    }
    private func showError(_ message: String) {
        let alert = UIAlertController(title: "Gamma", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { [weak self] _ in
            if self?.server == nil { self?.chooseServer() }
        })
        if presentedViewController == nil { present(alert, animated: true) }
    }

    func receive(_ message: WKScriptMessage, reply: @escaping (Any?, String?) -> Void) {
        guard message.frameInfo.isMainFrame, let server, let source = message.frameInfo.request.url,
              ServerAddress.sameOrigin(server, source), let current = web.url, ServerAddress.sameOrigin(server, current),
              let body = message.body as? [String: Any], let action = body["action"] as? String else {
            reply(nil, "Only the connected Gamma workspace may open handwriting."); return
        }
        if action == "open" {
            guard editor == nil, presentedViewController == nil else { reply(nil, "Handwriting is already open."); return }
            do {
                let data = try JSONSerialization.data(withJSONObject: body)
                guard data.count <= 64 * 1024 * 1024 else { throw InkFailure("This page is too large to open.") }
                let request = try JSONDecoder().decode(InkRequest.self, from: data)
                try request.validate()
                let next = try InkEditorController(request: request, origin: server.absoluteString)
                editor = next; openReply = reply
                next.onClose = { [weak self] in self?.closeEditor() }
                next.onSave = { [weak self] detail in
                    guard let self else { return }
                    self.web.callAsyncJavaScript("window.dispatchEvent(new CustomEvent('gamma-native-ink-save', {detail}));",
                        arguments: ["detail": detail], in: nil, contentWorld: .page) { [weak self] result in
                        if case .failure(let error) = result { self?.editor?.failed("The workspace could not receive the drawing: \(error.localizedDescription)") }
                    }
                }
                let nav = UINavigationController(rootViewController: next)
                nav.modalPresentationStyle = .fullScreen; nav.isModalInPresentation = true
                present(nav, animated: true)
            } catch { reply(nil, error.localizedDescription) }
        } else {
            guard let editor, body["requestId"] as? String == editor.request.requestId else { reply(nil, "No matching editor."); return }
            if action == "saved" { editor.saved(); reply(["ok": true], nil) }
            else if action == "failed" { editor.failed(body["error"] as? String ?? "Save failed. Your draft is still on this iPad."); reply(["ok": true], nil) }
            else { reply(nil, "Unknown handwriting action.") }
        }
    }
    private func closeEditor() {
        editor = nil
        let reply = openReply; openReply = nil
        dismiss(animated: true) { reply?(["closed": true], nil) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        // Subframes never receive bridge privileges (receive checks mainFrame).
        if action.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        if url.scheme == "blob" && action.shouldPerformDownload { decisionHandler(.download); return }
        guard let server, ServerAddress.sameOrigin(server, url) else {
            if action.navigationType == .linkActivated, ["https", "http", "mailto"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
            decisionHandler(.cancel); return
        }
        if editor != nil { closeEditor() }
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url else { return nil }
        if let server, ServerAddress.sameOrigin(server, url) { webView.load(navigationAction.request) }
        else if ["https", "http"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
        return nil
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { showError(error.localizedDescription) }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if editor != nil { closeEditor() }
        webView.reload()
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let name = (suggestedFilename as NSString).lastPathComponent
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let url = directory.appendingPathComponent(name.isEmpty ? "Gamma-export" : name)
            downloadURL = url; completionHandler(url)
        } catch { completionHandler(nil); showError(error.localizedDescription) }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let url = downloadURL else { return }
        downloadURL = nil
        let sheet = UIActivityViewController(activityItems: [url], applicationActivities: nil)
        sheet.popoverPresentationController?.barButtonItem = navigationItem.rightBarButtonItem
        sheet.completionWithItemsHandler = { _, _, _, _ in try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        present(sheet, animated: true)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { showError(error.localizedDescription) }
}

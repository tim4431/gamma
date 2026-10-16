import SafariServices
import UIKit
import WebKit

/// scheme://host:port, with the default port filled in, so a URL and a
/// WebKit security origin compare equal when a browser would say so.
struct Origin: Equatable {
    let scheme: String
    let host: String
    let port: Int

    init(scheme: String, host: String, port: Int?) {
        self.scheme = scheme.lowercased()
        // an IPv6 address comes with brackets from a URL and without from WebKit
        self.host = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        self.port = port ?? (self.scheme == "http" ? 80 : 443)
    }

    init?(_ url: URL) {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: true),
              let scheme = parts.scheme, let host = parts.host, !host.isEmpty else { return nil }
        self.init(scheme: scheme, host: host, port: parts.port)
    }

    /// WebKit reports the default port as 0.
    init(_ origin: WKSecurityOrigin) {
        self.init(scheme: origin.protocol, host: origin.host, port: origin.port == 0 ? nil : origin.port)
    }

    /// "https://gamma.example.org" (the port only when it is not the default).
    var string: String {
        let defaultPort = scheme == "http" ? 80 : 443
        let host = self.host.contains(":") ? "[\(self.host)]" : self.host
        return "\(scheme)://\(host)" + (port == defaultPort ? "" : ":\(port)")
    }
}

/// Where a navigation goes, decided from its URL alone (tested). The same
/// rules as the desktop shell's navigation guard (desktop/main.js): the
/// server's pages stay, anything else leaves the app's window.
enum Route: Equatable {
    /// In the web view.
    case load
    /// The page's Gamma Cloud sign-in start, which the shell takes over (CloudSignIn).
    case cloudSignIn
    /// Another site: in a Safari view over the app.
    case browser
    /// mailto:, tel: and other apps' links: handed to the system.
    case system

    static func of(_ url: URL, server: Origin?, mainFrame: Bool) -> Route {
        switch url.scheme?.lowercased() {
        case "http", "https":
            if let server, Origin(url) == server {
                return url.path == CloudWire.startPath ? .cloudSignIn : .load
            }
            // an embed inside the page (a video, a paper's preview) stays where it is
            return mainFrame ? .browser : .load
        case "about", "blob", "data", "javascript":
            return .load
        default:
            return .system
        }
    }
}

/// The web view's navigation and UI delegate: other sites in a Safari view,
/// `target=_blank` the same way, downloads into the share sheet, the Gamma
/// Cloud start into CloudSignIn, the microphone for the server's recordings,
/// and the web content process restarted when the system ends it.
@MainActor
final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    weak var presenter: UIViewController?
    var server: Origin?
    var onCloudSignIn: ((URL) -> Void)?
    /// A main-frame load failed (the message), or a page arrived (nil).
    var onLoad: ((String?) -> Void)?
    var onTerminate: (() -> Void)?

    private var downloads: [ObjectIdentifier: URL] = [:]

    // --- navigations -------------------------------------------------------------------

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // An <a download> (the web app's exports: a fetched blob clicked as a link).
        if navigationAction.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        // No target frame: a new window, which createWebViewWith answers.
        guard let url = navigationAction.request.url, let frame = navigationAction.targetFrame else {
            decisionHandler(.allow)
            return
        }
        switch Route.of(url, server: server, mainFrame: frame.isMainFrame) {
        case .load:
            decisionHandler(.allow)
        case .cloudSignIn:
            decisionHandler(.cancel)
            onCloudSignIn?(url)
        case .browser:
            decisionHandler(.cancel)
            openOutside(url)
        case .system:
            decisionHandler(.cancel)
            UIApplication.shared.open(url)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        let attachment = disposition.lowercased().hasPrefix("attachment")
        decisionHandler(attachment || !navigationResponse.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        onLoad?(nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard !Self.isInterruption(error) else { return }
        onLoad?(error.localizedDescription)
    }

    /// Not failures: a navigation cancelled here (a route above) or turned
    /// into a download.
    static func isInterruption(_ error: Error) -> Bool {
        let e = error as NSError
        return (e.domain == NSURLErrorDomain && e.code == NSURLErrorCancelled)
            || (e.domain == "WebKitErrorDomain" && e.code == 102) // frame load interrupted by a policy change
    }

    /// The system ended the page's process (memory, mostly): the page comes
    /// back, and the web app restores where it was as after any reload.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        onTerminate?()
    }

    // --- new windows, dialogs, the microphone -------------------------------------------

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            switch url.scheme?.lowercased() {
            case "http", "https": openOutside(url)
            case "about", "blob", "data", "javascript", nil: break // nothing a Safari view can open
            default: UIApplication.shared.open(url)
            }
        }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        guard present(alert) else { return completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
        guard present(alert) else { return completionHandler(false) }
    }

    /// The server's recordings: no second question after the system's own.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(server == Origin(origin) ? .grant : .prompt)
    }

    // --- downloads: a file in the temporary folder, then the share sheet -------------------

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        // A folder of its own per download, so the suggested name is kept as it is.
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("Downloads", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        } catch {
            return completionHandler(nil)
        }
        let name = suggestedFilename.replacingOccurrences(of: "/", with: "-")
        let file = folder.appendingPathComponent(name.isEmpty ? "Download" : name)
        downloads[ObjectIdentifier(download)] = file
        completionHandler(file)
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let file = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        share(file)
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloads.removeValue(forKey: ObjectIdentifier(download))
        alert("The download did not finish: \(error.localizedDescription)")
    }

    // --- presenting ---------------------------------------------------------------------

    func alert(_ message: String) {
        let alert = UIAlertController(title: "Gamma", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        present(alert)
    }

    private func openOutside(_ url: URL) {
        present(SFSafariViewController(url: url))
    }

    private func share(_ file: URL) {
        let sheet = UIActivityViewController(activityItems: [file], applicationActivities: nil)
        guard let top = topPresenter else { return }
        // On an iPad the share sheet is a popover and needs an anchor: the
        // middle of the window, with no arrow.
        if let popover = sheet.popoverPresentationController {
            popover.sourceView = top.view
            popover.sourceRect = CGRect(x: top.view.bounds.midX, y: top.view.bounds.midY, width: 1, height: 1)
            popover.permittedArrowDirections = []
        }
        top.present(sheet, animated: true)
    }

    @discardableResult
    private func present(_ controller: UIViewController) -> Bool {
        guard let top = topPresenter else { return false }
        top.present(controller, animated: true)
        return true
    }

    /// Whatever is frontmost (a Safari view, a sheet) presents the next one.
    private var topPresenter: UIViewController? {
        var top = presenter
        while let next = top?.presentedViewController, !next.isBeingDismissed { top = next }
        return top
    }
}

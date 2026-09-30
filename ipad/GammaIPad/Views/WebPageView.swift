import SwiftUI
import WebKit

/// Gamma's web app for what the iPad does not do itself (search, chat,
/// sharing, settings): the server's page in a web view with the session of
/// the sign-in (the default website data store). Online only; it writes to
/// the server, and the next round brings its changes here.
struct WebPageView: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            WebView(url: url)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle("Gamma")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}

struct WebView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        let view = WKWebView(frame: .zero, configuration: config)
        view.allowsBackForwardNavigationGestures = true
        view.load(URLRequest(url: url))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}
}

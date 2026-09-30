import SwiftUI
import WebKit

/// Connect this iPad to a Gamma server: sign in on the server's own web page
/// (any sign-in it offers — password or Gamma Cloud), pick a workspace, and
/// the app keeps a write token for it (the credential a desktop clone
/// keeps, docs/dev/mirror.md "Credentials"). The web session stays in the
/// app's web views for "Open on the web".
struct ConnectView: View {
    @EnvironmentObject private var model: AppModel
    @State private var address = ""
    @State private var signIn: SignInTarget?
    @State private var found: Found?
    @State private var busy = false
    @State private var error = ""

    struct SignInTarget: Identifiable {
        let id = UUID()
        let url: URL
    }

    struct Found {
        let base: URL
        let cookie: String
        let session: ServerSetup.Session
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://gamma.example.org", text: $address)
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit(begin)
                    Button("Sign in", action: begin)
                        .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty || busy)
                } header: {
                    Text("Your Gamma server")
                } footer: {
                    Text("The server's address, as you open it in a browser. You sign in on its own page.")
                }
                if let found {
                    Section {
                        ForEach(found.session.workspaces) { ws in
                            Button {
                                pick(ws, found)
                            } label: {
                                HStack {
                                    Image(systemName: ws.personal ? "person" : "person.2")
                                    Text(ws.name.isEmpty ? "Workspace" : ws.name)
                                    Spacer()
                                    Text(ws.role).foregroundStyle(.secondary)
                                }
                            }
                            .disabled(busy || ws.role == "viewer")
                        }
                    } header: {
                        Text("Keep which workspace on this iPad?")
                    } footer: {
                        Text("Signed in as \(found.session.user). The iPad keeps a copy of the workspace and syncs it with the server.")
                    }
                }
                if !error.isEmpty {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Gamma")
            .sheet(item: $signIn) { target in
                NavigationStack {
                    SignInWeb(url: target.url) { cookie in
                        Task { await signedIn(base: target.url, cookie: cookie) }
                    }
                    .navigationTitle("Sign in")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { signIn = nil } }
                    }
                }
            }
        }
    }

    private func begin() {
        var text = address.trimmingCharacters(in: .whitespaces)
        if !text.contains("://") { text = "https://" + text }
        guard let url = URL(string: text), url.scheme == "https" || url.scheme == "http", url.host != nil else {
            error = "That is not a web address."
            return
        }
        error = ""
        found = nil
        signIn = SignInTarget(url: url)
    }

    @MainActor
    private func signedIn(base: URL, cookie: String) async {
        guard found == nil else { return }
        do {
            guard let session = try await ServerSetup.session(base: base, cookie: cookie) else { return }
            found = Found(base: base, cookie: cookie, session: session)
            signIn = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func pick(_ ws: ServerSetup.Workspace, _ found: Found) {
        busy = true
        Task { @MainActor in
            defer { busy = false }
            do {
                let token = try await ServerSetup.mintToken(base: found.base, cookie: found.cookie, workspace: ws.id,
                                                            device: UIDevice.current.name)
                let connection = Connection(id: UUID().uuidString, server: found.base, user: found.session.user,
                                            workspace: ws.id, workspaceName: ws.name)
                try model.connect(connection, token: token)
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// The server's web page in a web view (the default website data store, so
/// the session lasts for "Open on the web"). The page signs in without a
/// navigation of its own (a fetch), so the cookie store is looked at every
/// second as well as after each load; `onSession` gets the `session`
/// cookie's value whenever there is one (the caller checks it is live).
struct SignInWeb: UIViewRepresentable {
    let url: URL
    let onSession: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onSession: onSession) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.load(URLRequest(url: url))
        context.coordinator.watch(view)
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    static func dismantleUIView(_ view: WKWebView, coordinator: Coordinator) {
        coordinator.stop()
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        let onSession: (String) -> Void
        private var timer: Timer?
        private var last = ""

        init(onSession: @escaping (String) -> Void) { self.onSession = onSession }

        func watch(_ view: WKWebView) {
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self, weak view] _ in
                guard let view else { return }
                self?.check(view)
            }
        }

        func stop() {
            timer?.invalidate()
            timer = nil
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { check(webView) }

        private func check(_ webView: WKWebView) {
            guard let host = webView.url?.host?.lowercased() else { return }
            webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self] cookies in
                let mine = cookies.first { cookie in
                    let domain = cookie.domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
                    return cookie.name == "session" && (host == domain || host.hasSuffix("." + domain))
                }
                guard let self, let value = mine?.value, value != self.last else { return }
                self.last = value
                DispatchQueue.main.async { self.onSession(value) }
            }
        }
    }
}

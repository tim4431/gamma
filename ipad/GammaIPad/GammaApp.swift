import SwiftUI
import UniformTypeIdentifiers
import GammaCore
import Security

func jsonProperties(_ value: [String: Any]) throws -> [String: JSONValue] {
    try JSONDecoder().decode([String: JSONValue].self, from: JSONSerialization.data(withJSONObject: value))
}
func propertyObject(_ value: [String: JSONValue]) -> [String: Any] {
    (try? JSONSerialization.jsonObject(with: JSONEncoder().encode(value))) as? [String: Any] ?? [:]
}
func gammaID() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "") }
let defaultPaper: [String: Any] = ["width": 612, "height": 792, "color": "#ffffff", "pattern": "ruled", "spacing": 24, "line_color": "#cbd5e1"]

enum MirrorCredential {
    static func set(_ value: String, key: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "GammaMirror", kSecAttrAccount as String: key]
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = Data(value.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw InkEngineError.failure("Could not keep the connection in Keychain (\(status)).") }
    }
    static func get(_ key: String) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "GammaMirror", kSecAttrAccount as String: key,
                                  kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

@MainActor
final class LibraryModel: ObservableObject {
    @Published var documents: [GammaDocument] = []
    @Published var error: String?
    @Published var busy = false
    @Published var mirror: GammaMirror?
    @Published var conflicts: [GammaConflict] = []
    @Published var profiles: [String] = UserDefaults.standard.stringArray(forKey: "gamma.profiles") ?? ["On this iPad"]
    @Published var profile = UserDefaults.standard.string(forKey: "gamma.profile") ?? "On this iPad"
    private(set) var repository: GammaRepository?
    private(set) var directory: URL?
    func open(_ profile: String? = nil) async {
        do {
            if let profile { self.profile = profile }
            let root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            // Profile identifiers are opaque UUIDs, never server-provided paths.
            let identifier = UserDefaults.standard.string(forKey: "gamma.profile.\(self.profile)") ?? gammaID()
            UserDefaults.standard.set(identifier, forKey: "gamma.profile.\(self.profile)")
            let directory = root.appendingPathComponent("Gamma", isDirectory: true).appendingPathComponent(identifier, isDirectory: true)
            let next = try GammaRepository(directory: directory)
            if let token = MirrorCredential.get(identifier) { await next.setMirrorToken(token) }
            repository = next; self.directory = directory
            UserDefaults.standard.set(self.profile, forKey: "gamma.profile")
            await refresh()
        } catch { self.error = error.localizedDescription }
    }
    func refresh() async {
        guard let repository else { return }
        do { documents = try await repository.documents(); mirror = try await repository.mirror(); conflicts = try await repository.conflicts() }
        catch { self.error = error.localizedDescription }
    }
    func notebook() async {
        guard let repository else { return }
        do { _ = try await repository.createNotebook(title: "Untitled notebook", paper: jsonProperties(defaultPaper)); await refresh() }
        catch { self.error = error.localizedDescription }
    }
    func importPDF(_ url: URL) async {
        guard let repository else { return }
        let scoped = url.startAccessingSecurityScopedResource(); defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do { _ = try await repository.importPDF(data: Data(contentsOf: url), title: url.deletingPathExtension().lastPathComponent); await refresh() }
        catch { self.error = error.localizedDescription }
    }
    func sync() async {
        guard let repository else { return }; busy = true; defer { busy = false }
        do { mirror = try await repository.sync(); await refresh() }
        catch { self.error = error.localizedDescription; await refresh() }
    }
    func mirrorAction(_ action: String) async {
        guard let repository else { return }
        do {
            switch action {
            case "off": try await repository.detachMirror()
            case "on": try await repository.reattachMirror()
            case "remove": try await repository.removeMirror()
            default: try await repository.setMirrorMode(action)
            }
            await refresh()
        } catch { self.error = error.localizedDescription }
    }
    func resolve(_ conflict: GammaConflict, choice: String) async {
        guard let repository else { return }
        do { try await repository.resolveConflict(id: conflict.id, choice: choice); await refresh() }
        catch { self.error = error.localizedDescription }
    }
    func connect(origin: String, account: String, workspace: String, token: String) async throws {
        guard let url = URL(string: origin), url.scheme == "https", url.host != nil,
              url.user == nil, url.password == nil, !account.isEmpty, !workspace.isEmpty, !token.isEmpty else {
            throw InkEngineError.failure("Enter an HTTPS server, account, workspace and access token.")
        }
        let label = "\(url.host!) / \(account) / \(workspace)"
        await open(label)
        guard let repository else { throw InkEngineError.failure("Could not open the local library.") }
        try await repository.configureMirror(origin: url, account: account, workspaceID: workspace, token: token)
        let identifier = UserDefaults.standard.string(forKey: "gamma.profile.\(label)")!
        try MirrorCredential.set(token, key: identifier)
        if !profiles.contains(label) { profiles.append(label); UserDefaults.standard.set(profiles, forKey: "gamma.profiles") }
        await sync()
    }
}

@main
struct GammaApp: App {
    var body: some Scene { WindowGroup { LibraryView() } }
}

struct LibraryView: View {
    @StateObject private var model = LibraryModel()
    @State private var importing = false
    @State private var connecting = false
    @State private var selected: GammaDocument?
    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Library", selection: Binding(get: { model.profile }, set: { value in Task { await model.open(value) } })) {
                        ForEach(model.profiles, id: \.self) { Text($0) }
                    }
                    if let mirror = model.mirror {
                        Label(mirror.lastError ?? (mirror.pending ? "Changes waiting to sync" : "Library is available offline"),
                              systemImage: mirror.lastError == nil ? "arrow.triangle.2.circlepath" : "exclamationmark.circle")
                        Button(model.busy ? "Syncing…" : "Sync now") { Task { await model.sync() } }.disabled(model.busy)
                        Menu("Connection: \(mirror.mode == "two-way" ? "Both ways" : mirror.mode == "pull" ? "Download only" : "Detached")") {
                            if mirror.mode == "off" { Button("Reattach") { Task { await model.mirrorAction("on") } } }
                            else {
                                Button("Both ways") { Task { await model.mirrorAction("two-way") } }
                                Button("Download only") { Task { await model.mirrorAction("pull") } }
                                Button("Detach") { Task { await model.mirrorAction("off") } }
                            }
                            Button("Remove connection; keep local files") { Task { await model.mirrorAction("remove") } }
                        }.disabled(model.busy)
                    } else { Label("Stored on this iPad", systemImage: "ipad") }
                }
                if !model.conflicts.isEmpty {
                    Section("Review sync conflicts") {
                        ForEach(model.conflicts) { conflict in
                            DisclosureGroup(conflict.kind.capitalized) {
                                if !conflict.mine.isEmpty { Text("On this iPad\n\(conflict.mine)") }
                                if !conflict.theirs.isEmpty { Text("On the server\n\(conflict.theirs)") }
                                if ["merged", "diverged"].contains(conflict.kind) {
                                    Button("Keep my text") { Task { await model.resolve(conflict, choice: "mine") } }
                                    Button("Keep server text") { Task { await model.resolve(conflict, choice: "theirs") } }
                                }
                                Button("Keep current result") { Task { await model.resolve(conflict, choice: "keep") } }
                            }
                        }
                    }
                }
                Section("Documents") {
                    if model.documents.isEmpty { ContentUnavailableView("Your library is empty", systemImage: "books.vertical", description: Text("Create a notebook or import a PDF.")) }
                    ForEach(model.documents) { document in
                        Button { selected = document } label: {
                            Label(document.title, systemImage: propertyObject(document.properties)["notebook"] == nil ? "doc.richtext" : "book.closed")
                        }
                    }
                }
            }
            .navigationTitle("Gamma")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("New notebook", systemImage: "book.closed.badge.plus") { Task { await model.notebook() } }
                        Button("Import PDF", systemImage: "doc.badge.plus") { importing = true }
                        Button("Connect a library", systemImage: "network") { connecting = true }
                    } label: { Label("Add", systemImage: "plus") }
                }
            }
            .refreshable { if model.mirror != nil { await model.sync() } else { await model.refresh() } }
            .task { await model.open() }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.pdf]) { result in
                switch result { case .success(let url): Task { await model.importPDF(url) }; case .failure(let error): model.error = error.localizedDescription }
            }
            .sheet(isPresented: $connecting) { ConnectLibraryView(model: model) }
            .fullScreenCover(item: $selected, onDismiss: { Task { await model.refresh() } }) { document in
                if let repository = model.repository, let directory = model.directory {
                    ReaderScreen(repository: repository, documentID: document.id, directory: directory) { selected = nil }
                }
            }
            .alert("Gamma", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
        }
    }
}

struct ConnectLibraryView: View {
    @ObservedObject var model: LibraryModel
    @Environment(\.dismiss) private var dismiss
    @State private var origin = "https://"
    @State private var account = ""
    @State private var workspace = ""
    @State private var token = ""
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                Section("Server library") {
                    TextField("HTTPS server", text: $origin).textContentType(.URL).keyboardType(.URL)
                    TextField("Account", text: $account).textContentType(.username)
                    TextField("Workspace", text: $workspace)
                    SecureField("Access token", text: $token)
                }.textInputAutocapitalization(.never).autocorrectionDisabled()
                Section { Text("Gamma keeps a local copy for reading and editing offline. Connect with an access token from your server account. Your token is stored in iPad Keychain.") }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle("Connect library")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(busy) }
                ToolbarItem(placement: .confirmationAction) { Button(busy ? "Connecting…" : "Connect") {
                    busy = true
                    Task { do { try await model.connect(origin: origin, account: account, workspace: workspace, token: token); dismiss() }
                        catch { self.error = error.localizedDescription }; busy = false }
                }.disabled(busy) }
            }
        }
    }
}

struct ReaderScreen: UIViewControllerRepresentable {
    let repository: GammaRepository
    let documentID: String
    let directory: URL
    let close: () -> Void
    func makeUIViewController(context: Context) -> UINavigationController {
        UINavigationController(rootViewController: ReaderController(repository: repository, documentID: documentID, directory: directory, close: close))
    }
    func updateUIViewController(_ uiViewController: UINavigationController, context: Context) {}
}

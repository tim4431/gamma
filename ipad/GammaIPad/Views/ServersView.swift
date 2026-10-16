import SwiftUI
import UIKit

/// The server picker: shown at first launch, and as a sheet when the web
/// app's "Server…" row posts `servers` (Bridge). "This iPad" is the server
/// inside the app (LocalServer); "Servers" are the others, by address, as
/// the desktop lists its servers. Signing in to another server happens on
/// its own page in the shell, with whatever it offers.
struct ServersView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var local: LocalServer
    @State private var address = ""
    @State private var error = ""
    @State private var dataSize: Int64?
    @State private var editing: URL?
    @State private var editText = ""

    var body: some View {
        NavigationStack {
            Form {
                thisIPad
                if !model.remotes.isEmpty {
                    Section("Servers") {
                        ForEach(model.remotes, id: \.self) { url in remoteRow(url) }
                    }
                }
                addForm
            }
            .navigationTitle("Servers")
            .toolbar {
                // As a sheet over the shell there is a server to go back to.
                if model.current != nil {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { model.choosingServer = false }
                    }
                }
            }
            .alert("Server address", isPresented: isEditing) {
                TextField("https://gamma.example.org", text: $editText)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Save", action: saveEdit)
                Button("Cancel", role: .cancel) { editing = nil }
            }
            .onAppear { model.probe(model.remotes) }
            .task { dataSize = await local.dataSize() }
        }
    }

    // --- This iPad -----------------------------------------------------------------------

    private var thisIPad: some View {
        Section {
            HStack(spacing: 12) {
                StatusDot(color: localColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("This iPad")
                    Text(localDetail).font(.footnote).foregroundStyle(.secondary)
                }
                Spacer()
                if model.current == .local { checkmark }
                Button("Open") { model.open(.local) }
                    .buttonStyle(.borderless)
                    .disabled(isFailed)
            }
            if case let .failed(message) = local.state {
                VStack(alignment: .leading, spacing: 8) {
                    Text(message).foregroundStyle(.red)
                    let tail = local.logTail()
                    if !tail.isEmpty {
                        Text(tail)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    Text("Close Gamma and open it again to try once more.").font(.footnote)
                }
            }
            if let login = local.login {
                LabeledContent("Account", value: login.username)
                Button("Copy the password") { UIPasteboard.general.string = login.password }
            } else if local.url != nil {
                // LocalServer.start: a data directory whose password this iPad does not keep.
                Text("This iPad keeps no password for this server's account, so you sign in on its page. A password the server made itself is in its log, Gamma/local/logs/server.log in the Files app.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("This iPad")
        } footer: {
            Text("Gamma's own server, inside this app: it works offline and keeps its data on this iPad. Offline copies of another server's workspaces are made in Gamma itself, as on the desktop.")
        }
    }

    private var isFailed: Bool {
        if case .failed = local.state { return true }
        return false
    }

    private var localColor: Color {
        switch local.state {
        case .running: return .green
        case .starting: return .orange
        case .failed: return .red
        case .stopped: return .gray
        }
    }

    private var localDetail: String {
        let size = dataSize.map { ByteCountFormatter.string(fromByteCount: $0, countStyle: .file) }
        let status: String
        switch local.state {
        case let .running(url): status = url.absoluteString
        case .starting: status = local.slowStart ? "Still starting…" : "Starting…"
        case .failed: status = "Did not start"
        case .stopped: status = "Starts when you open it"
        }
        return [status, size].compactMap { $0 }.joined(separator: " · ")
    }

    // --- the other servers ---------------------------------------------------------------

    private func remoteRow(_ url: URL) -> some View {
        HStack(spacing: 12) {
            StatusDot(color: model.reachable[url].map { $0 ? Color.green : Color.red } ?? .gray)
            Text(url.absoluteString)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            if model.current == .remote(url) { checkmark }
            Button("Open") { model.open(.remote(url)) }
                .buttonStyle(.borderless)
        }
        .swipeActions(edge: .trailing) {
            Button("Remove", role: .destructive) { model.remove(url) }
            Button("Edit") { startEditing(url) }
                .tint(.blue)
        }
        .contextMenu {
            Button("Edit address") { startEditing(url) }
            Button("Remove", role: .destructive) { model.remove(url) }
        }
    }

    private var addForm: some View {
        Section {
            TextField("https://gamma.example.org", text: $address)
                .keyboardType(.URL)
                .textContentType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .onSubmit(add)
            Button("Add and open", action: add)
                .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty)
            if !error.isEmpty {
                Text(error).foregroundStyle(.red)
            }
        } header: {
            Text("Add a server")
        } footer: {
            Text("The server's address, as you open it in a browser. You sign in on its own page.")
        }
    }

    private func add() {
        guard let url = ServerAddress.parse(address) else {
            error = "That is not a web address."
            return
        }
        error = ""
        address = ""
        model.open(.remote(url))
    }

    private var isEditing: Binding<Bool> {
        Binding(get: { editing != nil }, set: { if !$0 { editing = nil } })
    }

    private func startEditing(_ url: URL) {
        editText = url.absoluteString
        editing = url
    }

    private func saveEdit() {
        defer { editing = nil }
        guard let old = editing else { return }
        guard let new = ServerAddress.parse(editText) else {
            error = "That is not a web address."
            return
        }
        error = ""
        model.replace(old, with: new)
    }

    private var checkmark: some View {
        Image(systemName: "checkmark").foregroundStyle(.tint)
    }
}

/// The page over the shell while the local server starts, or when it could not.
struct LocalStartView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var local: LocalServer

    var body: some View {
        Group {
            if case let .failed(message) = local.state {
                ContentUnavailableView {
                    Label("Gamma could not start on this iPad", systemImage: "exclamationmark.triangle")
                } description: {
                    Text(message + "\nClose Gamma and open it again to try once more. The server's log is in the server picker.")
                } actions: {
                    Button("Choose a server") { model.choosingServer = true }
                        .buttonStyle(.borderedProminent)
                }
            } else if local.slowStart {
                VStack(spacing: 12) {
                    ProgressView("Still starting Gamma on this iPad…")
                    Text("The first start after an install or an update can take a few minutes.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } else {
                ProgressView("Starting Gamma on this iPad…")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(uiColor: .systemBackground))
    }
}

/// A server's state at a glance: green up, orange starting, red down, grey unknown.
private struct StatusDot: View {
    let color: Color

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 10, height: 10)
            .accessibilityHidden(true)
    }
}

import SwiftUI

/// The workspace kept on this iPad: its pages by folder, what a round is
/// doing, and the actions that make pages (a notebook, a text page).
struct LibraryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var rows: [Row] = []
    @State private var query = ""
    @State private var selection: String?
    @State private var confirmDisconnect = false
    @State private var showConflicts = false

    struct Row: Identifiable, Hashable {
        let id: String
        let title: String
        let folder: String
        let kind: String
    }

    private var sections: [(String, [Row])] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        let shown = q.isEmpty ? rows : rows.filter { $0.title.lowercased().contains(q) || $0.folder.lowercased().contains(q) }
        let groups = Dictionary(grouping: shown) { $0.folder.split(separator: ",").first.map(String.init) ?? "" }
        return groups.keys.sorted { a, b in a.isEmpty != b.isEmpty ? a.isEmpty : a.localizedStandardCompare(b) == .orderedAscending }
            .map { ($0, groups[$0] ?? []) }
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                ForEach(sections, id: \.0) { folder, pages in
                    Section(folder.isEmpty ? "Pages" : folder) {
                        ForEach(pages) { row in
                            Label(row.title, systemImage: icon(row.kind)).tag(row.id)
                        }
                    }
                }
            }
            .searchable(text: $query, prompt: "Search titles")
            .navigationTitle(model.replica?.connection.workspaceName ?? "Gamma")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { SyncButton() }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button { newNotebook() } label: { Label("New notebook", systemImage: "book.closed") }
                        Button { newPage() } label: { Label("New page", systemImage: "doc.text") }
                    } label: { Image(systemName: "plus") }
                }
                ToolbarItem(placement: .topBarTrailing) { accountMenu }
            }
            .overlay {
                if rows.isEmpty {
                    ContentUnavailableView(model.status.running ? "Bringing the workspace here…" : "No pages yet",
                                           systemImage: model.status.running ? "arrow.down.circle" : "tray")
                }
            }
        } detail: {
            if let selection {
                PageView(pageId: selection).id(selection)
            } else {
                ContentUnavailableView("Open a page", systemImage: "doc.richtext")
            }
        }
        .task(id: model.revision) { reload() }
        .sheet(isPresented: $showConflicts) { ConflictsView() }
        .confirmationDialog("Disconnect this iPad?", isPresented: $confirmDisconnect, titleVisibility: .visible) {
            Button("Disconnect, keep nothing here", role: .destructive) { model.disconnect(erase: true) }
        } message: {
            Text(model.pendingEdits > 0
                 ? "\(model.pendingEdits) page(s) have edits not sent yet: sync first, or they are lost."
                 : "The server keeps everything; the copy on this iPad is removed.")
        }
    }

    private var accountMenu: some View {
        Menu {
            if let c = model.replica?.connection {
                Section("\(c.user) · \(c.server.host ?? "")") {
                    Button { model.syncNow() } label: { Label("Sync now", systemImage: "arrow.triangle.2.circlepath") }
                    Button { showConflicts = true } label: { Label("Sync decisions", systemImage: "arrow.triangle.merge") }
                    Picker("Direction", selection: Binding(get: { c.mode }, set: { model.setMode($0) })) {
                        Text("Two-way").tag("two-way")
                        Text("Receive only").tag("pull")
                    }
                    Link(destination: c.server) { Label("Open Gamma on the web", systemImage: "safari") }
                }
            }
            Button(role: .destructive) { confirmDisconnect = true } label: { Label("Disconnect…", systemImage: "xmark.circle") }
        } label: { Image(systemName: "person.crop.circle") }
    }

    private func icon(_ kind: String) -> String {
        switch kind {
        case "pdf": return "doc.richtext"
        default: return "doc.text"
        }
    }

    private func reload() {
        rows = (model.replica?.libraryRows() ?? []).map {
            Row(id: $0.string("id"), title: $0.string("title"), folder: $0.string("folder"), kind: $0.string("kind"))
        }
        if let s = selection, !rows.contains(where: { $0.id == s }) { selection = nil }
    }

    private func newNotebook() {
        guard let replica = model.replica else { return }
        do {
            let id = try replica.edit("createNotebook", [["title": "Notebook"]]) as? String
            model.edited()
            reload()
            selection = id
        } catch {
            model.failure = error.localizedDescription
        }
    }

    private func newPage() {
        guard let replica = model.replica else { return }
        do {
            let id = try replica.edit("createPage", [["title": "Untitled"]]) as? String
            model.edited()
            reload()
            selection = id
        } catch {
            model.failure = error.localizedDescription
        }
    }
}

/// The round's state as a button: a spinner while it runs, the last error
/// or the time of the last round otherwise; a tap runs a round.
struct SyncButton: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Button {
            model.syncNow()
        } label: {
            HStack(spacing: 6) {
                if model.status.running {
                    ProgressView()
                    if !model.status.progress.isEmpty { Text(model.status.progress).font(.caption).monospacedDigit() }
                } else if !model.status.lastError.isEmpty {
                    Image(systemName: "exclamationmark.triangle").foregroundStyle(.orange)
                } else {
                    Image(systemName: model.pendingEdits > 0 ? "arrow.up.circle" : "checkmark.circle")
                }
            }
        }
        .help(help)
        .accessibilityLabel(help)
    }

    private var help: String {
        if model.status.running { return "Syncing…" }
        if !model.status.lastError.isEmpty { return "Sync problem: \(model.status.lastError)" }
        if let last = model.status.lastSync {
            return "Synced \(last.formatted(date: .omitted, time: .shortened))"
        }
        return "Sync now"
    }
}

/// The decisions rounds took on their own (sync_engine's conflicts: merged
/// texts, edits that beat deletions, pages restored) — to look at.
struct ConflictsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var items: [[String: Any]] = []

    var body: some View {
        NavigationStack {
            List {
                ForEach(items.indices, id: \.self) { i in
                    let c = items[i]
                    VStack(alignment: .leading, spacing: 4) {
                        Text(label(c.string("kind"))).font(.headline)
                        if !c.string("result").isEmpty { Text(c.string("result")).font(.callout) }
                        if !c.string("mine").isEmpty { Text("Here: \(c.string("mine"))").font(.caption).foregroundStyle(.secondary) }
                        if !c.string("theirs").isEmpty { Text("There: \(c.string("theirs"))").font(.caption).foregroundStyle(.secondary) }
                    }
                    .swipeActions {
                        Button("Done") {
                            try? model.replica?.store.resolveConflict((c["row"] as? Int64) ?? 0)
                            load()
                        }
                    }
                }
            }
            .overlay { if items.isEmpty { ContentUnavailableView("Nothing to look at", systemImage: "checkmark") } }
            .navigationTitle("Sync decisions")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Close") { dismiss() } } }
            .onAppear(perform: load)
        }
    }

    private func load() { items = (try? model.replica?.store.openConflicts()) ?? [] }

    private func label(_ kind: String) -> String {
        switch kind {
        case "merged": return "Both sides changed a text: merged"
        case "diverged": return "A text differed on both sides"
        case "kept_local_edit": return "Kept an edit made here the server deleted"
        case "restored_remote_edit": return "Kept an edit made there that was deleted here"
        case "page_restored": return "A page deleted there came back (edited here)"
        case "page_restored_from_remote": return "A page deleted here came back (edited there)"
        default: return kind
        }
    }
}

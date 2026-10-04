import SwiftUI

/// A page's notes as an outline: each block's text, editable here (sent as
/// an edit from the text it was edited from, so a text changed meanwhile
/// merges), handwriting groups and pages to write on as labelled rows (a tap
/// shows the drawing or the page; a group's play button replays its
/// writing), and a note or a page added at the end. While a note is edited
/// the editing bar sits over the keyboard (NoteEditBar).
struct NotesView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.undoManager) private var undoManager
    let pageId: String
    var onEdit: () -> Void
    var onJump: ((String) -> Void)? = nil
    var onReplay: ((String) -> Void)? = nil
    var replaying: String? = nil
    /// A page to write on, after the block named (nil: after the last page); → its id.
    var onAddPage: ((String?) -> String?)? = nil
    @State private var rows: [Row] = []
    @State private var drafts: [String: String] = [:]
    @State private var bases: [String: String] = [:]
    @FocusState private var focused: String?

    struct Row: Identifiable {
        let id: String
        let depth: Int
        let text: String
        let kind: String   // "note" | "ink" | "sheet"
        let label: String
        /// Siblings before and after it, and a parent above it: where it can be moved or nested.
        let hasPrev: Bool
        let hasNext: Bool
        var nested: Bool { depth > 0 }
    }

    var body: some View {
        List {
            ForEach(rows) { row in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: icon(row.kind)).foregroundStyle(.secondary).frame(width: 18)
                    VStack(alignment: .leading, spacing: 2) {
                        if row.kind != "note" {
                            HStack(spacing: 8) {
                                Button(row.label) { onJump?(row.id) }
                                    .font(.caption).foregroundStyle(.secondary).buttonStyle(.plain)
                                if row.kind == "ink", let onReplay {
                                    let playing = replaying == row.id
                                    Button { onReplay(row.id) } label: {
                                        Image(systemName: playing ? "stop.circle" : "play.circle")
                                    }
                                    .buttonStyle(.plain).foregroundStyle(.secondary)
                                    .help(playing ? "Stop the replay" : "Replay: watch the handwriting being written, stroke by stroke")
                                    .accessibilityLabel(playing ? "Stop replay" : "Replay handwriting")
                                }
                            }
                        }
                        TextField(row.kind == "note" ? "Note" : "Caption", text: binding(row), axis: .vertical)
                            .focused($focused, equals: row.id)
                            .onSubmit { commit(row.id) }
                    }
                }
                .padding(.leading, CGFloat(row.depth) * 16)
                .swipeActions {
                    if row.kind == "note" {
                        Button("Delete", role: .destructive) { delete(row.id) }
                    }
                }
            }
            Button { add() } label: { Label("Add a note", systemImage: "plus") }
            if let onAddPage {
                Button { _ = onAddPage(nil) } label: { Label("Add a page to write on", systemImage: "doc.badge.plus") }
            }
        }
        .listStyle(.plain)
        // in the safe area, which the keyboard takes: the bar rides on it
        .safeAreaInset(edge: .bottom) {
            if let id = focused, let row = rows.first(where: { $0.id == id }) {
                NoteEditBar(row: row, canAddPage: onAddPage != nil) { run($0, on: row) }
                    .padding(.bottom, 6)
            }
        }
        .onChange(of: focused) { old, _ in if let old { commit(old) } }
        .onAppear(perform: reload)
        .onChange(of: model.revision) { _, _ in reload() }
    }

    private func icon(_ kind: String) -> String {
        switch kind {
        case "ink": return "pencil.tip"
        case "sheet": return "doc"
        default: return "circle.fill"
        }
    }

    private func binding(_ row: Row) -> Binding<String> {
        Binding(get: { drafts[row.id] ?? row.text }, set: { drafts[row.id] = $0 })
    }

    private func reload() {
        guard let replica = model.replica, let snapshot = replica.store.snapshot(pageId),
              let tree = (try? replica.pure("tree", [snapshot, pageId])) as? [[String: Any]] else { rows = []; return }
        var out: [Row] = []
        var sheets = 0
        func walk(_ list: [[String: Any]], _ depth: Int) {
            for (i, node) in list.enumerated() {
                let props = node.dict("properties")
                let id = node.string("id")
                let kind: String
                var label = ""
                if !props.dict("sheet").isEmpty {
                    // a page, at any depth, numbered in document order (notebook.js sheetsOf)
                    sheets += 1
                    kind = "sheet"
                    label = "Page \(sheets)"
                } else if props["ink_url"] != nil {
                    kind = "ink"
                    let page = props.dict("pdf_position").int("pageNumber").map { " · p. \($0)" } ?? ""
                    label = "Handwriting\(page), \(props.int("ink_strokes") ?? 0) strokes"
                } else {
                    kind = "note"
                }
                out.append(Row(id: id, depth: depth, text: node.string("content"), kind: kind, label: label,
                               hasPrev: i > 0, hasNext: i < list.count - 1))
                if focused != id { bases[id] = node.string("content") }
                walk(node["children"] as? [[String: Any]] ?? [], depth + 1)
            }
        }
        walk(tree, 0)
        rows = out
        // a draft the store caught up with is done
        for (id, text) in drafts where bases[id] == text && focused != id { drafts[id] = nil }
    }

    private func commit(_ id: String) {
        guard let replica = model.replica, let text = drafts[id], let base = bases[id], text != base else { return }
        do {
            try replica.edit("setText", [pageId, id, text, base])
            bases[id] = text
            onEdit()
        } catch {
            model.failure = error.localizedDescription
        }
    }

    private func add() {
        guard let replica = model.replica else { return }
        do {
            let id = try replica.edit("addNote", [pageId, ["content": ""]]) as? String
            onEdit()
            reload()
            focused = id
        } catch {
            model.failure = error.localizedDescription
        }
    }

    private func delete(_ id: String) {
        guard let replica = model.replica else { return }
        do {
            try replica.edit("deleteBlock", [pageId, id])
            onEdit()
            reload()
        } catch {
            model.failure = error.localizedDescription
        }
    }

    // --- the editing bar ---------------------------------------------------------------

    private func run(_ command: NoteEditBar.Command, on row: Row) {
        let id = row.id
        switch command {
        case .outdent: reshape("Outdent", focus: id) { try $0.edit("outdent", [pageId, id]) }
        case .indent: reshape("Indent", focus: id) { try $0.edit("indent", [pageId, id]) }
        case .moveUp: reshape("Move Up", focus: id) { try $0.edit("moveBlock", [pageId, id, -1]) }
        case .moveDown: reshape("Move Down", focus: id) { try $0.edit("moveBlock", [pageId, id, 1]) }
        case .newBelow:
            guard let fresh = (try? model.replica?.pure("makeId", [])) as? String else { return }
            reshape("New Block", focus: fresh) { try $0.edit("addNoteAfter", [pageId, id, fresh]) }
        case .format(let name):
            typeIn(id) { try $0.pure("format", [name, $1, $2.from, $2.to]) }
        case .insert(let name):
            typeIn(id) { try $0.pure("insert", [name, $1, $2.to]) }
        case .addPage:
            commit(id)
            guard let replica = model.replica, let sheet = onAddPage?(id) else { return }
            let made: [String: Any] = ["id": sheet, "gone": true] // its undo takes it out (replica/edits.js)
            OutlineUndo.record([made], "Add Page", pageId: pageId, replica: replica, model: model, on: undoManager)
        case .undo: if undoManager?.canUndo == true { undoManager?.undo() }
        case .redo: if undoManager?.canRedo == true { undoManager?.redo() }
        case .done: focused = nil
        }
    }

    /// An outline edit (replica/edits.js, the browser's own), its undo put on
    /// the window's undo stack; the editor stays on `focus`.
    private func reshape(_ name: String, focus: String, _ edit: (Replica) throws -> Any?) {
        guard let replica = model.replica else { return }
        do {
            guard let undo = try edit(replica) as? [Any] else { return } // nothing changed
            OutlineUndo.record(undo, name, pageId: pageId, replica: replica, model: model, on: undoManager)
            onEdit()
            reload()
            focused = focus
        } catch {
            model.failure = error.localizedDescription
        }
    }

    /// A text command on the note being typed in (`plan`: the core's, the web
    /// editor's own rules), made through the keyboard's field.
    private func typeIn(_ id: String, _ plan: (Replica, String, (from: Int, to: Int)) throws -> Any?) {
        guard let replica = model.replica, let field = KeyboardField.current else { return }
        do {
            guard let edit = try plan(replica, field.text, field.selection) as? [String: Any] else { return }
            field.apply(edit)
            drafts[id] = field.text // what a commit sends
        } catch {
            model.failure = error.localizedDescription
        }
    }
}

import SwiftUI

/// A page's notes as an outline: each block's text, editable here (sent as
/// an edit from the text it was edited from, so a text changed meanwhile
/// merges), handwriting groups and notebook pages as labelled rows (a tap
/// shows the drawing), and a note added at the end.
struct NotesView: View {
    @EnvironmentObject private var model: AppModel
    let pageId: String
    var onEdit: () -> Void
    var onJump: ((String) -> Void)? = nil
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
    }

    var body: some View {
        List {
            ForEach(rows) { row in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: icon(row.kind)).foregroundStyle(.secondary).frame(width: 18)
                    VStack(alignment: .leading, spacing: 2) {
                        if row.kind != "note" {
                            Button(row.label) { onJump?(row.id) }
                                .font(.caption).foregroundStyle(.secondary).buttonStyle(.plain)
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
        }
        .listStyle(.plain)
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
            for node in list {
                let props = node.dict("properties")
                let id = node.string("id")
                let kind: String
                var label = ""
                if !props.dict("sheet").isEmpty && depth == 0 {
                    sheets += 1
                    kind = "sheet"
                    label = "Page \(sheets)"
                } else if props["ink_url"] != nil {
                    kind = "ink"
                    let page = props.int("pdf_page").map { " · p. \($0)" } ?? ""
                    label = "Handwriting\(page), \(props.int("ink_strokes") ?? 0) strokes"
                } else {
                    kind = "note"
                }
                out.append(Row(id: id, depth: depth, text: node.string("content"), kind: kind, label: label))
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
}

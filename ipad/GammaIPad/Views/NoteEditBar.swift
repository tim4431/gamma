import SwiftUI
import UIKit

/// The editing bar (docs/dev/ipad.md "Editing the notes"): the web app's
/// touch strip (frontend/src/editor/EditBar.jsx) over the keyboard while a
/// note is edited — the insert menu, outdent and indent, moving the note, a
/// new note after it, bold, italic, link, inline math, undo and redo, and
/// Done. NotesView runs each command; the rules are the core's.
struct NoteEditBar: View {
    enum Command {
        /// A "/" menu insertion by name (editor/slashInserts.js).
        case insert(String)
        /// A page to write on, after the note.
        case addPage
        case outdent, indent, moveUp, moveDown, newBelow
        /// bold, italic, link or math (ipad/core/entry.js `format`).
        case format(String)
        case undo, redo, done
    }

    let row: NotesView.Row
    var canAddPage: Bool
    var run: (Command) -> Void

    var body: some View {
        HStack(spacing: 4) {
            // the tools scroll when the notes are narrower than they are; Done stays
            ViewThatFits(in: .horizontal) {
                tools
                ScrollView(.horizontal, showsIndicators: false) { tools }
            }
            Divider().frame(height: 22)
            button("checkmark", "Done", .done)
        }
        .buttonStyle(.borderless)
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
        .background(.thinMaterial, in: Capsule())
    }

    private var tools: some View {
        HStack(spacing: 4) {
            insertMenu
            Divider().frame(height: 22)
            button("decrease.indent", "Outdent block", .outdent).disabled(!row.nested)
            button("increase.indent", "Indent block", .indent).disabled(!row.hasPrev)
            button("arrow.up", "Move block up", .moveUp).disabled(!row.hasPrev)
            button("arrow.down", "Move block down", .moveDown).disabled(!row.hasNext)
            button("text.append", "New block below", .newBelow)
            Divider().frame(height: 22)
            button("bold", "Bold", .format("bold"))
            button("italic", "Italic", .format("italic"))
            button("link", "Link", .format("link"))
            button("sum", "Inline equation", .format("math"))
            Divider().frame(height: 22)
            button("arrow.uturn.backward", "Undo", .undo)
            button("arrow.uturn.forward", "Redo", .redo)
        }
    }

    /// The "/" menu's entries that need no popup or file, in its groups.
    private var insertMenu: some View {
        Menu {
            Section {
                item("Heading 1", "h1")
                item("Heading 2", "h2")
                item("Heading 3", "h3")
                item("To-do", "todo")
                item("Bulleted list", "bullet")
                item("Numbered list", "number")
                item("Quote", "quote")
                item("Callout", "callout")
                item("Divider", "divider")
            }
            Section { item("Equation block", "equation") }
            Section {
                if canAddPage { Button("Handwritten note") { run(.addPage) } }
                item("Table", "table")
                item("Code block", "code")
                item("Mermaid diagram", "mermaid")
                item("Today's date", "date")
            }
            Section { item("Highlight text", "highlight") }
        } label: {
            symbol("slash.circle")
        }
        .menuOrder(.fixed)
        .help("Insert…")
        .accessibilityLabel("Insert…")
    }

    private func item(_ label: String, _ name: String) -> some View {
        Button(label) { run(.insert(name)) }
    }

    private func button(_ name: String, _ label: String, _ command: Command) -> some View {
        Button { run(command) } label: { symbol(name) }
            .help(label)
            .accessibilityLabel(label)
    }

    private func symbol(_ name: String) -> some View {
        Image(systemName: name).frame(width: 36, height: 36).contentShape(Rectangle())
    }
}

/// The field the keyboard types into, a SwiftUI text field's UIKit view:
/// its text and selection in UTF-16 offsets, as the core counts them, and
/// an edit made through it, so it undoes like typing.
@MainActor
struct KeyboardField {
    let input: UITextInput

    static var current: KeyboardField? {
        (UIResponder.firstResponder as? UITextInput).map { KeyboardField(input: $0) }
    }

    var text: String {
        guard let all = input.textRange(from: input.beginningOfDocument, to: input.endOfDocument) else { return "" }
        return input.text(in: all) ?? ""
    }

    var selection: (from: Int, to: Int) {
        guard let range = input.selectedTextRange else { return (0, 0) }
        return (input.offset(from: input.beginningOfDocument, to: range.start),
                input.offset(from: input.beginningOfDocument, to: range.end))
    }

    /// The core's edit: {from, to, insert} replaced, then {anchor, head} selected.
    func apply(_ edit: [String: Any]) {
        let start = input.beginningOfDocument
        guard let a = input.position(from: start, offset: edit.int("from") ?? 0),
              let b = input.position(from: start, offset: edit.int("to") ?? 0),
              let range = input.textRange(from: a, to: b) else { return }
        input.replace(range, withText: edit.string("insert"))
        let anchor = edit.int("anchor") ?? 0, head = edit.int("head") ?? anchor
        let begin = input.beginningOfDocument
        if let s = input.position(from: begin, offset: min(anchor, head)),
           let e = input.position(from: begin, offset: max(anchor, head)) {
            input.selectedTextRange = input.textRange(from: s, to: e)
        }
    }
}

extension UIResponder {
    private static var captured: UIResponder?

    /// The responder the keyboard serves now, if any.
    static var firstResponder: UIResponder? {
        captured = nil
        _ = UIApplication.shared.sendAction(#selector(UIResponder.captureFirstResponder), to: nil, from: nil, for: nil)
        defer { captured = nil }
        return captured
    }

    @objc private func captureFirstResponder() { UIResponder.captured = self }
}

/// The notes' outline edits on the window's undo manager (the environment's,
/// which the bar's Undo and Redo run): each records where the blocks it
/// moved stood (replica/edits.js), and undoing it records the way back, the
/// redo.
@MainActor
enum OutlineUndo {
    private final class Target {}
    private static let target = Target()

    static func record(_ placements: [Any], _ name: String, pageId: String, replica: Replica, model: AppModel,
                       on manager: UndoManager?) {
        guard let manager, !placements.isEmpty else { return }
        manager.registerUndo(withTarget: target) { _ in
            MainActor.assumeIsolated {
                do {
                    let back = try replica.edit("restore", [pageId, placements]) as? [Any] ?? []
                    OutlineUndo.record(back, name, pageId: pageId, replica: replica, model: model, on: manager)
                    model.edited()
                } catch {
                    model.failure = error.localizedDescription
                }
            }
        }
        manager.setActionName(name)
    }
}

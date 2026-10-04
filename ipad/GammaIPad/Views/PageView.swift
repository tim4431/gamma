import SwiftUI

/// One page of the library: a PDF to read and write on, or a page of notes
/// — with the page's notes beside it and the ink tools above
/// (docs/dev/ipad.md). A page with sheets of paper opens in the notebook
/// view (its sheets one under the other, written on with the Pencil); the
/// toolbar switches it to its notes alone and back. What it shows comes
/// from the replica's store through the core's pageView
/// (frontend/src/replica/views.js).
struct PageView: View {
    @EnvironmentObject private var model: AppModel
    let pageId: String
    @StateObject private var ink = InkSession()
    @State private var view: [String: Any] = [:]
    @State private var showNotes = true
    @State private var showPages = true
    @State private var showWeb = false

    private var kind: String { view.string("kind") }
    private var hasSheets: Bool { kind != "pdf" && !view.array("sheets").isEmpty }
    /// A reader beside the notes: the PDF, or the sheets in the notebook view.
    private var reads: Bool { kind == "pdf" || (hasSheets && showPages) }

    var body: some View {
        Group {
            if view.isEmpty {
                ContentUnavailableView("This page is not here", systemImage: "questionmark.folder")
            } else if kind == "pdf" {
                pdf
            } else if reads {
                NotebookReader(ink: ink)
            } else {
                NotesView(pageId: pageId, onEdit: edited, onJump: { show($0) }, onReplay: { show($0, replay: true) },
                          replaying: ink.replaying, onAddPage: addPage)
            }
        }
        .navigationTitle(view.string("title").isEmpty ? "Untitled" : view.string("title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if reads {
                ToolbarItem(placement: .principal) { InkToolbar(ink: ink) }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNotes.toggle() } label: { Image(systemName: "sidebar.right") }
                        .help("Notes")
                }
            }
            if hasSheets {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showPages.toggle() } label: { Image(systemName: showPages ? "text.alignleft" : "book") }
                        .help(showPages ? "The notes alone" : "Notebook view: the pages beside the notes")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { showWeb = true } label: { Image(systemName: "safari") }
                    .help("Open this page in Gamma on the web")
            }
        }
        .inspector(isPresented: Binding(get: { showNotes && reads }, set: { showNotes = $0 })) {
            NotesView(pageId: pageId, onEdit: edited, onJump: { ink.jump(to: $0) }, onReplay: { ink.replay($0) },
                      replaying: ink.replaying, onAddPage: addPage)
                .inspectorColumnWidth(min: 260, ideal: 340, max: 480)
        }
        .sheet(isPresented: $showWeb) {
            if let c = model.replica?.connection {
                WebPageView(url: c.server.appending(queryItems: [URLQueryItem(name: "page", value: pageId), URLQueryItem(name: "ws", value: c.workspace)]))
            }
        }
        .onAppear { load(first: true) }
        .onChange(of: model.revision) { _, _ in load(first: false) }
        .onDisappear {
            ink.stopReplay()
            ink.flush()
        }
    }

    @ViewBuilder private var pdf: some View {
        let docId = view.dict("pdf").string("docId")
        if let replica = model.replica, !docId.isEmpty, replica.files.has("\(docId).pdf") {
            PDFReader(url: replica.files.url("\(docId).pdf"), ink: ink)
        } else {
            ContentUnavailableView("The PDF is on its way", systemImage: "arrow.down.doc",
                                   description: Text("It arrives with the next sync."))
        }
    }

    private func load(first: Bool) {
        guard let replica = model.replica else { return }
        view = replica.view(of: pageId) ?? [:]
        if first { ink.attach(replica: replica, pageId: pageId, onSaved: edited) }
        ink.load(view)
    }

    /// A page to write on, on a page without a PDF: after the block named (the
    /// editing bar's Insert), else after the last page. → its id.
    private var addPage: ((String?) -> String?)? {
        guard kind != "pdf" else { return nil }
        return { ink.addSheet(after: $0) }
    }

    /// From the notes alone: the notebook view, at the page or drawing (replaying it).
    private func show(_ id: String, replay: Bool = false) {
        showPages = true
        if replay { ink.replay(id) } else { ink.jump(to: id) }
    }

    private func edited() { model.edited() }
}

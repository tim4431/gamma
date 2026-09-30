import SwiftUI

/// One page of the library: a PDF to read and write on, a notebook's sheets,
/// or a page of notes — with the page's notes beside it and the ink tools
/// above (docs/dev/ipad.md). What it shows comes from the replica's store
/// through the core's pageView (frontend/src/replica/views.js).
struct PageView: View {
    @EnvironmentObject private var model: AppModel
    let pageId: String
    @StateObject private var ink = InkSession()
    @State private var view: [String: Any] = [:]
    @State private var showNotes = true
    @State private var showWeb = false

    private var kind: String { view.string("kind") }

    var body: some View {
        Group {
            if view.isEmpty {
                ContentUnavailableView("This page is not here", systemImage: "questionmark.folder")
            } else if kind == "pdf" {
                pdf
            } else if kind == "notebook" {
                NotebookReader(ink: ink)
            } else {
                NotesView(pageId: pageId, onEdit: edited)
            }
        }
        .navigationTitle(view.string("title").isEmpty ? "Untitled" : view.string("title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if kind == "pdf" || kind == "notebook" {
                ToolbarItem(placement: .principal) { InkToolbar(ink: ink) }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNotes.toggle() } label: { Image(systemName: "sidebar.right") }
                        .help("Notes")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { showWeb = true } label: { Image(systemName: "safari") }
                    .help("Open this page in Gamma on the web")
            }
        }
        .inspector(isPresented: Binding(get: { showNotes && (kind == "pdf" || kind == "notebook") }, set: { showNotes = $0 })) {
            NotesView(pageId: pageId, onEdit: edited, onJump: { ink.jump(to: $0) })
                .inspectorColumnWidth(min: 260, ideal: 340, max: 480)
        }
        .sheet(isPresented: $showWeb) {
            if let c = model.replica?.connection {
                WebPageView(url: c.server.appending(queryItems: [URLQueryItem(name: "page", value: pageId), URLQueryItem(name: "ws", value: c.workspace)]))
            }
        }
        .onAppear { load(first: true) }
        .onChange(of: model.revision) { _, _ in load(first: false) }
        .onDisappear { ink.flush() }
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

    private func edited() { model.edited() }
}

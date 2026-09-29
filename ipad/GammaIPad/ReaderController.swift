import UIKit
import PDFKit
import SwiftUI
import GammaCore

@MainActor
final class ReaderController: UIViewController, PDFPageOverlayViewProvider, UIScrollViewDelegate {
    let repository: GammaRepository
    let documentID: String
    let directory: URL
    let closeReader: () -> Void
    private var document: GammaDocument?
    private var engine: InkEngine?
    private var audio: NoteAudioSession?
    private let pdf = PDFView()
    private let notebookScroll = UIScrollView()
    private let sheets = UIStackView()
    private let status = UILabel()
    private let tools = UISegmentedControl(items: InkCanvasView.Tool.allCases.map(\.rawValue))
    private var canvases: [String: InkCanvasView] = [:]
    private var allGroups: [String: InkGroup] = [:]
    private var baseURLs: [String: String] = [:]
    private var savedData: [String: Data] = [:]
    private var groupRemap: [String: String] = [:]
    private var saveTask: Task<Void, Never>?
    private var activeKey = ""
    private var sheetBlocks: [GammaBlock] = []
    private var recordButton: UIBarButtonItem!
    private var playbackEvents: [[String: Any]] = []
    private var playlist: [(String, URL)] = []
    private var audioBlockID: String?
    private var segmentValues: [[String: Any]] = []
    private var eventValues: [[String: Any]] = []
    private var followingKey: String?

    init(repository: GammaRepository, documentID: String, directory: URL, close: @escaping () -> Void) {
        self.repository = repository; self.documentID = documentID; self.directory = directory; closeReader = close
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func viewDidLoad() {
        super.viewDidLoad(); view.backgroundColor = .systemBackground
        navigationItem.leftBarButtonItem = UIBarButtonItem(title: "Library", style: .plain, target: self, action: #selector(close))
        recordButton = UIBarButtonItem(image: UIImage(systemName: "mic"), style: .plain, target: self, action: #selector(record))
        navigationItem.rightBarButtonItems = [UIBarButtonItem(image: UIImage(systemName: "ellipsis.circle"), menu: moreMenu()), recordButton,
            UIBarButtonItem(image: UIImage(systemName: "play.circle"), style: .plain, target: self, action: #selector(play))]
        tools.selectedSegmentIndex = 0; tools.addTarget(self, action: #selector(toolChanged), for: .valueChanged)
        tools.accessibilityLabel = "Handwriting tool"
        status.font = .preferredFont(forTextStyle: .caption1); status.textColor = .secondaryLabel; status.text = "Opening local document…"
        let toolbar = UIStackView(arrangedSubviews: [tools, status]); toolbar.axis = .vertical; toolbar.spacing = 6
        toolbar.isLayoutMarginsRelativeArrangement = true; toolbar.directionalLayoutMargins = NSDirectionalEdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12)
        let body = UIView()
        let stack = UIStackView(arrangedSubviews: [toolbar, body]); stack.axis = .vertical; stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor), stack.leadingAnchor.constraint(equalTo: view.leadingAnchor),
                                     stack.trailingAnchor.constraint(equalTo: view.trailingAnchor), stack.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor)])
        for surface in [pdf, notebookScroll] {
            surface.translatesAutoresizingMaskIntoConstraints = false; body.addSubview(surface)
            NSLayoutConstraint.activate([surface.topAnchor.constraint(equalTo: body.topAnchor), surface.bottomAnchor.constraint(equalTo: body.bottomAnchor),
                                         surface.leadingAnchor.constraint(equalTo: body.leadingAnchor), surface.trailingAnchor.constraint(equalTo: body.trailingAnchor)])
        }
        pdf.autoScales = true; pdf.displayMode = .singlePageContinuous; pdf.displayDirection = .vertical; pdf.pageOverlayViewProvider = self
        notebookScroll.backgroundColor = .secondarySystemBackground; notebookScroll.delegate = self; notebookScroll.isHidden = true
        notebookScroll.minimumZoomScale = 1; notebookScroll.maximumZoomScale = 4
        sheets.axis = .vertical; sheets.spacing = 20; sheets.translatesAutoresizingMaskIntoConstraints = false
        notebookScroll.addSubview(sheets)
        NSLayoutConstraint.activate([sheets.topAnchor.constraint(equalTo: notebookScroll.contentLayoutGuide.topAnchor, constant: 16),
            sheets.leadingAnchor.constraint(equalTo: notebookScroll.contentLayoutGuide.leadingAnchor, constant: 16),
            sheets.trailingAnchor.constraint(equalTo: notebookScroll.contentLayoutGuide.trailingAnchor, constant: -16),
            sheets.bottomAnchor.constraint(equalTo: notebookScroll.contentLayoutGuide.bottomAnchor, constant: -16),
            sheets.widthAnchor.constraint(equalTo: notebookScroll.frameLayoutGuide.widthAnchor, constant: -32)])
        NotificationCenter.default.addObserver(self, selector: #selector(pdfPageChanged), name: .PDFViewPageChanged, object: pdf)
        Task { await load() }
    }
    deinit { NotificationCenter.default.removeObserver(self) }
    private func load() async {
        do {
            let engine = try InkEngine(); self.engine = engine
            let document = try await repository.document(id: documentID); self.document = document; title = document.title
            for block in document.blocks {
                let props = propertyObject(block.properties)
                if let url = props["ink_url"] as? String, !url.isEmpty {
                    let file = try await repository.assetURL(reference: url)
                    let data = try Data(contentsOf: file)
                    guard let ink = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                    allGroups[block.id] = InkGroup(id: block.id, parentID: block.parent, ink: ink)
                    baseURLs[block.id] = url; savedData[block.id] = try engine.data(ink)
                }
            }
            let props = propertyObject(document.properties)
            if props["notebook"] != nil { try showNotebook(document) }
            else if let source = props["source_url"] as? String {
                let url = try await repository.assetURL(reference: source)
                guard let document = PDFDocument(url: url) else { throw InkEngineError.failure("Could not open this PDF.") }
                pdf.document = document
            } else { throw InkEngineError.failure("This page has no PDF or notebook paper.") }
            let audio = try NoteAudioSession(directory: directory.appendingPathComponent("Recordings", isDirectory: true).appendingPathComponent(documentID, isDirectory: true))
            self.audio = audio
            if let block = document.blocks.last(where: { propertyObject($0.properties)["type"] as? String == "audio" }) {
                audioBlockID = block.id
                let properties = propertyObject(block.properties)
                segmentValues = properties["audio_segments"] as? [[String: Any]] ?? []
                eventValues = properties["audio_events"] as? [[String: Any]] ?? []
            }
            audio.onChange = { [weak self] in
                guard let self else { return }
                self.recordButton.image = UIImage(systemName: self.audio?.recorder == nil ? "mic" : "pause.circle.fill")
                self.recordButton.isEnabled = self.audio?.isFinalizing != true
            }
            audio.onError = { [weak self] in self?.showError($0) }
            audio.onSegment = { [weak self] id, url, duration, events in try await self?.saveSegment(id: id, url: url, duration: duration, events: events) }
            audio.onPlayback = { [weak self] segment, time in self?.replay(segment: segment, time: time) }
            try await audio.recover()
            status.text = "Saved on this iPad · Pencil writes; fingers scroll"
        } catch { showError(error) }
    }
    private func key(for ink: [String: Any]) -> String {
        let space = ink["space"] as? [String: Any] ?? [:]
        if let sheet = space["sheet_id"] as? String { return sheet }
        return "pdf:\(space["page"] as? Int ?? 1)"
    }
    private func anchor(_ key: String) -> [String: Any] {
        key.hasPrefix("pdf:") ? ["pdf_page": Int(key.dropFirst(4)) ?? 1] : ["sheet_id": key]
    }
    private func canvas(key: String, size: CGSize) -> InkCanvasView? {
        if let canvas = canvases[key] { return canvas }
        guard let engine else { return nil }
        let canvas = InkCanvasView(engine: engine); canvas.pageSize = size
        canvas.groups = allGroups.values.filter { self.key(for: $0.ink) == key }.sorted { $0.id < $1.id }
        canvas.makeGroup = { [weak self] in
            let space: [String: Any] = key.hasPrefix("pdf:") ? ["kind": "pdf-page", "page": Int(key.dropFirst(4)) ?? 1, "width": size.width, "height": size.height] :
                ["kind": "notebook-page", "sheet_id": key, "width": size.width, "height": size.height]
            return InkGroup(id: gammaID(), parentID: self?.documentID ?? "", ink: ["format": "gamma-ink", "version": key.hasPrefix("pdf:") ? 1 : 2, "space": space, "strokes": []])
        }
        canvas.onChange = { [weak self] groups in self?.activeKey = key; self?.save(groups: groups, key: key) }
        canvas.onStroke = { [weak self] group, stroke, start, end in
            guard let self else { return }; self.activeKey = key
            self.audio?.recordStroke(groupID: group, stroke: stroke, start: start, end: end, anchor: self.anchor(key))
        }
        canvas.onError = { [weak self] in self?.showError($0) }
        canvases[key] = canvas; if activeKey.isEmpty { activeKey = key }; configureCanvas(canvas)
        return canvas
    }
    func pdfView(_ view: PDFView, overlayViewFor page: PDFPage) -> UIView? {
        guard let document = view.document else { return nil }
        let index = document.index(for: page)
        var size = page.bounds(for: .cropBox).size
        if abs(page.rotation) % 180 == 90 { size = CGSize(width: size.height, height: size.width) }
        return canvas(key: "pdf:\(index + 1)", size: size)
    }
    private func showNotebook(_ document: GammaDocument) throws {
        pdf.isHidden = true; notebookScroll.isHidden = false
        for subview in sheets.arrangedSubviews { sheets.removeArrangedSubview(subview); subview.removeFromSuperview() }
        sheetBlocks = document.blocks.filter { propertyObject($0.properties)["type"] as? String == "notebook-sheet" }.sorted { $0.position < $1.position }
        for sheet in sheetBlocks {
            let paper = propertyObject(sheet.properties)["paper"] as? [String: Any] ?? defaultPaper
            let size = CGSize(width: paper["width"] as? Double ?? 612, height: paper["height"] as? Double ?? 792)
            let page = PaperView(paper: paper); page.clipsToBounds = true
            page.heightAnchor.constraint(equalTo: page.widthAnchor, multiplier: size.height / size.width).isActive = true
            if let canvas = canvas(key: sheet.id, size: size) {
                canvas.removeFromSuperview(); canvas.translatesAutoresizingMaskIntoConstraints = false; page.addSubview(canvas)
                NSLayoutConstraint.activate([canvas.leadingAnchor.constraint(equalTo: page.leadingAnchor), canvas.trailingAnchor.constraint(equalTo: page.trailingAnchor),
                                             canvas.topAnchor.constraint(equalTo: page.topAnchor), canvas.bottomAnchor.constraint(equalTo: page.bottomAnchor)])
            }
            sheets.addArrangedSubview(page)
        }
        let add = UIButton(type: .system); add.setTitle("＋ Add a page", for: .normal); add.heightAnchor.constraint(equalToConstant: 56).isActive = true
        add.addAction(UIAction { [weak self] _ in self?.appendPage() }, for: .touchUpInside); sheets.addArrangedSubview(add)
    }
    private func save(groups: [InkGroup], key: String) {
        guard let engine else { return }
        // Undoing the first stroke persists an empty group, preserving captions.
        var values = groups
        for prior in allGroups.values where self.key(for: prior.ink) == key && !groups.contains(where: { $0.id == prior.id }) {
            var empty = prior; empty.ink["strokes"] = []; values.append(empty)
        }
        for group in values { allGroups[group.id] = group }
        do {
            let changed = try values.compactMap { group -> (InkGroup, Data)? in
                let data = try engine.data(group.ink); return data == savedData[group.id] ? nil : (group, data)
            }
            guard !changed.isEmpty else { return }
            status.text = "Saving on this iPad…"
            let previous = saveTask
            saveTask = Task { [weak self] in
                await previous?.value
                guard let self else { return }
                do {
                    for (group, data) in changed {
                        var target = group.id
                        while let mapped = self.groupRemap[target], mapped != target { target = mapped }
                        let saved = try await self.repository.saveInk(pageID: self.documentID, blockID: target,
                            parentID: group.parentID, ink: data, baseURL: self.baseURLs[target] ?? "")
                        let properties = propertyObject(saved.properties)
                        self.baseURLs[saved.id] = properties["ink_url"] as? String
                        self.savedData[saved.id] = data
                        if saved.id != target {
                            self.groupRemap[target] = saved.id
                            if var latest = self.allGroups.removeValue(forKey: target) { latest.id = saved.id; self.allGroups[saved.id] = latest }
                            for canvas in self.canvases.values {
                                for index in canvas.groups.indices where canvas.groups[index].id == target { canvas.groups[index].id = saved.id }
                                if canvas.activeGroupID == target { canvas.activeGroupID = saved.id }
                            }
                            self.status.text = "Both handwriting versions were kept after a sync conflict."
                        }
                    }
                    self.status.text = "Saved on this iPad"
                } catch { self.showError(error) }
            }
        } catch { showError(error) }
    }
    @objc private func close() {
        audio?.pause(); audio?.stopPlayback()
        navigationItem.leftBarButtonItem?.isEnabled = false
        Task {
            await saveTask?.value
            while audio?.isFinalizing == true { try? await Task.sleep(nanoseconds: 25_000_000) }
            closeReader()
        }
    }
    @objc private func toolChanged() { for canvas in canvases.values { configureCanvas(canvas) } }
    private func configureCanvas(_ canvas: InkCanvasView) { canvas.tool = InkCanvasView.Tool.allCases[max(0, tools.selectedSegmentIndex)] }
    private func moreMenu() -> UIMenu {
        UIMenu(children: [
            UIAction(title: "Undo", image: UIImage(systemName: "arrow.uturn.backward")) { [weak self] _ in self?.activeCanvas?.undoInk() },
            UIAction(title: "Redo", image: UIImage(systemName: "arrow.uturn.forward")) { [weak self] _ in self?.activeCanvas?.redoInk() },
            UIAction(title: "New ink group", image: UIImage(systemName: "plus")) { [weak self] _ in self?.activeCanvas?.newGroup() },
            UIMenu(title: "Color", children: [("Black", "#1f1f1f"), ("Blue", "#1d4ed8"), ("Red", "#dc2626"), ("Green", "#15803d"), ("Yellow", "#fde047")].map { name, color in
                UIAction(title: name) { [weak self] _ in self?.canvases.values.forEach { $0.inkColor = color; $0.editSelection("restyleStrokes", value: ["color": color]) } }
            }),
            UIMenu(title: "Width", children: [("Fine", 1.0), ("Medium", 2.0), ("Broad", 4.0)].map { name, size in
                UIAction(title: name) { [weak self] _ in self?.canvases.values.forEach { $0.inkSize = size; $0.editSelection("restyleStrokes", value: ["size": size]) } }
            }),
            UIMenu(title: "Selection", children: [("Duplicate", "duplicateStrokes"), ("Delete", "removeStrokes"), ("Larger", "grow"), ("Smaller", "shrink"), ("Rotate 15°", "rotate")].map { name, action in
                UIAction(title: name) { [weak self] _ in self?.activeCanvas?.editSelection(action) }
            }),
            UIAction(title: "Paper settings", image: UIImage(systemName: "doc")) { [weak self] _ in self?.paperSettings() },
            UIAction(title: "Add notebook page", image: UIImage(systemName: "doc.badge.plus")) { [weak self] _ in self?.appendPage() },
            UIAction(title: "Notes", image: UIImage(systemName: "note.text")) { [weak self] _ in self?.notes() },
            UIAction(title: "Highlight selected PDF text", image: UIImage(systemName: "highlighter")) { [weak self] _ in self?.highlightSelection() },
            UIAction(title: "Sync library", image: UIImage(systemName: "arrow.triangle.2.circlepath")) { [weak self] _ in self?.sync() }
        ])
    }
    private var activeCanvas: InkCanvasView? { canvases[activeKey] }
    @objc private func pdfPageChanged() {
        if let page = pdf.currentPage, let document = pdf.document { activeKey = "pdf:\(document.index(for: page) + 1)"; audio?.recordPage(anchor(activeKey)) }
    }
    func scrollViewDidScroll(_ scrollView: UIScrollView) {
        guard scrollView === notebookScroll else { return }
        let center = scrollView.contentOffset.y + scrollView.bounds.height / 2
        if let index = sheets.arrangedSubviews.firstIndex(where: { $0.frame.maxY > center }), sheetBlocks.indices.contains(index) {
            let key = sheetBlocks[index].id
            if key != activeKey { activeKey = key; audio?.recordPage(anchor(key)) }
        }
    }
    func viewForZooming(in scrollView: UIScrollView) -> UIView? { scrollView === notebookScroll ? sheets : nil }
    private func appendPage() {
        guard let document, let notebook = propertyObject(document.properties)["notebook"] as? [String: Any] else { return }
        Task {
            do {
                let paper = notebook["default_paper"] as? [String: Any] ?? defaultPaper
                let sheet = try await repository.appendSheet(notebookID: documentID, paper: jsonProperties(paper))
                let fresh = try await repository.document(id: documentID); self.document = fresh; try showNotebook(fresh); activeKey = sheet.id
                view.layoutIfNeeded(); notebookScroll.scrollRectToVisible(sheets.arrangedSubviews[max(0, sheets.arrangedSubviews.count - 2)].frame, animated: true)
            } catch { showError(error) }
        }
    }
    private func paperSettings() {
        guard let document, let notebook = propertyObject(document.properties)["notebook"] as? [String: Any] else { return }
        let sheet = sheetBlocks.first(where: { $0.id == activeKey })
        let paper = sheet.flatMap { propertyObject($0.properties)["paper"] as? [String: Any] } ?? notebook["default_paper"] as? [String: Any] ?? defaultPaper
        let settings = PaperSettingsView(paper: paper) { [weak self] values, future in
            guard let self else { return }
            self.dismiss(animated: true)
            Task {
                do {
                    if future {
                        var properties = notebook; properties["default_paper"] = values
                        try await self.repository.apply(pageID: self.documentID, ops: [GammaOperation(op: "set", id: self.documentID, props: jsonProperties(["notebook": properties]))])
                    } else if let sheet {
                        try await self.repository.apply(pageID: self.documentID, ops: [GammaOperation(op: "set", id: sheet.id, props: jsonProperties(["paper": values]))])
                        if let canvas = self.canvases[sheet.id] {
                            canvas.pageSize = CGSize(width: values["width"] as? Double ?? 612, height: values["height"] as? Double ?? 792)
                            for index in canvas.groups.indices {
                                var space = canvas.groups[index].ink["space"] as? [String: Any] ?? [:]
                                space["width"] = canvas.pageSize.width; space["height"] = canvas.pageSize.height; canvas.groups[index].ink["space"] = space
                            }; self.save(groups: canvas.groups, key: sheet.id)
                        }
                    }
                    let fresh = try await self.repository.document(id: self.documentID); self.document = fresh; try self.showNotebook(fresh)
                } catch { self.showError(error) }
            }
        }
        present(UIHostingController(rootView: settings), animated: true)
    }
    private func notes() {
        present(UINavigationController(rootViewController: NotesController(repository: repository, documentID: documentID)), animated: true)
    }
    private func highlightSelection() {
        guard let selection = pdf.currentSelection, let text = selection.string, !text.isEmpty, let document = pdf.document else { return }
        Task {
            do {
                for page in selection.pages {
                    let number = document.index(for: page) + 1
                    guard let canvas = canvases["pdf:\(number)"] else { continue }
                    let rect = canvas.convert(pdf.convert(selection.bounds(for: page), from: page), from: pdf)
                    let width = max(1, canvas.bounds.width), height = max(1, canvas.bounds.height)
                    let size = canvas.pageSize
                    let region: [String: Any] = ["x1": rect.minX / width * size.width, "y1": rect.minY / height * size.height,
                        "x2": rect.maxX / width * size.width, "y2": rect.maxY / height * size.height,
                        "width": size.width, "height": size.height, "pageNumber": number]
                    let id = gammaID()
                    let props = try jsonProperties(["highlight_id": id, "pdf_page": number, "quote": text, "color": "yellow",
                        "pdf_position": ["pageNumber": number, "boundingRect": region, "rects": [region]]])
                    try await repository.apply(pageID: documentID, ops: [GammaOperation(op: "insert", id: id, parent: documentID, content: "", props: props)])
                }
                status.text = "Highlight saved in notes"; pdf.clearSelection()
            } catch { showError(error) }
        }
    }
    private func sync() {
        Task { await saveTask?.value; do { _ = try await repository.sync(); status.text = "Library synced. Reopen to see remote changes." } catch { showError(error) } }
    }
    @objc private func record() {
        guard let audio else { return }
        if audio.recorder != nil { audio.pause() }
        else { Task { do { try await audio.record(); audio.recordPage(anchor(activeKey)); status.text = "Recording while you write" } catch { showError(error) } } }
    }
    private func saveSegment(id: String, url: URL, duration: Int, events: [[String: Any]]) async throws {
        if segmentValues.contains(where: { $0["id"] as? String == id }) { return }
        let reference = try await repository.storeAsset(data: Data(contentsOf: url), extension: "m4a")
        let nextSegments = segmentValues + [["id": id, "url": reference, "duration_ms": duration]]
        let nextEvents = eventValues + events
        let props = try jsonProperties(["type": "audio", "audio_segments": nextSegments, "audio_events": nextEvents])
        if let audioBlockID { try await repository.apply(pageID: documentID, ops: [GammaOperation(op: "set", id: audioBlockID, props: props)]) }
        else {
            let block = gammaID()
            try await repository.apply(pageID: documentID, ops: [GammaOperation(op: "insert", id: block, parent: documentID, content: "Recording", props: props)])
            audioBlockID = block
        }
        segmentValues = nextSegments; eventValues = nextEvents
        status.text = "Recording saved on this iPad"
    }
    @objc private func play() {
        guard let audio else { return }
        if audio.player != nil { audio.stopPlayback(); return }
        Task {
            do {
                playlist = []
                for segment in segmentValues {
                    if let id = segment["id"] as? String, let url = segment["url"] as? String {
                        playlist.append((id, try await repository.assetURL(reference: url)))
                    }
                }
                guard !playlist.isEmpty else { throw InkEngineError.failure("Record audio to replay your handwriting.") }
                playbackEvents = eventValues; try audio.play(playlist)
            } catch { showError(error) }
        }
    }
    private func replay(segment: String?, time: Double) {
        guard let segment, let engine else { for canvas in canvases.values { canvas.playback = nil }; followingKey = nil; return }
        let segmentOrder = segmentValues.compactMap { $0["id"] as? String }
        let currentIndex = segmentOrder.firstIndex(of: segment) ?? 0
        for canvas in canvases.values {
            canvas.playback = { [weak self] stroke in
                guard let self else { return stroke }
                let id = stroke["source_id"] as? String ?? stroke["id"] as? String ?? ""
                guard let event = self.playbackEvents.first(where: { $0["stroke_id"] as? String == id }), let eventSegment = event["segment_id"] as? String else { return stroke }
                let eventIndex = segmentOrder.firstIndex(of: eventSegment) ?? 0
                if eventIndex < currentIndex { return stroke }; if eventIndex > currentIndex { return nil }
                let start = event["start_ms"] as? Double ?? 0
                if time < start { return nil }
                if time >= (event["end_ms"] as? Double ?? start) { return stroke }
                guard let samples = try? engine.call("decodeStroke", [stroke]) as? [[String: Any]] else { return stroke }
                let visible = samples.filter { ($0["t"] as? Double ?? 0) <= time - start }
                guard !visible.isEmpty else { return nil }
                var request = stroke; request["samples"] = visible
                guard let encoded = try? engine.object("encodeStroke", [request]) else { return stroke }
                var result = stroke; result["pts"] = encoded["pts"]; return result
            }
        }
        if let event = playbackEvents.last(where: { $0["segment_id"] as? String == segment && ($0["start_ms"] as? Double ?? 0) <= time }) {
            let key = (event["sheet_id"] as? String) ?? "pdf:\(event["pdf_page"] as? Int ?? 1)"
            if followingKey != key {
                followingKey = key
                if key.hasPrefix("pdf:"), let index = Int(key.dropFirst(4)), let page = pdf.document?.page(at: index - 1) { pdf.go(to: page) }
                else if let index = sheetBlocks.firstIndex(where: { $0.id == key }), sheets.arrangedSubviews.indices.contains(index) {
                    notebookScroll.scrollRectToVisible(sheets.arrangedSubviews[index].frame, animated: false)
                }
            }
        }
    }
    private func showError(_ error: Error) {
        status.text = error.localizedDescription
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(title: "Gamma", message: error.localizedDescription, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default)); present(alert, animated: true)
    }
}

@MainActor
final class PaperView: UIView {
    let paper: [String: Any]
    init(paper: [String: Any]) { self.paper = paper; super.init(frame: .zero); backgroundColor = UIColor.gamma(paper["color"] as? String ?? "#ffffff") }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }
        let width = paper["width"] as? Double ?? 612, height = paper["height"] as? Double ?? 792
        context.scaleBy(x: bounds.width / width, y: bounds.height / height)
        let spacing = max(4, paper["spacing"] as? Double ?? 24), pattern = paper["pattern"] as? String ?? "blank"
        let color = UIColor.gamma(paper["line_color"] as? String ?? "#cbd5e1")
        context.setStrokeColor(color.cgColor); context.setFillColor(color.cgColor); context.setLineWidth(0.5)
        if pattern == "dots" {
            for x in stride(from: spacing, to: width, by: spacing) { for y in stride(from: spacing, to: height, by: spacing) { context.fillEllipse(in: CGRect(x: x - 0.8, y: y - 0.8, width: 1.6, height: 1.6)) } }
        } else if pattern == "ruled" || pattern == "grid" {
            for y in stride(from: spacing, to: height, by: spacing) { context.move(to: CGPoint(x: 0, y: y)); context.addLine(to: CGPoint(x: width, y: y)) }
            if pattern == "grid" { for x in stride(from: spacing, to: width, by: spacing) { context.move(to: CGPoint(x: x, y: 0)); context.addLine(to: CGPoint(x: x, y: height)) } }
            context.strokePath()
        }
    }
}

struct PaperSettingsView: View {
    let apply: ([String: Any], Bool) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var width: Double
    @State private var height: Double
    @State private var color: String
    @State private var pattern: String
    @State private var spacing: Double
    @State private var future = false
    let lineColor: String
    init(paper: [String: Any], apply: @escaping ([String: Any], Bool) -> Void) {
        self.apply = apply
        _width = State(initialValue: paper["width"] as? Double ?? 612); _height = State(initialValue: paper["height"] as? Double ?? 792)
        _color = State(initialValue: paper["color"] as? String ?? "#ffffff"); _pattern = State(initialValue: paper["pattern"] as? String ?? "blank")
        _spacing = State(initialValue: paper["spacing"] as? Double ?? 24); lineColor = paper["line_color"] as? String ?? "#cbd5e1"
    }
    var body: some View {
        NavigationStack {
            Form {
                Picker("Pattern", selection: $pattern) { ForEach(["blank", "ruled", "grid", "dots"], id: \.self) { Text($0.capitalized) } }
                Picker("Paper", selection: $color) { Text("White").tag("#ffffff"); Text("Cream").tag("#fff8dc"); Text("Light blue").tag("#eff6ff") }
                HStack { Text("Spacing"); Slider(value: $spacing, in: 8...48, step: 2); Text("\(Int(spacing)) pt") }
                HStack { Button("A4") { width = 595.28; height = 841.89 }; Spacer(); Button("Letter") { width = 612; height = 792 }; Spacer(); Button("Rotate") { swap(&width, &height) } }
                LabeledContent("Width") { TextField("Width", value: $width, format: .number).keyboardType(.decimalPad).multilineTextAlignment(.trailing) }
                LabeledContent("Height") { TextField("Height", value: $height, format: .number).keyboardType(.decimalPad).multilineTextAlignment(.trailing) }
                Toggle("Use for new pages", isOn: $future)
                Text("Paper settings do not move or resize your handwriting.").font(.footnote).foregroundStyle(.secondary)
            }
            .navigationTitle("Paper settings")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Apply") {
                    apply(["width": width, "height": height, "pattern": pattern, "color": color, "spacing": spacing, "line_color": lineColor], future)
                }.disabled(width < 72 || height < 72 || width > 2880 || height > 2880) }
            }
        }
    }
}

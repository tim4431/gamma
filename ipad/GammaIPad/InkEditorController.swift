import UIKit
import PencilKit
import ImageIO

/// A focused page editor. PDF.js supplies the rotated page background; the
/// canvas always stores scale-1 page points. Fingers zoom/pan, Pencil writes.
@MainActor
final class InkEditorController: UIViewController, PKCanvasViewDelegate, UIScrollViewDelegate {
    let request: InkRequest
    private let store: InkDraftStore
    private var draft: InkDraft
    private let codec = GammaInkCodec()
    private let canvas = PKCanvasView()
    private let contextCanvas = PKCanvasView()
    private let scroll = UIScrollView()
    private let paper = UIView()
    private let status = UILabel()
    private let tool = UISegmentedControl(items: ["Pen", "Monoline", "Highlighter", "Eraser"])
    private let width = UISegmentedControl(items: ["Fine", "Medium", "Broad"])
    private var color = UIColor.label
    private var initialized = false
    private var initialFit = false
    private var saving = false
    var onSave: (([String: Any]) -> Void)?
    var onClose: (() -> Void)?

    init(request: InkRequest, origin: String) throws {
        self.request = request
        store = try InkDraftStore()
        let key = try request.draftKey(origin: origin)
        if let recovered = try store.read(key) {
            draft = recovered
        } else {
            draft = InkDraft(key: key, blockId: request.blockId, expectedURL: request.expectedURL,
                             baseInk: request.ink, drawing: Data())
        }
        super.init(nibName: nil, bundle: nil)
        // Register originals so unchanged strokes keep their precise JSON.
        let original = try codec.drawing(from: draft.baseInk)
        canvas.drawing = draft.drawing.isEmpty ? original : try PKDrawing(data: draft.drawing)
        draft.drawing = canvas.drawing.dataRepresentation()
        try store.write(draft)
        let backgroundCodec = GammaInkCodec()
        contextCanvas.drawing = PKDrawing(strokes: try request.background.flatMap { try backgroundCodec.drawing(from: $0).strokes })
        title = "Handwriting · Page \(request.ink.space.page)"
        isModalInPresentation = true
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        color = .black
        navigationItem.leftBarButtonItem = UIBarButtonItem(title: "Close", style: .plain, target: self, action: #selector(close))
        navigationItem.rightBarButtonItem = UIBarButtonItem(title: "Save", style: .done, target: self, action: #selector(save))
        tool.selectedSegmentIndex = 0; width.selectedSegmentIndex = 1
        tool.addTarget(self, action: #selector(changeTool), for: .valueChanged)
        width.addTarget(self, action: #selector(changeTool), for: .valueChanged)
        let undo = UIButton(type: .system), redo = UIButton(type: .system)
        undo.setTitle("Undo", for: .normal); redo.setTitle("Redo", for: .normal)
        undo.addTarget(self, action: #selector(undoInk), for: .touchUpInside)
        redo.addTarget(self, action: #selector(redoInk), for: .touchUpInside)
        let palette = UIStackView(); palette.spacing = 10; palette.alignment = .center
        for (name, hex) in [("Black", "#1f1f1f"), ("Blue", "#1d4ed8"), ("Red", "#dc2626"), ("Green", "#15803d"), ("Yellow", "#fde047")] {
            let button = UIButton(type: .system)
            let swatch = (try? inkColor(hex)) ?? .black
            button.backgroundColor = swatch
            button.accessibilityLabel = name
            button.layer.cornerRadius = 14
            button.layer.borderWidth = 1; button.layer.borderColor = UIColor.separator.cgColor
            button.widthAnchor.constraint(equalToConstant: 28).isActive = true
            button.heightAnchor.constraint(equalToConstant: 28).isActive = true
            button.addAction(UIAction { [weak self] _ in self?.color = swatch; self?.changeTool() }, for: .touchUpInside)
            palette.addArrangedSubview(button)
        }
        let tools = UIStackView(arrangedSubviews: [tool, width]); tools.spacing = 12; tools.distribution = .fillProportionally
        let actions = UIStackView(arrangedSubviews: [palette, UIView(), undo, redo]); actions.spacing = 16
        status.numberOfLines = 0; status.font = .preferredFont(forTextStyle: .footnote)
        status.text = "Saved on this iPad until you tap Save. Pencil writes; fingers move the page."
        let toolbar = UIStackView(arrangedSubviews: [tools, actions, status]); toolbar.axis = .vertical; toolbar.spacing = 10
        toolbar.isLayoutMarginsRelativeArrangement = true
        toolbar.directionalLayoutMargins = NSDirectionalEdgeInsets(top: 12, leading: 16, bottom: 10, trailing: 16)
        let layout = UIStackView(arrangedSubviews: [toolbar, scroll]); layout.axis = .vertical
        layout.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(layout)
        NSLayoutConstraint.activate([layout.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            layout.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
            layout.leadingAnchor.constraint(equalTo: view.leadingAnchor), layout.trailingAnchor.constraint(equalTo: view.trailingAnchor)])
        scroll.delegate = self; scroll.backgroundColor = .secondarySystemBackground
        scroll.panGestureRecognizer.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        scroll.pinchGestureRecognizer?.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        let size = CGSize(width: request.ink.space.width, height: request.ink.space.height)
        paper.frame = CGRect(origin: .zero, size: size); paper.backgroundColor = .white
        scroll.addSubview(paper); scroll.contentSize = size
        let imageView = UIImageView(frame: paper.bounds)
        imageView.image = Self.pageImage(request.image)
        imageView.contentMode = .scaleToFill
        paper.addSubview(imageView)
        for layer in [contextCanvas, canvas] {
            layer.frame = paper.bounds; layer.backgroundColor = .clear; layer.isOpaque = false
            layer.overrideUserInterfaceStyle = .light
            layer.drawingPolicy = .pencilOnly; layer.isScrollEnabled = false
            layer.minimumZoomScale = 0.05; layer.maximumZoomScale = 8
            layer.contentInsetAdjustmentBehavior = .never
            layer.panGestureRecognizer.isEnabled = false; layer.pinchGestureRecognizer?.isEnabled = false
            paper.addSubview(layer)
        }
        contextCanvas.isUserInteractionEnabled = false
        canvas.delegate = self
        changeTool(); initialized = true
        if imageView.image == nil { failed("Could not render the PDF background. Close and reopen handwriting."); canvas.isUserInteractionEnabled = false }
    }

    static func pageImage(_ string: String) -> UIImage? {
        guard let comma = string.firstIndex(of: ","), let data = Data(base64Encoded: String(string[string.index(after: comma)...])),
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = properties[kCGImagePropertyPixelWidth] as? Int, let h = properties[kCGImagePropertyPixelHeight] as? Int,
              w > 0, h > 0, w <= 4096, h <= 4096, w * h <= 8_100_000 else { return nil }
        return UIImage(data: data)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        guard scroll.bounds.width > 0, scroll.bounds.height > 0 else { return }
        let fit = min(scroll.bounds.width / paper.bounds.width, scroll.bounds.height / paper.bounds.height)
        scroll.minimumZoomScale = min(1, fit); scroll.maximumZoomScale = max(4, fit)
        if !initialFit { initialFit = true; scroll.setZoomScale(fit, animated: false) }
        scrollViewDidZoom(scroll)
    }
    func viewForZooming(in scrollView: UIScrollView) -> UIView? { paper }
    func scrollViewDidZoom(_ scrollView: UIScrollView) {
        // Have PencilKit render at the actual zoom instead of stretching a
        // bitmap. The outer inverse transform leaves page coordinates intact.
        let zoom = scroll.zoomScale
        guard zoom > 0 else { return }
        for layer in [canvas, contextCanvas] {
            let size = CGSize(width: paper.bounds.width * zoom, height: paper.bounds.height * zoom)
            layer.bounds = CGRect(origin: .zero, size: size)
            layer.zoomScale = zoom; layer.contentSize = size; layer.contentOffset = .zero
            layer.transform = CGAffineTransform(scaleX: 1 / zoom, y: 1 / zoom)
            layer.center = CGPoint(x: paper.bounds.midX, y: paper.bounds.midY)
        }
        scroll.contentInset = UIEdgeInsets(top: max(0, (scroll.bounds.height - paper.frame.height) / 2),
            left: max(0, (scroll.bounds.width - paper.frame.width) / 2), bottom: 0, right: 0)
    }

    @objc private func changeTool() {
        if tool.selectedSegmentIndex == 3 { canvas.tool = PKEraserTool(.vector); return }
        let marker = tool.selectedSegmentIndex == 2
        let type: PKInkingTool.InkType = marker ? .marker : tool.selectedSegmentIndex == 1 ? .monoline : .pen
        let sizes: [CGFloat] = marker ? [8, 14, 24] : [1, 2, 4]
        canvas.tool = PKInkingTool(type, color: color, width: sizes[max(0, width.selectedSegmentIndex)])
    }
    @objc private func undoInk() { canvas.undoManager?.undo() }
    @objc private func redoInk() { canvas.undoManager?.redo() }
    func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
        guard initialized else { return }
        do { try persist(); status.text = "Saved on this iPad. Tap Save to sync." }
        catch { status.text = "Could not save locally: \(error.localizedDescription). Keep this page open." }
    }
    private func persist() throws {
        draft.drawing = canvas.drawing.dataRepresentation()
        try store.write(draft)
    }
    @objc private func close() {
        guard !saving else { return }
        do { try persist(); onClose?() }
        catch { failed("Could not keep the draft: \(error.localizedDescription)") }
    }
    @objc private func save() {
        guard !saving else { return }
        do {
            try persist()
            let ink = try codec.export(canvas.drawing, space: request.ink.space)
            let object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(ink))
            setSaving(true); status.text = "Saving to Gamma…"
            onSave?(["requestId": request.requestId, "blockId": draft.blockId,
                     "expectedURL": draft.expectedURL as Any? ?? NSNull(), "asCopy": draft.asCopy, "ink": object])
        } catch { failed(error.localizedDescription) }
    }
    private func setSaving(_ value: Bool) {
        saving = value; canvas.isUserInteractionEnabled = !value
        navigationItem.leftBarButtonItem?.isEnabled = !value
        navigationItem.rightBarButtonItem?.isEnabled = !value
        tool.isEnabled = !value; width.isEnabled = !value
    }
    func saved() {
        do { try store.remove(draft.key); onClose?() }
        catch { failed("Saved to Gamma, but could not clear the local draft. Close keeps it for recovery.") }
    }
    func failed(_ message: String) {
        setSaving(false); status.text = message
        let alert = UIAlertController(title: "Handwriting kept on iPad", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Keep editing", style: .cancel))
        alert.addAction(UIAlertAction(title: "Save as new group", style: .default) { [weak self] _ in
            guard let self else { return }
            self.draft.blockId = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            self.draft.expectedURL = nil; self.draft.asCopy = true; self.save()
        })
        if presentedViewController == nil { present(alert, animated: true) }
    }
}

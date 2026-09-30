import Combine
import PDFKit
import SwiftUI

/// A PDF with its handwriting: PDFKit shows the pages (fingers scroll,
/// zoom and select text), each page carries an InkPageView as its overlay,
/// and the Apple Pencil writes. Ink lives in pdf.js's frame at scale 1 —
/// the displayed page, points from its top-left, rotation applied — the
/// frame the web app draws and exports in; the core's viewportTransform
/// maps PDFKit's page space to it (docs/dev/handwriting.md "The stroke file").
struct PDFReader: UIViewControllerRepresentable {
    let url: URL
    @ObservedObject var ink: InkSession

    func makeUIViewController(context: Context) -> PDFReaderController {
        PDFReaderController(url: url, ink: ink)
    }

    func updateUIViewController(_ controller: PDFReaderController, context: Context) {}
}

final class PDFReaderController: UIViewController, PDFPageOverlayViewProvider, UIPencilInteractionDelegate {
    private struct Viewport {
        let toGamma: CGAffineTransform   // PDF page space → the ink frame
        let size: CGSize                 // the page in the ink frame
    }

    private struct Stroke {
        let page: PDFPage
        let index: Int
        let viewport: Viewport
        let style: [String: Any]
        var samples = StrokeSamples()
        var erasing: Bool
    }

    private let url: URL
    private let ink: InkSession
    private let pdfView = PDFView()
    private let pencil = PencilRecognizer(target: nil, action: nil)
    private var overlays: [Int: InkPageView] = [:]
    private var viewports: [Int: Viewport] = [:]
    private var stroke: Stroke?
    private var lastTool: InkTool = .preset(0)
    private var bag = Set<AnyCancellable>()

    init(url: URL, ink: InkSession) {
        self.url = url
        self.ink = ink
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override func viewDidLoad() {
        super.viewDidLoad()
        pdfView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(pdfView)
        NSLayoutConstraint.activate([
            pdfView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            pdfView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            pdfView.topAnchor.constraint(equalTo: view.topAnchor),
            pdfView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        pdfView.displayMode = .singlePageContinuous
        pdfView.displayDirection = .vertical
        pdfView.displaysPageBreaks = true
        pdfView.pageOverlayViewProvider = self
        pdfView.document = PDFDocument(url: url)
        pdfView.autoScales = true

        pencil.onBegan = { [weak self] touch in self?.began(touch) }
        pencil.onMoved = { [weak self] touches, predicted in self?.moved(touches, predicted) }
        pencil.onEnded = { [weak self] _, cancelled in self?.ended(cancelled: cancelled) }
        pdfView.addGestureRecognizer(pencil)
        let interaction = UIPencilInteraction()
        interaction.delegate = self
        view.addInteraction(interaction)

        ink.$changed.receive(on: RunLoop.main).sink { [weak self] _ in self?.redraw() }.store(in: &bag)
        ink.$tool.receive(on: RunLoop.main).sink { [weak self] _ in self?.touchTypes() }.store(in: &bag)
        ink.$jumpTarget.compactMap { $0 }.receive(on: RunLoop.main).sink { [weak self] j in self?.jump(to: j.id) }.store(in: &bag)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        touchTypes()
    }

    /// PDFKit's scrolling and zooming take fingers; the Pencil writes —
    /// unless the hand is armed, when the Pencil scrolls too.
    private func touchTypes() {
        let direct = NSNumber(value: UITouch.TouchType.direct.rawValue)
        let pencilType = NSNumber(value: UITouch.TouchType.pencil.rawValue)
        let types = ink.tool == .hand ? [direct, pencilType] : [direct]
        pencil.isEnabled = ink.tool != .hand
        for scroll in pdfView.descendants(of: UIScrollView.self) {
            scroll.panGestureRecognizer.allowedTouchTypes = types
            scroll.pinchGestureRecognizer?.allowedTouchTypes = [direct]
        }
    }

    // --- page geometry ------------------------------------------------------------------------

    private func viewport(_ page: PDFPage, _ index: Int) -> Viewport? {
        if let vp = viewports[index] { return vp }
        let box = page.bounds(for: .cropBox)
        let args: [Any] = [[Double(box.minX), Double(box.minY), Double(box.maxX), Double(box.maxY)], page.rotation]
        guard let out = (try? ink.replica?.pure("viewportTransform", args)) as? [String: Any] else { return nil }
        let t = out.array("transform").compactMap { ($0 as? NSNumber)?.doubleValue }
        guard t.count == 6 else { return nil }
        let vp = Viewport(toGamma: CGAffineTransform(a: t[0], b: t[1], c: t[2], d: t[3], tx: t[4], ty: t[5]),
                          size: CGSize(width: out.double("width") ?? Double(box.width), height: out.double("height") ?? Double(box.height)))
        viewports[index] = vp
        return vp
    }

    /// The ink frame → an overlay's coordinates: the inverse viewport
    /// transform, then PDFKit's page → view mapping (read off three points).
    private func inkToOverlay(_ page: PDFPage, _ index: Int, _ overlay: UIView) -> CGAffineTransform? {
        guard let vp = viewport(page, index) else { return nil }
        func map(_ p: CGPoint) -> CGPoint { overlay.convert(pdfView.convert(p, from: page), from: pdfView) }
        let o = map(.zero), x = map(CGPoint(x: 1, y: 0)), y = map(CGPoint(x: 0, y: 1))
        let pageToOverlay = CGAffineTransform(a: x.x - o.x, b: x.y - o.y, c: y.x - o.x, d: y.y - o.y, tx: o.x, ty: o.y)
        return vp.toGamma.inverted().concatenating(pageToOverlay)
    }

    private func inkPoint(_ touch: UITouch, _ s: Stroke) -> CGPoint {
        pdfView.convert(touch.preciseLocation(in: pdfView), to: s.page).applying(s.viewport.toGamma)
    }

    // --- overlays -----------------------------------------------------------------------------

    func pdfView(_ view: PDFView, overlayViewFor page: PDFPage) -> UIView? {
        guard let index = pdfView.document?.index(for: page) else { return nil }
        let overlay = overlays[index] ?? InkPageView()
        overlays[index] = overlay
        if let vp = viewport(page, index) { overlay.pageSize = vp.size }
        overlay.transformProvider = { [weak self, weak overlay] in
            guard let self, let overlay else { return nil }
            return self.inkToOverlay(page, index, overlay)
        }
        overlay.show(ink.groupList(on: .pdf(index + 1)))
        return overlay
    }

    func pdfView(_ pdfView: PDFView, willEndDisplayingOverlayView overlayView: UIView, for page: PDFPage) {
        if let index = pdfView.document?.index(for: page) { overlays[index] = nil }
    }

    private func redraw() {
        for (index, overlay) in overlays { overlay.show(ink.groupList(on: .pdf(index + 1))) }
    }

    // --- the Pencil -----------------------------------------------------------------------------

    private func began(_ touch: UITouch) {
        let location = touch.preciseLocation(in: pdfView)
        guard let page = pdfView.page(for: location, nearest: true), let index = pdfView.document?.index(for: page),
              let vp = viewport(page, index) else { return }
        let erasing = ink.tool == .eraser
        guard erasing || ink.drawing, let style = erasing ? [:] : ink.style else { return }
        var s = Stroke(page: page, index: index, viewport: vp, style: style, erasing: erasing)
        let point = inkPoint(touch, s)
        if erasing { ink.erase(at: point, radius: eraserRadius, key: .pdf(index + 1)) } else { s.samples.add(touch, at: point) }
        stroke = s
        drawLive([])
    }

    private func moved(_ touches: [UITouch], _ predicted: [UITouch]) {
        guard var s = stroke else { return }
        for t in touches {
            let point = inkPoint(t, s)
            if s.erasing { ink.erase(at: point, radius: eraserRadius, key: .pdf(s.index + 1)) } else { s.samples.add(t, at: point) }
        }
        stroke = s
        // the preview runs a little ahead (never stored), as in the browser
        drawLive(predicted.prefix(3).map { inkPoint($0, s) })
    }

    private func ended(cancelled: Bool) {
        guard let s = stroke else { return }
        stroke = nil
        overlays[s.index]?.clearLive()
        if cancelled || s.erasing { return }
        ink.addStroke(s.samples.samples, t0: s.samples.t0, key: .pdf(s.index + 1), size: s.viewport.size)
    }

    private func drawLive(_ ahead: [CGPoint]) {
        guard let s = stroke, !s.erasing, let overlay = overlays[s.index] else { return }
        overlay.drawLive(s.samples.points + ahead, width: CGFloat(s.style.double("size") ?? 2), color: s.style.string("color"),
                         opacity: CGFloat(s.style.double("opacity") ?? 1), highlighter: s.style.string("tool") == "highlighter")
    }

    /// The eraser's reach, 9 screen points whatever the zoom (the web's medium size).
    private var eraserRadius: Double { 9 / Double(max(pdfView.scaleFactor, 0.1)) }

    func pencilInteractionDidTap(_ interaction: UIPencilInteraction) {
        if ink.tool == .eraser {
            ink.tool = lastTool
        } else {
            lastTool = ink.tool
            ink.tool = .eraser
        }
    }

    // --- from the notes -------------------------------------------------------------------------

    private func jump(to id: String) {
        guard case .pdf(let n)? = ink.key(of: id), let page = pdfView.document?.page(at: n - 1), let vp = viewport(page, n - 1) else { return }
        if let g = ink.groups[id], let b = (try? ink.replica?.pure("inkBounds", [g.ink])) as? [Double], b.count == 4 {
            let rect = CGRect(x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1])
            pdfView.go(to: rect.applying(vp.toGamma.inverted()).insetBy(dx: -40, dy: -40), on: page)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.overlays[n - 1]?.flash(rect) }
        } else {
            pdfView.go(to: page)
        }
    }
}

extension UIView {
    /// Every view under this one of a type (PDFKit's inner scroll views).
    func descendants<T: UIView>(of type: T.Type) -> [T] {
        subviews.flatMap { sub -> [T] in ((sub as? T).map { [$0] } ?? []) + sub.descendants(of: type) }
    }
}

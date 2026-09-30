import Combine
import SwiftUI

/// A notebook: its sheets one under the other, each its paper (background
/// and pattern from the core's paperLines, the web's notebook.js) with its
/// handwriting over it, written on with the Pencil like a PDF page. Writing
/// low on the last sheet adds the next; "Add page" adds one too. The paper
/// menu sets a sheet's paper, or the paper new sheets get
/// (docs/dev/notebooks.md).
struct NotebookReader: View {
    @ObservedObject var ink: InkSession
    @State private var current = ""
    @State private var paperOpen = false

    var body: some View {
        NotebookCanvas(ink: ink, current: $current)
            .overlay(alignment: .bottomTrailing) {
                Button { paperOpen = true } label: {
                    Label("Paper", systemImage: "doc.plaintext")
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(.thinMaterial, in: Capsule())
                }
                .padding(16)
                .popover(isPresented: $paperOpen) {
                    PaperMenu(ink: ink, sheetId: current.isEmpty ? (ink.sheets.last?.string("id") ?? "") : current)
                        .frame(minWidth: 320)
                }
            }
    }
}

struct NotebookCanvas: UIViewControllerRepresentable {
    @ObservedObject var ink: InkSession
    @Binding var current: String

    func makeUIViewController(context: Context) -> NotebookController {
        let controller = NotebookController(ink: ink)
        controller.onCurrent = { id in DispatchQueue.main.async { current = id } }
        return controller
    }

    func updateUIViewController(_ controller: NotebookController, context: Context) {}
}

/// One sheet: its paper and its handwriting, in the sheet's own frame
/// (points from the top-left, the frame its ink files are in).
final class SheetView: UIView {
    let id: String
    let ink = InkPageView()
    private let lines = CAShapeLayer()
    private let dots = CAShapeLayer()
    private let number = UILabel()

    init(id: String) {
        self.id = id
        super.init(frame: .zero)
        layer.shadowColor = UIColor.black.cgColor
        layer.shadowOpacity = 0.18
        layer.shadowRadius = 4
        layer.shadowOffset = CGSize(width: 0, height: 1)
        for l in [lines, dots] {
            l.fillColor = nil
            layer.addSublayer(l)
        }
        dots.lineCap = .round
        number.font = .systemFont(ofSize: 10)
        number.textColor = UIColor(white: 0.55, alpha: 1)
        addSubview(ink)
        addSubview(number)
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    func configure(paper: [String: Any], geometry: [String: Any], index: Int) {
        let w = CGFloat(paper.double("width") ?? 595.28), h = CGFloat(paper.double("height") ?? 841.89)
        bounds = CGRect(x: 0, y: 0, width: w, height: h)
        backgroundColor = UIColor(gammaHex: paper.string("color"))
        let line = UIColor(gammaHex: paper.string("line")).cgColor
        let lp = CGMutablePath()
        for raw in geometry.array("lines") {
            let v = (raw as? [Any] ?? []).compactMap { ($0 as? NSNumber)?.doubleValue }
            guard v.count == 4 else { continue }
            lp.move(to: CGPoint(x: v[0], y: v[1]))
            lp.addLine(to: CGPoint(x: v[2], y: v[3]))
        }
        lines.path = lp
        lines.strokeColor = line
        lines.lineWidth = 0.5
        let dp = CGMutablePath()
        for p in InkPageView.points(geometry.array("dots")) {
            dp.move(to: p)
            dp.addLine(to: p)
        }
        dots.path = dp
        dots.strokeColor = line
        dots.lineWidth = 1.8
        for l in [lines, dots] { l.frame = bounds }
        ink.frame = bounds
        ink.pageSize = bounds.size
        number.text = "\(index + 1)"
        number.sizeToFit()
        number.frame.origin = CGPoint(x: w - number.frame.width - 8, y: h - number.frame.height - 6)
    }

    var paperSize: CGSize { bounds.size }

    func setZoom(_ zoom: CGFloat) {
        ink.zoom = zoom
        let scale = min(12, max(1, (window?.screen.scale ?? 2) * zoom))
        lines.contentsScale = scale
        dots.contentsScale = scale
    }
}

final class NotebookController: UIViewController, UIScrollViewDelegate, UIPencilInteractionDelegate {
    var onCurrent: ((String) -> Void)?
    private let ink: InkSession
    private let scroll = UIScrollView()
    private let content = UIView()
    private let addButton = UIButton(type: .system)
    private let pencil = PencilRecognizer(target: nil, action: nil)
    private var sheetViews: [String: SheetView] = [:]
    private var order: [String] = []
    private var stroke: (sheet: SheetView, style: [String: Any], samples: StrokeSamples, erasing: Bool)?
    private var fitted = false
    private var lastTool: InkTool = .preset(0)
    private var bag = Set<AnyCancellable>()
    private let margin: CGFloat = 24
    private let gap: CGFloat = 20

    init(ink: InkSession) {
        self.ink = ink
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .secondarySystemBackground
        scroll.frame = view.bounds
        scroll.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        scroll.delegate = self
        scroll.minimumZoomScale = 0.2
        scroll.maximumZoomScale = 8
        scroll.alwaysBounceVertical = true
        view.addSubview(scroll)
        scroll.addSubview(content)
        addButton.setTitle("Add page", for: .normal)
        addButton.setImage(UIImage(systemName: "plus"), for: .normal)
        addButton.layer.borderWidth = 1.5
        addButton.layer.borderColor = UIColor.separator.cgColor
        addButton.layer.cornerRadius = 10
        addButton.addAction(UIAction { [weak self] _ in self?.ink.addSheet() }, for: .touchUpInside)
        content.addSubview(addButton)

        let direct = NSNumber(value: UITouch.TouchType.direct.rawValue)
        scroll.panGestureRecognizer.allowedTouchTypes = [direct]
        scroll.pinchGestureRecognizer?.allowedTouchTypes = [direct]
        pencil.onBegan = { [weak self] t in self?.began(t) }
        pencil.onMoved = { [weak self] touches, predicted in self?.moved(touches, predicted) }
        pencil.onEnded = { [weak self] _, cancelled in self?.ended(cancelled: cancelled) }
        scroll.addGestureRecognizer(pencil)
        let interaction = UIPencilInteraction()
        interaction.delegate = self
        view.addInteraction(interaction)

        ink.$sheets.receive(on: RunLoop.main).sink { [weak self] _ in self?.rebuild() }.store(in: &bag)
        ink.$changed.receive(on: RunLoop.main).sink { [weak self] _ in self?.redraw() }.store(in: &bag)
        ink.$tool.receive(on: RunLoop.main).sink { [weak self] tool in
            guard let self else { return }
            let pencilType = NSNumber(value: UITouch.TouchType.pencil.rawValue)
            self.pencil.isEnabled = tool != .hand
            self.scroll.panGestureRecognizer.allowedTouchTypes = tool == .hand ? [direct, pencilType] : [direct]
        }.store(in: &bag)
        ink.$jumpTarget.compactMap { $0 }.receive(on: RunLoop.main).sink { [weak self] j in self?.jump(to: j.id) }.store(in: &bag)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        if !fitted, view.bounds.width > 0, !order.isEmpty {
            fitted = true
            fitWidth()
        }
    }

    func viewForZooming(in scrollView: UIScrollView) -> UIView? { content }

    func scrollViewDidEndZooming(_ scrollView: UIScrollView, with view: UIView?, atScale scale: CGFloat) {
        for s in sheetViews.values { s.setZoom(scale) }
    }

    func scrollViewDidScroll(_ scrollView: UIScrollView) { reportCurrent() }

    // --- layout ---------------------------------------------------------------------------

    private func rebuild() {
        guard let replica = ink.replica else { return }
        var y = margin, widest: CGFloat = 0
        var next: [String: SheetView] = [:]
        order = []
        for (i, sheet) in ink.sheets.enumerated() {
            let id = sheet.string("id")
            let view = sheetViews[id] ?? SheetView(id: id)
            let paper = sheet.dict("paper")
            let geometry = ((try? replica.pure("paperLines", [paper])) as? [String: Any]) ?? [:]
            view.configure(paper: paper, geometry: geometry, index: i)
            if view.superview == nil { content.addSubview(view) }
            view.frame.origin = CGPoint(x: 0, y: y)
            y += view.bounds.height + gap
            widest = max(widest, view.bounds.width)
            next[id] = view
            order.append(id)
        }
        for (id, view) in sheetViews where next[id] == nil { view.removeFromSuperview() }
        sheetViews = next
        widest = max(widest, 595.28)
        for id in order { sheetViews[id]?.frame.origin.x = margin + (widest - (sheetViews[id]?.bounds.width ?? 0)) / 2 }
        addButton.frame = CGRect(x: margin, y: y, width: widest, height: 56)
        let size = CGSize(width: widest + 2 * margin, height: y + 56 + margin)
        let zoom = scroll.zoomScale
        content.bounds = CGRect(origin: .zero, size: size)
        content.frame.origin = .zero
        scroll.contentSize = CGSize(width: size.width * zoom, height: size.height * zoom)
        for s in sheetViews.values { s.setZoom(zoom) }
        redraw()
        if !fitted, view.bounds.width > 0, !order.isEmpty {
            fitted = true
            fitWidth()
        }
    }

    private func fitWidth() {
        let width = content.bounds.width
        guard width > 0 else { return }
        let zoom = min(scroll.maximumZoomScale, max(scroll.minimumZoomScale, view.bounds.width / width))
        scroll.setZoomScale(zoom, animated: false)
        for s in sheetViews.values { s.setZoom(zoom) }
    }

    private func redraw() {
        for (id, view) in sheetViews { view.ink.show(ink.groupList(on: .sheet(id))) }
    }

    private func reportCurrent() {
        let mid = CGPoint(x: scroll.bounds.midX, y: scroll.bounds.midY)
        var best = "", distance = CGFloat.infinity
        for id in order {
            guard let v = sheetViews[id] else { continue }
            let r = scroll.convert(v.bounds, from: v)
            let d = mid.y < r.minY ? r.minY - mid.y : mid.y > r.maxY ? mid.y - r.maxY : 0
            if d < distance { distance = d; best = id }
        }
        if !best.isEmpty { onCurrent?(best) }
    }

    // --- the Pencil --------------------------------------------------------------------------

    private func sheetView(at touch: UITouch) -> SheetView? {
        for id in order {
            guard let v = sheetViews[id] else { continue }
            if v.bounds.contains(touch.preciseLocation(in: v)) { return v }
        }
        return nil
    }

    private func began(_ touch: UITouch) {
        guard let sheet = sheetView(at: touch) else { return }
        let erasing = ink.tool == .eraser
        guard erasing || ink.drawing, let style = erasing ? [:] : ink.style else { return }
        var samples = StrokeSamples()
        let point = touch.preciseLocation(in: sheet)
        if erasing { ink.erase(at: point, radius: eraserRadius, key: .sheet(sheet.id)) } else { samples.add(touch, at: point) }
        stroke = (sheet, style, samples, erasing)
        drawLive([])
    }

    private func moved(_ touches: [UITouch], _ predicted: [UITouch]) {
        guard var s = stroke else { return }
        for t in touches {
            let point = t.preciseLocation(in: s.sheet)
            if s.erasing { ink.erase(at: point, radius: eraserRadius, key: .sheet(s.sheet.id)) } else { s.samples.add(t, at: point) }
        }
        stroke = s
        drawLive(predicted.prefix(3).map { $0.preciseLocation(in: s.sheet) })
    }

    private func ended(cancelled: Bool) {
        guard let s = stroke else { return }
        stroke = nil
        s.sheet.ink.clearLive()
        if cancelled || s.erasing { return }
        ink.addStroke(s.samples.samples, t0: s.samples.t0, key: .sheet(s.sheet.id), size: s.sheet.paperSize)
    }

    private func drawLive(_ ahead: [CGPoint]) {
        guard let s = stroke, !s.erasing else { return }
        s.sheet.ink.drawLive(s.samples.points + ahead, width: CGFloat(s.style.double("size") ?? 2), color: s.style.string("color"),
                             opacity: CGFloat(s.style.double("opacity") ?? 1), highlighter: s.style.string("tool") == "highlighter")
    }

    private var eraserRadius: Double { 9 / Double(max(scroll.zoomScale, 0.1)) }

    func pencilInteractionDidTap(_ interaction: UIPencilInteraction) {
        if ink.tool == .eraser {
            ink.tool = lastTool
        } else {
            lastTool = ink.tool
            ink.tool = .eraser
        }
    }

    private func jump(to id: String) {
        guard case .sheet(let sheetId)? = ink.key(of: id), let v = sheetViews[sheetId] else { return }
        var box = v.bounds
        if let g = ink.groups[id], let b = (try? ink.replica?.pure("inkBounds", [g.ink])) as? [Double], b.count == 4 {
            box = CGRect(x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1])
        }
        scroll.scrollRectToVisible(scroll.convert(box.insetBy(dx: -40, dy: -80), from: v), animated: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { v.ink.flash(box) }
    }
}

/// A sheet's paper, and the paper new sheets get: the web's paper menu
/// (notebook/NotebookViewer.jsx PaperMenu) on the iPad.
struct PaperMenu: View {
    @ObservedObject var ink: InkSession
    let sheetId: String

    private var sheet: [String: Any] { ink.sheets.first { $0.string("id") == sheetId } ?? [:] }
    private var paper: [String: Any] { sheet.dict("paper") }
    private static let sizes: [(String, Double, Double)] = [("A4", 595.28, 841.89), ("Letter", 612, 792), ("A5", 419.53, 595.28)]
    private static let colors: [(String, String)] = [("White", "#ffffff"), ("Cream", "#fbf7ec"), ("Gray", "#f2f3f5"), ("Dark", "#2b2d31")]

    var body: some View {
        let w = paper.double("width") ?? 595.28, h = paper.double("height") ?? 841.89
        let landscape = w > h
        Form {
            Section("Page \(sheet.int("number") ?? 1)") {
                Picker("Size", selection: Binding(get: { Self.sizes.first { abs($0.1 - min(w, h)) < 0.6 && abs($0.2 - max(w, h)) < 0.6 }?.0 ?? "" },
                                                 set: { name in
                                                     guard let s = Self.sizes.first(where: { $0.0 == name }) else { return }
                                                     set(["width": landscape ? s.2 : s.1, "height": landscape ? s.1 : s.2])
                                                 })) {
                    ForEach(Self.sizes, id: \.0) { Text($0.0).tag($0.0) }
                }
                Picker("Orientation", selection: Binding(get: { landscape }, set: { turn in
                    if turn != landscape { set(["width": h, "height": w]) }
                })) {
                    Text("Portrait").tag(false)
                    Text("Landscape").tag(true)
                }
                Picker("Pattern", selection: Binding(get: { paper.string("pattern") }, set: { set(["pattern": $0]) })) {
                    Text("Blank").tag("blank")
                    Text("Ruled").tag("ruled")
                    Text("Grid").tag("grid")
                    Text("Dots").tag("dots")
                }
                .pickerStyle(.segmented)
                if paper.string("pattern") != "blank" {
                    Picker("Spacing", selection: Binding(get: { paper.double("spacing") ?? 24 }, set: { set(["spacing": $0]) })) {
                        Text("Narrow").tag(18.0)
                        Text("Medium").tag(24.0)
                        Text("Wide").tag(32.0)
                    }
                    .pickerStyle(.segmented)
                }
                HStack {
                    Text("Background")
                    Spacer()
                    ForEach(Self.colors, id: \.1) { item in swatch(name: item.0, hex: item.1) }
                }
            }
            Section {
                Button("Use for new pages") { ink.setPaper(paper, sheet: nil, forNew: true) }
            }
        }
    }

    private func swatch(name: String, hex: String) -> some View {
        let selected = paper.string("color") == hex
        return Button { set(["color": hex, "line": hex == "#2b2d31" ? "#51555c" : "#c8d1dc"]) } label: {
            Circle().fill(Color(UIColor(gammaHex: hex))).frame(width: 24, height: 24)
                .overlay(Circle().stroke(selected ? Color.accentColor : Color.secondary, lineWidth: selected ? 3 : 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(name)
    }

    private func set(_ patch: [String: Any]) {
        var next = paper
        for (k, v) in patch { next[k] = v }
        ink.setPaper(next, sheet: sheetId, forNew: false)
    }
}

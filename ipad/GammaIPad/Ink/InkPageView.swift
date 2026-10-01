import UIKit
import UIKit.UIGestureRecognizerSubclass

/// One page's (or one notebook sheet's) handwriting on screen: every stroke
/// as a vector layer drawn from the web app's own geometry (the outline a
/// pen stroke fills, ink.js penOutline; a highlighter's centre line), in the
/// page's frame — points from the top-left corner, what gamma-ink stores —
/// under one transform to this view, and the stroke being drawn on top.
/// It takes no touches: the reader's Pencil recognizer does.
final class InkPageView: UIView {
    /// The page's frame → this view's coordinates.
    var toView: CGAffineTransform = .identity {
        didSet { applyTransform() }
    }
    /// The page's size in its own frame (points).
    var pageSize: CGSize = .zero {
        didSet { container.bounds = CGRect(origin: .zero, size: pageSize) }
    }
    /// Asked again whenever this view's bounds change (a zoom, a relayout).
    var transformProvider: (() -> CGAffineTransform?)?
    /// A zoom applied above this view (a scroll view's): strokes render at
    /// the resolution it shows them at.
    var zoom: CGFloat = 1 {
        didSet { applyTransform() }
    }

    private let container = CALayer()
    private let strokes = CALayer()
    private let live = CAShapeLayer()
    private let flashLayer = CAShapeLayer()
    private var renderScale: CGFloat = 2

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        backgroundColor = .clear
        isOpaque = false
        container.anchorPoint = .zero
        container.position = .zero
        layer.addSublayer(container)
        container.addSublayer(strokes)
        live.fillColor = nil
        live.lineCap = .round
        live.lineJoin = .round
        container.addSublayer(live)
        flashLayer.fillColor = nil
        flashLayer.strokeColor = UIColor(red: 0.31, green: 0.55, blue: 1, alpha: 0.95).cgColor
        flashLayer.lineWidth = 1.5
        flashLayer.lineDashPattern = [5, 3]
        container.addSublayer(flashLayer)
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override func layoutSubviews() {
        super.layoutSubviews()
        if let t = transformProvider?() { toView = t }
    }

    private func applyTransform() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        container.setAffineTransform(toView)
        let scale = sqrt(abs(toView.a * toView.d - toView.b * toView.c)) * zoom
        let wanted = min(12, max(1, (window?.screen.scale ?? 2) * scale))
        if abs(wanted - renderScale) > 0.25 {
            renderScale = wanted
            let all: [CALayer] = (strokes.sublayers ?? []) + [live, flashLayer]
            for layer in all { layer.contentsScale = wanted }
        }
        CATransaction.commit()
    }

    // --- retained strokes -------------------------------------------------------------------

    /// Show these groups' strokes (their geometry from the core).
    func show(_ groups: [InkGroup]) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        strokes.sublayers?.forEach { $0.removeFromSuperlayer() }
        // pens of one colour share a layer; each highlighter stroke has its own
        // (they multiply onto the page one by one, as in the browser)
        var pens: [String: CGMutablePath] = [:]
        var penAlpha: [String: CGFloat] = [:]
        for g in groups {
            for item in g.geometry {
                let color = item.string("color")
                let opacity = CGFloat(item.double("opacity") ?? 1)
                let points = InkPageView.points(item.array("points"))
                if item.string("kind") == "line" {
                    let shape = CAShapeLayer()
                    shape.path = InkPageView.polyline(points)
                    shape.fillColor = nil
                    shape.strokeColor = UIColor(gammaHex: color, alpha: opacity).cgColor
                    shape.lineWidth = CGFloat(item.double("width") ?? 10)
                    shape.lineCap = .round
                    shape.lineJoin = .round
                    shape.compositingFilter = "multiplyBlendMode"
                    shape.contentsScale = renderScale
                    shape.frame = container.bounds
                    strokes.addSublayer(shape)
                } else {
                    let key = "\(color)|\(opacity)"
                    let path = pens[key] ?? CGMutablePath()
                    path.addPath(InkPageView.outline(points))
                    pens[key] = path
                    penAlpha[key] = opacity
                }
            }
        }
        for (key, path) in pens {
            let shape = CAShapeLayer()
            shape.path = path
            let color = String(key.split(separator: "|").first ?? "")
            shape.fillColor = UIColor(gammaHex: color, alpha: penAlpha[key] ?? 1).cgColor
            shape.strokeColor = nil
            shape.fillRule = .nonZero
            shape.contentsScale = renderScale
            shape.frame = container.bounds
            strokes.addSublayer(shape)
        }
        CATransaction.commit()
    }

    static func points(_ raw: [Any]) -> [CGPoint] {
        raw.compactMap { p in
            guard let xy = p as? [Any], xy.count >= 2, let x = xy[0] as? NSNumber, let y = xy[1] as? NSNumber else { return nil }
            return CGPoint(x: x.doubleValue, y: y.doubleValue)
        }
    }

    /// perfect-freehand's outline as the web draws it (ink.js
    /// svgPathFromPoints): quadratic curves through the edges' midpoints.
    static func outline(_ pts: [CGPoint]) -> CGPath {
        let path = CGMutablePath()
        guard let first = pts.first else { return path }
        if pts.count < 3 {
            path.move(to: first)
            path.addLine(to: CGPoint(x: first.x + 0.01, y: first.y))
            return path
        }
        path.move(to: first)
        for i in 0..<pts.count {
            let a = pts[i], b = pts[(i + 1) % pts.count]
            path.addQuadCurve(to: CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2), control: a)
        }
        path.closeSubpath()
        return path
    }

    static func polyline(_ pts: [CGPoint]) -> CGPath {
        let path = CGMutablePath()
        guard let first = pts.first else { return path }
        path.move(to: first)
        if pts.count == 1 { path.addLine(to: CGPoint(x: first.x + 0.01, y: first.y)) }
        for p in pts.dropFirst() { path.addLine(to: p) }
        return path
    }

    // --- the stroke being drawn ----------------------------------------------------------

    /// The stroke in progress, in the page's frame: a plain line at the
    /// tool's width (the saved stroke is the pen's outline).
    func drawLive(_ pts: [CGPoint], width: CGFloat, color: String, opacity: CGFloat, highlighter: Bool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        live.frame = container.bounds
        live.path = InkPageView.polyline(pts)
        live.lineWidth = width
        live.strokeColor = UIColor(gammaHex: color, alpha: opacity).cgColor
        live.compositingFilter = highlighter ? "multiplyBlendMode" : nil
        CATransaction.commit()
    }

    func clearLive() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        live.path = nil
        CATransaction.commit()
    }

    /// The brief outline after a jump from the notes (the web's inkFlash).
    func flash(_ box: CGRect) {
        flashLayer.frame = container.bounds
        flashLayer.path = CGPath(roundedRect: box.insetBy(dx: -6, dy: -6), cornerWidth: 4, cornerHeight: 4, transform: nil)
        flashLayer.opacity = 1
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1
        fade.toValue = 0
        fade.beginTime = CACurrentMediaTime() + 1.1
        fade.duration = 0.7
        fade.fillMode = .forwards
        fade.isRemovedOnCompletion = false
        flashLayer.add(fade, forKey: "fade")
    }
}

/// Apple Pencil only: the reader's pages and sheets take the Pencil's touches
/// through this, while fingers scroll, zoom and select as usual. It hands
/// out the coalesced touches (every sample the Pencil measured, 240 Hz)
/// with the predicted ones for the preview.
final class PencilRecognizer: UIGestureRecognizer {
    var onBegan: ((UITouch) -> Void)?
    var onMoved: (([UITouch], [UITouch]) -> Void)?
    var onEnded: ((UITouch?, Bool) -> Void)?
    private var tracked: UITouch?

    override init(target: Any?, action: Selector?) {
        super.init(target: target, action: action)
        allowedTouchTypes = [NSNumber(value: UITouch.TouchType.pencil.rawValue)]
        cancelsTouchesInView = true
        delaysTouchesBegan = false
        delaysTouchesEnded = false
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        guard tracked == nil, let t = touches.first, t.type == .pencil else { return }
        tracked = t
        state = .began
        onBegan?(t)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let t = tracked, touches.contains(t) else { return }
        state = .changed
        onMoved?(event.coalescedTouches(for: t) ?? [t], event.predictedTouches(for: t) ?? [])
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let t = tracked, touches.contains(t) else { return }
        onMoved?(event.coalescedTouches(for: t) ?? [t], [])
        state = .ended
        onEnded?(t, false)
        tracked = nil
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let t = tracked, touches.contains(t) else { return }
        state = .cancelled
        onEnded?(t, true)
        tracked = nil
    }

    override func reset() {
        super.reset()
        tracked = nil
    }
}

/// The samples of one Pencil stroke in a page's frame, as ink.js wants them:
/// {x, y, p (0..1), t (ms since the first)}; the lift keeps the last
/// contact's pressure (the Pencil reports none as it leaves the glass).
struct StrokeSamples {
    private(set) var samples: [[String: Double]] = []
    private(set) var points: [CGPoint] = []
    let t0 = Date().timeIntervalSince1970 * 1000
    private var start: TimeInterval?

    mutating func add(_ touch: UITouch, at point: CGPoint) {
        let begin = start ?? touch.timestamp
        start = begin
        let force = Double(touch.maximumPossibleForce > 0 ? touch.force / touch.maximumPossibleForce : 0.5)
        // no force (the lift, a touch that reports none): the last contact's pressure
        let p = force <= 0 ? (samples.last?["p"] ?? 0.5) : min(1, max(0, force))
        let t = max(samples.last?["t"] ?? 0, (touch.timestamp - begin) * 1000)
        if let last = points.last, last == point, samples.last?["p"] == p { return } // a repeat draws nothing new
        samples.append(["x": Double(point.x), "y": Double(point.y), "p": p, "t": t])
        points.append(point)
    }
}

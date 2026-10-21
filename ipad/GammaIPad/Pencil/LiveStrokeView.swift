import UIKit

/// The stroke being drawn, natively, over the web view, in the claim's look
/// (docs/dev/ipad.md "The pen", step 4), a frame or two ahead of what the
/// page could paint: a ribbon as wide as the brush's law makes each sample
/// (InkWidth; a calligraphy pen's from the travel against the claim's nib,
/// turned by the first sample's roll, its ends round where the page's are
/// the nib's flat corners), a dashed line, or the plain polyline at the tool's colour,
/// width and opacity; a highlighter multiplied onto the page; the samples
/// smoothed through the lazy brush when the preset smooths; along the
/// ruler (the claim's `snap`), the line between the outermost projections
/// of the samples onto its edge (RulerSnap), even end to end; and, once the
/// page snapped a held stroke, that shape instead (HeldShape), in the
/// selection blue for its first frames. The saved stroke is the web's (its
/// pen outline); each line stays until the web says it painted that stroke,
/// so the swap never blinks, and strokes written in quick succession each
/// keep theirs. It takes no touches.
final class LiveStrokeView: UIView {
    /// A finished stroke's line, waiting for the web's paint.
    struct Handle {
        fileprivate let layer: CAShapeLayer
    }

    /// A held shape's colour while it flashes: the web's SNAP_BLUE
    /// (InkLayer.jsx), the selection's.
    static let snapBlue = UIColor(red: 80 / 255, green: 140 / 255, blue: 1, alpha: 1)
    /// How long the flash lasts: the web's two frames at 60 Hz.
    static let flashDuration: CFTimeInterval = 0.034

    private var active: CAShapeLayer?
    /// The active stroke's held shape has flashed.
    private var flashed = false

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        isOpaque = false
        backgroundColor = .clear
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    /// The stroke in progress: its samples (`[x, y, p, t, …]`, StrokeRecorder),
    /// the predicted points after them, and the shape the page snapped it to,
    /// if any, which is drawn instead.
    func show(_ samples: [[Double]], predicted: [CGPoint], shape: HeldShape?, tool: InkTool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let line = active ?? newLayer(for: tool)
        active = line
        line.frame = bounds
        let width = CGFloat(max(tool.size, 0.5))
        let color = UIColor(cssHex: tool.color) ?? .label
        let ink = color.withAlphaComponent(CGFloat(tool.opacity))
        if let shape {
            // Even end to end, as a snapped stroke commits (no w, whatever its brush), in the preset's line.
            Self.stroke(line, path: shape.path, width: width, color: ink, dash: tool.dash)
            if !flashed {
                flashed = true
                line.add(Self.flash(), forKey: "flash")
            }
        } else if let snap = tool.snap {
            // Along the ruler: every sample projected onto its edge, the line
            // between the outermost two at the tool's width end to end and in
            // its dash, as the page commits it (a ruler line is even, like a
            // held one); no lazy brush and no prediction, as on the page.
            line.removeAnimation(forKey: "flash")
            let ends = RulerSnap.project(samples.map { Self.point($0) }, onto: snap)
            Self.stroke(line, path: Self.polyline(ends), width: width, color: ink, dash: tool.dash)
        } else {
            // a shape dropped while it flashed: no blue outline on the freehand line
            line.removeAnimation(forKey: "flash")
            // Smoothing trails the pen by its string, as the page's own line
            // does, so the prediction, which leads it, is left out.
            let drawn = LazyBrush.smooth(samples, level: tool.stabilize)
            let points: [CGPoint] = drawn.map { Self.point($0) } + (tool.stabilize > 0 ? [] : predicted)
            let read = drawn.map { InkWidth.sample($0) }
            if let dash = tool.dash {
                // even, even under pressure: the committed dashes have their widths
                Self.stroke(line, path: Self.polyline(points), width: width, color: ink, dash: dash)
            } else if let widths = InkWidth.widths(read, tool: tool.kind, brush: tool.brush, pressure: tool.pressure,
                                                    size: tool.size, live: true,
                                                    nib: tool.liveNib(roll: samples.first.flatMap { $0.count > 6 ? $0[6] : nil })) {
                var radii = widths.map { CGFloat(max(tool.size * $0, 0.5)) / 2 }
                // the predicted points at the width the stroke has reached
                if let last = radii.last, points.count > radii.count {
                    radii += Array(repeating: last, count: points.count - radii.count)
                }
                let (kept, keptRadii) = Self.thinned(points, radii)
                // A Pencil shades with its pressure: here at the band of its
                // mean so far, untextured (its grain comes with the web's stroke).
                let shades = InkWidth.opacities(read, tool: tool.kind, brush: tool.brush, pressure: tool.pressure)
                let shade = shades.map { InkWidth.opacityBand($0.reduce(0, +) / Double(max($0.count, 1))) } ?? 1
                Self.fill(line, path: Self.ribbon(kept, radii: keptRadii),
                          color: color.withAlphaComponent(CGFloat(tool.opacity * shade)))
            } else {
                Self.stroke(line, path: Self.polyline(points), width: width, color: ink, dash: nil)
            }
        }
        CATransaction.commit()
    }

    /// The stroke ended: its line stays until `remove`.
    func finish() -> Handle? {
        defer {
            active = nil
            flashed = false
        }
        return active.map(Handle.init)
    }

    /// The stroke was lost (a cancelled touch, or the page took the hold):
    /// its line goes now.
    func cancel() {
        if let active { remove(Handle(layer: active)) }
        active = nil
        flashed = false
    }

    func remove(_ handle: Handle) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        handle.layer.removeFromSuperlayer()
        CATransaction.commit()
    }

    private func newLayer(for tool: InkTool) -> CAShapeLayer {
        let shape = CAShapeLayer()
        shape.fillColor = nil
        shape.fillRule = .nonZero
        shape.lineCap = .round
        shape.lineJoin = .round
        shape.contentsScale = window?.screen.scale ?? traitCollection.displayScale
        // A highlighter multiplied onto the page, as the web paints it. Apple
        // documents compositingFilter as unsupported on iOS layers: where it
        // does not blend, the highlighter shows at its opacity, as before.
        if tool.multiply { shape.compositingFilter = "multiplyBlendMode" }
        layer.addSublayer(shape)
        return shape
    }

    private static func stroke(_ shape: CAShapeLayer, path: CGPath, width: CGFloat, color: UIColor, dash: [Double]?) {
        shape.path = path
        shape.fillColor = nil
        shape.strokeColor = color.cgColor
        shape.lineWidth = width
        shape.lineDashPattern = dash?.map { NSNumber(value: $0) }
        shape.lineDashPhase = 0
    }

    private static func fill(_ shape: CAShapeLayer, path: CGPath, color: UIColor) {
        shape.path = path
        shape.fillColor = color.cgColor
        shape.strokeColor = nil
        shape.lineDashPattern = nil
    }

    /// The held shape's first frames in the selection blue, then its colour
    /// (the layer's own, which the animation covers until it ends).
    private static func flash() -> CABasicAnimation {
        let flash = CABasicAnimation(keyPath: "strokeColor")
        flash.fromValue = snapBlue.cgColor
        flash.toValue = snapBlue.cgColor
        flash.duration = flashDuration
        return flash
    }

    private static func point(_ sample: [Double]) -> CGPoint {
        CGPoint(x: sample.first ?? 0, y: sample.count > 1 ? sample[1] : 0)
    }

    /// The ribbon's points without those closer than half a point to the
    /// last one kept (the last always kept): a 240 Hz trace is dense where
    /// the pen is slow, and each point is a disc to fill.
    private static func thinned(_ points: [CGPoint], _ radii: [CGFloat]) -> ([CGPoint], [CGFloat]) {
        let n = min(points.count, radii.count)
        guard n > 2 else { return (Array(points.prefix(n)), Array(radii.prefix(n))) }
        var keptPoints = [points[0]], keptRadii = [radii[0]]
        for i in 1..<n {
            let p = points[i], q = keptPoints[keptPoints.count - 1]
            let dx = p.x - q.x, dy = p.y - q.y
            if dx * dx + dy * dy >= 0.25 || i == n - 1 {
                keptPoints.append(p)
                keptRadii.append(radii[i])
            }
        }
        return (keptPoints, keptRadii)
    }

    static func polyline(_ points: [CGPoint]) -> CGPath {
        let path = CGMutablePath()
        guard let first = points.first else { return path }
        path.move(to: first)
        // a dot: a zero-length segment, which the round cap draws as a disc
        path.addLine(to: points.count == 1 ? first : points[1])
        for p in points.dropFirst(2) { path.addLine(to: p) }
        return path
    }

    /// A line whose width changes along it, filled: a disc of each point's
    /// radius, and between each two points the quadrilateral their discs'
    /// sides span. Every piece is wound the same way (the discs by a positive
    /// sweep, the quadrilaterals right side out, then back along the left),
    /// so under the non-zero rule the overlaps add up and the union is
    /// filled once, at the layer's one colour. A single point is a disc.
    /// Pure, so it is tested.
    nonisolated static func ribbon(_ points: [CGPoint], radii: [CGFloat]) -> CGPath {
        let path = CGMutablePath()
        let n = min(points.count, radii.count)
        for i in 0..<n where radii[i] > 0 {
            let p = points[i], r = radii[i]
            path.move(to: CGPoint(x: p.x + r, y: p.y))
            path.addRelativeArc(center: p, radius: r, startAngle: 0, delta: 2 * .pi)
            path.closeSubpath()
        }
        guard n > 1 else { return path }
        for i in 1..<n {
            let a = points[i - 1], b = points[i]
            let dx = b.x - a.x, dy = b.y - a.y
            let length = (dx * dx + dy * dy).squareRoot()
            guard length > 0 else { continue }
            // the unit normal to the left of the direction of travel
            let nx = -dy / length, ny = dx / length
            let ra = max(radii[i - 1], 0), rb = max(radii[i], 0)
            path.addLines(between: [
                CGPoint(x: a.x - nx * ra, y: a.y - ny * ra), CGPoint(x: b.x - nx * rb, y: b.y - ny * rb),
                CGPoint(x: b.x + nx * rb, y: b.y + ny * rb), CGPoint(x: a.x + nx * ra, y: a.y + ny * ra),
            ])
            path.closeSubpath()
        }
        return path
    }
}

/// The lazy brush behind a preset's smoothing, as the live line draws it
/// (the web's frontend/src/ink/stabilizer.js): the pen pulls the drawn point
/// behind it on a string, so a shaky hand draws a calm line. Pure. Without
/// the web's cusp rule and its catch-up at the lift: the committed stroke is
/// the web's, smoothed from the same samples, and the live line only has to
/// come close.
enum LazyBrush {
    /// The string's length per level, in CSS px (stabilizer.js STABILIZE_RADII).
    static let radii: [Double] = [0, 3, 6, 10, 16]

    /// The samples (`[x, y, …]`) as the brush draws them: the first where it
    /// is; then each sample farther from the brush than the string moves the
    /// brush toward it, to the string's length short of it, and comes out
    /// with the brush's x and y, the rest of it unchanged; a sample within
    /// the string moves nothing and is left out. Level 0, and a level
    /// without a string, give the samples back.
    static func smooth(_ samples: [[Double]], level: Int) -> [[Double]] {
        guard radii.indices.contains(level), radii[level] > 0, let first = samples.first, first.count >= 2 else {
            return samples
        }
        let string = radii[level]
        var bx = first[0], by = first[1]
        var out = [first]
        for sample in samples.dropFirst() where sample.count >= 2 {
            let dx = sample[0] - bx, dy = sample[1] - by
            let d = hypot(dx, dy)
            guard d > string else { continue }
            let pull = (d - string) / d
            bx += dx * pull
            by += dy * pull
            var moved = sample
            moved[0] = bx
            moved[1] = by
            out.append(moved)
        }
        return out
    }
}

/// A CSS hex colour, as the web app writes tool colours and its theme's
/// background: "#rgb", "#rrggbb" or "#rrggbbaa". Pure, so it is tested.
enum CSSColor {
    static func components(_ text: String) -> (red: Double, green: Double, blue: Double, alpha: Double)? {
        var hex = text.trimmingCharacters(in: .whitespaces)
        guard hex.hasPrefix("#") else { return nil }
        hex.removeFirst()
        if hex.count == 3 { hex = hex.map { "\($0)\($0)" }.joined() }
        guard hex.count == 6 || hex.count == 8, let n = UInt64(hex, radix: 16) else { return nil }
        let value = hex.count == 6 ? n << 8 | 0xff : n
        let byte = { (shift: UInt64) in Double((value >> shift) & 0xff) / 255 }
        return (byte(24), byte(16), byte(8), byte(0))
    }
}

extension UIColor {
    convenience init?(cssHex text: String) {
        guard let c = CSSColor.components(text) else { return nil }
        self.init(red: CGFloat(c.red), green: CGFloat(c.green), blue: CGFloat(c.blue), alpha: CGFloat(c.alpha))
    }
}

import UIKit

/// The stroke being drawn, natively, over the web view: a plain polyline at
/// the tool's colour and width, which is a frame or two ahead of what the
/// page could paint. The saved stroke is the web's (its pen outline); each
/// polyline stays until the web says it painted that stroke, so the swap
/// never blinks, and strokes written in quick succession each keep theirs.
/// It takes no touches.
final class LiveStrokeView: UIView {
    /// A finished stroke's polyline, waiting for the web's paint.
    struct Handle {
        fileprivate let layer: CAShapeLayer
    }

    private var active: CAShapeLayer?

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        isOpaque = false
        backgroundColor = .clear
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    /// The stroke in progress (its samples, then the predicted points).
    func show(_ points: [CGPoint], tool: InkTool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let shape = active ?? newLayer()
        active = shape
        shape.frame = bounds
        shape.path = Self.polyline(points)
        shape.lineWidth = CGFloat(max(tool.size, 0.5))
        shape.strokeColor = (UIColor(cssHex: tool.color) ?? .label).withAlphaComponent(CGFloat(tool.opacity)).cgColor
        CATransaction.commit()
    }

    /// The stroke ended: its polyline stays until `remove`.
    func finish() -> Handle? {
        defer { active = nil }
        return active.map(Handle.init)
    }

    /// The stroke was lost (a cancelled touch): its polyline goes now.
    func cancel() {
        if let active { remove(Handle(layer: active)) }
        active = nil
    }

    func remove(_ handle: Handle) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        handle.layer.removeFromSuperlayer()
        CATransaction.commit()
    }

    private func newLayer() -> CAShapeLayer {
        let shape = CAShapeLayer()
        shape.fillColor = nil
        shape.lineCap = .round
        shape.lineJoin = .round
        shape.contentsScale = window?.screen.scale ?? traitCollection.displayScale
        layer.addSublayer(shape)
        return shape
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

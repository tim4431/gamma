import CoreGraphics
import Foundation

/// The ruler's edge a claimed stroke runs along: the claim's `snap`
/// (docs/dev/ipad.md "The host contract"), the web's ink/ruler.js `onRail`
/// and `railLine` ported. Every point is projected onto the line through
/// (x0, y0) along (cos angle, −sin angle) — the angle in degrees
/// counterclockwise as seen, in viewport CSS px with y running down — and
/// the live line is the one between the two outermost projections, from the
/// end nearer the first point's, as the page commits it. Pure, so it is
/// tested (PencilTests).
enum RulerSnap {
    /// The edge's direction, a unit vector in view coordinates.
    static func direction(_ snap: InkTool.Snap) -> (dx: Double, dy: Double) {
        let a = snap.angle * .pi / 180
        return (cos(a), -sin(a))
    }

    /// How far along the edge a point lies, from (x0, y0).
    private static func along(_ point: CGPoint, _ snap: InkTool.Snap) -> Double {
        let d = direction(snap)
        return (Double(point.x) - snap.x0) * d.dx + (Double(point.y) - snap.y0) * d.dy
    }

    private static func at(_ t: Double, _ snap: InkTool.Snap) -> CGPoint {
        let d = direction(snap)
        return CGPoint(x: snap.x0 + t * d.dx, y: snap.y0 + t * d.dy)
    }

    /// One point projected onto the edge's line.
    static func point(_ point: CGPoint, onto snap: InkTool.Snap) -> CGPoint {
        at(along(point, snap), snap)
    }

    /// The line `points` draw along the edge: its two ends, the outermost
    /// projections, the one nearer the first point's first (one point gives
    /// both ends the same, a dot); none for no points.
    static func project(_ points: [CGPoint], onto snap: InkTool.Snap) -> [CGPoint] {
        guard let first = points.first else { return [] }
        var lo = Double.infinity, hi = -Double.infinity
        for p in points {
            let t = along(p, snap)
            lo = min(lo, t)
            hi = max(hi, t)
        }
        let t0 = along(first, snap)
        let (a, b) = t0 - lo <= hi - t0 ? (lo, hi) : (hi, lo)
        return [at(a, snap), at(b, snap)]
    }
}

import CoreGraphics
import Foundation

/// A shape the page snapped a held stroke to: its answer to `hold`
/// (HoldReply), in the parameters of the web's frontend/src/ink/shapes.js, in
/// viewport CSS px (the web view's points; `angle` in degrees, `n` a count).
/// The live line draws it (`path`) and refits it as the pen moves on
/// (`refit`, ShapeHold), by the rules the page runs again on the final
/// samples, so the shape under the pen is the one that commits. Pure, and
/// held to tests/shared/inkhold.json (HeldShapeTests), as shapes.js is.
struct HeldShape: Equatable {
    enum Kind: String, Decodable { case line, arrow, triangle, rect, polygon, circle, ellipse }

    let kind: Kind
    /// line, arrow `{x1, y1, x2, y2}` (an arrow's tip at x2, y2, with `head`,
    /// its barbs' length); triangle `{x1, y1, x2, y2, x3, y3}`; rect `{cx,
    /// cy, w, h, angle}`; polygon `{cx, cy, r, n, angle}`; circle `{cx, cy,
    /// r}`; ellipse `{cx, cy, rx, ry, angle}`.
    let params: [String: Double]

    init(kind: Kind, params: [String: Double]) {
        self.kind = kind
        self.params = params
    }

    /// Whether `params` holds what the kind is drawn from, as finite numbers
    /// (`angle` and an arrow's `head` may be left out, as shapes.js reads
    /// them), and a polygon has at least three sides.
    var isDrawable: Bool {
        let needed: [String]
        switch kind {
        case .line, .arrow: needed = ["x1", "y1", "x2", "y2"]
        case .triangle: needed = ["x1", "y1", "x2", "y2", "x3", "y3"]
        case .rect: needed = ["cx", "cy", "w", "h"]
        case .polygon: needed = ["cx", "cy", "r", "n"]
        case .circle: needed = ["cx", "cy", "r"]
        case .ellipse: needed = ["cx", "cy", "rx", "ry"]
        }
        return needed.allSatisfy { params[$0]?.isFinite == true } && params.values.allSatisfy { $0.isFinite }
            && (kind != .polygon || value("n") >= 3)
    }

    // --- the outline ---------------------------------------------------------------

    /// The shape as the live line strokes it: the polyline it commits as
    /// (shapes.js shapeSamples: a line's two ends; an arrow tail, tip, barb,
    /// tip, barb; a triangle's, a rectangle's or a polygon's corners,
    /// closed), and a circle or an ellipse as one.
    var path: CGPath {
        let path = CGMutablePath()
        switch kind {
        case .line:
            path.addLines(between: [point("x1", "y1"), point("x2", "y2")])
        case .arrow:
            path.addLines(between: arrowPoints)
        case .triangle:
            path.addLines(between: [point("x1", "y1"), point("x2", "y2"), point("x3", "y3")])
            path.closeSubpath()
        case .rect, .polygon:
            path.addLines(between: corners)
            path.closeSubpath()
        case .circle:
            let r: Double = value("r")
            let x: Double = value("cx") - r, y: Double = value("cy") - r, diameter: Double = 2 * r
            path.addEllipse(in: CGRect(x: x, y: y, width: diameter, height: diameter))
        case .ellipse:
            let rx: Double = value("rx"), ry: Double = value("ry")
            let turn: CGFloat = CGFloat(value("angle") / Self.degrees)
            let place: CGAffineTransform = CGAffineTransform(translationX: CGFloat(value("cx")), y: CGFloat(value("cy")))
                .rotated(by: turn)
            let x: Double = -rx, y: Double = -ry, width: Double = 2 * rx, height: Double = 2 * ry
            path.addEllipse(in: CGRect(x: x, y: y, width: width, height: height), transform: place)
        }
        return path
    }

    /// An arrow's polyline (shapes.js arrowPolyline): the barbs at 30° to the shaft.
    private var arrowPoints: [CGPoint] {
        let tail = point("x1", "y1"), tip = point("x2", "y2")
        let tx: Double = Double(tip.x), ty: Double = Double(tip.y)
        let along: Double = hypot(Double(tail.x) - tx, Double(tail.y) - ty)
        let length: Double = along > 0 ? along : 1
        let head: Double = params["head"] ?? min(length / 4, 24)
        // back along the shaft, from the tip
        let bx: Double = (Double(tail.x) - tx) / length, by: Double = (Double(tail.y) - ty) / length
        let signs: [Double] = [1.0, -1.0]
        let barbs: [CGPoint] = signs.map { (sign: Double) -> CGPoint in
            let a: Double = sign * 30 / Self.degrees
            let x: Double = tx + head * (bx * cos(a) - by * sin(a))
            let y: Double = ty + head * (bx * sin(a) + by * cos(a))
            return CGPoint(x: x, y: y)
        }
        return [tail, tip, barbs[0], tip, barbs[1]]
    }

    /// A rectangle's or a polygon's corners in order (shapes.js shapeCorners).
    private var corners: [CGPoint] {
        let cx: Double = value("cx"), cy: Double = value("cy"), angle: Double = value("angle")
        if kind == .rect {
            let turn: Double = angle / Self.degrees, c: Double = cos(turn), s: Double = sin(turn)
            let hw: Double = value("w") / 2, hh: Double = value("h") / 2
            // Typed step by step: as one inferred expression (the offsets, the
            // closure and the point at once) it was more than Swift's type
            // checker would solve in reasonable time.
            let offsets: [(Double, Double)] = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
            return offsets.map { (corner: (Double, Double)) -> CGPoint in
                let x: Double = cx + corner.0 * c - corner.1 * s
                let y: Double = cy + corner.0 * s + corner.1 * c
                return CGPoint(x: x, y: y)
            }
        }
        let sides: Double = Self.jsRound(value("n"))
        let n: Int = sides.isFinite ? Int(min(max(sides, 3), 64)) : 3
        let r: Double = value("r")
        return (0..<n).map { (i: Int) -> CGPoint in
            let a: Double = (angle + 360 / Double(n) * Double(i)) / Self.degrees
            let x: Double = cx + r * cos(a)
            let y: Double = cy + r * sin(a)
            return CGPoint(x: x, y: y)
        }
    }

    // --- the refit -----------------------------------------------------------------

    /// The shape as the pen has moved it on since the snap (shapes.js
    /// refitShape, without Shift), computed from the snap's params every
    /// time, never from an earlier refit: a line or an arrow takes `pointer`
    /// as its end; a closed shape scales about its centre by |pointer −
    /// centre| / |from − centre| and turns about it by the angle from `from`
    /// to `pointer`, a rectangle or a triangle keeping its proportions.
    /// Normalised and rounded as the web's recogniser returns a shape.
    func refit(pointer: CGPoint, from: CGPoint) -> HeldShape {
        let px: Double = Double(pointer.x), py: Double = Double(pointer.y)
        if kind == .line || kind == .arrow {
            var out: [String: Double] = ["x1": value("x1"), "y1": value("y1"), "x2": Self.round2(px), "y2": Self.round2(py)]
            if kind == .arrow, let head = params["head"] { out["head"] = head }
            return HeldShape(kind: kind, params: out)
        }
        let centre = self.centre
        let ox: Double = Double(from.x) - centre.x, oy: Double = Double(from.y) - centre.y
        let reach: Double = hypot(ox, oy)
        let scale: Double = reach > 1e-6 ? hypot(px - centre.x, py - centre.y) / reach : 1
        let deg: Double = reach > 1e-6 ? (atan2(py - centre.y, px - centre.x) - atan2(oy, ox)) * Self.degrees : 0
        let rad: Double = deg / Self.degrees
        let angle: Double = value("angle") + deg
        switch kind {
        case .triangle:
            var out: [String: Double] = [:]
            let keys: [(String, String)] = [("x1", "y1"), ("x2", "y2"), ("x3", "y3")]
            for (xKey, yKey) in keys {
                let dx: Double = (value(xKey) - centre.x) * scale, dy: Double = (value(yKey) - centre.y) * scale
                let x: Double = centre.x + dx * cos(rad) - dy * sin(rad)
                let y: Double = centre.y + dx * sin(rad) + dy * cos(rad)
                out[xKey] = x
                out[yKey] = y
            }
            return HeldShape(kind: kind, params: Self.rounded(out))
        case .rect:
            return HeldShape(kind: kind, params: Self.rounded(Self.normalisedRect(
                cx: value("cx"), cy: value("cy"), w: value("w") * scale, h: value("h") * scale, angle: angle)))
        case .polygon:
            let n = value("n")
            return HeldShape(kind: kind, params: Self.rounded([
                "cx": value("cx"), "cy": value("cy"), "r": value("r") * scale, "n": n,
                "angle": Self.mod(Self.round2(angle), 360 / n),
            ]))
        case .circle:
            return HeldShape(kind: kind, params: Self.rounded(["cx": value("cx"), "cy": value("cy"), "r": value("r") * scale]))
        case .ellipse:
            var rx = value("rx") * scale, ry = value("ry") * scale, turned = angle
            if rx < ry {
                swap(&rx, &ry)
                turned += 90
            }
            return HeldShape(kind: kind, params: Self.rounded([
                "cx": value("cx"), "cy": value("cy"), "rx": rx, "ry": ry, "angle": Self.mod(Self.round2(turned), 180),
            ]))
        case .line, .arrow:
            return self // refit above
        }
    }

    /// The point a closed shape scales and turns about: a triangle's
    /// centroid, else its centre.
    private var centre: (x: Double, y: Double) {
        if kind == .triangle {
            let x: Double = (value("x1") + value("x2") + value("x3")) / 3
            let y: Double = (value("y1") + value("y2") + value("y3")) / 3
            return (x, y)
        }
        return (value("cx"), value("cy"))
    }

    private func value(_ key: String) -> Double {
        params[key] ?? 0
    }

    private func point(_ x: String, _ y: String) -> CGPoint {
        CGPoint(x: value(x), y: value(y))
    }

    // --- shapes.js's arithmetic ------------------------------------------------------

    static let degrees = 180 / Double.pi

    /// Math.round: the nearest whole number, halves up.
    static func jsRound(_ v: Double) -> Double {
        (v + 0.5).rounded(.down)
    }

    /// Two decimals, as shapes.js round2 (Math.round(v × 100) / 100).
    static func round2(_ v: Double) -> Double {
        (v * 100 + 0.5).rounded(.down) / 100
    }

    /// shapes.js mod: a remainder in [0, m) whatever the sign of v.
    static func mod(_ v: Double, _ m: Double) -> Double {
        (v.truncatingRemainder(dividingBy: m) + m).truncatingRemainder(dividingBy: m)
    }

    /// Every param to two decimals but `n`, a count (shapes.js rounded).
    private static func rounded(_ params: [String: Double]) -> [String: Double] {
        var out: [String: Double] = [:]
        for (key, v) in params { out[key] = key == "n" ? v : round2(v) }
        return out
    }

    /// shapes.js normaliseRect: the angle in [−45°, 45°), the side along it
    /// staying `w` (a quarter turn swaps the pair), a turn of nothing 0.
    private static func normalisedRect(cx: Double, cy: Double, w: Double, h: Double, angle: Double) -> [String: Double] {
        var a = mod(round2(angle) + 45, 90) - 45
        let quarter = jsRound((angle - a) / 90)
        var width = w, height = h
        if quarter.truncatingRemainder(dividingBy: 2) != 0 { swap(&width, &height) }
        if abs(a) < 1e-9 { a = 0 }
        return ["cx": cx, "cy": cy, "w": width, "h": height, "angle": a]
    }
}

extension HeldShape: Decodable {
    private enum Keys: String, CodingKey { case kind, params }

    /// `{kind, params}`, refused when the kind is not one of the seven or the
    /// params cannot draw it (`isDrawable`).
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        self.init(kind: try c.decode(Kind.self, forKey: .kind), params: try c.decode([String: Double].self, forKey: .params))
        guard isDrawable else {
            throw DecodingError.dataCorruptedError(forKey: .params, in: c, debugDescription: "a \(kind.rawValue) without its params")
        }
    }
}

/// A held stroke after the snap (docs/dev/ipad.md "The pen", step 6): which
/// shape the samples come to as the pen moves on, the rule the page runs at
/// the lift too (shapes.js followHold; tests/shared/inkhold.json). `n` is
/// the number of samples the hold carried, `from` the last of them, where the
/// pen stood, and `tSnap` the snap's time on the samples' clock (the
/// stillness anchor's t + the hold's delay, in ms). Of the samples from
/// index `n` on, one more than `cancelSlop` from `from` within `cancelWindow`
/// of `tSnap` drops the shape for the rest of the stroke (nil: freehand comes
/// back); once one has been `refitSlop` from `from`, the shape is refit to
/// the latest sample; until then it is the snap.
struct ShapeHold: Equatable {
    /// shapes.js REFIT_PX, CANCEL_PX (CSS px) and CANCEL_MS.
    static let refitSlop = 6.0
    static let cancelSlop = 12.0
    static let cancelWindow = 150.0

    let snap: HeldShape
    let from: CGPoint
    let n: Int
    let tSnap: Double

    /// The shape for the stroke's samples so far (`[x, y, p, t, …]`, as
    /// StrokeRecorder makes them), or nil once it was dropped.
    func current(_ samples: [[Double]]) -> HeldShape? {
        let fx: Double = Double(from.x), fy: Double = Double(from.y)
        var refitting = false
        var latest: CGPoint?
        for s in samples.dropFirst(max(0, n)) where s.count >= 4 {
            let moved: Double = hypot(s[0] - fx, s[1] - fy)
            if moved > Self.cancelSlop && s[3] - tSnap <= Self.cancelWindow { return nil }
            if moved >= Self.refitSlop { refitting = true }
            latest = CGPoint(x: s[0], y: s[1])
        }
        guard refitting, let pointer = latest else { return snap }
        return snap.refit(pointer: pointer, from: from)
    }
}

import Foundation

/// The brush table of the web's frontend/src/ink/finishStroke.js (BRUSHES,
/// brushWidths, brushOpacities), ported for the live line (LiveStrokeView),
/// so the line under the Pencil is as wide as the stroke the page commits.
/// The web's table stays the one law: the page bakes the committed stroke's
/// widths from the samples the shell hands back, and these only draw the
/// line until it has. Pure, and held to every row of
/// tests/shared/inkwidth.json (InkWidthTests), as the web and the server are.
///
/// A sample's `w` multiplies the tool's size into the width drawn there; a
/// Pencil's `o` multiplies its opacity. A stroke has widths when its brush
/// has a width law for it: a pen's Pen, Brush, Pencil or Calligraphy pen
/// (not the Ballpoint), with pressure (only the Brush without, by its speed
/// law, and the Calligraphy pen, by its travel against the nib alone), and
/// never a highlighter or a shape (a held shape is even end to end,
/// whatever its brush).
enum InkWidth {
    /// One sample as a law reads it: x and y in one unit (the Brush's tapers
    /// and its speed law measure lengths in sizes of it), p the pressure,
    /// 0…1, and a the altitude in degrees, each nil when not reported.
    struct Sample: Equatable {
        var x: Double
        var y: Double
        var p: Double?
        var a: Double?
    }

    /// The Brush's tapers, in sizes of arc length (each at most half the
    /// stroke), and the range its multiplier is held to.
    static let taperStart = 2.0
    static let taperEnd = 3.0
    static let brushMin = 0.1
    static let brushMax = 2.0
    /// perfect-freehand's simulatePressure step (its RATE_OF_PRESSURE_CHANGE).
    static let simulateRate = 0.275
    /// The Calligraphy pen's hairline along its nib, in sizes: the law's
    /// floor (ink.js NIB_HAIR).
    static let nibHair = 0.12

    /// A host sample (StrokeRecorder: `[x, y, p, t]`, or `[x, y, p, t, a, z]`
    /// with the tilt) as a law reads it.
    static func sample(_ values: [Double]) -> Sample {
        Sample(x: values.first ?? 0, y: values.count > 1 ? values[1] : 0,
               p: values.count > 2 ? values[2] : nil, a: values.count > 4 ? values[4] : nil)
    }

    /// A brush's width law at one sample, before the Brush's tapers and its
    /// range: the Pen 1 + (p − 0.5), the Brush 2p, the Pencil the Pen's grown
    /// by up to three times as it leans from upright (an altitude of 90°, the
    /// default) to flat, capped at 4. The Ballpoint has none (an even line).
    static func law(_ brush: InkTool.Brush, _ s: Sample) -> Double? {
        let p = pressureOf(s)
        switch brush {
        case .pen: return clamp(1 + (p - 0.5), 0, 2)
        case .monoline: return nil
        case .brush: return 2 * p
        case .pencil:
            let lean: Double = 1 - clamp(s.a ?? 90, 0, 90) / 90
            let growth: Double = 1 + 2 * lean
            return clamp((1 + (p - 0.5)) * growth, 0, 4)
        case .calligraphy: return calligraphy(s, travel: nil, nib: 45)
        }
    }

    /// The Calligraphy pen's law at one sample: as wide as the nib where the
    /// stroke travels across it, the hairline (`nibHair`) along it — the sine
    /// between the travel (dx, dy) and the nib's edge, the vector (cos nib,
    /// −sin nib) in the view's y-down space, `nib` in degrees
    /// counterclockwise as seen; no travel (a tap) draws the whole nib — and
    /// a little wider with pressure, capped at 4 (finishStroke.js
    /// BRUSHES.calligraphy).
    static func calligraphy(_ s: Sample, travel: (dx: Double, dy: Double)?, nib: Double) -> Double {
        let r: Double = nib * .pi / 180
        var across = 1.0
        if let d = travel {
            let sine: Double = d.dx * sin(r) + d.dy * cos(r)
            across = abs(sine) / hypot(d.dx, d.dy)
        }
        let press: Double = 0.85 + 0.3 * pressureOf(s)
        return clamp(max(nibHair, across) * press, 0, 4)
    }

    /// The direction a stroke travels at sample `i` (finishStroke.js
    /// travelAt): from the sample before to the one after, one-sided at the
    /// ends; where those two coincide, the segment ending at it, else the one
    /// starting there; nil where the stroke has no length.
    static func travel(_ samples: [Sample], at i: Int) -> (dx: Double, dy: Double)? {
        guard samples.indices.contains(i) else { return nil }
        let n = samples.count
        func step(_ a: Int, _ b: Int) -> (dx: Double, dy: Double) {
            (dx: samples[b].x - samples[a].x, dy: samples[b].y - samples[a].y)
        }
        var tries = [step(max(i - 1, 0), min(i + 1, n - 1))]
        if i > 0 { tries.append(step(i - 1, i)) }
        if i < n - 1 { tries.append(step(i, i + 1)) }
        return tries.first { (t: (dx: Double, dy: Double)) -> Bool in hypot(t.dx, t.dy) > 1e-9 }
    }

    /// The Pencil's opacity law: pressure darkens it.
    static func opacity(_ s: Sample) -> Double {
        clamp(0.15 + 0.85 * pressureOf(s), 0, 1)
    }

    /// Whether a stroke has widths (finishStroke.js widthLaw): a pen's brush
    /// with a law, not a shape, and with pressure unless it is the Brush or
    /// the Calligraphy pen.
    static func hasWidths(tool: InkTool.Kind, brush: InkTool.Brush, pressure: Bool, shape: Bool) -> Bool {
        tool == .pen && !shape && brush != .monoline && (pressure || brush == .brush || brush == .calligraphy)
    }

    /// The width multipliers of a stroke, one per sample to three decimals,
    /// or nil when it has none (`hasWidths`). The Brush's tapers run over the
    /// samples' arc length: the start taper while it is under 2 sizes, the
    /// end taper while the rest is under 3, each at most half the stroke's
    /// length and neither on a stroke of no length; its pressure is the
    /// samples' own, or, without pressure (`pressure` false, or samples
    /// without p), the speed law's. `live` is a stroke still being written:
    /// no end taper and the start taper at its full length, as the web's
    /// live canvas draws it, so the line under the pen holds still. A
    /// Calligraphy pen's widths read each sample's travel and `nib` (the
    /// stroke's, in degrees; the claim's, turned by the roll), its pressure
    /// only when the stroke reads it (else 0.5), and have no tapers.
    static func widths(_ samples: [Sample], tool: InkTool.Kind, brush: InkTool.Brush, pressure: Bool, size: Double,
                       shape: Bool = false, live: Bool = false, nib: Double = 45) -> [Double]? {
        guard hasWidths(tool: tool, brush: brush, pressure: pressure, shape: shape), !samples.isEmpty else { return nil }
        // Whether the stroke reads its samples' pressure (finishStroke.js hasPressure).
        let read: Bool = pressure && samples[0].p != nil
        if brush == .calligraphy {
            return samples.indices.map { (i: Int) -> Double in
                let s: Sample = read ? samples[i] : Sample(x: samples[i].x, y: samples[i].y, p: 0.5, a: nil)
                return round3(calligraphy(s, travel: travel(samples, at: i), nib: nib))
            }
        }
        guard brush == .brush else { return samples.map { round3(law(brush, $0) ?? 1) } }
        let size = max(0.01, size)
        let simulated = read ? nil : simulatedPressure(samples, size: size)
        var arc = [Double](repeating: 0, count: samples.count)
        for i in samples.indices.dropFirst() {
            let dx: Double = samples[i].x - samples[i - 1].x
            let dy: Double = samples[i].y - samples[i - 1].y
            arc[i] = arc[i - 1] + hypot(dx, dy)
        }
        let total = arc[arc.count - 1]
        let most = live ? Double.infinity : total / 2
        let start = min(taperStart * size, most)
        let end = min(taperEnd * size, most)
        return samples.indices.map { (i: Int) -> Double in
            let ts: Double = arc[i] < start ? easeOutSine(arc[i] / start) : 1
            let te: Double = !live && total - arc[i] < end ? easeOutSine((total - arc[i]) / end) : 1
            let s: Sample = simulated.map { (pressures: [Double]) -> Sample in
                Sample(x: samples[i].x, y: samples[i].y, p: pressures[i], a: nil)
            } ?? samples[i]
            return round3(clamp((law(.brush, s) ?? 0) * min(ts, te), brushMin, brushMax))
        }
    }

    /// The opacity multipliers of a stroke, to three decimals, or nil: only
    /// a Pencil with pressure shades (finishStroke.js opacityLaw).
    static func opacities(_ samples: [Sample], tool: InkTool.Kind, brush: InkTool.Brush, pressure: Bool,
                          shape: Bool = false) -> [Double]? {
        guard tool == .pen, brush == .pencil, pressure, !shape, !samples.isEmpty else { return nil }
        return samples.map { round3(opacity($0)) }
    }

    /// The shade a page draws an opacity multiplier at: one of three bands,
    /// rounded up (ink.js opacityBand).
    static func opacityBand(_ o: Double) -> Double {
        o <= 0.33 ? 0.33 : o <= 0.66 ? 0.66 : 1
    }

    /// The pressure the speed law gives each sample, from the spacing alone
    /// (perfect-freehand's simulatePressure; time is never read): 0.5 at the
    /// first, then a step toward 1 − min(1, d / size) for a sample at
    /// distance d from the one before, so slow is wide and fast is thin.
    static func simulatedPressure(_ samples: [Sample], size: Double) -> [Double] {
        var out: [Double] = []
        out.reserveCapacity(samples.count)
        var p = 0.5
        for i in samples.indices {
            if i > 0 {
                let dx: Double = samples[i].x - samples[i - 1].x
                let dy: Double = samples[i].y - samples[i - 1].y
                let step: Double = min(1, hypot(dx, dy) / size)
                let next: Double = p + (1 - step - p) * step * simulateRate
                p = min(1, next)
            }
            out.append(p)
        }
        return out
    }

    /// Three decimals, as the web's Math.round does them (halves up).
    static func round3(_ v: Double) -> Double {
        (v * 1000 + 0.5).rounded(.down) / 1000
    }

    private static func pressureOf(_ s: Sample) -> Double {
        clamp(s.p ?? 0.5, 0, 1)
    }

    private static func easeOutSine(_ x: Double) -> Double {
        sin(x * .pi / 2)
    }

    private static func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double {
        max(lo, min(hi, v))
    }
}

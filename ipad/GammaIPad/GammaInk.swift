import Foundation
import PencilKit
import CryptoKit
import UIKit

struct InkFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct GammaSpace: Codable, Equatable {
    var kind = "pdf-page"
    var page: Int
    var width: Double
    var height: Double
}

struct GammaStroke: Codable, Equatable {
    var id: String
    var tool = "pen"
    var brush: String?
    var color = "#1f1f1f"
    var size = 1.6
    var opacity = 1.0
    var pen = true
    var t0: Int?
    var ch = "xy"
    var pts: [Int]
}

extension GammaStroke {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        tool = try c.decodeIfPresent(String.self, forKey: .tool) ?? "pen"
        brush = try c.decodeIfPresent(String.self, forKey: .brush)
        color = try c.decodeIfPresent(String.self, forKey: .color) ?? "#1f1f1f"
        size = try c.decodeIfPresent(Double.self, forKey: .size) ?? 1.6
        opacity = try c.decodeIfPresent(Double.self, forKey: .opacity) ?? 1
        pen = try c.decodeIfPresent(Bool.self, forKey: .pen) ?? true
        t0 = try c.decodeIfPresent(Int.self, forKey: .t0)
        ch = try c.decodeIfPresent(String.self, forKey: .ch) ?? "xy"
        pts = try c.decode([Int].self, forKey: .pts)
    }
}

struct GammaInk: Codable, Equatable {
    var format = "gamma-ink"
    var version = 1
    var space: GammaSpace
    var strokes: [GammaStroke]

    func validate() throws {
        guard format == "gamma-ink", version == 1, space.kind == "pdf-page", space.page > 0,
              space.width.isFinite, space.height.isFinite, space.width > 0, space.height > 0,
              space.width <= 100_000, space.height <= 100_000, strokes.count <= 5000,
              Set(strokes.map(\.id)).count == strokes.count else { throw InkFailure("Unsupported ink file.") }
        var count = 0
        for s in strokes {
            guard s.id.range(of: "^[A-Za-z0-9_-]{1,32}$", options: .regularExpression) != nil,
                  ["pen", "highlighter"].contains(s.tool), s.brush == nil || (s.tool == "pen" && s.brush == "monoline"),
                  s.size.isFinite, s.size > 0, s.size <= 100, s.opacity.isFinite, s.opacity > 0, s.opacity <= 1,
                  s.ch.hasPrefix("xy"), s.ch.count <= 6, Set(s.ch).count == s.ch.count,
                  s.ch.allSatisfy({ "xyptaz".contains($0) }), !s.pts.isEmpty, s.pts.count % s.ch.count == 0,
                  s.pts.allSatisfy({ abs(Double($0)) <= 1e10 }), s.t0 == nil || s.t0! >= 0 else {
                throw InkFailure("Unsupported or damaged ink stroke. The original has been kept.")
            }
            count += s.pts.count / s.ch.count
            guard count <= 500_000 else { throw InkFailure("This ink group has too many samples.") }
            _ = try inkColor(s.color)
        }
    }
}

func inkColor(_ text: String) throws -> UIColor {
    if text.range(of: "^#[0-9a-fA-F]{6}$", options: .regularExpression) != nil,
       let hex = UInt32(text.dropFirst(), radix: 16) {
        return UIColor(red: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
                       blue: CGFloat(hex & 255) / 255, alpha: 1)
    }
    if text.hasPrefix("rgb"), let open = text.firstIndex(of: "("), text.hasSuffix(")") {
        let values = text[text.index(after: open)..<text.index(before: text.endIndex)]
            .split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        if values.count == 3 || values.count == 4, values.allSatisfy(\.isFinite) {
            return UIColor(red: CGFloat(max(0, min(255, values[0]))) / 255,
                           green: CGFloat(max(0, min(255, values[1]))) / 255,
                           blue: CGFloat(max(0, min(255, values[2]))) / 255,
                           alpha: CGFloat(max(0, min(1, values.count == 4 ? values[3] : 1))))
        }
    }
    throw InkFailure("Unsupported ink color.")
}

// The open JSON remains authoritative. Unedited imported strokes are returned
// byte-for-byte in their original representation, avoiding repeated smoothing.
@MainActor
final class GammaInkCodec {
    private var originals: [String: [GammaStroke]] = [:]

    static func fingerprint(_ stroke: PKStroke) -> String {
        SHA256.hash(data: PKDrawing(strokes: [stroke]).dataRepresentation()).map { String(format: "%02x", $0) }.joined()
    }

    func drawing(from ink: GammaInk) throws -> PKDrawing {
        try ink.validate()
        let strokes = try ink.strokes.map { s -> PKStroke in
            let channels = Array(s.ch)
            var x = 0.0, y = 0.0, time = 0.0
            var points: [PKStrokePoint] = []
            for offset in stride(from: 0, to: s.pts.count, by: channels.count) {
                var pressure = 0.5, altitude = Double.pi / 2, azimuth = 0.0
                for (j, channel) in channels.enumerated() {
                    let value = Double(s.pts[offset + j])
                    switch channel {
                    case "x": x += value / 100
                    case "y": y += value / 100
                    case "p": pressure = max(0, min(1, value / 1000))
                    case "t": time += value / 1000
                    case "a": altitude = value * .pi / 180
                    case "z": azimuth = value * .pi / 180
                    default: break
                    }
                }
                guard abs(x) <= 1e6, abs(y) <= 1e6, time >= 0,
                      points.last.map({ time >= $0.timeOffset }) ?? true else { throw InkFailure("Invalid stroke coordinates or timing.") }
                let width = s.size * (s.tool == "pen" && s.brush == nil && s.pen ? 1 + 0.5 * (pressure - 0.5) : 1)
                points.append(PKStrokePoint(location: CGPoint(x: x, y: y), timeOffset: time,
                    size: CGSize(width: width, height: width), opacity: 1, force: pressure,
                    azimuth: azimuth, altitude: altitude))
            }
            let type: PKInkingTool.InkType = s.tool == "highlighter" ? .marker : s.brush == "monoline" ? .monoline : .pen
            let color = try inkColor(s.color)
            let stroke = PKStroke(ink: PKInk(type, color: color.withAlphaComponent(min(color.cgColor.alpha, s.opacity))),
                path: PKStrokePath(controlPoints: points, creationDate: Date(timeIntervalSince1970: Double(s.t0 ?? 0) / 1000)))
            originals[Self.fingerprint(stroke), default: []].append(s)
            return stroke
        }
        return PKDrawing(strokes: strokes)
    }

    func export(_ drawing: PKDrawing, space: GammaSpace) throws -> GammaInk {
        guard drawing.strokes.count <= 5000 else { throw InkFailure("Start another ink group; this one has 5,000 strokes.") }
        var total = 0
        var used: [String: Int] = [:]
        let strokes = try drawing.strokes.map { stroke -> GammaStroke in
            let key = Self.fingerprint(stroke)
            let index = used[key, default: 0]
            used[key] = index + 1
            if let matches = originals[key], index < matches.count {
                let original = matches[index]
                total += original.pts.count / original.ch.count
                guard total <= 500_000 else { throw InkFailure("This ink group has too many samples.") }
                return original
            }
            // Our toolbar offers whole-stroke erasing only. Refuse masks instead
            // of silently resurrecting ink erased by some future native tool.
            guard stroke.mask == nil, [.pen, .monoline, .marker].contains(stroke.ink.inkType) else {
                throw InkFailure("This brush or erasure cannot be saved in Gamma's current ink format.")
            }
            let marker = stroke.ink.inkType == .marker
            let mono = stroke.ink.inkType == .monoline
            let transform = stroke.transform
            let scale = max(hypot(transform.a, transform.b), hypot(transform.c, transform.d))
            var pts: [Int] = [], widths: [Double] = []
            var px = 0, py = 0, pt = 0
            let start = stroke.path.interpolatedPoint(at: 0).timeOffset
            for point in stroke.path.interpolatedPoints(by: .distance(1)) {
                total += 1
                guard total <= 500_000 else { throw InkFailure("This ink group has too many samples.") }
                let location = point.location.applying(transform)
                let p = max(0, min(1, Double(point.force)))
                let values = [Double(location.x), Double(location.y), point.timeOffset, Double(point.size.width),
                              Double(point.size.height), Double(point.altitude), Double(point.azimuth), Double(scale)]
                guard values.allSatisfy(\.isFinite), abs(location.x) <= 1e6, abs(location.y) <= 1e6,
                      abs(point.timeOffset) <= 1e9 else { throw InkFailure("Invalid Pencil stroke.") }
                let x = Int((location.x * 100).rounded()), y = Int((location.y * 100).rounded())
                let t = max(pt, Int(((point.timeOffset - start) * 1000).rounded()))
                pts += [x - px, y - py, Int((p * 1000).rounded()), t - pt,
                        Int((point.altitude * 180 / .pi).rounded()), Int((point.azimuth * 180 / .pi).rounded())]
                px = x; py = y; pt = t
                widths.append(Double(max(point.size.width, point.size.height) * scale) / (marker || mono ? 1 : 1 + 0.5 * (p - 0.5)))
            }
            guard !widths.isEmpty else { throw InkFailure("Empty Pencil stroke.") }
            widths.sort()
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 1
            guard stroke.ink.color.getRed(&r, green: &g, blue: &b, alpha: &a) else { throw InkFailure("Unsupported Pencil color.") }
            let color = String(format: "#%02x%02x%02x", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded()))
            let result = GammaStroke(id: String(key.prefix(24)), tool: marker ? "highlighter" : "pen", brush: mono ? "monoline" : nil,
                color: color, size: max(0.01, min(100, widths[widths.count / 2])), opacity: marker ? 0.6 : max(0.01, Double(a)),
                pen: true, t0: max(0, Int(((stroke.path.creationDate.timeIntervalSince1970 + start) * 1000).rounded())), ch: "xyptaz", pts: pts)
            originals[key, default: []].append(result)
            return result
        }
        let ink = GammaInk(space: space, strokes: strokes)
        try ink.validate()
        guard try JSONEncoder().encode(ink).count <= 4 * 1024 * 1024 else { throw InkFailure("This ink group exceeds 4 MB. Undo the last stroke and start another group.") }
        return ink
    }
}

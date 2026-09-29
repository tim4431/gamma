import Foundation
import UIKit
import JavaScriptCore

enum InkEngineError: LocalizedError {
    case failure(String)
    var errorDescription: String? { if case .failure(let message) = self { return message }; return nil }
}

@MainActor
final class InkEngine {
    let context: JSContext
    private var failure: String?
    private var paths: [String: (NSDictionary, CGPath, Bool)] = [:]
    init(bundle: Bundle = .main) throws {
        guard let context = JSContext(), let url = bundle.url(forResource: "gamma-ink", withExtension: "js") else {
            throw InkEngineError.failure("The handwriting resources are missing. Rebuild Gamma for iPad.")
        }
        self.context = context
        context.exceptionHandler = { [weak self] _, exception in self?.failure = exception?.toString() }
        context.evaluateScript(try String(contentsOf: url, encoding: .utf8))
        if let failure { throw InkEngineError.failure(failure) }
    }
    func call(_ name: String, _ arguments: [Any]) throws -> Any {
        failure = nil
        guard let function = context.objectForKeyedSubscript("GammaInk")?.objectForKeyedSubscript(name), !function.isUndefined,
              let result = function.call(withArguments: arguments), !result.isUndefined else {
            throw InkEngineError.failure(failure ?? "Invalid handwriting operation: \(name)")
        }
        if let failure { throw InkEngineError.failure(failure) }
        return result.toObject() ?? NSNull()
    }
    func object(_ name: String, _ arguments: [Any]) throws -> [String: Any] {
        guard let result = try call(name, arguments) as? [String: Any] else { throw InkEngineError.failure("Invalid handwriting result") }
        return result
    }
    func data(_ ink: [String: Any]) throws -> Data {
        let data = try JSONSerialization.data(withJSONObject: ink, options: [.sortedKeys])
        guard data.count <= 4 * 1024 * 1024 else { throw InkEngineError.failure("This group is full. Start a new group.") }
        return data
    }
    func draw(_ stroke: [String: Any], in context: CGContext) throws {
        let id = stroke["id"] as? String ?? ""
        if let cached = paths[id], cached.0.isEqual(to: stroke) {
            paint(cached.1, line: cached.2, stroke: stroke, in: context); return
        }
        let shape = try object("geometry", [stroke])
        guard let values = shape["points"] as? [[Double]], let first = values.first else { return }
        let path = CGMutablePath()
        path.move(to: CGPoint(x: first[0], y: first[1]))
        let line = shape["line"] as? Bool == true
        if line {
            for point in values.dropFirst() { path.addLine(to: CGPoint(x: point[0], y: point[1])) }
            if values.count == 1 { path.addLine(to: CGPoint(x: first[0] + 0.01, y: first[1])) }
        } else {
            let midpoints = shape["midpoints"] as? [[Double]] ?? values
            for index in values.indices {
                let a = values[index], middle = midpoints[index]
                path.addQuadCurve(to: CGPoint(x: middle[0], y: middle[1]), control: CGPoint(x: a[0], y: a[1]))
            }
            path.closeSubpath()
        }
        if paths.count > 6000 { paths.removeAll(keepingCapacity: true) }
        paths[id] = (NSDictionary(dictionary: stroke), path, line)
        paint(path, line: line, stroke: stroke, in: context)
    }
    private func paint(_ path: CGPath, line: Bool, stroke: [String: Any], in context: CGContext) {
        context.saveGState()
        context.setAlpha(stroke["opacity"] as? Double ?? 1)
        let color = UIColor.gamma(stroke["color"] as? String ?? "#1f1f1f")
        context.addPath(path)
        if line {
            context.setBlendMode(.multiply); context.setStrokeColor(color.cgColor)
            context.setLineWidth(stroke["size"] as? Double ?? 2); context.setLineCap(.round); context.setLineJoin(.round)
            context.strokePath()
        } else { context.setFillColor(color.cgColor); context.fillPath() }
        context.restoreGState()
    }
}

extension UIColor {
    var gammaHex: String {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, alpha: CGFloat = 1
        guard getRed(&r, green: &g, blue: &b, alpha: &alpha) else { return "#ffffff" }
        return String(format: "#%02x%02x%02x", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded()))
    }
    static func gamma(_ text: String) -> UIColor {
        if text.hasPrefix("#"), let value = UInt32(text.dropFirst(), radix: 16) {
            return UIColor(red: CGFloat((value >> 16) & 255) / 255, green: CGFloat((value >> 8) & 255) / 255,
                           blue: CGFloat(value & 255) / 255, alpha: 1)
        }
        if let start = text.firstIndex(of: "("), let end = text.lastIndex(of: ")") {
            let parts = text[text.index(after: start)..<end].split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
            if parts.count >= 3 { return UIColor(red: parts[0] / 255, green: parts[1] / 255, blue: parts[2] / 255, alpha: parts.count == 4 ? parts[3] : 1) }
        }
        return .black
    }
}

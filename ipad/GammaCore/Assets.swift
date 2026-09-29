import Foundation
import CryptoKit

public enum GammaAssets {
    private static let referencePattern = try! NSRegularExpression(pattern: "/api/uploads/([0-9a-f]{8,64}\\.[a-z0-9]{1,8})")
    public static func digest(_ data: Data) -> String { SHA256.hash(data: data).prefix(12).map { String(format: "%02x", $0) }.joined() }
    static func filename(_ reference: String) throws -> String {
        let name = reference.hasPrefix("/api/uploads/") ? String(reference.dropFirst(13)) : reference
        guard name.range(of: "^[0-9a-f]{8,64}\\.[a-z0-9]{1,8}$", options: .regularExpression) != nil else { throw GammaError.invalid("Invalid asset reference.") }
        return name
    }
    static func verify(_ data: Data, name: String) throws {
        _ = try filename(name)
        if name.hasSuffix(".pdf") {
            guard data.starts(with: Data("%PDF".utf8)) else { throw GammaError.invalid("The download is not a PDF.") }
        } else {
            let stem = String(name.split(separator: ".")[0])
            let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            guard hash.hasPrefix(stem) else { throw GammaError.invalid("The downloaded asset failed its content hash check.") }
        }
    }
    static func references(_ snapshot: GammaSnapshot) -> Set<String> {
        var result = Set<String>()
        for block in snapshot.values {
            result.formUnion(references(in: block.content + ((try? GammaJSON.string(block.properties)) ?? "")))
            if let doc = block.properties["doc_id"]?.string, (try? filename(doc + ".pdf")) != nil { result.insert(doc + ".pdf") }
        }
        return result
    }
    static func references(in text: String) -> Set<String> {
        Set(referencePattern.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { match in
            Range(match.range(at: 1), in: text).map { String(text[$0]) }
        })
    }
    /// Scan complete persisted records, including old property values and pending ops.
    /// Decode first so escaped JSON strings and PDF IDs retain their assets too.
    static func references(in value: JSONValue) -> Set<String> {
        switch value {
        case .string(let text): return references(in: text)
        case .array(let values): return values.reduce(into: Set<String>()) { $0.formUnion(references(in: $1)) }
        case .object(let values):
            var result = values.values.reduce(into: Set<String>()) { $0.formUnion(references(in: $1)) }
            if let doc = values["doc_id"]?.string, (try? filename(doc + ".pdf")) != nil { result.insert(doc + ".pdf") }
            return result
        default: return []
        }
    }
    static func write(_ data: Data, name: String, directory: URL) throws {
        try verify(data, name: name)
        let destination = directory.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: destination.path), let existing = try? Data(contentsOf: destination), existing == data {
            // A caller can attach this reused file after its next await. Give it the
            // same collection grace period as a newly written asset.
            try FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: destination.path)
            return
        }
        let temporary = directory.appendingPathComponent(".partial-" + GammaID.make())
        do {
            try data.write(to: temporary, options: .withoutOverwriting)
            let handle = try FileHandle(forWritingTo: temporary)
            try handle.synchronize(); try handle.close()
            if FileManager.default.fileExists(atPath: destination.path) { _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary) }
            else { try FileManager.default.moveItem(at: temporary, to: destination) }
        } catch { try? FileManager.default.removeItem(at: temporary); throw error }
    }
}

enum InkMetadata {
    static func parse(_ data: Data) throws -> (data: Data, props: [String: JSONValue]) {
        guard data.count <= 4 * 1024 * 1024 else { throw GammaError.invalid("Ink exceeds the 4 MB limit.") }
        let ink = try JSONDecoder().decode(JSONValue.self, from: data)
        guard ink["format"]?.string == "gamma-ink", let strokes = ink["strokes"]?.array, strokes.count <= 5000,
              let space = ink["space"]?.object, let width = space["width"]?.number, let height = space["height"]?.number,
              width > 0, height > 0, width <= 100_000, height <= 100_000 else { throw GammaError.invalid("Invalid Gamma ink file.") }
        let kind = space["kind"]?.string, version = ink["version"]?.number
        guard (version == 1 && (kind == "pdf-page" || kind == "canvas")) || (version == 2 && ["pdf-page", "canvas", "notebook-page"].contains(kind ?? "")) else { throw GammaError.invalid("Unsupported ink coordinate space.") }
        var ids = Set<String>(), samples = 0
        var x0 = Double.infinity, y0 = Double.infinity, x1 = -Double.infinity, y1 = -Double.infinity
        for stroke in strokes {
            if version == 1 && stroke["source_id"] != nil { throw GammaError.invalid("Stroke provenance requires ink version 2.") }
            guard let id = stroke["id"]?.string, !id.isEmpty, ids.insert(id).inserted,
                  let channels = stroke["ch"]?.string, channels.hasPrefix("xy"), Set(channels).count == channels.count,
                  Set(channels).isSubset(of: Set("xyptaz")), let pts = stroke["pts"]?.array, !pts.isEmpty,
                  pts.count % channels.count == 0, let size = stroke["size"]?.number, size > 0, size <= 100 else { throw GammaError.invalid("Invalid ink stroke.") }
            samples += pts.count / channels.count
            guard samples <= 500_000 else { throw GammaError.invalid("Too many ink samples.") }
            var x = 0.0, y = 0.0
            let ch = Array(channels)
            for offset in stride(from: 0, to: pts.count, by: ch.count) {
                guard let dx = pts[offset].number, let dy = pts[offset + 1].number else { throw GammaError.invalid("Ink points must be numbers.") }
                x += dx / 100; y += dy / 100
                let pressure = ch.firstIndex(of: "p").flatMap { pts[offset + $0].number }.map { min(1, max(0, $0 / 1000)) } ?? 0.5
                let constant = stroke["tool"]?.string != "pen" || stroke["pen"] == .bool(false) || stroke["brush"]?.string == "monoline"
                let radius = size * (constant ? 1 : 1 + 0.5 * (pressure - 0.5)) / 2
                x0 = min(x0, x - radius); y0 = min(y0, y - radius); x1 = max(x1, x + radius); y1 = max(y1, y + radius)
            }
        }
        var props: [String: JSONValue] = ["ink_strokes": .number(Double(strokes.count))]
        if kind == "notebook-page" {
            guard let sheet = space["sheet_id"]?.string, !sheet.isEmpty else { throw GammaError.invalid("Notebook ink needs a sheet ID.") }
            props["sheet_id"] = .string(sheet); props["pdf_position"] = .null; props["pdf_page"] = .null
        } else if kind == "pdf-page" {
            guard let page = space["page"]?.number, page >= 1, page.rounded() == page else { throw GammaError.invalid("PDF ink needs a page number.") }
            props["pdf_page"] = .number(page)
            if !strokes.isEmpty {
                let rect: [String: JSONValue] = ["x1": .number(x0), "y1": .number(y0), "x2": .number(x1), "y2": .number(y1), "width": .number(width), "height": .number(height), "pageNumber": .number(page)]
                props["pdf_position"] = .object(["pageNumber": .number(page), "boundingRect": .object(rect), "rects": .array([.object(rect)])])
            } else { props["pdf_position"] = .null }
        }
        // Validate without rewriting previously saved opaque bytes. Numeric spelling
        // is not a cross-language hash contract; uploaded bytes determine the digest.
        return (data, props)
    }
}

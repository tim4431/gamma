import CryptoKit
import Foundation

/// The replica's stored files (`uploads/<name>`), named like the server names
/// them: the first 24 hex digits of the content's SHA-256, then the extension
/// (gamma/storage.py content_digest), except a PDF, which may be named by the
/// URL it came from and only has to be a PDF (storage.matches_name).
final class FileStore {
    let directory: URL

    init(directory: URL) throws {
        self.directory = directory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    static let namePattern = try! NSRegularExpression(pattern: "^[0-9A-Za-z_-]+\\.[0-9A-Za-z]{1,12}$")

    static func validName(_ name: String) -> Bool {
        namePattern.firstMatch(in: name, range: NSRange(name.startIndex..., in: name)) != nil
    }

    static func digest(_ data: Data) -> String {
        let hex = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        return String(hex.prefix(24))
    }

    /// Whether `data` can be what the stored file `name` holds.
    static func matches(name: String, data: Data) -> Bool {
        let stem = (name as NSString).deletingPathExtension
        if name.lowercased().hasSuffix(".pdf") { return data.starts(with: Array("%PDF".utf8)) }
        return stem.count != 24 || digest(data) == stem
    }

    func url(_ name: String) -> URL { directory.appendingPathComponent(name, isDirectory: false) }

    func has(_ name: String) -> Bool {
        FileStore.validName(name) && FileManager.default.fileExists(atPath: url(name).path)
    }

    func read(_ name: String) -> Data? {
        guard FileStore.validName(name) else { return nil }
        return try? Data(contentsOf: url(name))
    }

    /// Written whole or not at all (a temporary file renamed over the name).
    func write(_ name: String, _ data: Data) throws {
        guard FileStore.validName(name) else { throw JSON.Failure(message: "bad file name \(name)") }
        try data.write(to: url(name), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    /// Store `data` under its hash with `ext` (".ink"): → the name.
    func store(_ data: Data, ext: String) throws -> String {
        let name = FileStore.digest(data) + ext
        if !has(name) { try write(name, data) }
        return name
    }

    func removeAll() {
        try? FileManager.default.removeItem(at: directory)
    }
}

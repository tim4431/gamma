import Foundation
import CryptoKit

struct InkRequest: Codable {
    var requestId: String
    var user: String
    var workspace: String
    var pageId: String
    var document: String
    var blockId: String
    var parentId: String
    var expectedURL: String?
    var existing: Bool
    var ink: GammaInk
    var background: [GammaInk]
    var image: String

    func draftKey(origin: String) throws -> String {
        let fields = [origin, user, workspace, pageId, document, parentId,
                      String(ink.space.page), String(ink.space.width), String(ink.space.height), existing ? blockId : "new"]
        return SHA256.hash(data: try JSONEncoder().encode(fields)).map { String(format: "%02x", $0) }.joined()
    }

    func validate() throws {
        let ids = [requestId, workspace, pageId, blockId, parentId]
        guard ids.allSatisfy({ $0.range(of: "^[A-Za-z0-9_-]{1,64}$", options: .regularExpression) != nil }),
              !user.isEmpty, user.count <= 256, document.count <= 4096,
              image.hasPrefix("data:image/png;base64,"), image.utf8.count <= 48 * 1024 * 1024,
              background.count <= 500 else { throw InkFailure("Invalid handwriting request.") }
        try ink.validate()
        var total = ink.strokes.count
        for other in background {
            try other.validate()
            guard other.space == ink.space else { throw InkFailure("The PDF page dimensions changed. Reopen the document.") }
            total += other.strokes.count
        }
        guard total <= 10_000 else { throw InkFailure("Too many strokes to open on this page.") }
    }
}

struct InkDraft: Codable {
    let key: String
    var blockId: String
    var expectedURL: String?
    var asCopy = false
    let baseInk: GammaInk
    var drawing: Data
}

struct InkDraftStore {
    let directory: URL
    init(directory: URL? = nil) throws {
        self.directory = try directory ?? FileManager.default.url(for: .applicationSupportDirectory,
            in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("InkDrafts", isDirectory: true)
        try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }
    private func url(_ key: String) throws -> URL {
        guard key.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { throw InkFailure("Invalid draft key.") }
        return directory.appendingPathComponent(key).appendingPathExtension("json")
    }
    func read(_ key: String) throws -> InkDraft? {
        let file = try url(key)
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        let data = try Data(contentsOf: file)
        let draft = try JSONDecoder().decode(InkDraft.self, from: data)
        guard draft.key == key else { throw InkFailure("Damaged local draft. It has been kept for recovery.") }
        try draft.baseInk.validate()
        return draft
    }
    func write(_ draft: InkDraft) throws {
        try JSONEncoder().encode(draft).write(to: url(draft.key), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    func remove(_ key: String) throws {
        let file = try url(key)
        if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
    }
}

import Foundation
import UniformTypeIdentifiers

/// The files the system opens in the app (the share sheet's Gamma, Files'
/// Open in, AirDrop), held until the web app takes them (docs/dev/ipad.md
/// "Opened files"). Each is copied into a folder of its own and listed; the
/// page asks for the list (`files`), reads each file in chunks (`fileData`)
/// and says when it is done with one (`fileDone`), and only then does the
/// copy go. So a file opened before the page is up, signed in or on its
/// workspace waits for it, across reloads too. The page decides what a file
/// becomes; nothing here knows.
@MainActor
final class OpenedFiles {
    struct File: Equatable {
        let id: String
        let name: String
        let size: Int
        /// The MIME type the extension has on this system, "" when none.
        let type: String
        let url: URL
    }

    /// The most one `fileData` reply carries, before base64.
    static let chunkBytes = 2 * 1024 * 1024

    /// The files waiting, in the order they came.
    private(set) var files: [File] = []
    /// A file was added: the shell tells the page (`filesOpened`).
    var onArrival: (() -> Void)?
    /// Where the copies are, one folder per file so its name is kept.
    let folder: URL
    /// The Inbox iOS copies a file into when the app it came from does not
    /// open it in place (a Mail attachment): Documents/Inbox, which the
    /// Files app shows. A file there is moved out, not copied.
    let inbox: URL

    init(folder: URL = FileManager.default.temporaryDirectory.appendingPathComponent("Opened", isDirectory: true),
         inbox: URL = URL.documentsDirectory.appendingPathComponent("Inbox", isDirectory: true)) {
        self.folder = folder
        self.inbox = inbox
        // What an earlier run held and its page never took went with that run.
        try? FileManager.default.removeItem(at: folder)
    }

    /// A file the system opened in the app, copied and listed; false when it
    /// could not be read (a folder, or a file gone or unreadable).
    @discardableResult
    func take(_ url: URL) async -> Bool {
        guard url.isFileURL else { return false }
        let id = UUID().uuidString
        let name = url.lastPathComponent.isEmpty ? "File" : url.lastPathComponent
        let destination = folder.appendingPathComponent(id, isDirectory: true).appendingPathComponent(name)
        let move = Self.isInside(url, inbox)
        let copied: Int? = await Task.detached(priority: .userInitiated) {
            try? Self.copy(url, to: destination, move: move)
        }.value
        guard let size = copied else {
            try? FileManager.default.removeItem(at: destination.deletingLastPathComponent())
            return false
        }
        let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? ""
        files.append(File(id: id, name: name, size: size, type: type, url: destination))
        onArrival?()
        return true
    }

    /// The `files` reply: `{files: [{id, name, size, type}]}`.
    var listing: [String: Any] {
        ["files": files.map { ["id": $0.id, "name": $0.name, "size": $0.size, "type": $0.type] as [String: Any] }]
    }

    /// The `fileData` reply: up to `chunkBytes` of the file from `offset`, as
    /// base64 (`{data}`, "" at the end), or nil when the shell holds no such
    /// file or the offset is outside it.
    func chunk(id: String, offset: Int) -> [String: Any]? {
        guard let file = files.first(where: { $0.id == id }), offset >= 0, offset <= file.size,
              let handle = try? FileHandle(forReadingFrom: file.url) else { return nil }
        defer { try? handle.close() }
        do {
            try handle.seek(toOffset: UInt64(offset))
            let data = try handle.read(upToCount: Self.chunkBytes) ?? Data()
            return ["data": data.base64EncodedString()]
        } catch {
            return nil
        }
    }

    /// The page is done with a file (imported, refused or put aside): its
    /// copy goes. An id the shell does not hold changes nothing.
    func done(id: String) {
        guard let index = files.firstIndex(where: { $0.id == id }) else { return }
        let file = files.remove(at: index)
        try? FileManager.default.removeItem(at: file.url.deletingLastPathComponent())
    }

    /// Copies (or moves) `source` to `destination` under its security scope
    /// and a file coordinator, as a file opened in place from another app's
    /// container needs: the bytes copied, or a throw. Not a folder: the page
    /// reads files.
    nonisolated private static func copy(_ source: URL, to destination: URL, move: Bool) throws -> Int {
        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }
        let manager = FileManager.default
        try manager.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        var coordination: NSError?
        var failure: Error?
        NSFileCoordinator().coordinate(readingItemAt: source, options: .withoutChanges, error: &coordination) { readable in
            do {
                var isFolder: ObjCBool = false
                guard manager.fileExists(atPath: readable.path, isDirectory: &isFolder), !isFolder.boolValue else {
                    throw CocoaError(.fileReadNoSuchFile)
                }
                if move {
                    try manager.moveItem(at: readable, to: destination)
                } else {
                    try manager.copyItem(at: readable, to: destination)
                }
            } catch {
                failure = error
            }
        }
        if let error: Error = coordination ?? failure { throw error }
        let size = try manager.attributesOfItem(atPath: destination.path)[.size] as? NSNumber
        return size?.intValue ?? 0
    }

    nonisolated static func isInside(_ url: URL, _ folder: URL) -> Bool {
        let path = url.standardizedFileURL.resolvingSymlinksInPath().path
        let base = folder.standardizedFileURL.resolvingSymlinksInPath().path
        return path.hasPrefix(base.hasSuffix("/") ? base : base + "/")
    }
}

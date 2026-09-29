import Foundation

struct PendingBatch: Codable, Sendable { var id: String; var ops: [GammaOperation]; var attempted: Bool = false }
struct PageCheckpoint: Codable, Sendable {
    var remoteSeq: Int = -1
    var base: GammaSnapshot = [:]
    var pending: [PendingBatch] = []
    var creating: Bool = false
}

/// One durable local workspace. All editors and the mirror write through this actor.
/// Network awaits allow editing to continue; each reconciliation reads the current
/// local snapshot again before committing. No localhost server or Python runtime.
public actor GammaRepository {
    let db: SQLiteStore
    let assets: URL
    let transport: any GammaTransport
    var token = ""
    var syncing = false
    public let directory: URL

    public init(directory: URL, transport: any GammaTransport = GammaURLSessionTransport()) throws {
        self.directory = directory; self.transport = transport
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        assets = directory.appendingPathComponent("uploads", isDirectory: true)
        try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
        db = try SQLiteStore(url: directory.appendingPathComponent("replica.sqlite"))
        if var mirror = try db.get("mirror", as: GammaMirror.self), mirror.running {
            mirror.running = false; mirror.lastError = "The previous sync was interrupted; it will resume from its saved checkpoint."
            try db.set("mirror", mirror)
        }
    }

    public func documents() throws -> [GammaDocument] {
        try pageIDs().compactMap { try documentOrNil(id: $0) }.sorted { ($0.title.localizedLowercase, $0.id) < ($1.title.localizedLowercase, $1.id) }
    }
    public func document(id: String) throws -> GammaDocument {
        guard let value = try documentOrNil(id: id) else { throw GammaError.missing("This document is not stored on the iPad.") }
        return value
    }
    func documentOrNil(id: String) throws -> GammaDocument? {
        let tree = try snapshot(id)
        guard let root = tree[id] else { return nil }
        return GammaDocument(root: root, blocks: [root] + GammaTree.ordered(tree, pageID: id))
    }
    public func createPage(title: String, properties: [String: JSONValue] = [:]) throws -> GammaDocument {
        let root = GammaBlock(parent: "root", position: GammaTree.nextPosition(after: try rootPositions().max()), content: title.isEmpty ? "Untitled" : title, properties: properties)
        try db.transaction { try replace([root.id: root], pageID: root.id, source: "local", ops: []) }
        return GammaDocument(root: root, blocks: [root])
    }
    public func apply(pageID: String, ops: [GammaOperation]) throws {
        try db.transaction {
            let before = try snapshot(pageID)
            guard before[pageID] != nil else { throw GammaError.missing("Document is missing.") }
            let after = try GammaTree.apply(ops, to: before, pageID: pageID)
            try replace(after, pageID: pageID, source: "local", ops: ops)
        }
    }
    public func deletePage(id: String) throws {
        try db.transaction {
            try db.run("DELETE FROM blocks WHERE page_id=?", [id])
            try db.run("INSERT OR REPLACE INTO deleted_pages(page_id,at) VALUES (?,?)", [id, GammaJSON.now()])
            try journal(id, source: "local", ops: [.init(op: "delete", id: id)])
        }
    }
    public func storeAsset(data: Data, extension ext: String) throws -> String {
        let clean = ext.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        guard clean.range(of: "^[a-z0-9]{1,8}$", options: .regularExpression) != nil else { throw GammaError.invalid("Invalid attachment extension.") }
        let name = GammaAssets.digest(data) + "." + clean
        try GammaAssets.write(data, name: name, directory: assets)
        return "/api/uploads/" + name
    }
    public func assetURL(reference: String) throws -> URL {
        let url = assets.appendingPathComponent(try GammaAssets.filename(reference))
        guard FileManager.default.fileExists(atPath: url.path) else { throw GammaError.missing("This attachment has not finished downloading.") }
        return url
    }
    public func importPDF(data: Data, title: String) throws -> GammaDocument {
        let reference = try storeAsset(data: data, extension: "pdf")
        let id = GammaAssets.digest(data)
        if let existing = try documents().first(where: { $0.properties["doc_id"]?.string == id }) { return existing }
        return try createPage(title: title, properties: ["doc_id": .string(id), "source_url": .string(reference), "original_filename": .string(title)])
    }
    public static let defaultPaper: [String: JSONValue] = ["width": .number(595.28), "height": .number(841.89), "color": .string("#ffffff"), "pattern": .string("ruled"), "spacing": .number(24), "line_color": .string("#cbd5e1")]
    public func createNotebook(title: String, paper: [String: JSONValue] = GammaRepository.defaultPaper) throws -> GammaDocument {
        let full = Self.defaultPaper.merging(paper) { _, new in new }
        let document = try createPage(title: title, properties: ["notebook": .object(["version": .number(1), "default_paper": .object(full)])])
        _ = try appendSheet(notebookID: document.id, paper: full)
        return try self.document(id: document.id)
    }
    public func appendSheet(notebookID: String, paper: [String: JSONValue] = [:]) throws -> GammaBlock {
        let document = try self.document(id: notebookID)
        guard document.properties["notebook"] != nil else { throw GammaError.invalid("This document is not a notebook.") }
        let inherited = document.properties["notebook"]?["default_paper"]?.object ?? Self.defaultPaper
        let full = inherited.merging(paper) { _, new in new }
        guard let width = full["width"]?.number, let height = full["height"]?.number, width > 0, height > 0 else { throw GammaError.invalid("Paper size must be positive.") }
        let block = GammaBlock(parent: notebookID, position: GammaTree.nextPosition(after: document.blocks.filter { $0.parent == notebookID }.map(\.position).max()), properties: ["type": .string("notebook-sheet"), "paper": .object(full)])
        try apply(pageID: notebookID, ops: [.init(op: "insert", id: block.id, parent: block.parent, position: block.position, props: block.properties)])
        return block
    }
    /// An empty stroke file is valid: erasing ink must not delete its caption or children.
    public func saveInk(pageID: String, blockID: String, parentID: String, ink: Data, baseURL: String? = nil) throws -> GammaBlock {
        let parsed = try InkMetadata.parse(ink)
        let reference = try storeAsset(data: parsed.data, extension: "ink")
        var props = parsed.props; props["ink_url"] = .string(reference)
        let tree = try snapshot(pageID)
        if let existing = tree[blockID] {
            let expected: JSONValue = baseURL.map { $0.isEmpty ? .null : .string($0) } ?? existing.properties["ink_url"] ?? .null
            if (existing.properties["ink_url"] ?? .null) != expected && existing.properties["ink_url"] != .string(reference) {
                var variant = existing
                variant.id = GammaID.make(); variant.position = GammaTree.nextPosition(after: GammaTree.children(tree, of: existing.parent).last?.position)
                for (key, value) in props { if value == .null { variant.properties.removeValue(forKey: key) } else { variant.properties[key] = value } }
                variant.properties["ink_conflict"] = .object(["source_block_id": .string(blockID), "base_url": expected, "remote_url": existing.properties["ink_url"] ?? .null])
                try apply(pageID: pageID, ops: [.init(op: "insert", id: variant.id, parent: variant.parent, position: variant.position, content: variant.content, props: variant.properties)])
                return variant
            }
            try apply(pageID: pageID, ops: [.init(op: "set", id: blockID, props: props, baseProps: ["ink_url": expected])])
        } else {
            guard tree[parentID] != nil else { throw GammaError.missing("The handwriting parent is missing.") }
            try apply(pageID: pageID, ops: [.init(op: "insert", id: blockID, parent: parentID, position: GammaTree.nextPosition(after: GammaTree.children(tree, of: parentID).last?.position), props: props.filter { $0.value != .null })])
        }
        guard let saved = try snapshot(pageID)[blockID] else { throw GammaError.missing("Ink was not saved.") }
        return saved
    }
    public func conflicts() throws -> [GammaConflict] { try db.rows("SELECT value FROM conflicts ORDER BY rowid").map { try GammaJSON.decode(GammaConflict.self, $0[0]) } }
    public func resolveConflict(id: String, choice: String) throws {
        guard let raw = try db.rows("SELECT value FROM conflicts WHERE id=?", [id]).first?.first else { return }
        let conflict = try GammaJSON.decode(GammaConflict.self, raw)
        if choice != "keep", ["merged", "diverged"].contains(conflict.kind) {
            guard choice == "mine" || choice == "theirs" else { throw GammaError.invalid("Unknown conflict choice.") }
            try apply(pageID: conflict.pageID, ops: [.init(op: "set", id: conflict.blockID, content: choice == "mine" ? conflict.mine : conflict.theirs, base: conflict.result)])
        }
        try db.run("DELETE FROM conflicts WHERE id=?", [id])
    }

    public func mirror() throws -> GammaMirror? {
        guard var value = try db.get("mirror", as: GammaMirror.self) else { return nil }
        value.running = syncing
        value.pending = try journalHead() > localCursor() || !value.retryPages.isEmpty
        return value
    }
    public func setMirrorToken(_ value: String) { token = value }
    public func configureMirror(origin: URL, account: String, workspaceID: String, token: String, mode: String = "two-way") async throws {
        guard !syncing else { throw GammaError.busy }
        guard ["two-way", "pull"].contains(mode), !account.isEmpty, !workspaceID.isEmpty,
              ["https", "http"].contains(origin.scheme ?? ""), origin.host != nil, origin.user == nil, origin.password == nil,
              origin.query == nil, origin.fragment == nil, origin.path.isEmpty || origin.path == "/" else { throw GammaError.invalid("Enter a server origin, account, workspace, and token.") }
        let normalized = origin.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if let old = try mirror(), old.origin != normalized || old.account != account || old.workspaceID != workspaceID { throw GammaError.invalid("This local copy belongs to another account or workspace. Create a separate local library.") }
        var value = GammaMirror(origin: normalized, account: account, workspaceID: workspaceID, mode: mode)
        let remote = GammaRemote(mirror: value, token: token, transport: transport)
        let who = try await remote.json("GET", "/api/sync/whoami")
        try validateIdentity(who, mirror: value)
        if who["role"]?.string == "viewer" || who["scope"]?.string != "write" { value.mode = "pull" }
        if var old = try db.get("mirror", as: GammaMirror.self) { old.mode = value.mode; value = old }
        try db.set("mirror", value); self.token = token
    }
    public func detachMirror() throws {
        guard !syncing else { throw GammaError.busy }
        guard var value = try mirror() else { return }
        value.lastMode = value.mode; value.mode = "off"; try db.set("mirror", value)
    }
    public func reattachMirror() throws {
        guard var value = try mirror() else { return }
        value.mode = value.lastMode == "pull" ? "pull" : "two-way"; try db.set("mirror", value)
    }
    public func removeMirror() throws {
        guard !syncing else { throw GammaError.busy }
        try db.transaction {
            try db.run("DELETE FROM meta WHERE key IN ('mirror','local_cursor')")
            try db.run("DELETE FROM sync_pages")
        }
        token = ""
    }
    public func setMirrorMode(_ mode: String) throws {
        guard ["two-way", "pull"].contains(mode), var value = try mirror(), value.mode != "off", !syncing else { throw GammaError.invalid("Cannot change this mirror's direction now.") }
        value.mode = mode; try db.set("mirror", value)
    }
    func validateIdentity(_ who: JSONValue, mirror: GammaMirror) throws {
        guard who["user"]?.string == mirror.account, who["workspace"]?["id"]?.string == mirror.workspaceID else { throw GammaError.authentication("The token belongs to a different account or workspace.") }
    }
    func snapshot(_ pageID: String) throws -> GammaSnapshot {
        let blocks = try db.rows("SELECT value FROM blocks WHERE page_id=?", [pageID]).map { try GammaJSON.decode(GammaBlock.self, $0[0]) }
        return Dictionary(uniqueKeysWithValues: blocks.map { ($0.id, $0) })
    }
    func pageIDs() throws -> [String] { try db.rows("SELECT DISTINCT page_id FROM blocks").map { $0[0] } }
    func rootPositions() throws -> [String] { try pageIDs().compactMap { try snapshot($0)[$0]?.position } }
    func journalHead() throws -> Int { Int(try db.rows("SELECT COALESCE(MAX(seq),0) FROM journal WHERE source='local'").first?[0] ?? "0") ?? 0 }
    func localCursor() throws -> Int { try db.get("local_cursor", as: Int.self) ?? 0 }
    func journal(_ pageID: String, source: String, ops: [GammaOperation]) throws {
        try db.run("INSERT INTO journal(page_id,source,value) VALUES (?,?,?)", [pageID, source, try GammaJSON.string(ops)])
    }
    func replace(_ tree: GammaSnapshot, pageID: String, source: String, ops: [GammaOperation]) throws {
        // A block ID is global to this workspace. Cross-page arrival is handled before
        // this write, never by silently replacing another page's row.
        for block in tree.values {
            if let other = try db.rows("SELECT page_id FROM blocks WHERE id=?", [block.id]).first?.first, other != pageID {
                throw GammaError.invalid("Block \(block.id) moved between pages; waiting for its source page to reconcile.")
            }
        }
        try db.run("DELETE FROM blocks WHERE page_id=?", [pageID])
        for block in tree.values { try db.run("INSERT INTO blocks(id,page_id,value) VALUES (?,?,?)", [block.id, pageID, try GammaJSON.string(block)]) }
        if tree[pageID] != nil { try db.run("DELETE FROM deleted_pages WHERE page_id=?", [pageID]) }
        try journal(pageID, source: source, ops: ops)
    }
    func checkpoint(_ pageID: String) throws -> PageCheckpoint? {
        guard let raw = try db.rows("SELECT value FROM sync_pages WHERE page_id=?", [pageID]).first?.first else { return nil }
        return try GammaJSON.decode(PageCheckpoint.self, raw)
    }
    func saveCheckpoint(_ pageID: String, _ state: PageCheckpoint) throws {
        try db.run("INSERT OR REPLACE INTO sync_pages(page_id,value) VALUES (?,?)", [pageID, try GammaJSON.string(state)])
    }
    func record(_ conflicts: [GammaConflict]) throws {
        for conflict in conflicts {
            // Deterministic key avoids duplicate review rows after an interrupted round.
            var value = conflict
            value.id = GammaAssets.digest(Data("\(conflict.pageID):\(conflict.blockID):\(conflict.kind):\(conflict.mine):\(conflict.theirs)".utf8))
            try db.run("INSERT OR IGNORE INTO conflicts(id,value) VALUES (?,?)", [value.id, try GammaJSON.string(value)])
        }
    }
}

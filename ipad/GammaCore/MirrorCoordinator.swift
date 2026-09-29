import Foundation

extension GammaRepository {
    /// The mirror is snapshot reconciliation, not replay of an indefinitely growing
    /// outbox. Only an unconfirmed delivery is kept as a pending batch checkpoint.
    public func sync() async throws -> GammaMirror {
        guard !syncing else { throw GammaError.busy }
        guard var mirror = try mirror() else { throw GammaError.invalid("This library has no origin.") }
        if mirror.mode == "off" { return mirror }
        guard !token.isEmpty else { throw GammaError.authentication("Unlock the saved integration token before syncing.") }
        syncing = true; mirror.running = true; mirror.lastError = nil
        roundLeft = [:]
        try db.set("mirror", mirror)
        defer { syncing = false; roundLeft = [:] }
        var remote = GammaRemote(mirror: mirror, token: token, transport: transport)
        let startHead = try journalHead()
        do {
            let who = try await remote.json("GET", "/api/sync/whoami")
            try validateIdentity(who, mirror: mirror)
            remote.capabilities = who["capabilities"]?.object ?? [:]
            let canPush = mirror.mode == "two-way" && who["scope"]?.string == "write" && who["role"]?.string != "viewer"
            var cursor = mirror.remoteCursor, pages: [String: Int] = [:], deleted = Set<String>()
            while true {
                let query = cursor.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "+&=#"))) ?? ""
                let feed = try await remote.json("GET", "/api/sync/changes?limit=500&since=" + query)
                for item in feed["pages"]?.array ?? [] { if let id = item["id"]?.string { pages[id] = Int(item["seq"]?.number ?? 0) } }
                for item in feed["deleted"]?.array ?? [] { if let id = item["id"]?.string { deleted.insert(id) } }
                let next = feed["cursor"]?.string ?? cursor
                guard feed["more"] != .bool(true) || next != cursor else { throw GammaError.invalid("The remote change feed did not advance.") }
                cursor = next
                if feed["more"] != .bool(true) { break }
            }
            var todo = Set(pages.keys).union(deleted).union(mirror.retryPages)
            if canPush {
                let changed = try db.rows("SELECT DISTINCT page_id FROM journal WHERE source='local' AND seq>?", [String(try localCursor())])
                todo.formUnion(changed.map { $0[0] })
            }
            // Saved uncertain deliveries must be revisited even if neither feed lists them.
            for row in try db.rows("SELECT page_id,value FROM sync_pages") {
                let checkpoint = try GammaJSON.decode(PageCheckpoint.self, row[1])
                if checkpoint.creating || !checkpoint.pending.isEmpty || checkpoint.remoteSeq < 0 { todo.insert(row[0]) }
            }
            var failures: [String] = [], firstError: String?
            for pageID in todo.sorted() {
                do { try await syncPage(pageID, remote: remote, canPush: canPush, remoteDeleted: deleted.contains(pageID) && pages[pageID] == nil, hint: pages[pageID]) }
                catch { failures.append(pageID); if firstError == nil { firstError = error.localizedDescription } }
            }
            // Recheck references even on unchanged pages: an interrupted download or a
            // missing local file must heal without a new edit in the origin's feed.
            for pageID in try pageIDs() {
                do { try await pullFiles(GammaAssets.references(try snapshot(pageID)), remote: remote) }
                catch { if !failures.contains(pageID) { failures.append(pageID) }; if firstError == nil { firstError = error.localizedDescription } }
            }
            mirror.remoteCursor = cursor; mirror.retryPages = failures
            mirror.running = false; mirror.lastSync = GammaJSON.now(); mirror.lastError = firstError
            if mirror.mode == "two-way" && !canPush { mirror.lastError = "The token or remote role is read-only. Local edits remain on this iPad." }
            try db.transaction {
                if canPush { try db.set("local_cursor", startHead) }
                mirror.pending = try journalHead() > localCursor() || !failures.isEmpty
                try db.set("mirror", mirror)
            }
            return mirror
        } catch {
            mirror.running = false; mirror.lastError = error.localizedDescription
            try? db.set("mirror", mirror)
            throw error
        }
    }

    func pullFiles(_ names: Set<String>, remote: GammaRemote) async throws {
        for name in names.sorted() {
            let path = assets.appendingPathComponent(try GammaAssets.filename(name))
            if FileManager.default.fileExists(atPath: path.path) { continue }
            let response = try await remote.call("GET", "/api/uploads/" + name)
            try GammaAssets.write(response.data, name: name, directory: assets)
        }
    }
    func pushFiles(_ names: Set<String>, remote: GammaRemote) async throws {
        for name in names.sorted() {
            let response = try await remote.call("HEAD", "/api/uploads/" + name, allowed: [200, 404])
            if response.status == 200 { continue }
            let file = try assetURL(reference: name)
            let data = try Data(contentsOf: file)
            try GammaAssets.verify(data, name: name)
            try await remote.upload(name: name, data: data)
        }
    }
    func syncPage(_ pageID: String, remote: GammaRemote, canPush: Bool, remoteDeleted: Bool, hint: Int?) async throws {
        if let old = try checkpoint(pageID), !old.pending.isEmpty { try await confirmPending(pageID, remote: remote, canPush: canPush) }
        let state = try checkpoint(pageID)
        var local = try snapshot(pageID)
        let base = state?.base ?? [:]
        let fetched: (GammaSnapshot?, Int)
        if let state, state.remoteSeq >= 0, state.pending.isEmpty, !state.creating, !remoteDeleted,
           (hint == nil || hint == state.remoteSeq), !base.isEmpty {
            fetched = (base, state.remoteSeq)
        } else { fetched = try await remote.tree(pageID) }
        // A local edit may have landed while the tree was loading.
        local = try snapshot(pageID)
        guard let there = fetched.0 else {
            if local.isEmpty {
                try db.run("DELETE FROM sync_pages WHERE page_id=?", [pageID]); return
            }
            if state != nil && !state!.creating && local == base {
                try db.transaction {
                    try db.run("DELETE FROM blocks WHERE page_id=?", [pageID])
                    try db.run("INSERT OR REPLACE INTO deleted_pages(page_id,at) VALUES (?,?)", [pageID, GammaJSON.now()])
                    try db.run("DELETE FROM sync_pages WHERE page_id=?", [pageID])
                }
                return
            }
            guard canPush else { return }
            // Checkpoint the creation before its request: a lost answer must not make
            // the next round mistake our just-created page for unrelated remote data.
            var creation = state ?? PageCheckpoint()
            creation.creating = true; try saveCheckpoint(pageID, creation)
            guard let root = local[pageID] else { return }
            try remote.requireCapabilities(for: [.init(op: "insert", id: root.id, props: root.properties)] + GammaTree.diff(base: [root.id: root], target: local, pageID: pageID))
            _ = try await remote.json("POST", "/api/pages", body: .object(["id": .string(pageID), "title": .string(root.content), "properties": .object(root.properties)]), allowed: [200, 201, 409])
            let created = try await remote.tree(pageID)
            guard let bare = created.0 else { throw GammaError.missing("The origin did not retain the created page.") }
            creation.base = bare; creation.remoteSeq = created.1; creation.creating = false
            try saveCheckpoint(pageID, creation)
            try await syncPage(pageID, remote: remote, canPush: canPush, remoteDeleted: false, hint: created.1)
            return
        }
        if local.isEmpty {
            if state != nil, there == base, canPush {
                _ = try await remote.call("DELETE", "/api/blocks/" + pageID, allowed: [200, 204, 404])
                try db.run("DELETE FROM sync_pages WHERE page_id=?", [pageID]); return
            }
            try await pullFiles(GammaAssets.references(there), remote: remote)
            // Restore rather than delete when the origin changed since our deletion.
            try db.transaction {
                let prepared = try carryCrossPage(pageID: pageID, base: [:], local: [:], remote: there)
                let merged = GammaReconciler.reconcile(pageID: pageID, base: prepared.base, local: prepared.local, remote: there)
                try replace(merged.snapshot, pageID: pageID, source: "sync", ops: [])
                try saveCheckpoint(pageID, PageCheckpoint(remoteSeq: fetched.1, base: there))
                try record(merged.conflicts)
                if state != nil { try record([.init(pageID: pageID, blockID: pageID, kind: "page_restored_from_remote")]) }
            }
            if canPush { try await syncPage(pageID, remote: remote, canPush: true, remoteDeleted: false, hint: fetched.1) }
            return
        }
        try await pullFiles(GammaAssets.references(there), remote: remote)
        try db.transaction {
            let current = try snapshot(pageID)
            let prepared = try carryCrossPage(pageID: pageID, base: base, local: current, remote: there)
            let merged = GammaReconciler.reconcile(pageID: pageID, base: prepared.base, local: prepared.local, remote: there)
            for (id, old) in prepared.base where there[id] == nil && merged.snapshot[id] != nil { roundLeft[id] = old }
            try replace(merged.snapshot, pageID: pageID, source: "sync", ops: GammaTree.diff(base: current, target: merged.snapshot, pageID: pageID))
            try saveCheckpoint(pageID, PageCheckpoint(remoteSeq: fetched.1, base: there))
            try record(merged.conflicts)
        }
        guard canPush else { return }
        let outgoing = try snapshot(pageID)
        let ops = GammaTree.diff(base: there, target: outgoing, pageID: pageID)
        if ops.isEmpty { return }
        try remote.requireCapabilities(for: ops)
        try await pushFiles(GammaAssets.references(outgoing), remote: remote)
        var delivery = PageCheckpoint(remoteSeq: fetched.1, base: there)
        for offset in stride(from: 0, to: ops.count, by: 500) { delivery.pending.append(PendingBatch(id: GammaID.make(), ops: Array(ops[offset..<min(offset + 500, ops.count)]))) }
        try saveCheckpoint(pageID, delivery)
        try await confirmPending(pageID, remote: remote, canPush: true)
        let after = try await remote.tree(pageID)
        guard let remoteAfter = after.0 else { return }
        try await pullFiles(GammaAssets.references(remoteAfter), remote: remote)
        try db.transaction {
            let current = try snapshot(pageID)
            // Edits made during uploads/pushes survive the remote's actual merged answer.
            let prepared = try carryCrossPage(pageID: pageID, base: outgoing, local: current, remote: remoteAfter)
            let merged = GammaReconciler.reconcile(pageID: pageID, base: prepared.base, local: prepared.local, remote: remoteAfter)
            try replace(merged.snapshot, pageID: pageID, source: "sync", ops: [])
            try saveCheckpoint(pageID, PageCheckpoint(remoteSeq: after.1, base: remoteAfter))
            try record(merged.conflicts)
        }
    }

    func confirmPending(_ pageID: String, remote: GammaRemote, canPush: Bool) async throws {
        guard var state = try checkpoint(pageID), !state.pending.isEmpty else { return }
        while let batch = state.pending.first {
            let answer = try await remote.tree(pageID)
            guard let there = answer.0 else { state.pending = []; state.remoteSeq = -1; try saveCheckpoint(pageID, state); return }
            var landed: [GammaOperation] = [], unsent: [GammaOperation] = []
            for operation in batch.ops {
                let parts = batch.attempted ? unlanded(operation, base: state.base, remote: there) : (nil, Optional(operation))
                if let value = parts.0 { landed.append(value) }
                if let value = parts.1 { unsent.append(value) }
            }
            if !unsent.isEmpty && canPush {
                do {
                    try remote.requireCapabilities(for: unsent)
                    // This flag is durable before the request; a process death can make
                    // its delivery uncertain, never silently turn it into a fresh edit.
                    state.pending[0].attempted = true
                    try saveCheckpoint(pageID, state)
                    let opsValue = try JSONDecoder().decode(JSONValue.self, from: GammaJSON.data(unsent))
                    let value = try await remote.json("POST", "/api/pages/\(pageID)/ops", body: .object(["client": .string("sync"), "batch": .string(batch.id), "ops": opsValue]))
                    if let actual = value["ops"] { landed += try JSONDecoder().decode([GammaOperation].self, from: GammaJSON.data(actual)) }
                    else { landed += unsent }
                } catch let error as GammaRemoteError where error.status == 409 && error.body?["conflict"]?.string == "property_changed" {
                    // A definitive refusal is different from an uncertain delivery.
                    // Preserve the competing file as an ordinary referenced ink block.
                    let latest = try await remote.tree(pageID)
                    guard let currentRemote = latest.0 else { throw error }
                    try await pullFiles(GammaAssets.references(currentRemote), remote: remote)
                    try db.transaction {
                        let current = try snapshot(pageID)
                        let merged = GammaReconciler.reconcile(pageID: pageID, base: state.base, local: current, remote: currentRemote)
                        try replace(merged.snapshot, pageID: pageID, source: "local", ops: [])
                        try record(merged.conflicts)
                        try saveCheckpoint(pageID, PageCheckpoint(remoteSeq: latest.1, base: currentRemote))
                    }
                    return
                }
            }
            state.base = try GammaTree.apply(landed, to: state.base, pageID: pageID, strict: false)
            state.pending.removeFirst(); state.remoteSeq = -1
            try saveCheckpoint(pageID, state)
        }
    }

    func unlanded(_ op: GammaOperation, base: GammaSnapshot, remote: GammaSnapshot) -> (GammaOperation?, GammaOperation?) {
        if op.op == "insert" { return remote[op.id] == nil ? (nil, op) : (op, nil) }
        if op.op == "delete" {
            guard remote[op.id] != nil else { return (op, nil) }
            let scope = GammaTree.descendants(remote, of: op.id)
            return scope == GammaTree.descendants(base, of: op.id) && scope.allSatisfy { remote[$0] == base[$0] } ? (nil, op) : (nil, nil)
        }
        guard let now = remote[op.id] else { return (nil, nil) }
        let was = base[op.id]
        if op.op == "move" {
            let unchanged = now.parent == was?.parent && now.position == was?.position
            let desired = now.parent == op.parent && now.position == op.position
            return unchanged && !desired ? (nil, op) : (op, nil)
        }
        var landed = GammaOperation(op: "set", id: op.id), rest = GammaOperation(op: "set", id: op.id)
        if let content = op.content {
            let before = op.base ?? was?.content ?? ""
            if now.content == content || GammaTextMerge.contains(base: before, change: content, current: now.content) { landed.content = content; landed.base = op.base }
            else { rest.content = content; rest.base = op.base }
        }
        for (key, value) in op.props ?? [:] {
            let current = now.properties[key] ?? .null, old = was?.properties[key] ?? .null
            if key == "ink_url" && current != value {
                // An intervening ink replacement cannot prove this revision landed.
                // Reissue its precondition: a collision retains the competing file.
                if rest.props == nil { rest.props = [:] }; rest.props?[key] = value
            } else if current == old && current != value { if rest.props == nil { rest.props = [:] }; rest.props?[key] = value }
            else { if landed.props == nil { landed.props = [:] }; landed.props?[key] = value }
        }
        if rest.props?["ink_url"] != nil {
            rest.baseProps = op.baseProps
            for key in ["pdf_position", "pdf_page", "sheet_id", "ink_strokes"] {
                if let value = op.props?[key] { rest.props?[key] = value; landed.props?.removeValue(forKey: key) }
            }
        }
        return (landed.content == nil && landed.props == nil ? nil : landed, rest.content == nil && rest.props == nil ? nil : rest)
    }

    /// Move incoming IDs out of their old local page before inserting them here.
    /// Carry edits relative to that page's base; local new descendants travel with
    /// their moved parent. Remote-removed old children stay parked on the source.
    func carryCrossPage(pageID: String, base: GammaSnapshot, local: GammaSnapshot, remote: GammaSnapshot) throws -> (base: GammaSnapshot, local: GammaSnapshot) {
        var augmentedBase = base, augmentedLocal = local, homes: [String: Set<String>] = [:]
        for id in remote.keys {
            if let home = try db.rows("SELECT page_id FROM blocks WHERE id=?", [id]).first?.first, home != pageID { homes[home, default: []].insert(id) }
        }
        for (home, moving) in homes {
            var source = try snapshot(home), sourceState = try checkpoint(home) ?? PageCheckpoint()
            let oldBase = sourceState.base
            var extra = Set<String>()
            for id in moving {
                for child in GammaTree.descendants(source, of: id) where oldBase[child] == nil && roundLeft[child] == nil && remote[child] == nil { extra.insert(child) }
            }
            for id in moving.union(extra) {
                guard var block = source.removeValue(forKey: id) else { continue }
                if let target = remote[id] {
                    block.parent = target.parent; block.position = target.position
                    if var was = oldBase[id] ?? roundLeft[id] { was.parent = target.parent; was.position = target.position; augmentedBase[id] = was }
                }
                augmentedLocal[id] = block; sourceState.base.removeValue(forKey: id)
            }
            sourceState.pending = sourceState.pending.compactMap { batch in
                let kept = batch.ops.filter { !moving.union(extra).contains($0.id) }
                return kept.isEmpty ? nil : PendingBatch(id: batch.id, ops: kept, attempted: batch.attempted)
            }
            for id in source.keys where id != home {
                guard var block = source[id], source[block.parent] == nil else { continue }
                block.parent = home; block.position = GammaTree.nextPosition(after: GammaTree.children(source, of: home).last?.position)
                source[id] = block
                if var was = sourceState.base[id] { was.parent = block.parent; was.position = block.position; sourceState.base[id] = was }
            }
            try db.run("DELETE FROM blocks WHERE page_id=?", [home])
            try replace(source, pageID: home, source: "sync", ops: [])
            try saveCheckpoint(home, sourceState)
        }
        return (augmentedBase, augmentedLocal)
    }
}

import Foundation

public enum GammaTextMerge {
    struct Hunk { var start: Int; var end: Int; var insert: [UInt32] }
    static func scalars(_ text: String) -> [UInt32] { text.unicodeScalars.map(\.value) }
    static func string(_ values: [UInt32]) -> String {
        String(String.UnicodeScalarView(values.compactMap(Unicode.Scalar.init)))
    }
    static func hunks(_ base: String, _ text: String) -> [Hunk] {
        let a = scalars(base), b = scalars(text)
        let difference = b.difference(from: a)
        var removed = Set<Int>(), inserted = Set<Int>()
        for change in difference {
            switch change {
            case .remove(let offset, _, _): removed.insert(offset)
            case .insert(let offset, _, _): inserted.insert(offset)
            }
        }
        var i = 0, j = 0, result: [Hunk] = []
        while i < a.count || j < b.count {
            if removed.contains(i) || inserted.contains(j) {
                let start = i
                while removed.contains(i) { i += 1 }
                var addition: [UInt32] = []
                while inserted.contains(j) { addition.append(b[j]); j += 1 }
                if let last = result.last, last.end == start {
                    result[result.count - 1] = Hunk(start: last.start, end: i, insert: last.insert + addition)
                } else { result.append(Hunk(start: start, end: i, insert: addition)) }
            } else { i += 1; j += 1 }
        }
        return result
    }
    /// The same span policy as gamma/textmerge.py: stored overlapping replacements win;
    /// independent spans and simultaneous insertions survive. Diff tie-breaking can differ
    /// from diff-match-patch for repeated text; the origin's final answer remains authoritative.
    public static func merge(base: String, ours: String, theirs: String) -> (text: String, clean: Bool) {
        if theirs == base { return (ours, true) }
        if ours == base || ours == theirs { return (theirs, true) }
        let stored = hunks(base, theirs), mine = hunks(base, ours)
        let kept = mine.filter { h in !stored.contains { max(h.start, $0.start) < min(h.end, $0.end) } }
        let all = (stored.map { ($0, 0) } + kept.map { ($0, 1) }).sorted { a, b in
            if a.0.start != b.0.start { return a.0.start < b.0.start }
            if (a.0.end > a.0.start) != (b.0.end > b.0.start) { return a.0.end == a.0.start }
            return a.1 < b.1
        }
        let original = scalars(base)
        var result: [UInt32] = [], at = 0
        for (h, _) in all {
            if h.start > at { result += original[at..<h.start]; at = h.start }
            result += h.insert; at = max(at, h.end)
        }
        result += original[at...]
        return (string(result), kept.count == mine.count)
    }
    public static func contains(base: String, change: String, current: String) -> Bool {
        if change == current || change == base { return true }
        let mine = hunks(base, change), theirs = hunks(base, current)
        let mineDeleted = Set(mine.flatMap { Array($0.start..<$0.end) })
        let theirDeleted = Set(theirs.flatMap { Array($0.start..<$0.end) })
        return mineDeleted.isSubset(of: theirDeleted) && mine.allSatisfy { h in
            h.insert.isEmpty || theirs.contains { $0.start == h.start && string($0.insert).contains(string(h.insert)) }
        }
    }
}

public enum GammaTree {
    static let inkKeys: Set<String> = ["ink_url", "ink_strokes", "pdf_position", "pdf_page", "sheet_id"]
    public static func children(_ snapshot: GammaSnapshot, of parent: String) -> [GammaBlock] {
        snapshot.values.filter { $0.parent == parent }.sorted { ($0.position, $0.id) < ($1.position, $1.id) }
    }
    public static func descendants(_ snapshot: GammaSnapshot, of id: String) -> Set<String> {
        var result = Set<String>(), todo = [id]
        while let current = todo.popLast() {
            if !result.insert(current).inserted { continue }
            todo += children(snapshot, of: current).map(\.id)
        }
        return result
    }
    public static func ordered(_ snapshot: GammaSnapshot, pageID: String) -> [GammaBlock] {
        var result: [GammaBlock] = [], todo = children(snapshot, of: pageID), seen = Set<String>()
        while !todo.isEmpty {
            let block = todo.removeFirst()
            if !seen.insert(block.id).inserted { continue }
            result.append(block); todo += children(snapshot, of: block.id)
        }
        return result
    }
    public static func nextPosition(after key: String?) -> String {
        guard let key, !key.isEmpty else { return "a0" }
        let bytes = Array(key.utf8), alphabet = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz".utf8)
        let head = bytes[0]
        let length = head >= 97 && head <= 122 ? Int(head - 97) + 2 : head >= 65 && head <= 90 ? Int(90 - head) + 2 : 0
        guard length > 0, bytes.count >= length else { return "a0" }
        var integer = Array(bytes.prefix(length))
        for index in stride(from: length - 1, through: 1, by: -1) {
            guard let digit = alphabet.firstIndex(of: integer[index]) else { return "a0" }
            if digit < alphabet.count - 1 { integer[index] = alphabet[digit + 1]; return String(decoding: integer, as: UTF8.self) }
            integer[index] = alphabet[0]
        }
        if head == 90 { return "a0" }
        if head == 122 { return key + "V" }
        let next = head + 1
        return String(decoding: [next] + Array(repeating: alphabet[0], count: next > 97 ? length : length - 2), as: UTF8.self)
    }
    public static func moved(_ before: GammaBlock, in snapshot: GammaSnapshot) -> Bool {
        guard let block = snapshot[before.id] else { return false }
        if block.parent != before.parent { return true }
        if block.position == before.position { return false }
        let siblings = children(snapshot, of: block.parent)
        guard let i = siblings.firstIndex(where: { $0.id == block.id }) else { return false }
        let lower = i > 0 ? siblings[i - 1].position : nil
        let upper = i + 1 < siblings.count ? siblings[i + 1].position : nil
        return !((lower == nil || lower! < before.position) && (upper == nil || before.position < upper!))
    }
    public static func diff(base: GammaSnapshot, target: GammaSnapshot, pageID: String) -> [GammaOperation] {
        var result: [GammaOperation] = []
        let nodes = target[pageID].map { [$0] } ?? []
        for block in nodes + ordered(target, pageID: pageID) {
            guard let was = base[block.id] else {
                if block.id != pageID { result.append(.init(op: "insert", id: block.id, parent: block.parent, position: block.position, content: block.content, props: block.properties)) }
                continue
            }
            if block.id != pageID && (block.parent != was.parent || block.position != was.position) {
                result.append(.init(op: "move", id: block.id, parent: block.parent, position: block.position))
            }
            var patch: [String: JSONValue] = [:]
            for key in Set(was.properties.keys).union(block.properties.keys) where was.properties[key] != block.properties[key] {
                patch[key] = block.properties[key] ?? .null
            }
            let content = block.content != was.content ? block.content : nil
            if content != nil || !patch.isEmpty {
                let inkChange = (was.properties["ink_url"] != nil || patch["ink_url"] != nil) && !Set(patch.keys).isDisjoint(with: inkKeys)
                let guardProps: [String: JSONValue]? = inkChange ? ["ink_url": was.properties["ink_url"] ?? .null] : nil
                result.append(.init(op: "set", id: block.id, content: content, base: content != nil ? was.content : nil, props: patch.isEmpty ? nil : patch, baseProps: guardProps))
            }
        }
        let removed = Set(base.keys).subtracting(target.keys)
        for id in removed.sorted() where id != pageID {
            if let was = base[id], !removed.contains(was.parent) { result.append(.init(op: "delete", id: id)) }
        }
        return result
    }
    public static func apply(_ ops: [GammaOperation], to snapshot: GammaSnapshot, pageID: String, strict: Bool = true) throws -> GammaSnapshot {
        var result = snapshot
        for op in ops {
            switch op.op {
            case "insert":
                if result[op.id] != nil { continue }
                guard let parent = op.parent, result[parent] != nil else { if strict { throw GammaError.missing("Insert parent is missing.") }; continue }
                var position = op.position ?? nextPosition(after: children(result, of: parent).last?.position)
                if children(result, of: parent).contains(where: { $0.position == position }) { position = nextPosition(after: children(result, of: parent).last?.position) }
                result[op.id] = GammaBlock(id: op.id, parent: parent, position: position, content: op.content ?? "", properties: op.props ?? [:])
            case "set":
                guard var block = result[op.id] else { if strict { throw GammaError.missing("The edited block is missing.") }; continue }
                let inkChange = (block.properties["ink_url"] != nil || op.props?["ink_url"] != nil) && inkKeys.contains { key in
                    guard let value = op.props?[key] else { return false }; return value != (block.properties[key] ?? .null)
                }
                if strict, inkChange, op.baseProps?["ink_url"] == nil {
                    throw GammaError.invalid("An ink replacement must identify the version it was edited from.")
                }
                for (key, expected) in op.baseProps ?? [:] {
                    let current = block.properties[key] ?? .null, desired = op.props?[key] ?? current
                    if current != expected && (key == "ink_url" ? inkChange : current != desired) { throw GammaError.propertyChanged(op.id) }
                }
                if let content = op.content {
                    block.content = op.base.map { GammaTextMerge.merge(base: $0, ours: content, theirs: block.content).text } ?? content
                    if op.props?["auto_title"] == nil { block.properties.removeValue(forKey: "auto_title") }
                }
                for (key, value) in op.props ?? [:] { if value == .null { block.properties.removeValue(forKey: key) } else { block.properties[key] = value } }
                result[op.id] = block
            case "move":
                guard var block = result[op.id], let parent = op.parent, result[parent] != nil, op.id != pageID else { if strict { throw GammaError.missing("The moved block or parent is missing.") }; continue }
                guard !descendants(result, of: op.id).contains(parent) else { throw GammaError.invalid("A block cannot be moved inside itself.") }
                block.parent = parent
                block.position = op.position ?? nextPosition(after: children(result, of: parent).last?.position)
                if children(result, of: parent).contains(where: { $0.id != block.id && $0.position == block.position }) { block.position = nextPosition(after: children(result, of: parent).last?.position) }
                result[op.id] = block
            case "delete":
                guard op.id != pageID else { throw GammaError.invalid("Delete a page through deletePage.") }
                for id in descendants(result, of: op.id) { result.removeValue(forKey: id) }
            default: throw GammaError.invalid("Unknown operation: \(op.op)")
            }
        }
        return result
    }
}

public struct GammaReconciliation: Sendable {
    public var snapshot: GammaSnapshot
    public var conflicts: [GammaConflict]
}

public enum GammaReconciler {
    /// Reconcile snapshots, preserving the remote as authority and only carrying edits
    /// made relative to base. This is used again over a fresh local snapshot after awaits.
    public static func reconcile(pageID: String, base: GammaSnapshot, local: GammaSnapshot, remote: GammaSnapshot) -> GammaReconciliation {
        var result = remote, conflicts: [GammaConflict] = []
        let all = Set(base.keys).union(local.keys).union(remote.keys)
        var rescued = Set<String>()
        // Protect deleted subtrees when a surviving descendant was edited or moved.
        for id in all where base[id] != nil {
            if remote[id] == nil, local[id] != nil {
                let scope = GammaTree.descendants(local, of: id)
                if scope.contains(where: { local[$0] != base[$0] }) { rescued.formUnion(scope) }
            }
        }
        for id in all.sorted() {
            let was = base[id], here = local[id], there = remote[id]
            guard let here else {
                if let was, there != nil {
                    let scope = GammaTree.descendants(remote, of: id)
                    let touched = scope.contains { remote[$0] != base[$0] }
                    if !touched {
                        for child in scope { result.removeValue(forKey: child) }
                    } else if id == pageID || was.parent == pageID || local[was.parent] != nil {
                        conflicts.append(.init(pageID: pageID, blockID: id, kind: "restored_remote_edit", theirs: there?.content ?? ""))
                    }
                }
                continue
            }
            guard let there else {
                if was == nil || here != was || rescued.contains(id) {
                    result[id] = here
                    if was != nil { conflicts.append(.init(pageID: pageID, blockID: id, kind: "kept_local_edit", mine: here.content)) }
                }
                continue
            }
            var merged = there
            if let was {
                if here.content != was.content {
                    if GammaTextMerge.contains(base: was.content, change: here.content, current: there.content) { merged.content = there.content }
                    else {
                        let value = GammaTextMerge.merge(base: was.content, ours: here.content, theirs: there.content)
                        merged.content = value.text
                        if there.content != was.content && here.content != there.content { conflicts.append(.init(pageID: pageID, blockID: id, kind: "merged", base: was.content, mine: here.content, theirs: there.content, result: value.text)) }
                    }
                }
                if GammaTree.moved(was, in: local) && !GammaTree.moved(was, in: remote) { merged.parent = here.parent; merged.position = here.position }
            } else if here.content != there.content {
                merged.content = here.content
                conflicts.append(.init(pageID: pageID, blockID: id, kind: "diverged", mine: here.content, theirs: there.content, result: here.content))
            }
            let oldProps = was?.properties ?? [:]
            let inkClash = here.properties["ink_url"] != oldProps["ink_url"] && there.properties["ink_url"] != oldProps["ink_url"] && here.properties["ink_url"] != there.properties["ink_url"]
            if inkClash, here.properties["ink_url"]?.string != nil {
                var variant = here
                // Stable derived id makes replay of a pull interrupted before bookkeeping idempotent.
                variant.id = GammaAssets.digest(Data("conflict:\(id):\(here.properties["ink_url"]?.string ?? ""):\(there.properties["ink_url"]?.string ?? "")".utf8))
                variant.properties["ink_conflict"] = .object(["source_block_id": .string(id), "base_url": oldProps["ink_url"] ?? .null, "remote_url": there.properties["ink_url"] ?? .null])
                variant.position = GammaTree.nextPosition(after: GammaTree.children(result, of: variant.parent).last?.position)
                if result[variant.id] == nil { result[variant.id] = variant }
                conflicts.append(.init(pageID: pageID, blockID: variant.id, kind: "ink", base: oldProps["ink_url"]?.string ?? "", mine: here.properties["ink_url"]?.string ?? "", theirs: there.properties["ink_url"]?.string ?? ""))
            }
            for key in Set(oldProps.keys).union(here.properties.keys) where here.properties[key] != oldProps[key] {
                if inkClash && GammaTree.inkKeys.contains(key) { continue }
                if was == nil || there.properties[key] == oldProps[key] || there.properties[key] == here.properties[key] {
                    merged.properties[key] = here.properties[key]
                }
            }
            result[id] = merged
        }
        // A moved child out of a deleted subtree survives. Restore an ancestor only when
        // a locally rescued child still needs it; otherwise park an orphan at the page.
        for id in result.keys.sorted() where id != pageID {
            guard var block = result[id], result[block.parent] == nil else { continue }
            var cursor = block.parent, visited = Set<String>()
            while cursor != pageID, result[cursor] == nil, visited.insert(cursor).inserted, let ancestor = local[cursor] {
                result[cursor] = ancestor; cursor = ancestor.parent
            }
            if result[block.parent] == nil { block.parent = pageID; result[id] = block }
        }
        return GammaReconciliation(snapshot: result, conflicts: conflicts)
    }
}

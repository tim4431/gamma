import Combine
import QuartzCore
import SwiftUI

/// Where a group of strokes is drawn: a PDF page (1-based) or a sheet (a
/// notebook's page, or one among a note's blocks).
enum InkKey: Hashable {
    case pdf(Int)
    case sheet(String)
}

/// One handwriting group as shown and edited here: its `gamma-ink` file
/// (JSON, frontend/src/ink/ink.js's shape), the file its strokes were drawn
/// onto (the save's base, so a drawing changed meanwhile merges by stroke),
/// and whether it has strokes not saved yet.
struct InkGroup {
    let id: String
    var key: InkKey
    var ink: [String: Any]
    var base: String
    var dirty = false
    var geometry: [[String: Any]] = []
}

enum InkTool: Equatable {
    case preset(Int)
    case eraser
    case hand
}

/// A group to scroll to (from the notes); the nonce makes the same one work twice.
struct JumpRequest: Equatable {
    let id: String
    let nonce: Int
}

/// The handwriting of one open page — the browser's ink state (app/App.jsx
/// "Handwriting") on the iPad: the groups by page or sheet, the group the
/// next stroke joins, the stroke history, the save. Every rule is the web
/// app's own JavaScript through the replica's edit core: strokes are encoded
/// by ink.js encodeStroke, undo and redo rebase by stroke id (mergeInk), a
/// save is replica/edits.js saveInk — the same file bytes, the same block
/// properties, the same base as a browser's save.
@MainActor
final class InkSession: ObservableObject {
    @Published var tool: InkTool = .preset(0)
    @Published private(set) var presets: [[String: Any]] = []
    @Published private(set) var canUndo = false
    @Published private(set) var canRedo = false
    /// Bumps whenever a group's strokes change: the readers redraw.
    @Published private(set) var changed = 0
    @Published private(set) var jumpTarget: JumpRequest?
    /// The page's sheets of paper, in document order: [{id, number, paper, ink}].
    @Published private(set) var sheets: [[String: Any]] = []
    /// The group whose writing is replaying, and a signal per frame of it
    /// (not published: the SwiftUI views need not redraw 60 times a second)
    /// on which the reader redraws the page the group is on (replayKey).
    @Published private(set) var replaying: String?
    let replayFrames = PassthroughSubject<Void, Never>()
    private(set) var replayKey: InkKey?
    private var replayFrame: [[String: Any]]?
    private var replayTask: Task<Void, Never>?

    private(set) var replica: Replica?
    private(set) var pageId = ""
    private var onSaved: () -> Void = {}
    private(set) var groups: [String: InkGroup] = [:]
    private var order: [String] = []
    private var active: (key: InkKey, id: String)?
    private var history: [[Change]] = []
    private var future: [[Change]] = []
    private var saveTask: Task<Void, Never>?
    private static let toolsKey = "gamma.inkTools"

    struct Change {
        let id: String
        let key: InkKey
        let before: [String: Any]
        let after: [String: Any]
    }

    // --- the page ---------------------------------------------------------------------

    func attach(replica: Replica, pageId: String, onSaved: @escaping () -> Void) {
        self.replica = replica
        self.pageId = pageId
        self.onSaved = onSaved
        let stored = UserDefaults.standard.string(forKey: Self.toolsKey).flatMap { try? JSON.parse($0) }
        presets = ((try? replica.pure("normalizeTools", [stored ?? NSNull()])) as? [[String: Any]]) ?? []
    }

    /// The page as the store has it now (pageView): groups whose file changed
    /// elsewhere reload unless they hold strokes not saved here (those merge
    /// when saved); groups gone elsewhere go unless edited here.
    func load(_ view: [String: Any]) {
        guard let replica else { return }
        var found: [(String, InkKey, String)] = []
        for (page, list) in view.dict("pdfInk") {
            guard let n = Int(page) else { continue }
            for g in list as? [[String: Any]] ?? [] { found.append((g.string("id"), .pdf(n), g.string("url"))) }
        }
        sheets = view.array("sheets").compactMap { $0 as? [String: Any] }
        for sheet in sheets {
            for g in sheet.array("ink").compactMap({ $0 as? [String: Any] }) {
                found.append((g.string("id"), .sheet(sheet.string("id")), g.string("url")))
            }
        }
        var next: [String: InkGroup] = [:]
        for (id, key, url) in found {
            if var mine = groups[id], mine.dirty || mine.base == url {
                mine.key = key
                next[id] = mine
                continue
            }
            guard let ink = replica.inkFile(url) else { continue }
            next[id] = InkGroup(id: id, key: key, ink: ink, base: url, dirty: false, geometry: geometry(ink))
        }
        for (id, g) in groups where next[id] == nil && g.dirty { next[id] = g }
        let replayed = replaying.flatMap { groups[$0]?.base }
        groups = next
        order = found.map { $0.0 } + next.keys.filter { id in !found.contains { $0.0 == id } }
        // a drawing that changed under its replay ends the replay
        if let id = replaying, groups[id]?.base != replayed { stopReplay() }
        changed += 1
    }

    /// The groups drawn on `key` — the replaying one as it stood at that
    /// moment of its replay.
    func groupList(on key: InkKey) -> [InkGroup] {
        order.compactMap { groups[$0] }.filter { $0.key == key }.map { (g: InkGroup) -> InkGroup in
            guard g.id == replaying, let frame = replayFrame else { return g }
            var shown = g
            shown.geometry = frame
            return shown
        }
    }

    private func geometry(_ ink: [String: Any]) -> [[String: Any]] {
        ((try? replica?.pure("geometry", [ink])) as? [[String: Any]]) ?? []
    }

    // --- the tools --------------------------------------------------------------------

    var style: [String: Any]? {
        guard case .preset(let i) = tool, i < presets.count else { return nil }
        return (try? replica?.pure("toolStyle", [presets[i]])) as? [String: Any]
    }

    var drawing: Bool {
        if case .preset = tool { return true }
        return false
    }

    /// The next stroke starts a new group.
    func newGroup() { active = nil }

    /// A preset's colour or width changed; the row is kept like the browser's
    /// (per device: ink.js normalizeTools validates it on the way back).
    func setPreset(_ index: Int, color: String? = nil, size: Double? = nil) {
        guard index < presets.count else { return }
        if let color { presets[index]["color"] = color }
        if let size { presets[index]["size"] = size }
        if let text = try? JSON.string(presets) { UserDefaults.standard.set(text, forKey: Self.toolsKey) }
    }

    // --- editing ------------------------------------------------------------------------

    /// A stroke drawn on `key`: `samples` [{x, y, p, t}] in the page's frame
    /// (points, top-left, y down), t in ms since the first; `t0` wall-clock
    /// ms; `size` the page's size in that frame.
    func addStroke(_ samples: [[String: Double]], t0: Double, key: InkKey, size: CGSize) {
        guard let replica, let style, samples.count > 0 else { return }
        var args = style
        args["pen"] = true
        args["t0"] = Int(t0.rounded())
        args["ch"] = "xypt"
        args["samples"] = samples
        guard let stroke = (try? replica.pure("encodeStroke", [args])) as? [String: Any] else { return }
        var id = active?.key == key ? active?.id : nil
        if let current = id, groups[current] == nil { id = nil }
        let groupId = id ?? ((try? replica.pure("makeId", [])) as? String ?? UUID().uuidString)
        active = (key, groupId)
        let before: [String: Any]
        if let g = groups[groupId] {
            before = g.ink
        } else {
            switch key {
            case .pdf(let page):
                before = ((try? replica.pure("newInk", [page, Double(size.width), Double(size.height)])) as? [String: Any]) ?? [:]
            case .sheet:
                before = ((try? replica.pure("newCanvasInk", [Double(size.width), Double(size.height)])) as? [String: Any]) ?? [:]
            }
        }
        guard let after = (try? replica.pure("appendStroke", [before, stroke])) as? [String: Any] else { return }
        apply([Change(id: groupId, key: key, before: before, after: after)], record: true)
        if case .sheet(let sheetId) = key, let last = sheets.last, last.string("id") == sheetId,
           let box = (try? replica.pure("strokeBounds", [stroke])) as? [Double], box.count == 4,
           box[3] > (last.dict("paper").double("height") ?? .infinity) * 0.75 {
            // writing into the last quarter of the last sheet adds the next one right after it
            // (Notability's continuous page), once: a page after it already there is the one
            addSheet(after: sheetId, once: true)
        }
    }

    /// The whole-stroke eraser at `point` (the page's frame) with `radius`.
    func erase(at point: CGPoint, radius: Double, key: InkKey) {
        guard let replica else { return }
        var changes: [Change] = []
        for g in groupList(on: key) {
            guard let ids = (try? replica.pure("hitStrokes", [g.ink, Double(point.x), Double(point.y), radius])) as? [String], !ids.isEmpty,
                  let after = (try? replica.pure("removeStrokes", [g.ink, ids])) as? [String: Any] else { continue }
            changes.append(Change(id: g.id, key: key, before: g.ink, after: after))
        }
        if !changes.isEmpty { apply(changes, record: true) }
    }

    func undo() { step(back: true) }
    func redo() { step(back: false) }

    /// An entry of the history applied onto the groups as they are now, by
    /// stroke id (ink.js mergeInk): strokes that arrived meanwhile stay.
    private func step(back: Bool) {
        guard let replica, let entry = back ? history.popLast() : future.popLast() else { return }
        let changes = entry.map { c -> Change in
            let (from, to) = back ? (c.after, c.before) : (c.before, c.after)
            let now = groups[c.id]?.ink ?? from
            let merged = ((try? replica.pure("mergeInk", [from, to, now])) as? [String: Any])?.dict("ink") ?? to
            return Change(id: c.id, key: c.key, before: now, after: merged)
        }
        apply(changes, record: false)
        if back { future.append(entry) } else { history.append(entry) }
        canUndo = !history.isEmpty
        canRedo = !future.isEmpty
    }

    private func apply(_ changes: [Change], record: Bool) {
        if let id = replaying, changes.contains(where: { $0.id == id }) { stopReplay() }
        for c in changes {
            let base = groups[c.id]?.base ?? ""
            groups[c.id] = InkGroup(id: c.id, key: c.key, ink: c.after, base: base, dirty: true, geometry: geometry(c.after))
            if !order.contains(c.id) { order.append(c.id) }
        }
        if record {
            history.append(changes)
            if history.count > 200 { history.removeFirst() }
            future = []
        }
        canUndo = !history.isEmpty
        canRedo = !future.isEmpty
        changed += 1
        scheduleSave()
    }

    // --- saving ---------------------------------------------------------------------------

    private func scheduleSave() {
        saveTask?.cancel()
        saveTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 700_000_000)
            if !Task.isCancelled { self?.flush() }
        }
    }

    /// Every group with strokes not saved: stored through replica/edits.js
    /// saveInk (a new group is inserted — under its sheet in a notebook; a
    /// group erased empty goes unless it holds a caption or notes).
    func flush() {
        saveTask?.cancel()
        guard let replica else { return }
        var saved = false
        for (id, g) in groups where g.dirty {
            var args: [String: Any] = ["blockId": id, "ink": g.ink, "baseUrl": g.base]
            if case .sheet(let sheet) = g.key { args["parent"] = sheet }
            do {
                let url = try replica.edit("saveInk", [pageId, args]) as? String ?? ""
                saved = true
                if url.isEmpty {
                    groups[id] = nil
                    continue
                }
                var next = g
                next.base = url
                next.dirty = false
                if let stored = replica.inkFile(url) {
                    next.ink = stored  // the merge of someone else's strokes, when there was one
                    next.geometry = geometry(stored)
                }
                groups[id] = next
            } catch {
                NSLog("[ink] not saved yet: %@", error.localizedDescription)
            }
        }
        if saved {
            changed += 1
            onSaved()
        }
    }

    // --- sheets ----------------------------------------------------------------------------

    /// A page right after `after`, else after the page's last sheet (at its
    /// end when it has none): replica/edits.js addSheet, the browser's rules.
    /// `once`: none when the page after it is there already.
    @discardableResult
    func addSheet(after: String? = nil, once: Bool = false) -> String? {
        guard let replica else { return nil }
        var opts: [String: Any] = [:]
        if let after { opts["after"] = after }
        if once { opts["once"] = true }
        let id = try? replica.edit("addSheet", [pageId, opts]) as? String
        onSaved()
        return id
    }

    func setPaper(_ paper: [String: Any], sheet: String) {
        guard let replica else { return }
        _ = try? replica.edit("setSheetPaper", [pageId, sheet, paper])
        onSaved()
    }

    // --- replay ---------------------------------------------------------------------------

    /// A group's writing, replayed on its page in the order and at the pace
    /// it was written, pauses shortened (ink.js inkTimeline, the browser's
    /// replay): the reader scrolls to it and draws it stroke by stroke. The
    /// same group again while it plays stops it.
    func replay(_ id: String) {
        if replaying == id { stopReplay(); return }
        stopReplay()
        guard let replica, let g = groups[id],
              let timeline = (try? replica.pure("inkTimeline", [g.ink])) as? [String: Any] else { return }
        let items = timeline.array("strokes").compactMap { $0 as? [String: Any] }
        let duration = timeline.double("duration") ?? 0
        guard !items.isEmpty else { return }
        var whole: [String: [String: Any]] = [:]
        for item in g.geometry { whole[item.string("id")] = item }
        let strokes = g.ink.array("strokes").compactMap { $0 as? [String: Any] }
        jump(to: id)
        replaying = id
        replayKey = g.key
        replayFrame = []
        let start = CACurrentMediaTime()
        replayTask = Task { @MainActor [weak self] in
            while let session = self, !Task.isCancelled {
                let t = (CACurrentMediaTime() - start) * 1000
                if t >= duration { session.stopReplay(); return }
                session.replayFrame = session.frame(items, strokes, whole, at: t)
                session.replayFrames.send()
                try? await Task.sleep(nanoseconds: 16_000_000)
            }
        }
    }

    func stopReplay() {
        replayTask?.cancel()
        replayTask = nil
        guard replaying != nil else { return }
        replaying = nil
        replayKey = nil
        replayFrame = nil
        changed += 1
    }

    /// The group `t` ms into its replay: the strokes begun by then, whole, or
    /// (the one being written) cut to the samples it had — a prefix of its
    /// delta-coded pts is its first samples (ink.js inkAtTime).
    private func frame(_ items: [[String: Any]], _ strokes: [[String: Any]], _ whole: [String: [String: Any]],
                       at t: Double) -> [[String: Any]] {
        var out: [[String: Any]] = []
        for item in items {
            guard let start = item.double("start"), start <= t else { break }
            guard let index = item.int("index"), index < strokes.count else { continue }
            if t >= (item.double("end") ?? 0), let done = whole[item.string("id")] {
                out.append(done)
                continue
            }
            let times = item.array("times").compactMap { ($0 as? NSNumber)?.doubleValue }
            var k = 1
            while k < times.count && times[k] <= t { k += 1 }
            var stroke = strokes[index]
            let channels = stroke.string("ch").isEmpty ? 2 : stroke.string("ch").count
            stroke["pts"] = Array(stroke.array("pts").prefix(k * channels))
            if let part = (try? replica?.pure("strokeGeometry", [stroke])) as? [String: Any] { out.append(part) }
        }
        return out
    }

    // --- the notes' jump --------------------------------------------------------------------

    func jump(to blockId: String) {
        jumpTarget = JumpRequest(id: blockId, nonce: (jumpTarget?.nonce ?? 0) + 1)
    }

    func key(of blockId: String) -> InkKey? { groups[blockId]?.key }
}

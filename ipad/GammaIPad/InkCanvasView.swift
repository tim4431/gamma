import UIKit

struct InkGroup {
    var id: String
    var parentID: String
    var ink: [String: Any]
}

/// Pencil input is native; every committed stroke and edit uses Gamma's codec.
/// Fingers are left to the enclosing PDFView/scroll view.
@MainActor
final class InkCanvasView: UIView, UIPencilInteractionDelegate {
    enum Tool: String, CaseIterable { case pen = "Pen", monoline = "Monoline", highlighter = "Highlighter", eraser = "Eraser", partial = "Partial", select = "Select" }
    let engine: InkEngine
    var groups: [InkGroup] = [] { didSet { setNeedsDisplay() } }
    var pageSize = CGSize(width: 612, height: 792)
    var makeGroup: (() -> InkGroup)?
    var onChange: (([InkGroup]) -> Void)?
    var onStroke: ((String, [String: Any], Double, Double) -> Void)?
    var onError: ((Error) -> Void)?
    var tool: Tool = .pen
    var inkColor = "#1f1f1f"
    var inkSize: Double = 2
    var isReadOnly = false
    var activeGroupID: String?
    var playback: (([String: Any], String) -> [String: Any])? { didSet { setNeedsDisplay() } }
    private var samples: [[String: Double]] = []
    private var predicted: [[String: Double]] = []
    private var estimated: [NSNumber: Int] = [:]
    private var completedEstimates: [NSNumber: (group: String, stroke: String, index: Int)] = [:]
    private var strokeStart: TimeInterval = 0
    private var wallStart = 0.0
    private var activeStrokeID = ""
    private var polygon: [[Double]] = []
    private var selected: [String: [String]] = [:]
    private var gestureStart = CGPoint.zero
    private var moving = false
    private var before: [InkGroup] = []
    private var undoStack: [[InkGroup]] = []
    private var redoStack: [[InkGroup]] = []
    private var selectionRect: CGRect?
    private weak var activeTouch: UITouch?
    private var predictionGeneration = 0
    var isEditing: Bool { activeTouch != nil }

    init(engine: InkEngine) {
        self.engine = engine
        super.init(frame: .zero)
        isOpaque = false; backgroundColor = .clear; isMultipleTouchEnabled = true
        let interaction = UIPencilInteraction(); interaction.delegate = self; addInteraction(interaction)
        accessibilityLabel = "Handwriting canvas"
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        if isReadOnly || playback != nil { return nil }
        if activeTouch != nil { return self }
        // A finger may move a lasso selection, otherwise PDFKit owns it.
        if let touch = event?.allTouches?.first, touch.type != .pencil,
           !(tool == .select && selectionRect?.contains(pagePoint(point)) == true) { return nil }
        return super.hitTest(point, with: event)
    }
    private func pagePoint(_ point: CGPoint) -> CGPoint {
        CGPoint(x: point.x * pageSize.width / max(1, bounds.width), y: point.y * pageSize.height / max(1, bounds.height))
    }
    private func sample(_ touch: UITouch) -> [String: Double] {
        let point = pagePoint(touch.preciseLocation(in: self))
        let pressure = touch.type == .pencil && touch.maximumPossibleForce > 0 ? touch.force / touch.maximumPossibleForce : 0.5
        return ["x": point.x, "y": point.y, "p": min(1, max(0, pressure)),
                "t": max(0, (touch.timestamp - strokeStart) * 1000),
                "a": Double(touch.altitudeAngle) * 180 / Double.pi, "z": Double(touch.azimuthAngle(in: self)) * 180 / Double.pi]
    }
    private func take(_ touch: UITouch, event: UIEvent?) {
        for point in event?.coalescedTouches(for: touch) ?? [touch] {
            let next = sample(point)
            if let last = samples.last, next["t"]! < last["t"]! { continue }
            if let last = samples.last, next == last { continue }
            if let index = point.estimationUpdateIndex, !point.estimatedPropertiesExpectingUpdates.isEmpty { estimated[index] = samples.count }
            samples.append(next)
        }
        predicted = (event?.predictedTouches(for: touch) ?? []).filter { $0.timestamp - touch.timestamp <= 0.016 }.map(sample)
        predictionGeneration += 1
        let generation = predictionGeneration
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.032) { [weak self] in
            guard let self, self.predictionGeneration == generation else { return }
            self.predicted = []; self.setNeedsDisplay()
        }
    }
    private func encoded(_ points: [[String: Double]]) throws -> [String: Any] {
        var parameters: [String: Any] = ["id": activeStrokeID, "tool": tool == .highlighter ? "highlighter" : "pen",
            "color": inkColor, "size": tool == .highlighter ? max(8, inkSize * 5) : inkSize,
            "opacity": tool == .highlighter ? 0.6 : 1, "pen": true, "t0": wallStart, "ch": "xyptaz", "samples": points]
        if tool == .monoline { parameters["brush"] = "monoline" }
        return try engine.object("encodeStroke", [parameters])
    }
    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }
        context.scaleBy(x: bounds.width / pageSize.width, y: bounds.height / pageSize.height)
        do {
            for group in groups {
                let ink = playback?(group.ink, group.id) ?? group.ink
                for stroke in ink["strokes"] as? [[String: Any]] ?? [] { try engine.draw(stroke, in: context) }
            }
            if !samples.isEmpty { try engine.draw(encoded(samples + predicted), in: context) }
        } catch { onError?(error) }
        context.setStrokeColor(UIColor.systemBlue.cgColor); context.setLineWidth(1.5 * pageSize.width / max(1, bounds.width))
        context.setLineDash(phase: 0, lengths: [4, 3])
        if let first = polygon.first {
            context.move(to: CGPoint(x: first[0], y: first[1]))
            for point in polygon.dropFirst() { context.addLine(to: CGPoint(x: point[0], y: point[1])) }
            context.strokePath()
        }
        if let selectionRect { context.stroke(selectionRect.insetBy(dx: -3, dy: -3)) }
    }
    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard !isReadOnly, let touch = touches.first, samples.isEmpty else { return }
        guard activeTouch == nil else { return }
        activeTouch = touch
        before = groups; gestureStart = pagePoint(touch.preciseLocation(in: self)); predicted = []
        if tool == .select {
            moving = selectionRect?.contains(gestureStart) == true
            if !moving { selected = [:]; selectionRect = nil; polygon = [[gestureStart.x, gestureStart.y]] }
        } else if tool == .eraser || tool == .partial { erase(gestureStart) }
        else if touch.type == .pencil {
            selected = [:]; selectionRect = nil; strokeStart = touch.timestamp
            wallStart = Date().timeIntervalSince1970 * 1000
            activeStrokeID = UUID().uuidString.replacingOccurrences(of: "-", with: "")
            samples = []; estimated = [:]; take(touch, event: event)
        }
        setNeedsDisplay()
    }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = activeTouch, touches.contains(touch) else { return }
        let point = pagePoint(touch.preciseLocation(in: self))
        do {
            if tool == .select {
                if moving {
                    groups = try before.map { group in
                        var next = group
                        next.ink = try engine.object("translateStrokes", [group.ink, selected[group.id] ?? [], point.x - gestureStart.x, point.y - gestureStart.y])
                        return next
                    }; updateSelection()
                } else { polygon.append([point.x, point.y]) }
            } else if tool == .eraser || tool == .partial {
                for item in event?.coalescedTouches(for: touch) ?? [touch] { erase(pagePoint(item.preciseLocation(in: self))) }
            } else if !samples.isEmpty { take(touch, event: event) }
        } catch { onError?(error) }
        setNeedsDisplay()
    }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = activeTouch, touches.contains(touch) else { return }
        do {
            if !samples.isEmpty {
                take(touch, event: event); predicted = []
                // Pencil-up often reports zero force; retain the final contact pressure.
                if samples.count > 1 { samples[samples.count - 1]["p"] = samples[samples.count - 2]["p"] }
                let stroke = try encoded(samples)
                var index = groups.firstIndex(where: { $0.id == activeGroupID })
                if index == nil, let group = makeGroup?() { groups.append(group); activeGroupID = group.id; index = groups.count - 1 }
                if let index {
                    guard (groups[index].ink["strokes"] as? [Any] ?? []).count < 5000 else { throw InkEngineError.failure("Start a new ink group.") }
                    groups[index].ink = try engine.object("appendStroke", [groups[index].ink, stroke])
                    _ = try engine.data(groups[index].ink)
                    for (key, value) in estimated { completedEstimates[key] = (groups[index].id, activeStrokeID, value) }
                    onStroke?(groups[index].id, stroke, strokeStart, touch.timestamp)
                    commit()
                }
            } else if tool == .select && !moving {
                for group in groups { selected[group.id] = try engine.call("strokesInLasso", [group.ink, polygon]) as? [String] ?? [] }
                updateSelection()
            } else { commit() }
        } catch { groups = before; onError?(error) }
        samples = []; predicted = []; estimated = [:]; polygon = []; moving = false; setNeedsDisplay()
        activeTouch = nil
    }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = activeTouch, touches.contains(touch) else { return }
        groups = before; samples = []; predicted = []; estimated = [:]; polygon = []; moving = false; activeTouch = nil; setNeedsDisplay()
    }
    override func touchesEstimatedPropertiesUpdated(_ touches: Set<UITouch>) {
        var changed = false
        for touch in touches {
            guard let key = touch.estimationUpdateIndex else { continue }
            if let index = estimated[key], index < samples.count {
                let updated = sample(touch)
                for channel in ["p", "a", "z"] { samples[index][channel] = updated[channel] }
            } else if let location = completedEstimates[key], let group = groups.firstIndex(where: { $0.id == location.group }),
                      var strokes = groups[group].ink["strokes"] as? [[String: Any]], let index = strokes.firstIndex(where: { $0["id"] as? String == location.stroke }),
                      var points = strokes[index]["pts"] as? [Int], location.index * 6 + 5 < points.count {
                let updated = sample(touch), offset = location.index * 6
                points[offset + 2] = Int(((updated["p"] ?? 0.5) * 1000).rounded())
                points[offset + 4] = Int((updated["a"] ?? 90).rounded()); points[offset + 5] = Int((updated["z"] ?? 0).rounded())
                strokes[index]["pts"] = points; groups[group].ink["strokes"] = strokes; changed = true
            }
            if touch.estimatedPropertiesExpectingUpdates.isEmpty { completedEstimates.removeValue(forKey: key) }
        }
        if changed { onChange?(groups) }; setNeedsDisplay()
    }
    private func erase(_ point: CGPoint) {
        do {
            for index in groups.indices {
                let ink = groups[index].ink, radius = 8 * pageSize.width / max(1, bounds.width)
                if tool == .partial { groups[index].ink = try engine.object("eraseAt", [ink, point.x, point.y, radius])["ink"] as? [String: Any] ?? ink }
                else {
                    let ids = try engine.call("hitStrokes", [ink, point.x, point.y, radius])
                    groups[index].ink = try engine.object("removeStrokes", [ink, ids])
                }
            }
        } catch { onError?(error) }
    }
    private func commit() {
        if NSDictionary(dictionary: snapshot(before)).isEqual(to: snapshot(groups)) { return }
        undoStack.append(before); if undoStack.count > 100 { undoStack.removeFirst() }; redoStack = []
        onChange?(groups)
    }
    private func snapshot(_ groups: [InkGroup]) -> [String: Any] { Dictionary(uniqueKeysWithValues: groups.map { ($0.id, $0.ink) }) }
    func undoInk() { guard playback == nil, !isReadOnly else { return }; if let prior = undoStack.popLast() { redoStack.append(groups); groups = prior; selected = [:]; selectionRect = nil; onChange?(groups) } }
    func redoInk() { guard playback == nil, !isReadOnly else { return }; if let next = redoStack.popLast() { undoStack.append(groups); groups = next; onChange?(groups) } }
    func newGroup() { guard playback == nil, !isReadOnly else { return }; activeGroupID = nil }
    func replaceRemoteGroups(_ groups: [InkGroup]) {
        self.groups = groups; undoStack = []; redoStack = []; selected = [:]; selectionRect = nil
        if !groups.contains(where: { $0.id == activeGroupID }) { activeGroupID = nil }
    }
    func editSelection(_ action: String, value: Any? = nil) {
        guard playback == nil, !isReadOnly else { return }
        before = groups
        do {
            for index in groups.indices {
                let ids = selected[groups[index].id] ?? []
                guard !ids.isEmpty else { continue }
                let args: [Any]
                switch action {
                case "removeStrokes": args = [groups[index].ink, ids]
                case "duplicateStrokes": args = [groups[index].ink, ids, 12, 12]
                case "restyleStrokes": args = [groups[index].ink, ids, value ?? [:]]
                default:
                    guard let rect = selectionRect else { continue }
                    args = [groups[index].ink, ids, ["cx": rect.midX, "cy": rect.midY, "scale": action == "grow" ? 1.1 : action == "shrink" ? 1 / 1.1 : 1, "angle": action == "rotate" ? Double.pi / 12 : 0]]
                }
                groups[index].ink = try engine.object(["grow", "shrink", "rotate"].contains(action) ? "transformStrokes" : action, args)
            }
            commit(); updateSelection()
        } catch { groups = before; onError?(error) }
    }
    private func updateSelection() {
        var rect = CGRect.null
        for group in groups {
            if let box = try? engine.call("boundsOf", [group.ink, selected[group.id] ?? []]) as? [Double], box.count == 4 {
                rect = rect.union(CGRect(x: box[0], y: box[1], width: box[2] - box[0], height: box[3] - box[1]))
            }
        }
        selectionRect = rect.isNull ? nil : rect; setNeedsDisplay()
    }
    func pencilInteractionDidTap(_ interaction: UIPencilInteraction) { tool = tool == .eraser ? .pen : .eraser }
}

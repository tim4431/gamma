import UIKit

/// The feedback each `haptic` kind plays on iPadOS 17.5 and later: Apple's
/// for its event ("Playing haptic feedback in your app"; WWDC24 "Squeeze
/// the most out of Apple Pencil"). Pure, so it is tested.
enum HapticFeel {
    /// A held stroke snapped to a shape: the canvas generator's
    /// pathCompleted(at:), which Apple gives to shape recognition (its
    /// alignmentOccurred(at:) is for a snap to a guide or a ruler). It
    /// plays only at a place.
    case pathCompleted
    /// A pen lasso selected strokes: selectionChanged(at:).
    case selectionChanged
    /// A Pencil gesture switched the tool, for which Apple names no
    /// feedback, and a snap that came without a place: a light impact.
    case lightImpact

    init(_ kind: HapticKind, placed: Bool) {
        switch kind {
        case .snap: self = placed ? .pathCompleted : .lightImpact
        case .select: self = .selectionChanged
        case .tool: self = .lightImpact
        }
    }
}

/// The web's `haptic` (Bridge), sent once per event, so nothing is limited
/// here, and never with *Haptics* off (App's hostHaptic checks it). On
/// iPadOS 17.5 and later each kind plays its own feedback (HapticFeel) from
/// a generator made for the web view, and an Apple Pencil Pro plays it in
/// the Pencil at `at` (the web view's points): where the page says, or for
/// a gesture's tick where the Pencil hovered for the gesture
/// (ShellController, PencilGestures). Without a place it is the generator's
/// plain call, and before 17.5 every kind is the plain light impact. An
/// iPad itself has no motor: only an Apple Pencil Pro plays these.
@MainActor
enum PencilHaptics {
    /// The view the generators were made for, and one generator per feel:
    /// one made for a view on iPadOS 17.5 is attached to it, so one per
    /// tick would pile up.
    private static var attachedTo: ObjectIdentifier?
    private static var generators: [HapticFeel: UIFeedbackGenerator] = [:]

    static func play(_ kind: HapticKind, at point: CGPoint?, in view: UIView) {
        guard #available(iOS 17.5, *) else {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            return
        }
        switch HapticFeel(kind, placed: point != nil) {
        case .pathCompleted:
            let canvas = generator(.pathCompleted, in: view) { UICanvasFeedbackGenerator(view: view) }
            if let point { canvas.pathCompleted(at: point) }
        case .selectionChanged:
            let selection = generator(.selectionChanged, in: view) { UISelectionFeedbackGenerator(view: view) }
            if let point { selection.selectionChanged(at: point) } else { selection.selectionChanged() }
        case .lightImpact:
            let impact = generator(.lightImpact, in: view) { UIImpactFeedbackGenerator(style: .light, view: view) }
            if let point { impact.impactOccurred(at: point) } else { impact.impactOccurred() }
        }
    }

    /// The generator kept for `feel` in `view`, made by `make` the first time.
    private static func generator<G: UIFeedbackGenerator>(_ feel: HapticFeel, in view: UIView, make: () -> G) -> G {
        if attachedTo != ObjectIdentifier(view) {
            attachedTo = ObjectIdentifier(view)
            generators = [:]
        }
        if let kept = generators[feel] as? G { return kept }
        let made = make()
        generators[feel] = made
        return made
    }
}

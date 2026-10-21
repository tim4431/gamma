import UIKit

/// The web's `haptic` (Bridge), played as a light impact: a held stroke
/// snapped to a shape, a pen lasso selected strokes, a Pencil gesture
/// switched the tool. The page sends one per event, so nothing is limited
/// here. On iPadOS 17.5 and later the generator belongs to the web view,
/// and an Apple Pencil Pro plays the impact in the Pencil, at `at` (the web
/// view's points): where the page says, or for a gesture's tick where the
/// Pencil hovered for the gesture (ShellController, PencilGestures); before
/// 17.5 it is the plain light impact, which an iPad has no motor to play.
@MainActor
enum PencilHaptics {
    /// The generator made for a view, kept: one made for a view on iPadOS
    /// 17.5 is attached to it, so one per tick would pile up.
    private static var attached: (view: ObjectIdentifier, generator: UIImpactFeedbackGenerator)?

    /// Every kind is the same light tick for now: `kind` names the event.
    static func play(_ kind: HapticKind, at point: CGPoint?, in view: UIView) {
        if #available(iOS 17.5, *) {
            let generator: UIImpactFeedbackGenerator
            if let kept = Self.attached, kept.view == ObjectIdentifier(view) {
                generator = kept.generator
            } else {
                generator = UIImpactFeedbackGenerator(style: .light, view: view)
                Self.attached = (view: ObjectIdentifier(view), generator: generator)
            }
            if let point {
                generator.impactOccurred(at: point)
            } else {
                generator.impactOccurred()
            }
        } else {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }
}

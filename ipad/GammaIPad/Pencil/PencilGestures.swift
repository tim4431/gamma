import UIKit

/// The Pencil's own gestures: the double tap, and the squeeze of an Apple
/// Pencil Pro (iPadOS 17.5). Safari never gets these, so they are the one
/// thing the shell adds to the web app's pen. They are forwarded with the
/// system's preferred action (Settings → Apple Pencil); the web app decides
/// what they do (the `pencil` message, Bridge).
@MainActor
final class PencilGestures: NSObject, UIPencilInteractionDelegate {
    /// Added to the web view's container (ShellController).
    lazy var interaction: UIPencilInteraction = {
        let interaction = UIPencilInteraction()
        interaction.delegate = self
        return interaction
    }()
    var onGesture: ((HostMessage) -> Void)?

    @available(iOS 17.5, *)
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveTap tap: UIPencilInteraction.Tap) {
        forward(.tap, UIPencilInteraction.preferredTapAction)
    }

    @available(iOS 17.5, *)
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveSqueeze squeeze: UIPencilInteraction.Squeeze) {
        // A squeeze is one gesture with phases; it counts once, when it ends.
        guard squeeze.phase == .ended else { return }
        forward(.squeeze, UIPencilInteraction.preferredSqueezeAction)
    }

    /// iPadOS 17.0 to 17.4. Later systems call the method above instead.
    func pencilInteractionDidTap(_ interaction: UIPencilInteraction) {
        if #available(iOS 17.5, *) { return }
        forward(.tap, UIPencilInteraction.preferredTapAction)
    }

    private func forward(_ gesture: PencilGesture, _ action: UIPencilPreferredAction) {
        onGesture?(.pencil(gesture: gesture, action: PencilAction.name(action)))
    }
}

/// The preferred action as the `pencil` message names it (tested).
enum PencilAction {
    static func name(_ action: UIPencilPreferredAction) -> String {
        if #available(iOS 17.5, *) {
            switch action {
            // The palette by the Pencil's tip: in the web app, the tool's options.
            case .showContextualPalette: return "showInkAttributes"
            case .runSystemShortcut: return "runSystemShortcut"
            default: break
            }
        }
        switch action {
        case .switchEraser: return "switchEraser"
        case .switchPrevious: return "switchPrevious"
        case .showColorPalette: return "showColorPalette"
        case .showInkAttributes: return "showInkAttributes"
        default: return "ignore"
        }
    }
}

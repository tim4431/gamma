import UIKit

/// The Pencil's own gestures: the double tap, and the squeeze of an Apple
/// Pencil Pro (iPadOS 17.5). Safari never gets these, so they are the one
/// thing the shell adds to the web app's pen. They are forwarded with the
/// system's preferred action (Settings → Apple Pencil); the web app decides
/// what they do (the `pencil` and `squeeze` messages, Bridge).
@MainActor
final class PencilGestures: NSObject, UIPencilInteractionDelegate {
    /// Added to the web view's container (ShellController).
    lazy var interaction: UIPencilInteraction = {
        let interaction = UIPencilInteraction()
        interaction.delegate = self
        return interaction
    }()
    var onGesture: ((HostMessage) -> Void)?
    /// The web view: where the Pencil hovered is kept in its points, the
    /// space of the page's `haptic` places.
    weak var surface: UIView?
    private var hover = GestureHover()

    /// Where the Pencil hovered at the gesture just made, in the surface's
    /// points, or nil (it did not hover, or no gesture was just made): the
    /// place the page's tick for that gesture plays at (`haptic` `tool`
    /// comes without one), which an Apple Pencil Pro needs to play it.
    var recentHover: CGPoint? { hover.location(now: ProcessInfo.processInfo.systemUptime) }

    @available(iOS 17.5, *)
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveTap tap: UIPencilInteraction.Tap) {
        remember(tap.hoverPose, from: interaction)
        forward(.tap, UIPencilInteraction.preferredTapAction)
    }

    /// A squeeze in its phases, so the web can hold a tool while the squeeze
    /// lasts: its start as `squeeze` `began`; its end as `pencil` with
    /// `phase: "ended"`, the message an older page acts on, once per squeeze;
    /// or, when the system cancels it, `squeeze` `cancelled` in its place.
    /// The repeating `changed` is not sent.
    @available(iOS 17.5, *)
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveSqueeze squeeze: UIPencilInteraction.Squeeze) {
        remember(squeeze.hoverPose, from: interaction)
        let action = PencilAction.name(UIPencilInteraction.preferredSqueezeAction)
        switch squeeze.phase {
        case .began: onGesture?(.squeeze(phase: .began, action: action))
        case .ended: onGesture?(.pencil(gesture: .squeeze, action: action, phase: .ended))
        case .cancelled: onGesture?(.squeeze(phase: .cancelled, action: action))
        default: break
        }
    }

    /// iPadOS 17.0 to 17.4. Later systems call the method above instead.
    func pencilInteractionDidTap(_ interaction: UIPencilInteraction) {
        if #available(iOS 17.5, *) { return }
        forward(.tap, UIPencilInteraction.preferredTapAction)
    }

    private func forward(_ gesture: PencilGesture, _ action: UIPencilPreferredAction) {
        onGesture?(.pencil(gesture: gesture, action: PencilAction.name(action)))
    }

    /// A gesture's hover pose (nil when the Pencil was not near enough to
    /// hover), its location taken from the interaction's view to the surface.
    @available(iOS 17.5, *)
    private func remember(_ pose: UIPencilHoverPose?, from interaction: UIPencilInteraction) {
        var point: CGPoint?
        if let pose, let view = interaction.view {
            point = view.convert(pose.location, to: surface ?? view)
        }
        hover.gesture(at: point, time: ProcessInfo.processInfo.systemUptime)
    }
}

/// Where the Pencil hovered at its last gesture, for the tick the page
/// answers that gesture with, which comes at once: kept `lifetime` seconds,
/// so a tick long after is no gesture's. Pure, so it is tested.
struct GestureHover {
    static let lifetime: TimeInterval = 1

    private var point: CGPoint?
    private var time: TimeInterval = 0

    /// A gesture at `time` (seconds of system uptime), where the Pencil
    /// hovered, or nil when it did not.
    mutating func gesture(at point: CGPoint?, time: TimeInterval) {
        self.point = point
        self.time = time
    }

    /// The place, while it is at most `lifetime` old at `now`.
    func location(now: TimeInterval) -> CGPoint? {
        guard let point, now - time <= Self.lifetime else { return nil }
        return point
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

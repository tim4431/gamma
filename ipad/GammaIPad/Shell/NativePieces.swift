import Foundation

/// The three native pieces that reach the page's web view, each with a
/// switch in the iPad's Settings app (Gamma: Resources/Settings.bundle),
/// for the freeze only an iPad Air with an Apple Pencil Pro shows
/// (docs/research/ipad-pencil-pro-freeze.md): with one off and the app
/// launched again, the freeze tells whether that piece makes it. All are on
/// by default, and read once, so a change takes effect at the next launch.
/// They go when the cause is known.
struct NativePieces: Equatable {
    /// A piece's key in the defaults, which the Settings bundle writes.
    enum Key: String, CaseIterable {
        case pencilCapture = "gamma.native.pencilCapture"
        case accessoryBar = "gamma.native.hideAccessoryBar"
        case pencilGestures = "gamma.native.pencilGestures"
    }

    /// `PencilCapture` on the web views' container. Off, the page is told
    /// the shell takes no strokes, and draws them itself as in Safari.
    var pencilCapture = true
    /// The content view's class without the keyboard's accessory bar
    /// (`WebShell.hideKeyboardAccessoryBar`). Off, the bar shows.
    var hidesAccessoryBar = true
    /// `UIPencilInteraction` on the container. Off, the page hears of no
    /// double tap and no squeeze.
    var pencilGestures = true

    static let current = NativePieces(.standard)

    /// On unless the Settings app (or a launch argument) says otherwise: it
    /// writes a key only once the switch has been touched.
    init(_ defaults: UserDefaults) {
        func on(_ key: Key) -> Bool { defaults.object(forKey: key.rawValue) == nil || defaults.bool(forKey: key.rawValue) }
        pencilCapture = on(.pencilCapture)
        hidesAccessoryBar = on(.accessoryBar)
        pencilGestures = on(.pencilGestures)
    }

    /// As the touch report names them.
    var summary: String {
        func word(_ on: Bool) -> String { on ? "on" : "OFF" }
        return "Pencil drawing \(word(pencilCapture)), hide keyboard bar \(word(hidesAccessoryBar)), Pencil gestures \(word(pencilGestures))"
    }
}

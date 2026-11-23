#if DEBUG && targetEnvironment(simulator)
import UIKit
import UIKit.UIGestureRecognizerSubclass
import WebKit

/// A finger standing in for the Apple Pencil, which no simulator sends: a
/// debug simulator build only, under the launch argument
/// `-gamma.pencilStandIn` (docs/dev/ipad.md "Tests"). A touch that begins in
/// the window's left half is the Pencil, to `PencilCapture` and to the page
/// (`pointerType`, `touchType`); one in the right half stays a finger. It is
/// the Pencil's place in the gestures' arbitration only: no hover, pressure,
/// tilt, roll or squeeze, and none of the system's own Pencil recognizers.
///
/// `-gamma.pencilStandIn.waits` adds one of those: a recognizer that gets
/// only Pencil touches, that WebKit's tap recognizer waits on and that waits
/// on `PencilCapture` itself. It is made up (which of the system's does so on
/// an iPad is not known), and it is what makes the Pencil Pro freeze
/// (docs/research/ipad-pencil-pro-freeze.md) when the capture never settles.
@MainActor
enum PencilStandIn {
    private static let arguments = ProcessInfo.processInfo.arguments
    static let on = arguments.contains("-gamma.pencilStandIn")
    private static let waits = arguments.contains("-gamma.pencilStandIn.waits")
    private static var pencils: Set<ObjectIdentifier> = []

    static func isPencil(_ touch: UITouch) -> Bool {
        guard on, touch.type == .direct else { return false }
        let key = ObjectIdentifier(touch)
        if touch.phase == .began { // a touch object is used again
            let left = touch.location(in: nil).x < (touch.window?.bounds.midX ?? 0)
            if left { pencils.insert(key) } else { pencils.remove(key) }
        }
        return pencils.contains(key)
    }

    static func install(in controller: WKUserContentController) {
        guard on else { return }
        controller.addUserScript(WKUserScript(source: """
        (() => {
          const type = Object.getOwnPropertyDescriptor(PointerEvent.prototype, "pointerType");
          const pens = new Map();
          Object.defineProperty(PointerEvent.prototype, "pointerType", { configurable: true, get() {
            const is = type.get.call(this);
            if (is !== "touch") return is;
            if (this.type === "pointerdown") pens.set(this.pointerId, this.clientX < innerWidth / 2);
            return pens.get(this.pointerId) ? "pen" : is;
          } });
          Object.defineProperty(Touch.prototype, "touchType", { configurable: true, get() {
            return this.clientX < innerWidth / 2 ? "stylus" : "direct";
          } });
        })();
        """, injectionTime: .atDocumentStart, forMainFrameOnly: true, in: .page))
    }

    /// WebKit makes its recognizers with its content view, after the page
    /// has begun to load, so this waits for them.
    static func link(_ webView: WKWebView, to capture: UIGestureRecognizer) {
        guard on, waits else { return }
        Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { timer in
            MainActor.assumeIsolated {
                for content in webView.scrollView.subviews {
                    let taps = (content.gestureRecognizers ?? []).filter { String(describing: type(of: $0)).hasPrefix("WKSyntheticTap") }
                    guard !taps.isEmpty else { continue }
                    let waiting = Waiting(target: nil, action: nil)
                    waiting.delegate = waiting
                    content.addGestureRecognizer(waiting)
                    waiting.require(toFail: capture)
                    for tap in taps { tap.require(toFail: waiting) }
                    timer.invalidate()
                }
            }
        }
    }

    private final class Waiting: UIGestureRecognizer, UIGestureRecognizerDelegate {
        func gestureRecognizer(_ recognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool { PencilStandIn.isPencil(touch) }
        override func canPrevent(_ other: UIGestureRecognizer) -> Bool { false }
        override func canBePrevented(by other: UIGestureRecognizer) -> Bool { false }
        override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) { state = .ended }
        override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) { state = .cancelled }
    }
}
#endif

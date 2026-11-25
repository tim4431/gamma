#if DEBUG && targetEnvironment(simulator)
import Foundation
import WebKit

/// What a UI test asks of the page, in a debug simulator build only
/// (docs/dev/ipad.md "Tests"). `-gamma.noTours` turns *Suggest tours* off in
/// the page's storage before its scripts run, as the browser suite does
/// (frontend/tests/e2e/harness.mjs): a welcome deck or a tour's card would
/// cover what a test taps and draws on.
@MainActor
enum UITestPage {
    static func install(in controller: WKUserContentController) {
        guard ProcessInfo.processInfo.arguments.contains("-gamma.noTours") else { return }
        controller.addUserScript(WKUserScript(source: #"try { localStorage.setItem("gamma-suggest-tours", "0"); } catch {}"#,
                                              injectionTime: .atDocumentStart, forMainFrameOnly: true, in: .page))
    }
}
#endif

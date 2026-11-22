import XCTest

/// The app driven by a finger in a simulator (docs/dev/ipad.md "Tests"):
/// This iPad's page answers taps before a PDF text selection, after it,
/// after the selection is gone and after a reload. The selection turns the
/// shell's edit menu off (`editMenu`, ShellWebView), which a reload leaves
/// off; this is the check that a page in that state still takes taps,
/// kept from the Pencil Pro freeze's investigation
/// (docs/research/ipad-pencil-pro-freeze.md). No simulator sends Apple
/// Pencil touches, so the freeze itself is not reproduced here.
///
/// Screenshots are attached to the result bundle, and written as PNGs to
/// the folder `GAMMA_UITEST_SHOTS` names when it is set (pass it to
/// xcodebuild as `TEST_RUNNER_GAMMA_UITEST_SHOTS=<folder>`).
final class TapsTests: XCTestCase {
    private var app: XCUIApplication!

    /// This iPad as the saved server (AppModel's `SavedServers`), so the
    /// app starts its own server and skips the launcher.
    private static let thisIPad = #"{"current":{"local":{}},"remotes":[]}"#

    override func setUp() {
        continueAfterFailure = true
        XCUIDevice.shared.orientation = .landscapeLeft
        app = XCUIApplication()
        // A Data default in the argument domain, in the old plist notation.
        let hex = Self.thisIPad.utf8.map { String(format: "%02x", $0) }.joined()
        app.launchArguments = ["-gamma.servers", "<\(hex)>"]
        app.launch()
    }

    func testTapsStillAnswerAfterAPdfSelectionAndAReload() throws {
        try XCTSkipUnless(openLibrary(), "the library never loaded")
        XCTAssertTrue(menuOpens("before"), "a tap opens the account menu")

        // A tap just after the menu closed can go to its closing popover:
        // the PDF is opened again until its text shows.
        let text = app.webViews.staticTexts.matching(NSPredicate(format: "label CONTAINS 'arXiv link'")).firstMatch
        var shown = false
        for _ in 0..<3 where !shown {
            XCTAssertTrue(openWelcome(), "the welcome PDF is listed")
            shown = text.waitForExistence(timeout: 10)
        }
        shot("opened")
        XCTAssertTrue(shown, "the PDF's text layer")
        text.press(forDuration: 1.2) // a word selected: the edit menu goes off
        sleep(2)
        shot("selected")
        XCTAssertTrue(menuOpens("selected"), "a tap still opens it with the PDF's text selected")

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9)).tap()
        sleep(1)
        XCTAssertTrue(menuOpens("cleared"), "and once the selection is gone")

        app.typeKey("r", modifierFlags: .command)
        sleep(8)
        XCTAssertTrue(menuOpens("reloaded"), "and after a reload, the edit menu still off")
    }

    /// The library, signed in. An unsigned build has no Keychain, so This
    /// iPad's silent sign-in cannot happen (LocalServer `seed`): the sign-in
    /// page then continues as a guest. The first start of the server on a
    /// fresh simulator takes minutes.
    private func openLibrary() -> Bool {
        if menuButton.waitForExistence(timeout: 240) { return true }
        let guest = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Continue as guest'")).firstMatch
        guard guest.exists else { shot("no-library"); return false }
        guest.tap()
        return menuButton.waitForExistence(timeout: 60)
    }

    /// The page's account menu button: *Account & settings* in landscape,
    /// *More* in the upright compact layout. Its menu has the same label.
    private var menuButton: XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == 'Account & settings' OR label == 'More'")).firstMatch
    }

    /// A tap on the account menu button opens the menu (its *Settings…*
    /// item shows); Escape closes it again.
    private func menuOpens(_ step: String) -> Bool {
        let button = menuButton
        guard button.waitForExistence(timeout: 10) else { return false }
        button.tap()
        let item = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH 'Settings'")).firstMatch
        let opened = item.waitForExistence(timeout: 4)
        shot("menu-\(step)")
        if opened {
            app.typeKey(XCUIKeyboardKey.escape, modifierFlags: [])
            sleep(1)
        }
        return opened
    }

    /// The welcome PDF every new account has: its tab when an earlier run
    /// left it open, else its row in the library, else the thumbnail of its
    /// *Recently viewed* card (the card's title opens nothing).
    private func openWelcome() -> Bool {
        let tab = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Welcome'")).firstMatch
        let titles = app.staticTexts.matching(NSPredicate(format: "label == 'Welcome'"))
        guard tab.waitForExistence(timeout: 5) || titles.firstMatch.waitForExistence(timeout: 20) else { return false }
        if tab.exists {
            tab.tap()
            return true
        }
        let shown = (0..<titles.count).map { titles.element(boundBy: $0) }.filter(\.isHittable)
        guard let title = shown.last else { return false }
        if shown.count > 1 {
            title.tap() // the library's row, below the card
        } else {
            title.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: -3)).tap() // the card's thumbnail
        }
        return true
    }

    private func shot(_ name: String) {
        let screenshot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let folder = ProcessInfo.processInfo.environment["GAMMA_UITEST_SHOTS"], !folder.isEmpty {
            try? screenshot.pngRepresentation.write(to: URL(fileURLWithPath: folder).appendingPathComponent("\(name).png"))
        }
    }
}

import XCTest

/// The app driven by a finger in a simulator (docs/dev/ipad.md "Tests"), for
/// the Pencil Pro freeze (docs/research/ipad-pencil-pro-freeze.md): This
/// iPad's page still answers taps after a tap of a stand-in Pencil that
/// nothing claims, and after a PDF text selection, which turns the shell's
/// edit menu off (`editMenu`, ShellWebView) and a reload leaves off.
///
/// Screenshots are attached to the result bundle, and written as PNGs to
/// the folder `GAMMA_UITEST_SHOTS` names when it is set (pass it to
/// xcodebuild as `TEST_RUNNER_GAMMA_UITEST_SHOTS=<folder>`).
final class TapsTests: XCTestCase {
    private var app: XCUIApplication!

    /// This iPad as the saved server (AppModel's `SavedServers`), so the
    /// app starts its own server and skips the launcher.
    private static let thisIPad = #"{"current":{"local":{}},"remotes":[]}"#

    private func launch(_ arguments: [String] = []) {
        continueAfterFailure = true
        XCUIDevice.shared.orientation = .landscapeLeft
        app = XCUIApplication()
        // A Data default in the argument domain, in the old plist notation.
        let hex = Self.thisIPad.utf8.map { String(format: "%02x", $0) }.joined()
        app.launchArguments = ["-gamma.servers", "<\(hex)>"] + arguments
        app.launch()
    }

    /// The Pencil Pro freeze with a stand-in Pencil (PencilStandIn) and one
    /// recognizer that waits on the capture: an unclaimed Pencil tap must
    /// leave the page answering fingers, and a reload too.
    func testAFingerStillTapsAfterAnUnclaimedPencilTap() throws {
        launch(["-gamma.pencilStandIn", "-gamma.pencilStandIn.waits"])
        try XCTSkipUnless(openLibrary(), "the library never loaded")
        XCTAssertTrue(menuOpens("finger first"), "a finger's tap opens the account menu")
        let window = app.windows.firstMatch.frame
        XCTAssertGreaterThan(menuButton.frame.midX, window.midX, "the account button is in the fingers' half")
        // the Home button, in the Pencil's half: a tap nothing claims
        app.buttons["Home"].firstMatch.tap()
        sleep(1)
        for n in 1...3 { XCTAssertTrue(menuOpens("finger after \(n)"), "a finger's tap \(n) after the Pencil's") }
        app.typeKey("r", modifierFlags: .command)
        sleep(8)
        XCTAssertTrue(menuOpens("finger reloaded"), "and after a reload")
    }

    func testTapsStillAnswerAfterAPdfSelectionAndAReload() throws {
        launch()
        try XCTSkipUnless(openLibrary(), "the library never loaded")
        XCTAssertTrue(menuOpens("before"), "a tap opens the account menu")

        XCTAssertTrue(openWelcome(), "the welcome PDF is listed")
        let text = app.webViews.staticTexts.matching(NSPredicate(format: "label CONTAINS 'arXiv link'")).firstMatch
        XCTAssertTrue(text.waitForExistence(timeout: 20), "the PDF's text layer")
        shot("opened")
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
    /// page then continues as a guest, whose first visit opens the welcome
    /// carousel over the library. The first start of the server on a fresh
    /// simulator takes minutes.
    private func openLibrary() -> Bool {
        let guest = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Continue as guest'")).firstMatch
        for _ in 0..<240 {
            if menuButton.exists || guest.exists { break }
            sleep(1)
        }
        if !menuButton.exists {
            guard guest.exists else { shot("no-library"); return false }
            guest.tap()
        }
        guard menuButton.waitForExistence(timeout: 60) else { return false }
        let close = app.buttons["Dismiss guide"].firstMatch
        if close.waitForExistence(timeout: 4) {
            close.tap()
            return close.waitForNonExistence(timeout: 4)
        }
        return true
    }

    /// The page's account menu button: *Account & settings* in landscape,
    /// *More* in the upright compact layout. Its menu has the same label.
    private var menuButton: XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == 'Account & settings' OR label == 'More'")).firstMatch
    }

    /// A tap on the account menu button opens the menu, and a second closes
    /// it. Its *Settings…* item is told by the whole label: the welcome PDF's
    /// own text has a word "Settings". (Escape does not close it here.)
    private func menuOpens(_ step: String) -> Bool {
        let button = menuButton
        guard button.waitForExistence(timeout: 10) else { return false }
        let item = app.descendants(matching: .any).matching(NSPredicate(format: "label == 'Settings…'")).firstMatch
        guard !item.exists else {
            XCTFail("\(step): the menu is open already")
            return false
        }
        button.tap()
        let opened = item.waitForExistence(timeout: 4)
        shot("menu-\(step)")
        if opened {
            button.tap()
            XCTAssertTrue(item.waitForNonExistence(timeout: 4), "\(step): a second tap closes the menu")
        }
        return opened
    }

    /// The welcome PDF every new account has: its tab when an earlier run
    /// left it open, else its title in the library.
    private func openWelcome() -> Bool {
        let tab = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Welcome'")).firstMatch
        let titles = app.staticTexts.matching(NSPredicate(format: "label == 'Welcome'"))
        guard tab.waitForExistence(timeout: 5) || titles.firstMatch.waitForExistence(timeout: 20) else { return false }
        if tab.exists {
            tab.tap()
            return true
        }
        guard let title = titles.allElementsBoundByIndex.last(where: \.isHittable) else { return false }
        title.tap()
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

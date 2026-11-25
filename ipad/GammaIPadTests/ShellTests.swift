import UIKit
import WebKit
import XCTest
@testable import Gamma

/// The shell's rules that need no web view: what the address field accepts,
/// which navigations stay in the app, the theme's colours and the screen kept
/// on while the page asks. Then the ones that need a web view: the
/// keyboard's accessory bar and the edit menu.
final class ServerAddressTests: XCTestCase {
    func testABareHostMeansHTTPS() {
        XCTAssertEqual(ServerAddress.parse("  gamma.example.org \n"), URL(string: "https://gamma.example.org"))
    }

    func testALinkKeepsOnlyItsOrigin() {
        XCTAssertEqual(ServerAddress.parse("http://NAS.local:8080/p/abc?x=1#top"), URL(string: "http://nas.local:8080"))
    }

    func testOtherSchemesAndBlanksAreRefused() {
        XCTAssertNil(ServerAddress.parse("ftp://gamma.example.org"))
        XCTAssertNil(ServerAddress.parse(""))
        XCTAssertNil(ServerAddress.parse("https://"))
    }
}

final class RouteTests: XCTestCase {
    private let server = Origin(scheme: "https", host: "gamma.example.org", port: nil)

    private func route(_ text: String, mainFrame: Bool = true) throws -> Route {
        Route.of(try XCTUnwrap(URL(string: text)), server: server, mainFrame: mainFrame)
    }

    func testTheServersPagesStay() throws {
        XCTAssertEqual(try route("https://gamma.example.org/p/abc"), .load)
        XCTAssertEqual(try route("https://GAMMA.example.org:443/"), .load, "the default port is the same origin")
    }

    func testTheCloudStartIsTakenOver() throws {
        XCTAssertEqual(try route("https://gamma.example.org/api/auth/cloud/start?next=%2F"), .cloudSignIn)
        XCTAssertEqual(try route("https://elsewhere.example/api/auth/cloud/start"), .browser, "only the server's own start")
    }

    func testOtherSitesLeaveTheWindowButEmbedsStay() throws {
        XCTAssertEqual(try route("https://arxiv.org/abs/1234"), .browser)
        XCTAssertEqual(try route("http://gamma.example.org/"), .browser, "another scheme is another origin")
        XCTAssertEqual(try route("https://www.youtube.com/embed/x", mainFrame: false), .load)
    }

    func testOtherAppsLinksGoToTheSystem() throws {
        XCTAssertEqual(try route("mailto:someone@example.org"), .system)
        XCTAssertEqual(try route("about:blank"), .load)
        XCTAssertEqual(try route("blob:https://gamma.example.org/1b2c"), .load)
    }

    func testOriginsPrintWithoutTheDefaultPort() throws {
        XCTAssertEqual(Origin(try XCTUnwrap(URL(string: "https://gamma.example.org/x")))?.string, "https://gamma.example.org")
        XCTAssertEqual(Origin(try XCTUnwrap(URL(string: "http://192.168.1.5:9001")))?.string, "http://192.168.1.5:9001")
        XCTAssertEqual(Origin(scheme: "http", host: "[::1]", port: 9001), Origin(scheme: "http", host: "::1", port: 9001))
    }
}

final class CSSColorTests: XCTestCase {
    private func rgba(_ text: String) -> [Double]? {
        CSSColor.components(text).map { [$0.red, $0.green, $0.blue, $0.alpha] }
    }

    func testHexColoursInEveryLength() {
        XCTAssertEqual(rgba("#fff"), [1, 1, 1, 1])
        XCTAssertEqual(rgba("#ff0000"), [1, 0, 0, 1])
        XCTAssertEqual(rgba("#00000080"), [0, 0, 0, 128.0 / 255])
    }

    func testAnythingElseIsNoColour() {
        XCTAssertNil(rgba("red"))
        XCTAssertNil(rgba("#12345"))
        XCTAssertNil(rgba("#gggggg"))
    }
}

final class ScreenAwakeTests: XCTestCase {
    @MainActor
    func testTheScreenStaysOnOnlyWhileThePageAsksAndTheAppIsInFront() {
        var disabled: [Bool] = []
        let awake = ScreenAwake { disabled.append($0) }
        awake.page(asks: true)
        XCTAssertEqual(disabled.last, true)
        awake.app(inFront: false)
        XCTAssertEqual(disabled.last, false, "the background lets the screen sleep")
        awake.app(inFront: true)
        XCTAssertEqual(disabled.last, true, "and the return keeps it on again")
        awake.page(asks: false)
        XCTAssertEqual(disabled.last, false, "the page's word ends it")
        awake.page(asks: true)
        awake.pageGone()
        XCTAssertEqual(disabled.last, false, "so does a new document, or the page's process ending")
        awake.app(inFront: false)
        awake.app(inFront: true)
        XCTAssertEqual(disabled.last, false, "nothing asked: the return leaves the timer on")
    }
}

final class KeyboardAccessoryTests: XCTestCase {
    @MainActor
    func testTheContentViewLosesItsAccessoryBar() throws {
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 320, height: 240))
        let content = try XCTUnwrap(webView.scrollView.subviews.first { NSStringFromClass(type(of: $0)).hasPrefix("WKContent") },
                                    "WebKit's content view is the scroll view's child")
        XCTAssertTrue(WebShell.hideKeyboardAccessoryBar(of: webView))
        XCTAssertNil(content.inputAccessoryView)
        XCTAssertTrue(NSStringFromClass(type(of: content)).hasPrefix("GammaNoAccessory_"))
        XCTAssertTrue(WebShell.hideKeyboardAccessoryBar(of: webView), "a second call is harmless")
        XCTAssertEqual(NSStringFromClass(type(of: content)).components(separatedBy: "GammaNoAccessory_").count, 2,
                       "the subclass is made once, not nested")
    }
}

/// The native pieces' switches (NativePieces, Settings.bundle).
final class NativePiecesTests: XCTestCase {
    private func defaults(_ values: [NativePieces.Key: Bool] = [:]) throws -> UserDefaults {
        let name = "NativePiecesTests." + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        for (key, on) in values { defaults.set(on, forKey: key.rawValue) }
        return defaults
    }

    func testEveryPieceIsOnUntilItsOwnSwitchIsTurnedOff() throws {
        let untouched = NativePieces(try defaults())
        XCTAssertEqual([untouched.pencilCapture, untouched.hidesAccessoryBar, untouched.pencilGestures], [true, true, true])
        let noCapture = NativePieces(try defaults([.pencilCapture: false]))
        XCTAssertEqual([noCapture.pencilCapture, noCapture.hidesAccessoryBar, noCapture.pencilGestures], [false, true, true])
        let noBar = NativePieces(try defaults([.accessoryBar: false]))
        XCTAssertEqual([noBar.pencilCapture, noBar.hidesAccessoryBar, noBar.pencilGestures], [true, false, true])
        let noGestures = NativePieces(try defaults([.pencilGestures: false, .pencilCapture: true]))
        XCTAssertEqual([noGestures.pencilCapture, noGestures.hidesAccessoryBar, noGestures.pencilGestures], [true, true, false])
    }

    func testThePageIsToldWhatAPieceThatIsOffWouldDo() throws {
        let all = HostInfo(version: "0")
        XCTAssertEqual(all.with(NativePieces(try defaults())), all)
        XCTAssertEqual(all.with(NativePieces(try defaults([.accessoryBar: false]))), all)
        let noCapture = all.with(NativePieces(try defaults([.pencilCapture: false])))
        XCTAssertEqual([noCapture.ink, noCapture.hold, noCapture.pencil, noCapture.pencilPhases], [false, false, true, true])
        let noGestures = all.with(NativePieces(try defaults([.pencilGestures: false])))
        XCTAssertEqual([noGestures.ink, noGestures.hold, noGestures.pencil, noGestures.pencilPhases], [true, true, false, false])
    }

    func testTheSettingsAppWritesTheKeysTheAppReadsAndStartsThemOn() throws {
        let file = try XCTUnwrap(Bundle.main.url(forResource: "Root", withExtension: "plist", subdirectory: "Settings.bundle"))
        let root = try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: file), format: nil) as? [String: Any])
        let switches = try XCTUnwrap(root["PreferenceSpecifiers"] as? [[String: Any]]).filter { $0["Type"] as? String == "PSToggleSwitchSpecifier" }
        XCTAssertEqual(Set(switches.compactMap { $0["Key"] as? String }), Set(NativePieces.Key.allCases.map(\.rawValue)))
        XCTAssertEqual(switches.compactMap { $0["DefaultValue"] as? Bool }, [true, true, true])
    }
}

final class EditMenuTests: XCTestCase {
    @MainActor
    func testTheEditMenuOffersNothingWhileOffButAKeyCommandStillRuns() {
        let webView = ShellWebView(frame: CGRect(x: 0, y: 0, width: 320, height: 240))
        let actions = [#selector(UIResponderStandardEditActions.copy(_:)), #selector(UIResponderStandardEditActions.selectAll(_:)),
                       NSSelectorFromString("_lookup:"), NSSelectorFromString("_translate:"), NSSelectorFromString("_share:")]
        let key = UIKeyCommand(input: "c", modifierFlags: .command, action: #selector(UIResponderStandardEditActions.copy(_:)))
        let asOn = actions.map { webView.canPerformAction($0, withSender: nil) }
        let keyOn = webView.canPerformAction(#selector(UIResponderStandardEditActions.copy(_:)), withSender: key)
        webView.editMenuShown = false
        for action in actions {
            XCTAssertFalse(webView.canPerformAction(action, withSender: nil), NSStringFromSelector(action))
        }
        XCTAssertEqual(webView.canPerformAction(#selector(UIResponderStandardEditActions.copy(_:)), withSender: key), keyOn,
                       "⌘C is not the menu's")
        webView.editMenuShown = true
        XCTAssertEqual(actions.map { webView.canPerformAction($0, withSender: nil) }, asOn, "on again: WebKit's answers")
    }

    @MainActor
    func testANewDocumentAndTheEndOfThePagesProcessBringTheMenuBack() throws {
        let name = "EditMenuTests." + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let shell = ShellController(model: AppModel(defaults: defaults)).shell
        shell.webView.editMenuShown = false
        shell.navigation.webView(shell.webView, didCommit: nil)
        XCTAssertTrue(shell.webView.editMenuShown, "a page says nothing as it unloads")
        shell.webView.editMenuShown = false
        shell.onProcessEnded?()
        XCTAssertTrue(shell.webView.editMenuShown)
    }
}

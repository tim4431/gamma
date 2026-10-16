import XCTest
@testable import Gamma

/// The shell's rules that need no web view: what the address field accepts,
/// which navigations stay in the app, and the theme's colours.
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

    func testRecentServersPutTheLatestFirstOnce() throws {
        let a = try XCTUnwrap(URL(string: "https://a.example")), b = try XCTUnwrap(URL(string: "https://b.example"))
        XCTAssertEqual(ServerAddress.remember(a, in: [b, a]), [a, b])
        let many = (0..<(ServerAddress.recentLimit + 5)).compactMap { URL(string: "https://s\($0).example") }
        XCTAssertEqual(ServerAddress.remember(a, in: many).count, ServerAddress.recentLimit)
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

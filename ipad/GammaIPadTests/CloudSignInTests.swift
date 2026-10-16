import XCTest
@testable import Gamma

/// The Gamma Cloud sign-in's contract with the server (Shell/CloudSignIn.swift
/// CloudWire, PKCE): the same requests the desktop app makes
/// (desktop/lib/cloudSignIn.js), which backend/gamma/routers/cloud_auth.py
/// answers. The sheet and the loopback listener need a device.
final class CloudSignInTests: XCTestCase {
    func testTheChallengeIsS256OfTheVerifier() {
        // RFC 7636 appendix B
        XCTAssertEqual(PKCE.challenge(for: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                       "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
    }

    func testAVerifierIsFortyThreeURLSafeCharactersAndNewEachTime() {
        let a = PKCE(), b = PKCE()
        XCTAssertEqual(a.verifier.count, 43)
        XCTAssertEqual(a.challenge.count, 43, "the server's challenge check wants 43 characters")
        XCTAssertNil(a.verifier.rangeOfCharacter(from: CharacterSet(charactersIn: "+/=")))
        XCTAssertNotEqual(a.verifier, b.verifier)
    }

    func testAppStartCarriesWhatThePageAskedFor() throws {
        let start = try XCTUnwrap(URL(string: "https://gamma.example.org/api/auth/cloud/start?link=1&next=%2Fsettings%3Ftab%3Daccount"))
        let body = CloudWire.AppStart(start: start, returnTo: "http://127.0.0.1:50123/cloud-signin", challenge: "c")
        XCTAssertEqual(body.next, "/settings?tab=account")
        XCTAssertTrue(body.link)
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        XCTAssertEqual(String(decoding: try encoder.encode(body), as: UTF8.self),
                       #"{"challenge":"c","link":true,"next":"\/settings?tab=account","return_to":"http:\/\/127.0.0.1:50123\/cloud-signin"}"#)
    }

    func testAPlainStartSignsInAndLandsHome() throws {
        let start = try XCTUnwrap(URL(string: "https://gamma.example.org/api/auth/cloud/start"))
        let body = CloudWire.AppStart(start: start, returnTo: "r", challenge: "c")
        XCTAssertEqual(body.next, "/")
        XCTAssertFalse(body.link)
    }

    func testAppStartsAnswer() throws {
        XCTAssertEqual(try CloudWire.authorizeURL(status: 200, text: #"{"url": "https://account.gammapdf.com/authorize?state=s"}"#),
                       URL(string: "https://account.gammapdf.com/authorize?state=s"))
        XCTAssertNil(try CloudWire.authorizeURL(status: 404, text: #"{"detail": "Not Found"}"#), "a server older than app-start")
        XCTAssertThrowsError(try CloudWire.authorizeURL(status: 400, text: #"{"detail": "The app's challenge is not an S256 code challenge."}"#)) {
            XCTAssertEqual($0.localizedDescription, "The app's challenge is not an S256 code challenge.")
        }
        XCTAssertThrowsError(try CloudWire.authorizeURL(status: 502, text: "Bad gateway")) {
            XCTAssertEqual($0.localizedDescription, "HTTP 502")
        }
    }

    func testTheClaimIsTheDesktopsFormPost() throws {
        let origin = Origin(scheme: "https", host: "gamma.example.org", port: nil)
        let request = try XCTUnwrap(CloudWire.claimRequest(origin: origin, result: "R-1_x", verifier: "V~2"))
        XCTAssertEqual(request.url?.absoluteString, "https://gamma.example.org/api/auth/cloud/app-claim")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Gamma-Desktop"), "claim")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/x-www-form-urlencoded")
        XCTAssertEqual(request.httpBody.map { String(decoding: $0, as: UTF8.self) }, "result=R-1_x&verifier=V~2")
    }

    func testAFormBodyEscapesWhatAFormMust() {
        XCTAssertEqual(String(decoding: CloudWire.formBody([("a", "x y&z=+")]), as: UTF8.self), "a=x%20y%26z%3D%2B")
    }

    func testTheLoopbackTakesTheResultOrTheError() {
        XCTAssertEqual(CloudWire.loopbackAnswer("GET /cloud-signin?result=abc_DEF-1 HTTP/1.1\r\nHost: 127.0.0.1:50123\r\n\r\n"),
                       .result("abc_DEF-1"))
        XCTAssertEqual(CloudWire.loopbackAnswer("GET /cloud-signin?error=Sign-in+cancelled. HTTP/1.1\r\n\r\n"),
                       .error("Sign-in cancelled."), "the server's urlencode writes spaces as +")
        XCTAssertNil(CloudWire.loopbackAnswer("GET /favicon.ico HTTP/1.1\r\n\r\n"))
        XCTAssertNil(CloudWire.loopbackAnswer("POST /cloud-signin?result=abc HTTP/1.1\r\n\r\n"))
        XCTAssertNil(CloudWire.loopbackAnswer("GET /cloud-signin HTTP/1.1\r\n\r\n"))
        XCTAssertNil(CloudWire.loopbackAnswer(""))
    }

    func testTheLoopbackSendsTheSheetToTheCustomScheme() {
        let found = String(decoding: CloudWire.loopbackResponse(for: .result("r")), as: UTF8.self)
        XCTAssertTrue(found.hasPrefix("HTTP/1.1 302 Found\r\n"))
        XCTAssertTrue(found.contains("\r\nLocation: gamma-ipad://cloud-signin\r\n"))
        XCTAssertTrue(found.hasSuffix("\r\n\r\n"))
        XCTAssertTrue(String(decoding: CloudWire.loopbackResponse(for: nil), as: UTF8.self).hasPrefix("HTTP/1.1 404"))
    }
}

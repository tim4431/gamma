import XCTest
import UIKit

final class InkScreenTests: XCTestCase {
    @MainActor
    func testInkIsVisibleOnThePage() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ink-ui-test"]
        app.launch()
        XCTAssertTrue(app.buttons["Save"].waitForExistence(timeout: 15))
        // Capturing through XCTest includes the live PencilKit surface.
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = "Gamma native handwriting screen"; attachment.lifetime = .keepAlways
        add(attachment)
        let image = try XCTUnwrap(shot.image.cgImage)
        let width = image.width, height = image.height
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let context = try XCTUnwrap(CGContext(data: &pixels, width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue))
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        var blue = 0
        // Only the middle of the page; exclude the toolbar's blue controls.
        for y in height / 4 ..< height * 3 / 4 {
            for x in 0..<width {
                let p = (y * width + x) * 4
                if pixels[p] < 100 && pixels[p + 1] < 150 && pixels[p + 2] > 150 && pixels[p + 3] > 200 { blue += 1 }
            }
        }
        XCTAssertGreaterThan(blue, 50, "Imported ink must actually render over the page")
    }
}

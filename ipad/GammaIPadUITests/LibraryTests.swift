import XCTest

final class LibraryTests: XCTestCase {
    @MainActor func testCreateNotebookAndShowPaper() throws {
        let app = XCUIApplication(); app.launch()
        let addButton = app.buttons["Add"]
        XCTAssertTrue(addButton.waitForExistence(timeout: 15)); addButton.tap()
        app.buttons["New notebook"].tap()
        let notebook = app.buttons["Untitled notebook"].firstMatch
        XCTAssertTrue(notebook.waitForExistence(timeout: 10)); notebook.tap()
        XCTAssertTrue(app.buttons["Library"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.segmentedControls["Handwriting tool"].waitForExistence(timeout: 10))
        let screenshot = XCTAttachment(screenshot: app.screenshot()); screenshot.name = "Native notebook"; screenshot.lifetime = .keepAlways; add(screenshot)
    }
}

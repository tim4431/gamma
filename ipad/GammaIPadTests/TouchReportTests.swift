import UIKit
import XCTest
@testable import Gamma

/// The touch report's parts that need no touch (TouchReport,
/// docs/research/ipad-pencil-pro-freeze.md): the latest lines kept, the
/// recognizer tree, and the file it is appended to.
final class TouchReportTests: XCTestCase {
    func testTheLogKeepsTheNewestLinesInOrder() {
        var log = TouchLog(capacity: 3)
        for n in 1...5 { log.add("line \(n)") }
        XCTAssertEqual(log.lines, ["line 3", "line 4", "line 5"])
    }

    @MainActor
    func testATouchsRecognizersAreCountedInTheOrderTheyCome() {
        XCTAssertEqual(TouchReport.counted(["A:possible", "B:possible", "A:possible", "A:ended", "B:possible"]),
                       "A:possible×2, B:possible×2, A:ended")
        XCTAssertEqual(TouchReport.counted(["A:possible"]), "A:possible")
    }

    @MainActor
    func testTheTreeListsRecognizersAndInteractionsByDepthAndSkipsBareViews() {
        let root = UIView()
        let bare = UIView()
        let tapped = UIView()
        let tap = UITapGestureRecognizer()
        tap.isEnabled = false
        tapped.addGestureRecognizer(tap)
        // one of the test's own: UIKit's add recognizers of their own
        // (UIPencilInteraction a hover recognizer)
        tapped.addInteraction(MarkInteraction())
        root.addSubview(bare)
        bare.addSubview(tapped)
        tapped.isHidden = true

        XCTAssertEqual(TouchReport.tree(root), [
            "    UIView hidden",
            "      UITapGestureRecognizer possible disabled",
            "      interactions: MarkInteraction",
        ])
    }

    private final class MarkInteraction: NSObject, UIInteraction {
        weak var view: UIView?
        func willMove(to view: UIView?) {}
        func didMove(to view: UIView?) { self.view = view }
    }

    @MainActor
    func testATreeTooLongIsCut() {
        let root = UIView()
        for _ in 0..<10 {
            let child = UIView()
            child.addGestureRecognizer(UITapGestureRecognizer())
            root.addSubview(child)
        }
        let lines = TouchReport.tree(root, limit: 6)
        XCTAssertEqual(lines.last, "… cut at 6 lines")
        XCTAssertEqual(lines.count, 7)
    }

    func testTheFileGrowsBySectionsAndKeepsItsNewestHalfPastTheCap() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let file = folder.appendingPathComponent("logs/touch-report.log")

        try TouchReport.append(["first", "report"], to: file, cap: 500)
        try TouchReport.append(["second"], to: file, cap: 500)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), "first\nreport\n\nsecond\n\n")

        // 100 lines of 9 bytes with their newlines: the file passes the cap,
        // and the next report finds it too long.
        let long = (0..<100).map { String(format: "line %03d", $0) }
        try TouchReport.append(long, to: file, cap: 500)
        try TouchReport.append(["last"], to: file, cap: 500)
        let kept = try String(contentsOf: file, encoding: .utf8)
        XCTAssertLessThanOrEqual(kept.utf8.count, 250 + "last\n\n".utf8.count)
        XCTAssertTrue(kept.hasPrefix("line "), "it starts at a line: \(kept.prefix(12))")
        XCTAssertTrue(kept.hasSuffix("line 099\n\nlast\n\n"))
        XCTAssertFalse(kept.contains("first"))
    }
}

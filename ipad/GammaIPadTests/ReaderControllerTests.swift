import XCTest
import UIKit
import PDFKit
import GammaCore
@testable import GammaIPad

@MainActor
final class ReaderControllerTests: XCTestCase {
    func testRotatedPDFOverlayUsesDisplayedPagePoints() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let repository = try GammaRepository(directory: directory)
        let raw = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 200, height: 300)).pdfData { renderer in
            renderer.beginPage()
            UIColor.systemGray.setFill(); renderer.cgContext.fill(CGRect(x: 20, y: 30, width: 160, height: 2))
            ("PDF coordinate test" as NSString).draw(at: CGPoint(x: 20, y: 60), withAttributes: [.font: UIFont.systemFont(ofSize: 14)])
        }
        let source = try XCTUnwrap(PDFDocument(data: raw)); source.page(at: 0)?.rotation = 90
        let document = try await repository.importPDF(data: XCTUnwrap(source.dataRepresentation()), title: "Rotated PDF")
        let engine = try InkEngine()
        var ink = try engine.object("newInk", [1, 300, 200])
        let stroke = try engine.object("encodeStroke", [["id": "rotated", "size": 3, "pen": false, "ch": "xy",
            "samples": [["x": 40, "y": 50], ["x": 240, "y": 50]]]])
        ink["strokes"] = [stroke]
        _ = try await repository.saveInk(pageID: document.id, blockID: "ink1", parentID: document.id, ink: engine.data(ink))
        let controller = ReaderController(repository: repository, documentID: document.id, directory: directory, close: {})
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.first as? UIWindowScene)
        let previous = scene.keyWindow
        let window = UIWindow(windowScene: scene); window.rootViewController = UINavigationController(rootViewController: controller); window.makeKeyAndVisible()
        defer { window.isHidden = true; previous?.makeKeyAndVisible() }
        controller.loadViewIfNeeded()
        func findPDF(_ view: UIView) -> PDFView? {
            if let pdf = view as? PDFView { return pdf }
            return view.subviews.lazy.compactMap(findPDF).first
        }
        let view = try XCTUnwrap(findPDF(controller.view))
        for _ in 0..<100 {
            if view.document != nil { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        let page = try XCTUnwrap(view.document?.page(at: 0))
        let overlay = try XCTUnwrap(controller.pdfView(view, overlayViewFor: page) as? PDFInkOverlay)
        let canvas = overlay.canvas
        XCTAssertEqual(canvas.pageSize, CGSize(width: 300, height: 200))
        XCTAssertEqual(canvas.groups.count, 1)
        XCTAssertEqual((canvas.groups[0].ink["space"] as? [String: Any])?["page"] as? Int, 1)
        window.layoutIfNeeded()
        XCTAssertNotNil(canvas.window)
        let left = canvas.convert(CGPoint.zero, to: window)
        let right = canvas.convert(CGPoint(x: canvas.bounds.width, y: 0), to: window)
        XCTAssertGreaterThan(right.x, left.x)
        XCTAssertEqual(right.y, left.y, accuracy: 0.01, "A horizontal Gamma stroke must remain horizontal on a rotated PDF")
        let screenshot = UIGraphicsImageRenderer(bounds: window.bounds).image { _ in window.drawHierarchy(in: window.bounds, afterScreenUpdates: true) }
        let attachment = XCTAttachment(image: screenshot); attachment.name = "Rotated PDF and canonical ink"; attachment.lifetime = .keepAlways; add(attachment)
    }
}

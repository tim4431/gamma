#if DEBUG
import UIKit

// Simulator fixture for a real screen capture, including PencilKit's Metal
// surface (which UIKit drawHierarchy snapshots omit). Absent from Release.
@MainActor
enum InkPreview {
    static func controller() -> InkEditorController {
        let size = CGSize(width: 612, height: 792)
        let image = UIGraphicsImageRenderer(size: size).image { context in
            UIColor.white.setFill(); context.fill(CGRect(origin: .zero, size: size))
            ("Gamma · Handwriting" as NSString).draw(at: CGPoint(x: 40, y: 50),
                withAttributes: [.font: UIFont.systemFont(ofSize: 24), .foregroundColor: UIColor.black])
        }
        let stroke = GammaStroke(id: "preview", tool: "pen", brush: "monoline", color: "#1d4ed8", size: 3,
            ch: "xy", pts: [10000, 25000, 10000, 5000, 10000, -3000])
        let request = InkRequest(requestId: "preview", user: "test", workspace: "test", pageId: UUID().uuidString,
            document: "/test.pdf", blockId: "preview", parentId: "preview", expectedURL: nil, existing: false,
            ink: GammaInk(space: GammaSpace(page: 1, width: 612, height: 792), strokes: [stroke]), background: [],
            image: "data:image/png;base64," + image.pngData()!.base64EncodedString())
        return try! InkEditorController(request: request, origin: "https://test.example/")
    }
}
#endif

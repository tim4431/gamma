import SwiftUI

@main
struct GammaApp: App {
    var body: some Scene {
        WindowGroup { WorkspaceView().ignoresSafeArea(.container, edges: .bottom) }
    }
}

struct WorkspaceView: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> UINavigationController {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ink-ui-test") {
            return UINavigationController(rootViewController: InkPreview.controller())
        }
        #endif
        return UINavigationController(rootViewController: WorkspaceController())
    }
    func updateUIViewController(_ controller: UINavigationController, context: Context) {}
}

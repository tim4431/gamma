import SwiftUI

/// Gamma for iPad (ipad/README.md, docs/dev/ipad.md): a Gamma server's own
/// web app in a web view, under the desktop app's own bar and launcher,
/// with Apple Pencil captured natively over it, and a Gamma server of its
/// own inside the app (LocalServer). The web app owns every page, tool and
/// decision; this app is a window, an input device, a preview layer and a
/// process manager.
@main
struct GammaApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            // Full-screen: the bar paints the strip under the status bar
            // itself, and the web view starts below it (ShellController).
            ShellView(model: model, local: model.local)
                .ignoresSafeArea()
                // Back from the background: the local server may have lost its
                // socket meanwhile (LocalServer.recheck).
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active { model.local.recheck() }
                }
                // A file the system opens in the app (the share sheet, Files'
                // Open in, AirDrop: Info.plist's document types) waits in
                // OpenedFiles until the page takes it, so one opened on the
                // launcher or while This iPad starts is not lost. Files
                // only: the gamma-ipad: scheme is the Gamma Cloud sign-in's, and
                // its browser sheet takes it (CloudSignIn).
                .onOpenURL { url in
                    guard url.isFileURL else { return }
                    Task { await model.opened.take(url) }
                }
        }
    }
}

import SwiftUI

/// Gamma for iPad (ipad/README.md, docs/dev/ipad.md): a Gamma server's own
/// web app in a web view, with Apple Pencil captured natively over it, and
/// a Gamma server of its own inside the app (LocalServer). The web app owns
/// every page, tool and decision; this app is a window, an input device, a
/// preview layer and a process manager.
@main
struct GammaApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(model.local)
        }
    }
}

/// The server picker until a server is chosen, then the shell for good: the
/// shell's web view is made once and kept, and a change of server loads the
/// new address into it (ShellView). While the local server starts, or when
/// it could not, a page of its own covers the shell.
struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var local: LocalServer
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        content
            // Back from the background: the local server may have lost its
            // socket meanwhile (LocalServer.recheck).
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { local.recheck() }
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.current == nil {
            ServersView()
        } else {
            ZStack {
                if let url = model.shellURL {
                    ShellView(server: url, login: model.login(for: url))
                }
                if model.current == .local, local.url == nil {
                    LocalStartView()
                }
            }
            .sheet(isPresented: $model.choosingServer) { ServersView() }
        }
    }
}

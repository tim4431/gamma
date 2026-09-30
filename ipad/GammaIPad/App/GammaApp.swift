import SwiftUI

/// Gamma for iPad (ipad/README.md): a copy of one workspace of a Gamma
/// server, read and written here — PDFs and notebooks with Apple Pencil —
/// and kept in step with the server by the same mirror rounds the desktop
/// app runs.
@main
struct GammaApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
        .onChange(of: phase) { _, now in model.scene(now) }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if model.replica != nil {
                LibraryView()
            } else {
                ConnectView()
            }
        }
        .alert("Gamma", isPresented: Binding(get: { model.failure != nil }, set: { if !$0 { model.failure = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.failure ?? "")
        }
    }
}

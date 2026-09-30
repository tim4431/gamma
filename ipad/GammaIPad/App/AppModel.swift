import Foundation
import SwiftUI

/// The app's one piece of state: the connection, its replica, the sync
/// status. Rounds run one at a time on a queue of their own (every 30
/// seconds while the app is in front, when it comes back, a couple of
/// seconds after an edit, and on request) — the desktop mirror's cadence
/// (docs/dev/mirror.md "Rounds and cadence").
@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var replica: Replica?
    @Published private(set) var status = SyncStatus()
    /// Bumps whenever the library here changed (a round, an edit): views reload.
    @Published private(set) var revision = 0
    @Published var failure: String?

    private var timer: Timer?
    private var pending: Task<Void, Never>?
    private let rounds = DispatchQueue(label: "net.gammapdf.ipad.rounds", qos: .utility)
    private static let connectionKey = "gamma.connection"

    init() {
        restore()
    }

    // --- the connection -----------------------------------------------------------------

    private func restore() {
        guard let data = UserDefaults.standard.data(forKey: Self.connectionKey),
              let connection = try? JSONDecoder().decode(Connection.self, from: data),
              let token = Keychain.load(account: connection.id) else { return }
        open(connection, token: token)
    }

    private func open(_ connection: Connection, token: String) {
        do {
            let replica = try Replica(connection: connection, token: token, directory: Replica.directory(for: connection.id))
            replica.onProgress = { [weak self] text in
                Task { @MainActor in self?.status.progress = text }
            }
            self.replica = replica
            revision += 1
        } catch {
            failure = error.localizedDescription
        }
    }

    func connect(_ connection: Connection, token: String) throws {
        try Keychain.save(token, account: connection.id)
        UserDefaults.standard.set(try JSONEncoder().encode(connection), forKey: Self.connectionKey)
        open(connection, token: token)
        syncNow()
    }

    /// Forget the server here. The pages kept here go too when `erase` (edits
    /// not yet sent are lost with them: the caller asks first).
    func disconnect(erase: Bool) {
        stopTimer()
        if let connection = replica?.connection {
            Keychain.delete(account: connection.id)
            if erase { try? FileManager.default.removeItem(at: Replica.directory(for: connection.id)) }
        }
        UserDefaults.standard.removeObject(forKey: Self.connectionKey)
        replica = nil
        status = SyncStatus()
    }

    func setMode(_ mode: String) {
        guard let replica else { return }
        var connection = replica.connection
        connection.mode = mode
        guard let token = Keychain.load(account: connection.id) else { return }
        UserDefaults.standard.set(try? JSONEncoder().encode(connection), forKey: Self.connectionKey)
        open(connection, token: token)
        syncNow()
    }

    // --- rounds -----------------------------------------------------------------------------

    func scene(_ phase: ScenePhase) {
        switch phase {
        case .active:
            startTimer()
            syncNow()
        case .background:
            stopTimer()
        default:
            break
        }
    }

    private func startTimer() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.syncNow() }
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    func syncNow() {
        guard let replica, !status.running else { return }
        status.running = true
        status.progress = ""
        rounds.async { [weak self] in
            let result = Result { try replica.syncRound() }
            Task { @MainActor in
                guard let self else { return }
                self.status.running = false
                self.status.progress = ""
                switch result {
                case .success(let out):
                    self.status.lastSync = Date()
                    self.status.lastError = out.string("last_error")
                    self.status.pulled = out.int("pages_pulled") ?? 0
                    self.status.pushed = out.int("pages_pushed") ?? 0
                    self.status.deleted = out.int("pages_deleted") ?? 0
                case .failure(let error):
                    self.status.lastError = error.localizedDescription
                }
                self.revision += 1
            }
        }
    }

    /// An edit landed in the library here: views reload, a round follows soon.
    func edited() {
        revision += 1
        pending?.cancel()
        pending = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if !Task.isCancelled { self?.syncNow() }
        }
    }

    var pendingEdits: Int { replica?.store.pendingEdits ?? 0 }
}

import Foundation
import AVFoundation
import UIKit

/// One recording block contains independently recoverable AAC segments.
@MainActor
final class NoteAudioSession: NSObject, AVAudioRecorderDelegate, AVAudioPlayerDelegate {
    var onChange: (() -> Void)?
    var onError: ((Error) -> Void)?
    var onSegment: ((String, URL, Int, [[String: Any]]) async throws -> Void)?
    var onPlayback: ((String?, Double) -> Void)?
    private(set) var recorder: AVAudioRecorder?
    private(set) var player: AVAudioPlayer?
    private(set) var segmentID = ""
    private(set) var isFinalizing = false
    private var events: [[String: Any]] = []
    private var rollTimer: Timer?
    private var playbackTimer: Timer?
    private var playlist: [(String, URL)] = []
    private var playlistIndex = 0
    private var roll = false
    private var backgroundTask = UIBackgroundTaskIdentifier.invalid
    let directory: URL

    init(directory: URL) throws {
        self.directory = directory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(backgrounded), name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted), name: AVAudioSession.interruptionNotification, object: nil)
    }
    deinit { rollTimer?.invalidate(); playbackTimer?.invalidate(); NotificationCenter.default.removeObserver(self) }
    func record() async throws {
        guard recorder == nil, !isFinalizing else { return }
        let allowed = await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard allowed else { throw InkEngineError.failure("Allow microphone access in iPad Settings to record notes.") }
        stopPlayback()
        try AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetooth])
        try AVAudioSession.sharedInstance().setActive(true)
        segmentID = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let url = directory.appendingPathComponent(segmentID + ".m4a")
        let next = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44100, AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000])
        next.delegate = self
        guard next.record() else { throw InkEngineError.failure("Could not start recording.") }
        recorder = next; events = []; roll = false
        try persistEvents()
        rollTimer = Timer.scheduledTimer(withTimeInterval: 300, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.roll = true; self?.pause() }
        }
        onChange?()
    }
    func recordStroke(groupID: String, stroke: [String: Any], start: Double, end: Double, anchor: [String: Any]) {
        guard let recorder, let strokeID = stroke["id"] as? String else { return }
        let now = ProcessInfo.processInfo.systemUptime, clock = recorder.currentTime
        var event = anchor
        event.merge(["kind": "stroke", "segment_id": segmentID, "block_id": groupID, "stroke_id": strokeID,
                     "start_ms": max(0, Int((clock + start - now) * 1000)),
                     "end_ms": max(0, Int((clock + end - now) * 1000))]) { _, new in new }
        events.append(event)
        do { try persistEvents() } catch { onError?(error) }
    }
    func recordPage(_ anchor: [String: Any]) {
        guard let recorder else { return }
        var event = anchor
        let time = Int(recorder.currentTime * 1000)
        event.merge(["kind": "page", "segment_id": segmentID, "start_ms": time, "end_ms": time]) { _, new in new }
        events.append(event)
        do { try persistEvents() } catch { onError?(error) }
    }
    private func persistEvents() throws {
        try JSONSerialization.data(withJSONObject: events, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent(segmentID + ".json"), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    func pause() {
        rollTimer?.invalidate(); rollTimer = nil
        guard let recorder else { return }
        isFinalizing = true
        recorder.stop(); onChange?()
    }
    func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        let id = segmentID, url = recorder.url, savedEvents = events
        self.recorder = nil
        Task {
            do {
                // Read duration from the finalized asset; recorder.currentTime resets after stop.
                let duration = try AVAudioPlayer(contentsOf: url).duration
                guard flag || duration > 0 else { throw InkEngineError.failure("The interrupted recording is kept for recovery.") }
                try await onSegment?(id, url, Int(duration * 1000), savedEvents)
                try FileManager.default.removeItem(at: url)
                try? FileManager.default.removeItem(at: directory.appendingPathComponent(id + ".json"))
            } catch { onError?(error) }
            isFinalizing = false; onChange?()
            if backgroundTask != .invalid { UIApplication.shared.endBackgroundTask(backgroundTask); backgroundTask = .invalid }
            if roll { roll = false; do { try await record() } catch { onError?(error) } }
        }
    }
    func recover() async throws {
        for url in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) where url.pathExtension == "m4a" {
            let id = url.deletingPathExtension().lastPathComponent
            let duration = try AVAudioPlayer(contentsOf: url).duration
            let metadata = directory.appendingPathComponent(id + ".json")
            let data = try? Data(contentsOf: metadata)
            let recovered = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [[String: Any]] } ?? []
            try await onSegment?(id, url, Int(duration * 1000), recovered)
            try FileManager.default.removeItem(at: url); try? FileManager.default.removeItem(at: metadata)
        }
    }
    @objc private func backgrounded() {
        roll = false
        if recorder != nil {
            backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Finish audio segment") { [weak self] in
                Task { @MainActor in
                    guard let self, self.backgroundTask != .invalid else { return }
                    UIApplication.shared.endBackgroundTask(self.backgroundTask); self.backgroundTask = .invalid
                }
            }
        }
        pause(); stopPlayback()
    }
    @objc private func interrupted() { roll = false; pause(); stopPlayback() }
    func play(_ segments: [(String, URL)], startingAt segment: String? = nil, milliseconds: Double = 0) throws {
        guard recorder == nil, !isFinalizing else { return }
        stopPlayback(); playlist = segments
        playlistIndex = segments.firstIndex(where: { $0.0 == segment }) ?? 0
        try startPlayer(milliseconds: milliseconds)
    }
    private func startPlayer(milliseconds: Double = 0) throws {
        guard playlist.indices.contains(playlistIndex) else { stopPlayback(); return }
        try AVAudioSession.sharedInstance().setCategory(.playback)
        try AVAudioSession.sharedInstance().setActive(true)
        let next = try AVAudioPlayer(contentsOf: playlist[playlistIndex].1)
        next.delegate = self; next.currentTime = max(0, milliseconds / 1000)
        guard next.play() else { throw InkEngineError.failure("Could not play this recording.") }
        player = next
        playbackTimer?.invalidate()
        playbackTimer = Timer.scheduledTimer(withTimeInterval: 1 / 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, let player = self.player else { return }
                self.onPlayback?(self.playlist[self.playlistIndex].0, player.currentTime * 1000)
            }
        }; onChange?()
    }
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        playlistIndex += 1
        do { try startPlayer() } catch { onError?(error) }
    }
    func stopPlayback() { player?.stop(); player = nil; playbackTimer?.invalidate(); playbackTimer = nil; onPlayback?(nil, 0); onChange?() }
}

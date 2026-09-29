import XCTest
import AVFoundation
@testable import GammaIPad

@MainActor
final class AudioSessionTests: XCTestCase {
    private func recording(in directory: URL) throws -> (audio: URL, metadata: URL) {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let audio = directory.appendingPathComponent("segment.m4a")
        // Create a finalized AAC segment without microphone access or playback.
        try autoreleasepool {
            let file = try AVAudioFile(forWriting: audio, settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100,
                AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000
            ])
            let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 4410))
            buffer.frameLength = 4410
            let channel = try XCTUnwrap(buffer.floatChannelData?[0])
            for index in 0..<Int(buffer.frameLength) { channel[index] = 0 }
            try file.write(from: buffer)
        }
        let metadata = directory.appendingPathComponent("segment.json")
        try Data("[{\"kind\":\"page\",\"segment_id\":\"segment\",\"pdf_page\":1}]".utf8).write(to: metadata)
        return (audio, metadata)
    }

    func testRecoveryWithoutSaveHandlerKeepsRecordingAndEvents() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let files = try recording(in: directory)
        let session = try NoteAudioSession(directory: directory)
        do {
            try await session.recover()
            XCTFail("Recovery must not discard a recording without saving it")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("kept for recovery"))
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: files.audio.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: files.metadata.path))
    }

    func testFailedSaveRetainsBytesAndCanBeRetriedAfterReopening() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let files = try recording(in: directory)
        let audio = try Data(contentsOf: files.audio), metadata = try Data(contentsOf: files.metadata)
        let session = try NoteAudioSession(directory: directory)
        session.onSegment = { _, _, _, _ in throw InkEngineError.failure("Save failed") }
        do {
            try await session.recover()
            XCTFail("Recovery must report a failed save")
        } catch { XCTAssertEqual(error.localizedDescription, "Save failed") }
        XCTAssertEqual(try Data(contentsOf: files.audio), audio)
        XCTAssertEqual(try Data(contentsOf: files.metadata), metadata)

        let reopened = try NoteAudioSession(directory: directory)
        var saved = 0
        reopened.onSegment = { id, url, duration, events in
            saved += 1
            XCTAssertEqual(id, "segment")
            XCTAssertEqual(try Data(contentsOf: url), audio)
            XCTAssertGreaterThan(duration, 0)
            XCTAssertEqual(events.count, 1)
            XCTAssertEqual(events.first?["pdf_page"] as? Int, 1)
            XCTAssertTrue(FileManager.default.fileExists(atPath: files.metadata.path))
        }
        try await reopened.recover()
        XCTAssertEqual(saved, 1)
        XCTAssertFalse(FileManager.default.fileExists(atPath: files.audio.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: files.metadata.path))
        try await reopened.recover()
        XCTAssertEqual(saved, 1, "Committed segments must not be recovered again")
    }
}

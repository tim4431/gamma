import XCTest
@testable import Gamma

/// The brush table's Swift port (Pencil/InkWidth.swift) against every row
/// of tests/shared/inkwidth.json, the rows frontend/tests/finishStroke.test.mjs
/// and backend/tests/test_shared_fixtures.py are held to: the live line
/// under the Pencil is as wide as the stroke the page commits. A few rows
/// are copied here too, one per law, so a law is checked even where the
/// fixture is not in the bundle.
final class InkWidthTests: XCTestCase {
    private func sample(_ p: Double?, a: Double? = nil) -> InkWidth.Sample {
        InkWidth.Sample(x: 0, y: 0, p: p, a: a)
    }

    /// A `samples` row's stroke: `[x, y, p | null, t, a | null]`.
    private func stroke(_ rows: [[Double?]]) -> [InkWidth.Sample] {
        rows.map { InkWidth.Sample(x: $0[0] ?? 0, y: $0[1] ?? 0, p: $0[2], a: $0.count > 4 ? $0[4] : nil) }
    }

    private func assertNear(_ got: [Double]?, _ want: [Double]?, _ note: String,
                            file: StaticString = #filePath, line: UInt = #line) {
        guard let want else {
            XCTAssertNil(got, note, file: file, line: line)
            return
        }
        guard let got, got.count == want.count else {
            XCTFail("\(note): \(String(describing: got)), where \(want) was expected", file: file, line: line)
            return
        }
        for (i, (mine, theirs)) in zip(got, want).enumerated() {
            XCTAssertEqual(mine, theirs, accuracy: 0.0005, "\(note): sample \(i)", file: file, line: line)
        }
    }

    func testEveryRowOfTheSharedFixture() throws {
        let fixture = try SharedFixture.load("inkwidth", as: WidthFixture.self)
        XCTAssertFalse(fixture.rows.isEmpty)
        for row in fixture.rows {
            let kind = try XCTUnwrap(InkTool.Kind(rawValue: row.tool), row.note)
            let brush = try XCTUnwrap(InkTool.Brush(rawValue: row.brush ?? "pen"), row.note)
            let samples = row.samples.map { stroke($0) } ?? [sample(row.p, a: row.a)]
            let ws = InkWidth.widths(samples, tool: kind, brush: brush, pressure: row.pen, size: row.size,
                                     shape: row.shape != nil)
            let os = InkWidth.opacities(samples, tool: kind, brush: brush, pressure: row.pen, shape: row.shape != nil)
            if row.samples != nil {
                assertNear(ws, row.ws, row.note)
                assertNear(os, row.os, row.note)
                if let at = row.at, let w = row.w, let ws, ws.indices.contains(at) {
                    XCTAssertEqual(ws[at], w, accuracy: 0.0005, "\(row.note): the sample at \(at)")
                }
                if let simulated = row.pSim {
                    assertNear(InkWidth.simulatedPressure(samples, size: row.size), simulated, "\(row.note): the speed law")
                }
            } else {
                assertNear(ws, row.w.map { [$0] }, row.note)
                // only a Pencil writes o; every Pencil row says what it is
                assertNear(os, brush == .pencil ? row.o.map { [$0] } : nil, row.note)
            }
        }
    }

    func testThePensLaw() {
        XCTAssertEqual(InkWidth.law(.pen, sample(0.5)), 1, "at rest pressure, the nominal size")
        XCTAssertEqual(InkWidth.law(.pen, sample(0)), 0.5)
        XCTAssertEqual(InkWidth.law(.pen, sample(1)), 1.5)
        XCTAssertEqual(InkWidth.law(.pen, sample(nil)), 1, "no pressure reads as 0.5")
        XCTAssertEqual(InkWidth.widths([sample(0.731)], tool: .pen, brush: .pen, pressure: true, size: 3), [1.231],
                       "three decimals")
        XCTAssertEqual(InkWidth.widths([sample(0.123)], tool: .pen, brush: .pen, pressure: true, size: 1.6), [0.623])
        XCTAssertEqual(InkWidth.widths([sample(0.8, a: 20)], tool: .pen, brush: .pen, pressure: true, size: 2), [1.3],
                       "the Pen ignores the tilt")
    }

    func testWhatHasNoWidths() {
        XCTAssertNil(InkWidth.widths([sample(0.9)], tool: .pen, brush: .monoline, pressure: true, size: 3), "the Ballpoint")
        XCTAssertNil(InkWidth.widths([sample(0.9)], tool: .highlighter, brush: .pen, pressure: true, size: 14))
        XCTAssertNil(InkWidth.widths([sample(0.9, a: 60)], tool: .pen, brush: .pen, pressure: false, size: 2),
                     "a stroke without pressure")
        XCTAssertNil(InkWidth.widths([sample(nil)], tool: .pen, brush: .pencil, pressure: false, size: 2))
        XCTAssertNil(InkWidth.opacities([sample(nil)], tool: .pen, brush: .pencil, pressure: false))
        let line = stroke([[0, 0, nil, 0, nil], [50, 0, nil, 144, nil]])
        XCTAssertNil(InkWidth.widths(line, tool: .pen, brush: .brush, pressure: false, size: 2, shape: true),
                     "a held shape is even, whatever its brush")
        XCTAssertNil(InkWidth.widths([], tool: .pen, brush: .pen, pressure: true, size: 2))
    }

    func testTheBrushsTapersAndRange() {
        XCTAssertEqual(InkWidth.widths([sample(0.5)], tool: .pen, brush: .brush, pressure: true, size: 2), [1],
                       "a tap has no length to taper")
        // a 2 pt comma, each taper held to half its length
        let comma = stroke((0...8).map { [Double($0) * 0.25, 0, 0.5, Double($0) * 8, nil] })
        assertNear(InkWidth.widths(comma, tool: .pen, brush: .brush, pressure: true, size: 2),
                   [0.1, 0.383, 0.707, 0.924, 1, 0.924, 0.707, 0.383, 0.1], "a short mark")
        // a 20 pt line with pressure rising 0.2 to 1
        let rising = stroke((0...20).map { [Double($0), 0, 0.2 + 0.04 * Double($0), Double($0) * 8, nil] })
        assertNear(InkWidth.widths(rising, tool: .pen, brush: .brush, pressure: true, size: 2),
                   [0.1, 0.184, 0.396, 0.591, 0.72, 0.8, 0.88, 0.96, 1.04, 1.12, 1.2, 1.28, 1.36, 1.44, 1.52, 1.545, 1.455,
                    1.245, 0.92, 0.497, 0.1], "the start taper over 4 pt, the end taper over 6")
    }

    func testTheBrushsSpeedLawWithoutPressure() {
        let xs: [Double] = [0, 0.5, 1, 1.5, 2, 4, 6, 8, 10, 12, 12.5, 13, 13.5, 14]
        let mouse = stroke(xs.enumerated().map { [$0.element, 0, nil, Double($0.offset) * 16, nil] })
        assertNear(InkWidth.simulatedPressure(mouse, size: 2),
                   [0.5, 0.517, 0.533, 0.548, 0.562, 0.407, 0.295, 0.214, 0.155, 0.113, 0.156, 0.197, 0.235, 0.271],
                   "slow is wide, fast is thin")
        assertNear(InkWidth.widths(mouse, tool: .pen, brush: .brush, pressure: false, size: 2),
                   [0.1, 0.202, 0.408, 0.609, 0.795, 0.815, 0.591, 0.428, 0.269, 0.113, 0.12, 0.102, 0.1, 0.1],
                   "the speed law under the tapers")
    }

    func testTheLiveLineHasOnlyTheStartTaper() {
        let comma = stroke((0...8).map { [Double($0) * 0.25, 0, 0.5, Double($0) * 8, nil] })
        let live = InkWidth.widths(comma, tool: .pen, brush: .brush, pressure: true, size: 2, live: true)
        // the start taper at its full 4 pt, whatever the length so far, and no end taper
        assertNear(live, [0.1, 0.1, 0.195, 0.29, 0.383, 0.471, 0.556, 0.634, 0.707], "the stroke as it is written")
    }

    func testThePencilsTiltAndShade() {
        XCTAssertEqual(InkWidth.widths([sample(0.5, a: 90)], tool: .pen, brush: .pencil, pressure: true, size: 1.6), [1])
        XCTAssertEqual(InkWidth.widths([sample(0.5, a: 60)], tool: .pen, brush: .pencil, pressure: true, size: 2), [1.667])
        XCTAssertEqual(InkWidth.widths([sample(0.5, a: 30)], tool: .pen, brush: .pencil, pressure: true, size: 2), [2.333])
        XCTAssertEqual(InkWidth.widths([sample(1, a: 30)], tool: .pen, brush: .pencil, pressure: true, size: 2), [3.5],
                       "the widest: 1.5 × 2.333")
        XCTAssertEqual(InkWidth.widths([sample(0.2, a: 45)], tool: .pen, brush: .pencil, pressure: true, size: 2), [1.4])
        XCTAssertEqual(InkWidth.widths([sample(0.7)], tool: .pen, brush: .pencil, pressure: true, size: 2), [1.2],
                       "no tilt reported: upright")
        XCTAssertEqual(InkWidth.opacities([sample(0.5, a: 90)], tool: .pen, brush: .pencil, pressure: true), [0.575])
        XCTAssertEqual(InkWidth.opacities([sample(0)], tool: .pen, brush: .pencil, pressure: true), [0.15])
        XCTAssertEqual(InkWidth.opacities([sample(0.2, a: 45)], tool: .pen, brush: .pencil, pressure: true), [0.32])
        XCTAssertEqual(InkWidth.opacities([sample(1)], tool: .pen, brush: .pencil, pressure: true), [1])
        XCTAssertNil(InkWidth.opacities([sample(0.5)], tool: .pen, brush: .pen, pressure: true), "only the Pencil shades")
    }

    func testTheShadeBands() {
        XCTAssertEqual(InkWidth.opacityBand(0.15), 0.33)
        XCTAssertEqual(InkWidth.opacityBand(0.33), 0.33)
        XCTAssertEqual(InkWidth.opacityBand(0.575), 0.66)
        XCTAssertEqual(InkWidth.opacityBand(0.661), 1)
        XCTAssertEqual(InkWidth.opacityBand(1), 1)
    }

    func testAHostSampleAsTheLawsReadIt() {
        XCTAssertEqual(InkWidth.sample([10, 20, 0.4, 8, 55, 120]), InkWidth.Sample(x: 10, y: 20, p: 0.4, a: 55))
        XCTAssertEqual(InkWidth.sample([10, 20, 0.4, 8]), InkWidth.Sample(x: 10, y: 20, p: 0.4, a: nil),
                       "an older shell's four values: no tilt")
    }
}

/// tests/shared/inkwidth.json: single-sample rows (`p`, `a`, and `w`, `o`
/// for that sample) and multi-sample rows (`samples`, `ws`, `os`, `at`).
private struct WidthFixture: Decodable {
    struct Shape: Decodable {
        let kind: String
    }

    struct Row: Decodable {
        let note: String
        let tool: String
        let brush: String?
        let pen: Bool
        let size: Double
        let p: Double?
        let a: Double?
        let w: Double?
        let o: Double?
        let shape: Shape?
        let samples: [[Double?]]?
        let ws: [Double]?
        let os: [Double]?
        let pSim: [Double]?
        let at: Int?

        private enum CodingKeys: String, CodingKey {
            case note, tool, brush, pen, size, p, a, w, o, shape, samples, ws, os, at
            case pSim = "p_sim"
        }
    }

    let rows: [Row]
}

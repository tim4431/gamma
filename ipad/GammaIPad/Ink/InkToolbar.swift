import SwiftUI
import UIKit

extension UIColor {
    /// `#rrggbb` (or `rgb[a](…)`, as gamma-ink allows); black when unreadable.
    convenience init(gammaHex text: String, alpha: CGFloat = 1) {
        var hex = text.trimmingCharacters(in: .whitespaces)
        if hex.hasPrefix("#") { hex.removeFirst() }
        if hex.count == 6, let n = UInt32(hex, radix: 16) {
            self.init(red: CGFloat((n >> 16) & 0xff) / 255, green: CGFloat((n >> 8) & 0xff) / 255,
                      blue: CGFloat(n & 0xff) / 255, alpha: alpha)
            return
        }
        let parts = text.components(separatedBy: CharacterSet(charactersIn: "0123456789.").inverted).compactMap(Double.init)
        if parts.count >= 3 {
            self.init(red: parts[0] / 255, green: parts[1] / 255, blue: parts[2] / 255, alpha: parts.count > 3 ? parts[3] * alpha : alpha)
            return
        }
        self.init(white: 0.12, alpha: alpha)
    }
}

/// The ink tools above a PDF or a notebook — the web's tool strip
/// (ink/InkLayer.jsx InkToolbar) as the iPad shows it: the presets (pens and
/// highlighters, each its own colour and width; press and hold one to change
/// them), the eraser, the hand (the Pencil scrolls), a new group, undo, redo.
/// The Pencil's double tap switches between the pen and the eraser.
struct InkToolbar: View {
    @ObservedObject var ink: InkSession

    private static let penColors = ["#1f1f1f", "#1d4ed8", "#dc2626", "#15803d", "#7c3aed", "#ea580c", "#6b7280"]
    private static let highlighterColors = ["#fde047", "#86efac", "#7dd3fc", "#f9a8d4", "#fdba74"]
    private static let penSizes: [Double] = [0.6, 1, 1.4, 2, 2.8, 4]
    private static let highlighterSizes: [Double] = [7, 10, 14, 18, 24]

    var body: some View {
        HStack(spacing: 4) {
            ForEach(ink.presets.indices, id: \.self) { i in
                preset(i)
            }
            Divider().frame(height: 22)
            toolButton(.eraser, "eraser", "Eraser")
            toolButton(.hand, "hand.raised", "Hand: the Pencil scrolls")
            Button { ink.newGroup() } label: { Image(systemName: "plus.square.on.square") }
                .help("New group: the next stroke starts a new handwriting note")
            Divider().frame(height: 22)
            Button { ink.undo() } label: { Image(systemName: "arrow.uturn.backward") }
                .disabled(!ink.canUndo).help("Undo ink")
            Button { ink.redo() } label: { Image(systemName: "arrow.uturn.forward") }
                .disabled(!ink.canRedo).help("Redo ink")
        }
        .buttonStyle(.borderless)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(.thinMaterial, in: Capsule())
    }

    private func toolButton(_ tool: InkTool, _ symbol: String, _ help: String) -> some View {
        Button { ink.tool = tool } label: {
            Image(systemName: symbol)
                .padding(5)
                .background(ink.tool == tool ? Color.accentColor.opacity(0.2) : .clear, in: RoundedRectangle(cornerRadius: 6))
        }
        .help(help)
    }

    private func preset(_ i: Int) -> some View {
        let p = ink.presets[i]
        let highlighter = p.string("kind") == "highlighter"
        let color = Color(UIColor(gammaHex: p.string("color")))
        return Button { ink.tool = .preset(i) } label: {
            VStack(spacing: 1) {
                Image(systemName: highlighter ? "highlighter" : "pencil.tip")
                RoundedRectangle(cornerRadius: 2).fill(color)
                    .frame(width: 16, height: max(2, min(6, (p.double("size") ?? 2) / (highlighter ? 4 : 1))))
            }
            .padding(4)
            .background(ink.tool == .preset(i) ? Color.accentColor.opacity(0.2) : .clear, in: RoundedRectangle(cornerRadius: 6))
        }
        .contextMenu {
            Section("Colour") {
                ForEach(highlighter ? Self.highlighterColors : Self.penColors, id: \.self) { hex in
                    Button { ink.setPreset(i, color: hex); ink.tool = .preset(i) } label: {
                        Label(hex == p.string("color") ? "✓ \(hex)" : hex, systemImage: "circle.fill")
                    }
                }
            }
            Section("Width") {
                ForEach(highlighter ? Self.highlighterSizes : Self.penSizes, id: \.self) { size in
                    Button("\(size, specifier: "%g") pt") { ink.setPreset(i, size: size); ink.tool = .preset(i) }
                }
            }
        }
        .help(highlighter ? "Highlighter" : "Pen")
    }
}

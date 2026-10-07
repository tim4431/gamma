"""Notes and the AI: live rendering, the outline, [[links]], the chat, the library agent."""
from scene import (AMBER, BAR, BAR_DARK, BLUE, CARD, EDGE, FIG_LINE, INK, MATH, MONO, MUTED, SOFT, YELLOW, anim,
                   bullet, button, chip, field, frame, hide, key, keytimes, panel, paper, pointer, show, typed)
from branding import _advance

LINK = '#e8f0fb'  # the pale blue behind a callout, a [[link]] chip, a citation
# A softer, tighter shadow for small things that float (a preview, a picker, a dragged row): frame()'s
# shadow is cut off at the edge of its filter region on anything short and wide.
LIFT = ('<defs><filter id="lift" x="-20%" y="-40%" width="140%" height="200%">'
        '<feDropShadow dx="0" dy="6" stdDeviation="9" flood-color="#000000" flood-opacity="0.13"/></filter></defs>')


def width(text, size):
    """How wide typed() lays `text` out, so a chip or a caret can sit right after it."""
    return sum(_advance(ch, size, False) for ch in text)


def label(text, x, y, size=22, fill=INK, weight=None):
    """Static text set to the width typed() would give it, so it lines up with typed text."""
    extra = f' font-weight="{weight}"' if weight else ''
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{extra} textLength="{width(text, size):.1f}" '
            f'lengthAdjust="spacing">{text}</text>')


def track(loop, *keys, origin=(0, 0)):
    """A translate from `origin` through (second, dx, dy) keys, held to the end and back to
    `origin` in the last 0.3 s (when breathe() or show() has it faded out)."""
    pts = [(0, *origin), *keys]
    pts += [(loop - 0.3, pts[-1][1], pts[-1][2]), (loop, *origin)]
    values = ';'.join(f'{x} {y}' for _, x, y in pts)
    return (f'<animateTransform attributeName="transform" type="translate" values="{values}" '
            f'keyTimes="{keytimes(loop, *(t for t, _, _ in pts))}" dur="{loop}s" repeatCount="indefinite"/>')


def breathe(loop):
    """Opacity for content that moves during the scene: out before the restart, back in after it."""
    return anim('opacity', '0;1;1;0;0', loop, 0, 0.25, loop - 0.45, loop - 0.3, loop)


def page_icon(x, y):
    """A small page glyph, its top left at (x, y)."""
    return (f'<path d="M{x} {y} h10 l6 6 v14 h-16 z M{x + 10} {y} v6 h6" fill="none" stroke="{MUTED}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>')


def handle(x, y):
    """The ⋮⋮ drag handle, centred at (x, y)."""
    return ''.join(f'<circle cx="{x + dx}" cy="{y + dy}" r="2.6" fill="{BAR_DARK}"/>'
                   for dx in (-4.5, 4.5) for dy in (-8, 0, 8))


def notes():
    """Raw markdown while the caret is in a block; rendered once it leaves."""
    loop = 13
    X = 180
    y1, y2, y3 = 268, 392, 506
    body = [LIFT]
    for y, t in ((y1, 0.95), (y2, 4.3), (y3, 8.4)):
        body.append('<g>' + bullet(152, y - 9) + show(loop, t) + '</g>')
    # A display equation typed as LaTeX, a live preview over it, rendered when the caret leaves.
    body.append('<g>' + typed(r'$$ N(t) = N_0 e^{-\Gamma t} $$', X, y1, loop, 1.2, 3.8, 30, family=MONO)
                + hide(loop, 4.3) + '</g>')
    body.append('<g>'
                f'<rect x="{X}" y="{y1 - 100}" width="282" height="62" rx="10" fill="{CARD}" stroke="{EDGE}" filter="url(#lift)"/>'
                f'<text x="{X + 24}" y="{y1 - 58}" font-family="{MATH}" font-size="32" font-style="italic" fill="{INK}">N(t) = N'
                '<tspan font-size="21" dy="7">0</tspan><tspan dy="-7" dx="4">e</tspan><tspan font-size="21" dy="-15" dx="2">−Γt</tspan></text>'
                + show(loop, 2.1, 4.3) + '</g>')
    body.append('<g>'
                f'<text x="{X}" y="{y1 + 4}" font-family="{MATH}" font-size="50" font-style="italic" fill="{INK}">N(t) = N'
                '<tspan font-size="32" dy="11">0</tspan><tspan dy="-11" dx="6">e</tspan>'
                f'<tspan font-size="32" dy="-24" dx="2">−<tspan fill="{AMBER}" font-weight="600">Γ</tspan>t</tspan></text>'
                + show(loop, 4.3) + '</g>')
    # A callout typed as markdown becomes a blue box.
    body.append('<g>' + typed('> [!note] Ask Maya about the detuning sweep', X, y2, loop, 4.6, 7.8, 26, family=MONO)
                + hide(loop, 8.35) + '</g>')
    body.append('<g>'
                f'<rect x="{X - 6}" y="{y2 - 54}" width="660" height="100" rx="12" fill="{LINK}"/>'
                f'<rect x="{X - 6}" y="{y2 - 54}" width="6" height="100" rx="3" fill="{BLUE}"/>'
                f'<circle cx="{X + 24}" cy="{y2 - 26}" r="10" fill="none" stroke="{BLUE}" stroke-width="2.2"/>'
                f'<path d="M{X + 24} {y2 - 27} v7 M{X + 24} {y2 - 32} v0.5" stroke="{BLUE}" stroke-width="2.4" stroke-linecap="round"/>'
                f'<text x="{X + 46}" y="{y2 - 18}" font-size="21" font-weight="600" fill="{BLUE}">Note</text>'
                f'<text x="{X + 14}" y="{y2 + 26}" font-size="26" fill="{INK}">Ask Maya about the detuning sweep</text>'
                + show(loop, 8.35) + '</g>')
    # A to-do typed as markdown becomes a checkbox, then gets ticked.
    todo = 're-run the sweep'
    body.append('<g>' + typed('- [ ] ' + todo, X, y3, loop, 8.6, 9.9, 26, family=MONO)
                + hide(loop, 10.45) + '</g>')
    box = f'<rect x="{X}" y="{y3 - 23}" width="26" height="26" rx="6" '
    body.append('<g>' + box + f'fill="{CARD}" stroke="{BAR_DARK}" stroke-width="2"/>'
                + f'<g>{label(todo, X + 42, y3, 26)}{show(loop, 0, 11.25)}</g>'
                + show(loop, 10.45) + '</g>')
    body.append('<g>' + box + f'fill="{AMBER}" stroke="{AMBER}" stroke-width="2"/>'
                f'<path d="M{X + 6} {y3 - 10} l5 6 l10 -12" fill="none" stroke="#ffffff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
                + label(todo, X + 42, y3, 26, fill=MUTED)
                + f'<path d="M{X + 42} {y3 - 9} h{width(todo, 26):.0f}" stroke="{MUTED}" stroke-width="1.8"/>'
                + show(loop, 11.25) + '</g>')
    rest = (1150, 480)
    body.append(pointer(
        [rest, rest, (188, y1 - 9), (188, y1 - 9), (250, y1 + 40), (250, y1 + 40), (188, y2 - 9), (188, y2 - 9),
         (250, y2 + 64), (250, y2 + 64), (188, y3 - 9), (188, y3 - 9), (300, y3 + 34), (300, y3 + 34),
         (820, 470), (820, 470), (X + 13, y3 - 10)],
        loop,
        [0, 0.3, 0.9, 1.0, 1.3, 3.7, 4.2, 4.35, 4.6, 7.8, 8.25, 8.4, 8.6, 9.9, 10.3, 10.6, 11.05],
        press=(0.95, 4.3, 8.35, 10.4, 11.2)))
    scene = panel(100, 50, 1200, 520, 'Notes') + ''.join(body)
    return frame('Notes render as you type',
                 'In a notes window a display equation is typed as LaTeX, with a live preview floating above it, and '
                 'renders in place when the caret leaves. A line typed as “> [!note] Ask Maya about the detuning sweep” '
                 'becomes a blue callout box, and “- [ ] re-run the sweep” becomes a checkbox, which is then ticked.', scene)


def outline():
    """Shift+Enter makes a note, Tab nests it, the ⋮⋮ handle moves one."""
    loop = 11
    size, step = 26, 78
    rows = [206 + i * step for i in range(4)]
    HX, BX, TX = 336, 366, 390
    A, B, C, N = 'Calibrate the 420 nm laser', 'Measure T1 of the clock state', 'Write up the blockade data', 'check the beam waist'

    def line(text):
        return bullet(BX, rows[0] - 9) + label(text, TX, rows[0], size)

    def at(i, inner, *motion):
        return f'<g transform="translate(0 {rows[i] - rows[0]})"><g>{inner}{"".join(motion)}</g></g>'

    end_a = TX + width(A, size)
    insert = ((1.8, 0, 0), (2.1, 0, step))
    drop = ((6.3, 0, step), (6.9, 0, 2 * step))
    rows_svg = [
        at(0, line(A), track(loop, (6.3, 0, 0), (6.9, 0, step))),
        at(1, line(B), track(loop, *insert, *drop)),
        # The new note: typed, then indented by Tab; it moves with the note it is under.
        at(1, '<g>' + bullet(BX, rows[0] - 9) + typed(N, TX, rows[0], loop, 2.2, 3.6, size)
           + track(loop, (4.2, 0, 0), (4.45, 40, 0)) + '</g>' + show(loop, 1.9),
           track(loop, (6.3, 0, 0), (6.9, 0, step))),
        # The dragged note: lifted on a card while it moves up past the others.
        at(2, f'<g><rect x="{HX - 24}" y="{rows[0] - 42}" width="{TX + width(C, size) + 46 - HX:.0f}" height="60" rx="12" '
              f'fill="{CARD}" stroke="{EDGE}" filter="url(#lift)"/>{show(loop, 5.75, 7.2)}</g>'
              f'<g>{handle(HX, rows[0] - 10)}{show(loop, 5.0, 7.6)}</g>' + line(C),
           track(loop, *insert, (5.9, 0, step), (7.0, 0, -2 * step))),
    ]
    body = [LIFT, '<g>' + ''.join(rows_svg) + breathe(loop) + '</g>']
    # The caret at the end of the first note, until Shift+Enter.
    body.append(f'<g><rect x="{end_a + 4:.0f}" y="{rows[0] - 24}" width="2.5" height="32" fill="{AMBER}"/>'
                + show(loop, 1.0, 1.8) + '</g>')
    body.append('<g>' + key(end_a + 36, rows[0] - 30, 'Shift+Enter', 20) + show(loop, 1.5, 2.7) + '</g>')
    body.append('<g>' + key(TX + 40 + width(N, size) + 36, rows[1] - 30, 'Tab', 20) + show(loop, 3.95, 5.0) + '</g>')
    rest = (1000, 500)
    grab = HX - 16
    body.append(pointer(
        [rest, rest, (end_a + 6, rows[0] - 10), (end_a + 6, rows[0] - 10), (960, 500), (960, 500),
         (grab, rows[3] - 10), (grab, rows[3] - 10), (grab, rows[0] - 10), (grab, rows[0] - 10), rest],
        loop, [0, 0.3, 0.9, 1.1, 1.7, 4.6, 5.3, 5.9, 7.0, 7.5, 8.5], press=(1.0, 5.75)))
    scene = panel(290, 50, 820, 520, 'Notes') + ''.join(body)
    return frame('Notes are a nested outline',
                 'Three one-line notes. Shift+Enter starts a new note under the first and a line is typed; Tab indents '
                 'it under the note above. Then the ⋮⋮ handle of the last note is dragged up, and it moves above the '
                 'first while the others slide down.', scene)


def page_links():
    """[[ opens a picker; the picked page becomes a chip, and that page lists the note as Linked from."""
    loop = 10
    y, size = 256, 24
    lead = 'Compare the fidelity with '
    cx = 156 + width(lead, size)
    body = [LIFT, bullet(132, 186) + f'<rect x="156" y="180" width="400" height="11" rx="5.5" fill="{BAR}"/>',
            bullet(132, y - 9)]
    body.append(typed(lead, 156, y, loop, 0.8, 2.8, size))
    body.append('<g>' + typed('[[', cx, y, loop, 3.1, 3.4, size) + hide(loop, 4.65) + '</g>')
    # The picker: three page titles, the first one marked.
    titles = ['Rydberg arrays', 'Rydberg blockade review', 'Neutral-atom gates']
    px, pw = 352, 340
    pick = [f'<rect x="{px}" y="{y + 20}" width="{pw}" height="166" rx="12" fill="{CARD}" stroke="{EDGE}" filter="url(#lift)"/>',
            f'<rect x="{px + 8}" y="{y + 28}" width="{pw - 16}" height="48" rx="8" fill="{SOFT}"/>']
    for i, title in enumerate(titles):
        ty = y + 28 + i * 50
        pick.append(page_icon(px + 24, ty + 14) + f'<text x="{px + 54}" y="{ty + 32}" font-size="21" fill="{INK}">{title}</text>')
    body.append('<g>' + ''.join(pick) + show(loop, 3.55, 4.65) + '</g>')
    body.append('<g>' + chip(cx, y - 27, 'Rydberg arrays', fill=LINK, ink=BLUE, size=21) + show(loop, 4.65) + '</g>')
    # The page it links to lists the note under Linked from.
    rx = 780
    snippet = 'Compare the fidelity with'
    backlink = (f'<path d="M{cx + 206:.0f} {y - 10} C720 {y - 10} 720 404 {rx + 22} 404" fill="none" stroke="{AMBER}" '
                f'stroke-width="2.5" stroke-dasharray="2 9" stroke-linecap="round"/>'
                f'<text x="{rx + 36}" y="306" font-size="18" font-weight="600" fill="{MUTED}">▾ Linked from 1 page</text>'
                + page_icon(rx + 40, 330) + f'<text x="{rx + 68}" y="347" font-size="20" font-weight="600" fill="{INK}">Lab notes</text>'
                f'<rect x="{rx + 36}" y="370" width="468" height="66" rx="12" fill="{SOFT}"/>'
                + label(snippet, rx + 56, 410, 20)
                + chip(rx + 64 + width(snippet, 20), 387, 'Rydberg arrays', fill=LINK, ink=BLUE, size=16))
    body.append('<g>' + backlink + show(loop, 5.3) + '</g>')
    rest = (1180, 520)
    body.append(pointer([rest, rest, (px + 220, y + 52), (px + 220, y + 52), (1080, 488)], loop,
                        [0, 3.6, 4.3, 4.8, 5.8], press=(4.5,)))
    scene = (panel(80, 60, 640, 500, 'Lab notes')
             + panel(rx, 60, 540, 500, 'Rydberg arrays')
             + bullet(rx + 46, 186) + f'<rect x="{rx + 68}" y="180" width="360" height="11" rx="5.5" fill="{BAR}"/>'
             + bullet(rx + 46, 228) + f'<rect x="{rx + 68}" y="222" width="280" height="11" rx="5.5" fill="{BAR}"/>'
             + ''.join(body))
    return frame('Link notes and pages',
                 'In a note “[[” is typed and a picker lists three page titles; “Rydberg arrays” is clicked and becomes a '
                 'link chip in the line. The page Rydberg arrays then lists that note under “Linked from”.', scene)


def figure(x, y, w, h, stroke_w=3):
    """A plot: axes and a curve that rises to a plateau."""
    return (f'<path d="M{x} {y} V{y + h} H{x + w}" fill="none" stroke="{FIG_LINE}" stroke-width="{stroke_w * 0.7:.1f}"/>'
            f'<path d="M{x} {y + h * 0.95:.0f} C{x + w * 0.18:.0f} {y + h * 0.3:.0f} {x + w * 0.3:.0f} {y + h * 0.22:.0f} '
            f'{x + w * 0.45:.0f} {y + h * 0.22:.0f} H{x + w * 0.8:.0f} C{x + w * 0.88:.0f} {y + h * 0.22:.0f} '
            f'{x + w * 0.92:.0f} {y + h * 0.5:.0f} {x + w:.0f} {y + h * 0.62:.0f}" fill="none" stroke="{AMBER}" '
            f'stroke-width="{stroke_w}" stroke-linecap="round"/>')


def chat():
    """A region of the page goes to the chat; the answer's citation jumps back to the page."""
    loop = 13
    body = []
    # The paper: a figure, and the lines under it (the third is the one the answer cites).
    paper_svg = (paper(80, 50, 560, 520, 3)
                 + '<rect x="150" y="262" width="420" height="150" rx="8" fill="none" stroke="#d6d3cb" stroke-width="2"/>'
                 + figure(180, 282, 360, 110)
                 # The cited line turns yellow when the citation is clicked.
                 + f'<rect x="122" y="{488 - 8}" width="412" height="25" rx="4" fill="{YELLOW}" opacity="0">'
                 + anim('opacity', '0;0;0.9;0.9;0', loop, 0, 9.55, 9.75, loop - 0.3, loop) + '</rect>')
    for i, length in enumerate((1, 0.92, 0.86, 0.6)):
        paper_svg += f'<rect x="130" y="{440 + i * 24}" width="{460 * length:.0f}" height="9" rx="4.5" fill="{BAR}"/>'
    paper_svg += f'<text x="360" y="552" text-anchor="middle" font-size="14" fill="{MUTED}">4</text>'
    # Ctrl+drag: a dashed rectangle over the figure.
    x0, y0, x1, y1 = 140, 252, 580, 422
    body.append('<g>' + key(500, 92, 'Ctrl') + show(loop, 0.7, 2.5) + '</g>')
    body.append(f'<rect x="{x0}" y="{y0}" width="0" height="0" fill="{AMBER}" fill-opacity="0.06" stroke="{AMBER}" '
                f'stroke-width="2.5" stroke-dasharray="8 6">'
                + anim('width', f'0;0;{x1 - x0};{x1 - x0}', loop, 0, 1.2, 2.3, loop)
                + anim('height', f'0;0;{y1 - y0};{y1 - y0}', loop, 0, 1.2, 2.3, loop)
                + show(loop, 1.15, 2.6) + '</rect>')
    # The region lands as a thumbnail over the message box.
    thumb = (f'<rect x="0" y="0" width="92" height="58" rx="8" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
             + figure(12, 10, 68, 38, stroke_w=2))
    body.append('<g>' + thumb + track(loop, (2.5, 300, 300), (3.0, 736, 396), origin=(300, 300))
                + show(loop, 2.5, 6.35) + '</g>')
    q = 'What does the 3 µs plateau mean?'
    body.append('<g>' + typed(q, 756, 510, loop, 3.3, 5.7, 22) + hide(loop, 6.35) + '</g>')
    send = (f'<circle cx="1254" cy="502" r="19" fill="{AMBER}"/>'
            f'<path d="M1254 512 V492 M1246 499 L1254 491 L1262 499" fill="none" stroke="#ffffff" stroke-width="2.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')
    # Sent: the question as a bubble, with its picture.
    qw = width(q, 22)
    bx = 1284 - qw - 36
    body.append('<g>'
                f'<rect x="{bx - 74:.0f}" y="152" width="62" height="40" rx="6" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
                + figure(bx - 66, 159, 46, 26, stroke_w=1.6)
                + f'<rect x="{bx:.0f}" y="148" width="{qw + 36:.0f}" height="48" rx="14" fill="{SOFT}"/>'
                + label(q, bx + 18, 180, 22) + show(loop, 6.4) + '</g>')
    # The answer streams in, with one real citation.
    bars = ((520, 6.9), (480, 7.2), (505, 7.5), (470, 7.8), (240, 8.1))
    for i, (w, start) in enumerate(bars):
        body.append(f'<rect x="736" y="{232 + i * 30}" width="0" height="11" rx="5.5" fill="{BAR}">'
                    + anim('width', f'0;0;{w};{w}', loop, 0, start, start + 0.3, loop) + show(loop, start) + '</rect>')
    cy = 232 + 4 * 30 + 5
    body.append('<g>' + chip(736 + 240 + 14, cy - 17, 'p. 4', fill=LINK, ink=BLUE, size=18) + show(loop, 8.45) + '</g>')
    cite = (736 + 240 + 14 + 33, cy)
    rest = (1100, 560)
    body.append(pointer(
        [rest, rest, (x0, y0), (x0, y0), (x1, y1), (x1, y1), (1254, 502), (1254, 502), cite, cite, (1110, 420)],
        loop, [0, 0.4, 1.0, 1.2, 2.3, 5.4, 6.0, 6.5, 9.0, 9.7, 10.3], press=(1.1, 6.3, 9.45)))
    scene = (paper_svg
             + panel(700, 50, 620, 520, 'Chat')
             + field(736, 470, 548, 64) + send
             + ''.join(body))
    return frame('Ask the AI about the paper',
                 'Ctrl+drag draws a dashed rectangle over a figure in the paper, and the region lands as a thumbnail over '
                 'the chat’s message box. “What does the 3 µs plateau mean?” is typed and sent; the answer streams in '
                 'with a citation “p. 4”, and clicking it highlights the cited line on the page.', scene)


def agent():
    """The assistant shows a rename and waits; allowed, the folder's titles change."""
    loop = 12
    body = []
    papers = [('logical-processor.pdf', 'Lukin 2024'), ('gas-microscopy.pdf', 'Bloch 2023'),
              ('blockade-gates.pdf', 'Saffman 2022'), ('atom-assembly.pdf', 'Browaeys 2021')]
    cards = []
    for i, (old, new) in enumerate(papers):
        x, y = 96 + (i % 2) * 282, 150 + (i // 2) * 200
        t = 7.3 + i * 0.3
        cards.append(f'<rect x="{x}" y="{y}" width="266" height="184" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
                     f'<rect x="{x}" y="{y}" width="266" height="184" rx="12" fill="none" stroke="{AMBER}" stroke-width="2.5" opacity="0">'
                     + anim('opacity', '0;0;1;0;0', loop, 0, t, t + 0.1, t + 0.8, loop) + '</rect>'
                     f'<rect x="{x + 20}" y="{y + 18}" width="64" height="84" rx="4" fill="{SOFT}" stroke="{EDGE}"/>'
                     + ''.join(f'<rect x="{x + 28}" y="{y + 30 + j * 12}" width="{[40, 48, 44, 48, 30][j]}" height="5" rx="2.5" fill="{BAR}"/>'
                               for j in range(5))
                     + f'<g>{label(old, x + 20, y + 140, 19, INK, 600)}{hide(loop, t)}</g>'
                     + f'<g>{label(new, x + 20, y + 140, 19, INK, 600)}{show(loop, t)}</g>'
                     + f'<rect x="{x + 20}" y="{y + 156}" width="120" height="8" rx="4" fill="{BAR}"/>')
    # The request, typed and sent.
    req = 'rename these AuthorYear'
    body.append('<g>' + typed(req, 776, 514, loop, 0.8, 2.6, 20) + hide(loop, 3.25) + '</g>')
    rw = width(req, 20)
    body.append('<g>' + f'<rect x="{1304 - rw - 36:.0f}" y="140" width="{rw + 36:.0f}" height="46" rx="14" fill="{SOFT}"/>'
                + label(req, 1304 - rw - 18, 170, 20) + show(loop, 3.3) + '</g>')
    # The approval card: one rename as a word diff, and the answers.
    old, new = papers[0]
    cy = 206
    ow = width(old, 19)
    card = (f'<rect x="756" y="{cy}" width="548" height="202" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
            f'<text x="780" y="{cy + 34}" font-size="15" fill="{MUTED}">Approval needed · Rename pages</text>'
            f'<text x="780" y="{cy + 66}" font-size="19" font-weight="600" fill="{INK}">Rename “{old}”</text>'
            + label(old, 780, cy + 106, 19, MUTED)
            + f'<path d="M780 {cy + 99} h{ow:.0f}" stroke="{MUTED}" stroke-width="1.6"/>'
            f'<rect x="{780 + ow + 12:.0f}" y="{cy + 84}" width="{width(new, 19) + 16:.0f}" height="30" rx="6" fill="{YELLOW}"/>'
            + label(new, 780 + ow + 20, cy + 106, 19, INK))
    buttons = (button(780, cy + 134, 'Allow once', primary=True, size=17)
               + button(924, cy + 134, 'Allow in this chat', size=17)
               + button(1144, cy + 134, 'Don’t allow', size=17))
    body.append('<g>' + card + f'<g>{buttons}{hide(loop, 6.75)}</g>'
                + f'<g><text x="780" y="{cy + 160}" font-size="17" fill="{MUTED}">Allowed. The assistant goes on…</text>'
                + show(loop, 6.8) + '</g>' + show(loop, 4.0) + '</g>')
    # What changed, with the way back.
    pill = (f'<rect x="756" y="422" width="548" height="36" rx="18" fill="{SOFT}"/>'
            f'<text x="778" y="446" font-size="17" fill="{MUTED}">Changed in your library · 4</text>'
            f'<path d="M1184 436 a7 7 0 1 1 2 6 M1184 430 v6 h6" fill="none" stroke="{INK}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<text x="1208" y="446" font-size="17" font-weight="600" fill="{INK}">Revert all</text>')
    body.append('<g>' + pill + show(loop, 8.6) + '</g>')
    rest = (1200, 450)
    body.append(pointer([rest, rest, (1274, 506), (1274, 506), (1112, cy + 152), (1112, cy + 152), (700, 300)],
                        loop, [0, 2.4, 2.9, 3.4, 5.9, 6.9, 7.5], press=(3.15, 6.65)))
    send = (f'<circle cx="1274" cy="506" r="18" fill="{AMBER}"/>'
            f'<path d="M1274 515 V497 M1267 503 L1274 496 L1281 503" fill="none" stroke="#ffffff" stroke-width="2.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')
    scene = (panel(60, 50, 620, 520, 'Neutral atoms') + ''.join(cards)
             + panel(720, 50, 620, 520, 'Chat') + field(756, 476, 548, 60) + send
             + ''.join(body))
    return frame('The assistant asks before changing anything',
                 'A folder of four papers. In its chat “rename these AuthorYear” is typed and sent. The reply shows a card '
                 'with one rename, the old title struck through and the new one highlighted, and the answers Allow once, '
                 'Allow in this chat and Don’t allow. Allow in this chat is clicked; the four titles change to Lukin 2024, '
                 'Bloch 2023, Saffman 2022 and Browaeys 2021, and a line says “Changed in your library · 4” with Revert all.',
                 scene)


SCENES = [
    ('notes', notes),
    ('outline', outline),
    ('page-links', page_links),
    ('chat', chat),
    ('agent', agent),
]

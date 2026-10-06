"""The library: add a paper, folders and labels, search, metadata and citations, sharing."""
from scene import (AMBER, BAR, BAR_DARK, BLUE, CARD, CHIP, CHIP_INK, EDGE, GREEN, INK, MONO, MUTED, SOFT, YELLOW,
                   anim, block, bullet, button, chip, field, frame, hide, move, panel, paper, pointer, show, typed,
                   typewriter)


# ---- Pieces only these scenes draw ----------------------------------------------------

POP = ('<defs><filter id="pop" x="-20%" y="-20%" width="140%" height="170%">'
       '<feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#000000" flood-opacity="0.10"/></filter></defs>')


def popover(x, y, w, h):
    """A popover card; its shadow filter (POP, put once in the scene) leaves room below a short box."""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#pop)"/>'


def window(x, y, w, h, tab=None):
    """The app: a white window with a top bar (a tab on the left, then + and the link button on the right)."""
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#shadow)"/>'
           f'<path d="M{x} {y + 68} H{x + w}" stroke="{EDGE}" stroke-width="1.5"/>')
    if tab:
        out += f'<text x="{x + 36}" y="{y + 43}" font-size="21" font-weight="600" fill="{INK}">{tab}</text>'
    return out + plus_icon(x + w - 102, y + 34) + link_icon(x + w - 46, y + 34)


def plus_icon(cx, cy):
    return f'<path d="M{cx - 10} {cy} H{cx + 10} M{cx} {cy - 10} V{cy + 10}" stroke="{INK}" stroke-width="2.6" stroke-linecap="round"/>'


def link_icon(cx, cy):
    return (f'<g transform="translate({cx} {cy}) rotate(-45)" fill="none" stroke="{INK}" stroke-width="2.4">'
            '<rect x="-14" y="-6" width="16" height="12" rx="6"/><rect x="-2" y="-6" width="16" height="12" rx="6"/></g>')


def icon_ring(cx, cy, loop, start, end=None):
    """The pressed state of a top-bar icon button: an amber-tinted square behind it."""
    return (f'<g><rect x="{cx - 22}" y="{cy - 22}" width="44" height="44" rx="10" fill="{AMBER}" fill-opacity="0.16" '
            f'stroke="{AMBER}" stroke-width="1.5"/>' + show(loop, start, end) + '</g>')


def card(x, y, w=190, h=250, seed=0, thumb=150):
    """A paper in the library grid: a page thumbnail, then two title bars."""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>',
           f'<rect x="{x + 10}" y="{y + 10}" width="{w - 20}" height="{thumb}" rx="8" fill="{SOFT}"/>',
           f'<rect x="{x + 34}" y="{y + 22}" width="{w - 68}" height="{thumb - 12}" rx="3" fill="{CARD}"/>',
           f'<rect x="{x + 46}" y="{y + 36}" width="{(w - 92) * 0.7:.0f}" height="6" rx="3" fill="{BAR_DARK}"/>']
    for i in range(6):
        length = [1, 0.8, 1, 0.9, 0.6, 1, 0.75][(i + seed) % 7]
        out.append(f'<rect x="{x + 46}" y="{y + 54 + i * 14}" width="{(w - 92) * length:.0f}" height="5" rx="2.5" fill="{BAR}"/>')
    t = y + thumb + 28
    out.append(f'<rect x="{x + 16}" y="{t}" width="{(w - 32) * [0.88, 0.7, 0.8][seed % 3]:.0f}" height="11" rx="5" fill="{BAR_DARK}"/>')
    out.append(f'<rect x="{x + 16}" y="{t + 22}" width="{(w - 32) * [0.5, 0.62, 0.42][seed % 3]:.0f}" height="9" rx="4.5" fill="{BAR}"/>')
    return ''.join(out)


def key(x, y, text, size=24):
    """A keyboard key, for a shortcut or Enter."""
    w = int(len(text) * size * 0.6 + 34)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{size + 24}" rx="10" fill="{CARD}" stroke="{BAR}" stroke-width="1.5"/>'
            f'<rect x="{x}" y="{y + size + 20}" width="{w}" height="4" rx="2" fill="{BAR}"/>'
            f'<text x="{x + w / 2:.0f}" y="{y + size + 6}" text-anchor="middle" font-family="{MONO}" font-size="{size}" fill="{INK}">{text}</text>')


def file_icon(x, y):
    """A PDF file, as a desktop shows one."""
    return (f'<path d="M{x} {y} H{x + 44} L{x + 62} {y + 18} V{y + 80} H{x} Z" fill="{CARD}" stroke="{BAR_DARK}" stroke-width="1.8" stroke-linejoin="round"/>'
            f'<path d="M{x + 44} {y} V{y + 18} H{x + 62}" fill="none" stroke="{BAR_DARK}" stroke-width="1.8" stroke-linejoin="round"/>'
            f'<rect x="{x + 8}" y="{y + 48}" width="40" height="18" rx="4" fill="#d9534f"/>'
            f'<text x="{x + 28}" y="{y + 62}" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">PDF</text>')


def glow(x, y, w, h, loop, start, end=None, rx=12):
    """An amber outline that marks what just changed."""
    return (f'<g><rect x="{x - 4}" y="{y - 4}" width="{w + 8}" height="{h + 8}" rx="{rx + 3}" fill="none" stroke="{AMBER}" stroke-width="2.5"/>'
            + show(loop, start, end) + '</g>')


# ---- Scenes -----------------------------------------------------------------------------

def add_paper():
    """+ in the top bar, an arXiv link, Enter: the paper lands in the library and opens. Then two files dropped in."""
    loop = 13
    wx, wy, ww, wh = 120, 40, 1160, 540
    plus = (wx + ww - 102, wy + 34)
    slots = [165 + i * 220 for i in range(5)]
    cy = 214
    body = []
    # The library grid: two papers, then the new one, then the two dropped files.
    grid = card(slots[0], cy, seed=0) + card(slots[1], cy, seed=1)
    grid += '<g>' + card(slots[2], cy, seed=2) + show(loop, 4.3) + '</g>' + glow(slots[2], cy, 190, 250, loop, 4.3, 5.3)
    grid += ''.join('<g>' + card(slots[3 + i], cy, seed=3 + i) + show(loop, 10.5 + i * 0.12) + '</g>' for i in range(2))
    grid += glow(slots[3], cy, 410, 250, loop, 10.5, 11.6)
    body.append('<g>' + grid + hide(loop, 6.0, 8.5) + '</g>')
    # The opened paper: a page beside its notes.
    body.append('<g>' + f'<rect x="{wx + 2}" y="{wy + 70}" width="{ww - 4}" height="{wh - 72}" rx="12" fill="{CARD}"/>'
                + paper(170, 132, 470, 420, 8, seed=2)
                + panel(690, 132, 540, 420, 'Notes', bullet(736, 252) + f'<rect x="756" y="246" width="330" height="11" rx="5" fill="{BAR}"/>'
                        + bullet(736, 300) + f'<rect x="756" y="294" width="250" height="11" rx="5" fill="{BAR}"/>')
                + show(loop, 6.0, 8.5) + '</g>')
    # The + popover: a link typed in, Enter.
    body.append(icon_ring(*plus, loop, 1.0, 4.0))
    px, py = wx + ww - 470, wy + 70
    body.append('<g>' + popover(px, py, 440, 190)
                + '<g>' + field(px + 20, py + 20, 400, 52, 'Paste a URL, DOI or arXiv id') + hide(loop, 1.5) + '</g>'
                + f'<rect x="{px + 20}" y="{py + 20}" width="400" height="52" rx="12" fill="none" stroke="{AMBER}" stroke-width="1.5"/>'
                + typed('arxiv.org/abs/2406.01234', px + 40, py + 53, loop, 1.7, 3.4, 20)
                + f'<text x="{px + 40}" y="{py + 118}" font-size="18" fill="{MUTED}">Upload files…</text>'
                + f'<text x="{px + 40}" y="{py + 158}" font-size="18" fill="{MUTED}">New page</text>'
                + '<g>' + key(px + 326, py + 26, 'Enter', 16) + show(loop, 3.6) + '</g>'
                + show(loop, 1.1, 4.0) + '</g>')
    # Two PDFs dragged in from outside the window; the window lights up as a drop target.
    body.append(f'<g><rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="14" fill="{AMBER}" fill-opacity="0.05" '
                f'stroke="{AMBER}" stroke-width="3" stroke-dasharray="12 8"/>' + show(loop, 9.4, 10.4) + '</g>')
    fx, fy = 1300, 440
    drag = (1030 - (fx + 30), 330 - (fy + 28))
    body.append('<g><g>' + file_icon(fx + 18, fy + 14) + file_icon(fx, fy) + move(loop, *drag, 9.2, 10.3) + '</g>'
                + show(loop, 8.7, 10.45) + '</g>')
    body.append(pointer([(640, 520), (640, 520), plus, plus, (slots[2] + 95, cy + 110), (slots[2] + 95, cy + 110),
                         (fx + 30, fy + 28), (fx + 30, fy + 28), (1030, 330), (1030, 330), (640, 520)],
                        loop, [0, 0.2, 0.9, 4.5, 5.2, 7.6, 8.9, 9.2, 10.3, 11.2, 12.4], press=(1.0, 5.4, 5.6, 9.1)))
    tab = (f'<g><text x="{wx + 36}" y="{wy + 43}" font-size="21" font-weight="600" fill="{INK}">Library</text>' + hide(loop, 6.0, 8.5) + '</g>'
           f'<g><rect x="{wx + 36}" y="{wy + 28}" width="220" height="12" rx="6" fill="{BAR_DARK}"/>' + show(loop, 6.0, 8.5) + '</g>')
    scene = POP + window(wx, wy, ww, wh) + tab + ''.join(body)
    return frame('Add a paper from a link, or drop PDFs into the window',
                 'The + button in the top bar is clicked and an arXiv link is pasted into its field; on Enter the paper '
                 'appears in the library and opens beside its notes. Then two PDF files are dragged into the window and '
                 'become two more papers.', scene)


def folder_tile(x, y, name, count):
    """A folder in the library's folder row: the icon, its name, a page count."""
    return (f'<rect x="{x}" y="{y}" width="230" height="62" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
            f'<rect x="{x + 20}" y="{y + 19}" width="16" height="10" rx="3" fill="{CHIP}"/>'
            f'<rect x="{x + 20}" y="{y + 23}" width="34" height="22" rx="4" fill="{CHIP}"/>'
            f'<text x="{x + 68}" y="{y + 39}" font-size="20" font-weight="500" fill="{INK}">{name}</text>'
            + badge(x + 196, y + 31, count))


def badge(cx, cy, text, fill=SOFT, ink=MUTED):
    return (f'<rect x="{cx - 17}" y="{cy - 13}" width="34" height="26" rx="13" fill="{fill}"/>'
            f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" font-size="16" font-weight="600" fill="{ink}">{text}</text>')


def library():
    """A card dragged onto a folder; a label typed under another card; the label clicked to filter."""
    loop = 12
    wx, wy, ww, wh = 120, 40, 1160, 540
    slots = [165 + i * 220 for i in range(5)]
    cy, ch = 222, 282
    lab = cy + 224  # the label row under a card's title
    body = []
    # The folder row: Quantum's count goes from 2 to 3 when the card lands.
    qx, qy = 165, 128
    body.append(folder_tile(qx, qy, 'Quantum', '2')
                + '<g>' + badge(qx + 196, qy + 31, '3', AMBER, '#ffffff') + show(loop, 2.5) + '</g>'
                + glow(qx, qy, 230, 62, loop, 2.45, 3.4) + folder_tile(415, qy, 'Reviews', '5'))
    # The cards; the label filter dims all but the two carrying to-read.
    dim = anim('opacity', '1;1;0.25;0.25;1;1', loop, 0, 7.0, 7.25, loop - 0.3, loop - 0.15, loop)
    for i, x in enumerate(slots):
        c = card(x, cy, h=ch, seed=i, thumb=140)
        if i == 2:  # the dragged one greys while its ghost travels
            c = '<g>' + c + anim('opacity', '1;1;0.4;0.4;1;1', loop, 0, 1.0, 1.1, 2.4, 2.5, loop) + '</g>'
        if i == 3:
            c += chip(x + 12, lab, 'to-read', size=16)
        body.append('<g>' + c + (dim if i in (0, 2, 4) else '') + '</g>')
    # Both to-read chips turn amber while the filter is on.
    on = show(loop, 6.85)
    body.append('<g>' + chip(slots[3] + 12, lab, 'to-read', AMBER, '#ffffff', 16) + on + '</g>')
    # The ghost: a half-size copy of the card follows the pointer to the folder.
    gx, gy = slots[2] + 95, cy + 120
    fx, fy = qx + 120, qy + 34
    body.append('<g><g>' + f'<g transform="translate({gx - 48} {gy - 70}) scale(0.5)" filter="url(#pop)">' + card(0, 0, h=ch, seed=2, thumb=140) + '</g>'
                + move(loop, fx - gx, fy - gy, 1.15, 2.35) + '</g>' + show(loop, 1.05, 2.45) + '</g>')
    # The label row: a field opens, to-read is typed, Enter makes it a chip.
    lx = slots[1] + 12
    body.append('<g>' + f'<rect x="{lx}" y="{lab}" width="166" height="32" rx="8" fill="{SOFT}" stroke="{AMBER}" stroke-width="1.5"/>'
                + typed('to-read', lx + 12, lab + 23, loop, 3.8, 4.8, 17) + show(loop, 3.5, 5.3) + '</g>')
    body.append('<g>' + chip(lx, lab, 'to-read', size=16) + show(loop, 5.3) + '</g>')
    body.append('<g>' + chip(lx, lab, 'to-read', AMBER, '#ffffff', 16) + on + '</g>')
    body.append(pointer([(700, 540), (700, 540), (gx, gy), (gx, gy), (fx, fy), (fx, fy), (lx + 132, lab + 16), (lx + 132, lab + 16),
                         (lx + 44, lab + 16), (lx + 44, lab + 16), (700, 540)],
                        loop, [0, 0.2, 0.85, 1.15, 2.35, 2.7, 3.3, 5.6, 6.4, 7.6, 8.6], press=(1.0, 2.4, 3.45, 6.6)))
    scene = POP + window(wx, wy, ww, wh, 'Library') + ''.join(body)
    return frame('File a paper in a folder, label it, filter by the label',
                 'A paper card is dragged onto the Quantum folder, whose count goes from 2 to 3. Then the label to-read is '
                 'typed into another card\'s label row and becomes a chip; clicking the chip filters the library, and the '
                 'cards without that label dim.', scene)


def search():
    """Ctrl+F, a query, grouped results, a folder chip narrows them, a hit opens the paper at the match."""
    loop = 11
    body = []
    body.append('<g>' + key(1000, 260, 'Ctrl+F', 28) + show(loop, 0.15, 1.3) + '</g>')
    body.append('<g>' + key(1030, 260, 'Tab', 28) + show(loop, 4.3, 5.0) + '</g>')
    panel_ = (f'<rect x="100" y="40" width="660" height="540" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#shadow)"/>'
              + field(124, 64, 612, 58)
              + f'<g fill="none" stroke="{MUTED}" stroke-width="2.3" stroke-linecap="round"><circle cx="152" cy="90" r="9"/><path d="M159 97 168 106"/></g>'
              + typed('3000 qubit', 182, 102, loop, 1.0, 2.3, 24)
              + '<g>' + typed('qc', 312, 102, loop, 3.6, 4.0, 24) + show(loop, 3.4, 4.55) + '</g>'
              + '<g>' + chip(304, 76, 'qc/', size=18) + show(loop, 4.55) + '</g>')
    body.append('<g>' + panel_ + show(loop, 0.45, fade=0.25) + '</g>')
    groups = [
        ('Titles', 152, [('Continuous operation of a coherent…', '3,000-qubit', 'qc/neutral-atom')]),
        ('Notes on this page', 250, [('“…the 3,000-qubit array holds…”', '3,000-qubit', 'qc/neutral-atom')]),
        ('Library PDFs', 348, [('…arrays of 3,000 atoms in tweezers…', 'p. 1', 'qc/neutral-atom'),
                                     ('…scaling toward 3000 qubits requires…', 'p. 7', 'qec')]),
    ]
    n = 0
    for label, top, rows in groups:
        body.append(f'<g><text x="126" y="{top}" font-size="16" font-weight="600" fill="{MUTED}">{label}</text>'
                    + show(loop, 2.5 + n * 0.15) + '</g>')
        for i, (line, mark, folder) in enumerate(rows):
            ry = top + 12 + i * 68
            t = 2.55 + n * 0.15
            n += 1
            fade = (anim('opacity', '0;0;1;1;0.25;0.25;0;0', loop, 0, t, t + 0.15, 4.6, 4.8, loop - 0.3, loop - 0.15, loop)
                    if folder == 'qec' else show(loop, t))
            bw = len(mark) * 9 + 18
            body.append(f'<g><rect x="124" y="{ry}" width="612" height="60" rx="10" fill="{SOFT}"/>'
                        f'<text x="142" y="{ry + 27}" font-size="18" fill="{INK}">{line}</text>'
                        f'<text x="142" y="{ry + 49}" font-size="14" fill="{MUTED}">{folder}</text>'
                        f'<rect x="{718 - bw}" y="{ry + 17}" width="{bw}" height="26" rx="13" fill="{YELLOW}"/>'
                        f'<text x="{718 - bw / 2:.0f}" y="{ry + 35}" text-anchor="middle" font-size="15" fill="{CHIP_INK}">{mark}</text>'
                        + fade + '</g>')
    hit = 360
    body.append(glow(124, hit, 612, 60, loop, 6.2, rx=10))
    # The paper opens at the match, highlighted.
    body.append('<g>' + paper(800, 40, 500, 540, 14, seed=3)
                + f'<rect x="846" y="{40 + 54 + 76 + 5 * 24 - 7}" width="0" height="24" rx="4" fill="{YELLOW}" opacity="0.85">'
                + anim('width', '0;0;330;330;0', loop, 0, 7.0, 7.4, loop - 0.3, loop) + '</rect>'
                + show(loop, 6.6, fade=0.3) + '</g>')
    body.append(pointer([(560, 540), (560, 540), (600, hit + 30), (600, hit + 30), (1020, 300), (1020, 300), (560, 540)],
                        loop, [0, 5.4, 6.0, 6.7, 7.5, 9.6, 10.5], press=(6.2,)))
    return frame('Search notes, highlights and every PDF at once',
                 'Ctrl+F opens the search and 3000 qubit is typed; results appear grouped as Titles, Notes on this page '
                 'and Library PDFs, each with its match marked. A folder chip qc/ narrows them and the row from '
                 'another folder dims. One hit is clicked and the paper opens with the match highlighted.', ''.join(body))


def metadata():
    """The (i) button: title, authors, year and DOI fill in. Then the share popover's BibTeX entry is copied."""
    loop = 12
    wx, wy, ww, wh = 100, 40, 1200, 540
    link = (wx + ww - 46, wy + 34)
    body = []
    ix, iy = 1220, 177
    body.append('<g>' + paper(130, 128, 400, 430, 10, seed=1)
                + panel(560, 128, 710, 430, 'Notes', bullet(606, 248) + f'<rect x="626" y="242" width="380" height="11" rx="5" fill="{BAR}"/>'
                        + bullet(606, 296) + f'<rect x="626" y="290" width="300" height="11" rx="5" fill="{BAR}"/>')
                + f'<circle cx="{ix}" cy="{iy}" r="15" fill="none" stroke="{MUTED}" stroke-width="2"/>'
                f'<circle cx="{ix}" cy="{iy - 6}" r="1.8" fill="{MUTED}"/><path d="M{ix} {iy - 1} V{iy + 7}" stroke="{MUTED}" stroke-width="2.4" stroke-linecap="round"/>'
                + '</g>')
    # The (i) popover: the fields fill in one after another; clicking the link button closes it.
    mx, my = 584, 206
    pop = [popover(mx, my, 664, 240)]
    rows = [('Title', 'Continuous operation of a coherent 3,000-qubit system'), ('Authors', 'Chiu, Manetsch, …'),
            ('Year', '2025'), ('DOI', '10.1038/…')]
    defs = []
    for i, (name, value) in enumerate(rows):
        y = my + 20 + i * 52
        t = 2.0 + i * 0.6
        pop.append(f'<text x="{mx + 24}" y="{y + 26}" font-size="16" fill="{MUTED}">{name}</text>'
                   f'<rect x="{mx + 116}" y="{y}" width="524" height="40" rx="9" fill="{SOFT}" stroke="{EDGE}"/>'
                   f'<text x="{mx + 132}" y="{y + 26}" font-size="16" fill="{INK}" clip-path="url(#fill{i})">{value}</text>')
        # Fetched, not typed: each value sweeps in from the left.
        defs.append(f'<clipPath id="fill{i}"><rect x="{mx + 116}" y="{y}" width="0" height="40">'
                    + anim('width', '0;0;524;524;0', loop, 0, t, t + 0.45, loop - 0.3, loop) + '</rect></clipPath>')
    body.append('<g>' + ''.join(pop) + show(loop, 1.6, 5.15) + '</g>')
    # The share popover holds the BibTeX entry and its Copy button.
    body.append(icon_ring(*link, loop, 5.15))
    ox, oy, ow = 846, 112, 438
    copied = 6.95
    cb = (ox + ow - 24 - 110, oy + 96)
    share_pop = (popover(ox, oy, ow, 280)
                 + button(ox + 24, oy + 20, 'Share', primary=True)
                 + f'<path d="M{ox + 24} {oy + 80} H{ox + ow - 24}" stroke="{EDGE}" stroke-width="1.5"/>'
                 f'<text x="{ox + 24}" y="{oy + 121}" font-size="17" font-weight="600" fill="{INK}">BibTeX</text>'
                 f'<rect x="{ox + 24}" y="{oy + 146}" width="{ow - 48}" height="112" rx="10" fill="{SOFT}"/>'
                 + ''.join(f'<text x="{ox + 42}" y="{oy + 178 + j * 28}" font-family="{MONO}" font-size="14" fill="{MUTED}" xml:space="preserve">{t}</text>'
                           for j, t in enumerate(['@article{chiu2025continuous,', '  title = {Continuous operation…},', '  year = {2025}, doi = {10.1038/…}}']))
                 + f'<rect x="{cb[0]}" y="{cb[1]}" width="110" height="38" rx="10" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
                 # "Copy" stays hidden from the moment "Copied" shows until the loop restarts.
                 + f'<text x="{cb[0] + 55}" y="{cb[1] + 25}" text-anchor="middle" font-size="17" font-weight="600" fill="{INK}">Copy'
                 + anim('opacity', '1;1;0;0;1', loop, 0, copied, copied + 0.1, loop - 0.02, loop) + '</text>'
                 + f'<g><path d="M{cb[0] + 16} {cb[1] + 19} l6 6 l11 -12" fill="none" stroke="{GREEN}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'
                 f'<text x="{cb[0] + 40}" y="{cb[1] + 25}" font-size="17" font-weight="600" fill="{GREEN}">Copied</text>' + show(loop, copied) + '</g>')
    body.append('<g>' + share_pop + show(loop, 5.3) + '</g>')
    copy_at = (cb[0] + 55, cb[1] + 19)
    body.append(pointer([(900, 520), (900, 520), (ix, iy), (ix, iy), (1180, 500), (1180, 500), link, link, copy_at, copy_at,
                         (1100, 500), (1100, 500), (900, 520)],
                        loop, [0, 0.6, 1.3, 1.6, 2.1, 4.4, 5.0, 5.4, 6.4, 7.1, 7.6, 10.4, 11.4], press=(1.45, 5.15, 6.85)))
    tab = f'<rect x="{wx + 36}" y="{wy + 28}" width="220" height="12" rx="6" fill="{BAR_DARK}"/>'
    scene = '<defs>' + ''.join(defs) + '</defs>' + POP + window(wx, wy, ww, wh) + tab + ''.join(body)
    return frame('Metadata, and the BibTeX entry in the share popover',
                 'A paper is open beside its notes. The (i) button in the Notes title row opens the metadata: title, '
                 'authors, year and DOI fill in one after another. Then the link button in the top bar opens the share '
                 'popover, whose BibTeX entry is copied with its Copy button, which then reads Copied.', scene)


def share():
    """The link button, Share, the access controls, View flipped to Edit, a collaborator typing."""
    loop = 12
    wx, wy, ww, wh = 100, 40, 1200, 540
    link = (wx + ww - 46, wy + 34)
    body = []
    body.append(paper(130, 128, 330, 430, 10, seed=4)
                + panel(490, 128, 350, 430, 'Notes', block(526, 228, 278, 52, YELLOW)
                        + f'<rect x="546" y="249" width="200" height="10" rx="5" fill="{BAR}"/>'
                        + bullet(536, 314) + f'<rect x="556" y="308" width="220" height="11" rx="5" fill="{BAR}"/>'))
    # Maya, invited with Edit, joins: her avatar in the top bar, her cursor typing in the notes.
    body.append('<g>' + f'<circle cx="{wx + ww - 160}" cy="{wy + 34}" r="17" fill="{BLUE}"/>'
                f'<text x="{wx + ww - 160}" y="{wy + 41}" text-anchor="middle" font-size="18" font-weight="600" fill="#ffffff">M</text>'
                + show(loop, 5.2) + '</g>')
    body.append('<g>' + bullet(536, 362) + typewriter('Matches our run.', 556, 369, loop, 5.8, 7.6, size=20, caret=BLUE, label=('Maya', BLUE))
                + show(loop, 5.5) + '</g>')
    body.append(icon_ring(*link, loop, 1.0))
    # The popover: first only Share; pressed, it grows into the access controls.
    ox, oy, ow = 860, 112, 424
    pop = [f'<rect x="{ox}" y="{oy}" width="{ow}" height="134" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#pop)">'
           + anim('height', '134;134;318;318;134', loop, 0, 2.3, 2.6, loop - 0.3, loop) + '</rect>']
    pop.append('<g>' + f'<rect x="{ox + 24}" y="{oy + 26}" width="300" height="10" rx="5" fill="{BAR}"/>'
               f'<rect x="{ox + 24}" y="{oy + 46}" width="220" height="10" rx="5" fill="{BAR}"/>'
               + button(ox + 24, oy + 72, 'Share', primary=True) + hide(loop, 2.3) + '</g>')
    seg = ox + ow - 24 - 124
    after = [field(ox + 24, oy + 22, 244, 44)
             + f'<g transform="translate({ox + 46} {oy + 44}) rotate(-45) scale(0.75)" fill="none" stroke="{MUTED}" stroke-width="2.6">'
             '<rect x="-14" y="-6" width="16" height="12" rx="6"/><rect x="-2" y="-6" width="16" height="12" rx="6"/></g>'
             f'<rect x="{ox + 66}" y="{oy + 39}" width="170" height="10" rx="5" fill="{BAR}"/>'
             + button(ox + ow - 24 - 121, oy + 24, 'Copy link', size=17),
             f'<text x="{ox + 24}" y="{oy + 108}" font-size="15" font-weight="600" letter-spacing="1" fill="{MUTED}">General access</text>'
             f'<text x="{ox + 24}" y="{oy + 148}" font-size="18" fill="{INK}">Anyone with the link</text>'
             f'<rect x="{seg}" y="{oy + 122}" width="124" height="38" rx="10" fill="{SOFT}"/>'
             f'<rect x="{seg + 3}" y="{oy + 125}" width="59" height="32" rx="8" fill="{CARD}" stroke="{EDGE}">'
             + anim('x', f'{seg + 3};{seg + 3};{seg + 62};{seg + 62};{seg + 3}', loop, 0, 4.45, 4.7, loop - 0.3, loop) + '</rect>'
             f'<text x="{seg + 32}" y="{oy + 147}" text-anchor="middle" font-size="16" font-weight="600" fill="{INK}">View'
             + anim('fill', f'{INK};{INK};{MUTED};{MUTED};{INK}', loop, 0, 4.45, 4.7, loop - 0.3, loop) + '</text>'
             f'<text x="{seg + 92}" y="{oy + 147}" text-anchor="middle" font-size="16" font-weight="600" fill="{MUTED}">Edit'
             + anim('fill', f'{MUTED};{MUTED};{INK};{INK};{MUTED}', loop, 0, 4.45, 4.7, loop - 0.3, loop) + '</text>',
             f'<path d="M{ox + 24} {oy + 182} H{ox + ow - 24}" stroke="{EDGE}" stroke-width="1.5"/>'
             f'<text x="{ox + 24}" y="{oy + 216}" font-size="15" font-weight="600" letter-spacing="1" fill="{MUTED}">Who has access</text>'
             f'<circle cx="{ox + 41}" cy="{oy + 254}" r="17" fill="{BLUE}"/>'
             f'<text x="{ox + 41}" y="{oy + 260}" text-anchor="middle" font-size="17" font-weight="600" fill="#ffffff">M</text>'
             f'<text x="{ox + 70}" y="{oy + 260}" font-size="18" fill="{INK}">Maya</text>'
             f'<text x="{ox + ow - 24}" y="{oy + 260}" text-anchor="end" font-size="17" fill="{MUTED}">Edit</text>']
    pop += ['<g>' + a + show(loop, 2.5 + i * 0.15) + '</g>' for i, a in enumerate(after)]
    body.append('<g>' + ''.join(pop) + show(loop, 1.1) + '</g>')
    share_btn = (ox + 68, oy + 92)
    toggle = (seg + 92, oy + 141)
    body.append(pointer([(700, 520), (700, 520), link, link, share_btn, share_btn, toggle, toggle, (1100, 500), (1100, 500), (700, 520)],
                        loop, [0, 0.2, 0.9, 1.3, 1.9, 3.4, 4.1, 4.6, 5.2, 10.2, 11.4], press=(1.0, 2.1, 4.4)))
    tab = f'<rect x="{wx + 36}" y="{wy + 28}" width="220" height="12" rx="6" fill="{BAR_DARK}"/>'
    scene = POP + window(wx, wy, ww, wh) + tab + ''.join(body)
    return frame('Share a page with a link',
                 'The link button in the top bar is clicked and Share makes the link. The popover then shows the link with '
                 'Copy link, General access set to Anyone with the link with a View / Edit toggle, and Who has access: Maya, '
                 'Edit. The toggle is flipped to Edit, and Maya\'s blue cursor appears typing in the notes.', scene)


SCENES = [
    ('add-paper', add_paper),
    ('library', library),
    ('search', search),
    ('metadata', metadata),
    ('share', share),
]

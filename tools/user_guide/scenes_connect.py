"""Working with others and other tools: a shared workspace, offline copies, the Gamma
Connector, assistants (Codex, Claude Code), import and export."""
from scene import (AMBER, BAR, BAR_DARK, BG, BLUE, CARD, EDGE, GREEN, INK, MONO, MUTED, SOFT, TINT, YELLOW,
                   anim, block, bullet, button, chip, frame, mark, move, panel, paper, pointer, show, stroke,
                   keytimes, typed, typewriter)

LIGHT = '#eeebe4'     # text on the dark terminal
DIM = '#9a988f'       # muted text on the dark terminal


def during(loop, *spans, fade=0.12):
    """Opacity 1 inside each (start, end) span, 0 outside; a span may start at 0 or end at
    `loop`, so a state can hold across the restart."""
    pts = [(0, 1 if spans[0][0] <= 0 else 0)]
    for a, b in spans:
        if a > 0:
            pts += [(a, 0), (a + fade, 1)]
        if b < loop:
            pts += [(b, 1), (b + fade, 0)]
    pts.append((loop, 1 if spans[-1][1] >= loop else 0))
    return anim('opacity', ';'.join(str(v) for _, v in pts), loop, *(t for t, _ in pts))


def slide(attr, loop, *pairs):
    """An <animate> of `attr` through (second, value) pairs; the first at 0, the last at `loop`."""
    return anim(attr, ';'.join(str(v) for _, v in pairs), loop, *(t for t, _ in pairs))


def spin(cx, cy, loop, *pairs):
    """A rotation about (cx, cy) through (second, degrees) pairs; the first at 0, the last at `loop`."""
    values = ';'.join(f'{d} {cx} {cy}' for _, d in pairs)
    return (f'<animateTransform attributeName="transform" type="rotate" values="{values}" '
            f'keyTimes="{keytimes(loop, *(t for t, _ in pairs))}" dur="{loop}s" repeatCount="indefinite"/>')


def check(cx, cy, r=14):
    """A tick in a filled circle."""
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{GREEN}"/>'
            f'<path d="M{cx - r * 0.45:.1f} {cy + r * 0.02:.1f} L{cx - r * 0.1:.1f} {cy + r * 0.36:.1f} L{cx + r * 0.48:.1f} {cy - r * 0.34:.1f}" '
            f'fill="none" stroke="#ffffff" stroke-width="{max(2, r / 5.5):.1f}" stroke-linecap="round" stroke-linejoin="round"/>')


# ---- Edit together ---------------------------------------------------------------------

def avatar(cx, cy, colour, letter=None):
    out = f'<circle cx="{cx}" cy="{cy}" r="17" fill="{colour}" stroke="{CARD}" stroke-width="3"/>'
    if letter:
        return out + f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" font-size="16" font-weight="700" fill="#ffffff">{letter}</text>'
    # You: a head and shoulders.
    return out + (f'<circle cx="{cx}" cy="{cy - 4}" r="5" fill="#ffffff"/>'
                  f'<path d="M{cx - 9} {cy + 11} C{cx - 8} {cy + 2} {cx + 8} {cy + 2} {cx + 9} {cy + 11}" fill="#ffffff"/>')


def remote_cursor(name, colour, points, loop, times):
    """Another member's arrow cursor with a name tag, gliding through (x, y) at `times`."""
    w = int(len(name) * 11 + 24)
    values = ';'.join(f'{x} {y}' for x, y in points)
    return (f'<g><path d="M0 0 L0 22 L6 16.5 L10 25 L13.5 23.5 L9.5 15 L17 15 Z" fill="{colour}" stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round"/>'
            f'<rect x="16" y="20" width="{w}" height="27" rx="6" fill="{colour}"/>'
            f'<text x="{16 + w / 2:.0f}" y="39" text-anchor="middle" font-size="17" font-weight="600" fill="#ffffff">{name}</text>'
            f'<animateTransform attributeName="transform" type="translate" values="{values}" '
            f'keyTimes="{keytimes(loop, *times)}" dur="{loop}s" repeatCount="indefinite"/></g>')


def collab():
    """One shared page: you and Maya type into two notes at once while Sam reads along."""
    loop = 11
    out = [panel(240, 60, 920, 480, 'Notes')]
    out.append(chip(790, 93, 'Lab library · shared'))
    out.append(avatar(1112, 110, GREEN, 'S') + avatar(1084, 110, BLUE, 'M') + avatar(1056, 110, AMBER))
    # What is already there: a highlighted passage.
    out.append(block(276, 166, 700, 74, YELLOW)
               + f'<text x="300" y="196" font-family="Georgia, serif" font-size="20" font-style="italic" fill="{MUTED}">“Repeated measurements reveal</text>'
               f'<text x="300" y="225" font-family="Georgia, serif" font-size="20" font-style="italic" fill="{MUTED}">the coherence time of the array.”</text>')
    # You click under it and type; Maya types into the next note at the same time.
    out.append('<g>' + bullet(286, 303) + show(loop, 1.0) + '</g>')
    out.append(typed('Compare the two trapping geometries.', 308, 311, loop, 1.4, 5.8, 24))
    out.append('<g>' + bullet(286, 393) + show(loop, 1.6) + '</g>')
    out.append(typewriter('Add the lifetime data from Fig. 3.', 308, 401, loop, 2.0, 6.8, size=24, caret=BLUE, label=('Maya', BLUE)))
    # Sam reads the passage's last line, then rests beside it.
    out.append(remote_cursor('Sam', GREEN, [(318, 229), (318, 229), (600, 229), (600, 229), (760, 212), (760, 212), (318, 229)],
                             loop, [0, 0.8, 6.0, 6.6, 7.4, 10.4, 11]))
    rest = (1000, 470)
    out.append(pointer([rest, rest, (302, 306), (302, 306), rest], loop, [0, 0.2, 0.9, 1.2, 2.2], press=[1.0]))
    return frame('Edit together in a shared workspace',
                 'A Notes window in the shared workspace "Lab library", with three members at the top right. You click '
                 'under a highlighted passage and type a note; at the same time Maya, with a blue caret and name tag, types '
                 'into the next note, and Sam\'s green cursor moves along the passage as he reads.', ''.join(out))


# ---- Offline copies --------------------------------------------------------------------

def server_box(cx, cy):
    x, y = cx - 70, cy - 55
    return (f'<rect x="{x}" y="{y}" width="140" height="110" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
            + ''.join(f'<rect x="{x + 20}" y="{y + 20 + i * 38}" width="100" height="30" rx="7" fill="{SOFT}" stroke="{EDGE}"/>'
                      f'<circle cx="{x + 36}" cy="{y + 35 + i * 38}" r="4.5" fill="{GREEN}"/>'
                      f'<path d="M{x + 52} {y + 35 + i * 38} H{x + 104}" stroke="{BAR}" stroke-width="4" stroke-linecap="round"/>' for i in range(2))
            + f'<text x="{cx}" y="{cy + 92}" text-anchor="middle" font-size="20" fill="{MUTED}">lab server</text>')


def pulse(x1, x2, y, loop, start, end):
    """An amber dot running along a sync line."""
    return (f'<circle cx="{x1}" cy="{y}" r="7" fill="{AMBER}" opacity="0">'
            + slide('cx', loop, (0, x1), (start, x1), (end, x2), (loop, x2))
            + during(loop, (start - 0.05, end), fade=0.08) + '</circle>')


def sync():
    """A laptop's offline copy syncing through the lab server to a phone; offline, the edit waits."""
    loop = 13
    y = 270                       # the sync lines
    l1, l2 = (470, 630), (770, 1020)
    out = []
    # The lines first, under the devices. Laptop - server goes grey and dashed while offline.
    out.append(f'<path d="M{l1[0]} {y} H{l1[1]}" stroke="{AMBER}" stroke-width="3" opacity="0.55"/>')
    out.append(f'<g><path d="M{l1[0]} {y} H{l1[1]}" stroke="{BG}" stroke-width="6"/>'
               f'<path d="M{l1[0]} {y} H{l1[1]}" stroke="{BAR_DARK}" stroke-width="3" stroke-dasharray="7 8"/>'
               + during(loop, (5.6, 9.0)) + '</g>')
    out.append(f'<path d="M{l2[0]} {y} H{l2[1]}" stroke="{AMBER}" stroke-width="3" opacity="0.55"/>')
    out.append(f'<g>{chip(497, 216, "✈ offline", fill=CARD, ink=MUTED, size=16)}' + during(loop, (5.6, 9.0)) + '</g>')
    # The laptop: a screen with the page header (the sync pill at its right) and the notes.
    out.append(f'<rect x="110" y="140" width="360" height="260" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#shadow)"/>'
               f'<path d="M78 400 H502 L486 424 Q484 428 478 428 H102 Q96 428 94 424 Z" fill="#e9e6de" stroke="{EDGE}" stroke-width="1.5"/>'
               f'<rect x="138" y="170" width="160" height="13" rx="4" fill="{BAR_DARK}"/>'
               f'<path d="M134 206 H446" stroke="{EDGE}" stroke-width="1.5"/>'
               + bullet(146, 238) + f'<rect x="162" y="233" width="220" height="10" rx="5" fill="{BAR}"/>'
               f'<text x="290" y="470" text-anchor="middle" font-size="20" fill="{MUTED}">laptop</text>')
    out.append('<g>' + bullet(146, 284) + show(loop, 0.5) + '</g>')
    out.append(typed('Lifetime: 42 µs', 162, 291, loop, 0.7, 2.3, 22))
    out.append('<g>' + bullet(146, 330) + show(loop, 6.0) + '</g>')
    out.append(typed('Check trap depth', 162, 337, loop, 6.2, 8.0, 22))
    # The sync pill: green when up to date, a dot for unsynced edits, an arc spinning in a round.
    px, py = 414, 176
    out.append(f'<rect x="{px - 28}" y="{py - 17}" width="56" height="34" rx="17" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>')
    out.append(f'<g>{check(px, py, 10)}' + during(loop, (0, 0.75), (4.5, 6.25), (11.1, loop)) + '</g>')
    out.append(f'<circle cx="{px}" cy="{py}" r="6" fill="{AMBER}">' + during(loop, (0.75, 2.6), (6.25, 9.3)) + '</circle>')
    out.append(f'<g><path d="M{px} {py - 10} A10 10 0 1 1 {px - 10} {py}" fill="none" stroke="{AMBER}" stroke-width="3" stroke-linecap="round">'
               + spin(px, py, loop, (0, 0), (2.6, 0), (4.4, 720), (9.3, 720), (11.0, 1440), (loop, 1440))
               + '</path>' + during(loop, (2.6, 4.45), (9.3, 11.05)) + '</g>')
    # The server, whose lights blink as a note passes, and the phone.
    out.append(server_box(700, y))
    for t in (3.4, 10.1):
        for i in range(2):
            out.append(f'<circle cx="666" cy="{y - 20 + i * 38}" r="5.5" fill="{AMBER}" opacity="0">' + during(loop, (t, t + 0.35)) + '</circle>')
    out.append(f'<rect x="1020" y="110" width="200" height="320" rx="28" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
               f'<rect x="1095" y="124" width="50" height="8" rx="4" fill="{EDGE}"/>'
               f'<rect x="1044" y="156" width="104" height="11" rx="4" fill="{BAR_DARK}"/>'
               f'<path d="M1040 186 H1200" stroke="{EDGE}" stroke-width="1.5"/>'
               + bullet(1050, 214) + f'<rect x="1062" y="209" width="120" height="9" rx="4.5" fill="{BAR}"/>'
               f'<text x="1120" y="470" text-anchor="middle" font-size="20" fill="{MUTED}">phone</text>')
    for i, (text, t) in enumerate((('Lifetime: 42 µs', 4.3), ('Check trap depth', 11.0))):
        cy = 250 + i * 34
        out.append(f'<rect x="1036" y="{cy - 15}" width="168" height="28" rx="6" fill="{AMBER}" opacity="0">'
                   + anim('opacity', '0;0;0.25;0;0', loop, 0, t, t + 0.1, t + 1.2, loop) + '</rect>')
        out.append('<g>' + bullet(1050, cy - 1) + f'<text x="1062" y="{cy + 5}" font-size="16" fill="{INK}">{text}</text>' + show(loop, t) + '</g>')
    # The note travels: laptop -> server -> phone, once online and once back from the flight.
    out += [pulse(*l1, y, loop, 2.7, 3.4), pulse(*l2, y, loop, 3.5, 4.3),
            pulse(*l1, y, loop, 9.4, 10.1), pulse(*l2, y, loop, 10.2, 11.0)]
    return frame('Offline copies keep in step by themselves',
                 'A laptop holding an offline copy, the lab server and a phone, joined by sync lines. A note typed on the '
                 'laptop travels to the server and on to the phone while the sync pill spins, then turns green. The laptop '
                 'goes offline; a second note waits with a dot on the pill, and goes out as soon as the line is back.',
                 '<g transform="translate(40 0)">' + ''.join(out) + '</g>')


# ---- Gamma Connector -------------------------------------------------------------------

def connector():
    """The toolbar badge lights up on a paper; one click, a folder and a label, Save to Gamma."""
    loop = 11
    out = []
    # The browser: a top bar with the address and the Gamma badge, then the paper.
    out.append(f'<rect x="60" y="50" width="800" height="500" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#shadow)"/>'
               f'<path d="M60 114 V64 Q60 50 74 50 H846 Q860 50 860 64 V114 Z" fill="{SOFT}"/>'
               f'<path d="M60 114 H860" stroke="{EDGE}" stroke-width="1.5"/>'
               + ''.join(f'<circle cx="{88 + i * 20}" cy="82" r="6" fill="{BAR}"/>' for i in range(3))
               + f'<rect x="160" y="64" width="560" height="36" rx="18" fill="{CARD}" stroke="{EDGE}"/>'
               f'<text x="184" y="88" font-size="18" fill="{MUTED}">journals.aps.org/prl/…</text>'
               f'<rect x="120" y="160" width="430" height="16" rx="4" fill="{BAR_DARK}"/>'
               f'<rect x="120" y="190" width="300" height="10" rx="4" fill="{BAR_DARK}"/>'
               + ''.join(f'<rect x="120" y="{240 + i * 24}" width="{680 * [1, 1, 0.86, 1, 0.94, 1, 0.7][i % 7]:.0f}" height="9" rx="4.5" fill="{BAR}"/>'
                         for i in range(11)))
    # The badge: grey until the tab holds a paper, then lit; a tick once saved.
    bx, by = 770, 66
    out.append(f'<rect x="{bx - 6}" y="{by - 6}" width="44" height="44" rx="10" fill="{AMBER}" fill-opacity="0.18" stroke="{AMBER}" stroke-width="1.5" opacity="0">'
               + show(loop, 0.8) + '</rect>')
    out.append(f'<g opacity="0.35">{mark(bx, by)}' + anim('opacity', '0.35;0.35;1;1;0.35', loop, 0, 0.8, 0.95, loop - 0.3, loop) + '</g>')
    out.append(f'<g>{check(bx + 32, by + 2, 9)}' + show(loop, 4.1) + '</g>')
    # The popup under the badge.
    pop = (f'<rect x="530" y="116" width="316" height="220" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
           f'<rect x="556" y="144" width="236" height="13" rx="4" fill="{BAR_DARK}"/>'
           f'<rect x="556" y="168" width="160" height="9" rx="4" fill="{BAR_DARK}"/>'
           + chip(556, 202, 'Quantum') + chip(664, 202, 'to-read', fill='#e4ecf8', ink=BLUE)
           + button(556, 266, 'Save to Gamma', primary=True))
    out.append(f'<g>{pop}' + show(loop, 2.3) + '</g>')
    out.append(f'<g>{check(762, 287, 15)}' + show(loop, 4.0) + '</g>')
    # The Gamma window: the Quantum folder, where the paper lands.
    out.append(panel(910, 110, 430, 400, 'Quantum') + mark(1280, 136, 30))
    def card(y, extra=''):
        return (f'<rect x="940" y="{y}" width="370" height="70" rx="10" fill="{SOFT}"/>'
                f'<rect x="956" y="{y + 12}" width="36" height="46" rx="4" fill="{CARD}" stroke="{EDGE}"/>'
                f'<rect x="1008" y="{y + 20}" width="200" height="11" rx="4" fill="{BAR_DARK}"/>'
                f'<rect x="1008" y="{y + 42}" width="130" height="8" rx="4" fill="{BAR}"/>' + extra)
    out.append(card(216) + card(300))
    new = card(384, f'<rect x="1222" y="{384 + 34}" width="76" height="24" rx="12" fill="#e4ecf8"/>'
                    f'<text x="1260" y="{384 + 51}" text-anchor="middle" font-size="14" font-weight="500" fill="{BLUE}">to-read</text>'
                    f'<rect x="940" y="384" width="370" height="70" rx="10" fill="none" stroke="{AMBER}" stroke-width="2">'
                    + anim('opacity', '0;0;1;1;0;0', loop, 0, 4.9, 5.0, 6.6, 7.2, loop) + '</rect>')
    out.append(f'<g>{new}' + show(loop, 4.9, fade=0.3) + '</g>')
    out.append(f'<g><path d="M782 287 C860 287 860 419 930 419" fill="none" stroke="{AMBER}" stroke-width="2.5" stroke-dasharray="2 9" stroke-linecap="round"/>'
               + show(loop, 4.5) + '</g>')
    out.append(pointer([(420, 400), (420, 400), (786, 82), (786, 82), (640, 287), (640, 287), (470, 470)],
                       loop, [0, 1.1, 1.9, 2.4, 3.4, 3.9, 5.0], press=[2.0, 3.6]))
    return frame('Save a paper from the browser',
                 'A journal article is open in the browser and the Gamma Connector badge in the toolbar lights up. A click '
                 'opens the popup with the paper, the folder Quantum and the label to-read; a click on Save to Gamma shows a '
                 'tick, and the paper appears in the Quantum folder in Gamma.', ''.join(out))


# ---- Assistants ------------------------------------------------------------------------

def assistant():
    """An assistant asks Gamma about a paper and answers with the page it read."""
    loop = 12
    out = [f'<rect x="60" y="90" width="820" height="400" rx="16" fill="{INK}" filter="url(#shadow)"/>'
           + ''.join(f'<circle cx="{92 + i * 20}" cy="120" r="6" fill="#3d3c38"/>' for i in range(3))
           + '<path d="M60 148 H880" stroke="#2e2d2a" stroke-width="1.5"/>']
    out.append(typed('> @Gamma, in the blockade paper,', 100, 210, loop, 0.5, 2.3, 24, MONO, LIGHT))
    out.append(typed('  how is the blockade radius measured?', 100, 250, loop, 2.5, 4.2, 24, MONO, LIGHT))
    # The tools it calls, then the answer streaming in.
    out.append(f'<g><circle cx="108" cy="308" r="5" fill="{AMBER}"/>'
               f'<text x="126" y="314" font-family="{MONO}" font-size="19" fill="{DIM}" xml:space="preserve">'
               f'<tspan>gamma · search_library → 1 page</tspan><tspan opacity="0"> · read_page{show(loop, 5.1)}</tspan></text>'
               + show(loop, 4.6) + '</g>')
    out.append(typed('From the spacing where double', 100, 372, loop, 5.8, 7.6, 24, MONO, LIGHT))
    out.append(typed('excitation is suppressed (p. 3).', 100, 412, loop, 7.8, 9.4, 24, MONO, LIGHT))
    # The page it read, in Gamma: that line lights up.
    out.append(paper(940, 70, 400, 440, 11) + mark(1294, 88, 30)
               + f'<text x="1140" y="494" text-anchor="middle" font-size="18" fill="{MUTED}">p. 3</text>')
    hy = 70 + 130 + 4 * 24 - 7
    out.append(f'<rect x="986" y="{hy}" width="290" height="24" rx="4" fill="{YELLOW}" fill-opacity="0.85" opacity="0">'
               + slide('width', loop, (0, 0), (9.5, 0), (10.0, 290), (loop, 290)) + show(loop, 9.5, fade=0.05) + '</rect>')
    out.append(f'<g><path d="M562 405 C700 405 760 {hy + 12} 978 {hy + 12}" fill="none" stroke="{AMBER}" stroke-width="2.5" stroke-dasharray="2 9" stroke-linecap="round"/>'
               + show(loop, 9.5) + '</g>')
    return frame('Ask Codex or Claude Code about your papers',
                 'In a terminal, an assistant is asked: @Gamma, in the blockade paper, how is the blockade radius measured? '
                 'It searches the library and reads one page, then answers in two lines citing p. 3, and that line of the '
                 'page lights up yellow in Gamma.', ''.join(out))


# ---- Import and export -----------------------------------------------------------------

def file_icon(x, y):
    return (f'<path d="M{x} {y} H{x + 46} L{x + 66} {y + 20} V{y + 86} H{x} Z" fill="{CARD}" stroke="{BAR_DARK}" stroke-width="2" stroke-linejoin="round"/>'
            f'<path d="M{x + 46} {y} V{y + 20} H{x + 66}" fill="none" stroke="{BAR_DARK}" stroke-width="2" stroke-linejoin="round"/>'
            f'<rect x="{x + 10}" y="{y + 52}" width="46" height="22" rx="4" fill="{AMBER}"/>'
            f'<text x="{x + 33}" y="{y + 69}" text-anchor="middle" font-size="14" font-weight="700" fill="#ffffff">PDF</text>'
            f'<rect x="{x + 10}" y="{y + 30}" width="30" height="6" rx="3" fill="{BAR}"/>')


def toggle(x, y, label):
    return (f'<rect x="{x}" y="{y}" width="36" height="20" rx="10" fill="{AMBER}"/><circle cx="{x + 26}" cy="{y + 10}" r="7" fill="#ffffff"/>'
            f'<text x="{x + 44}" y="{y + 16}" font-size="17" fill="{INK}">{label}</text>')


def export():
    """View menu: Export… writes an annotated PDF; Import… takes Zotero, Obsidian, Notion and PDF annotations in."""
    loop = 12
    out = [paper(430, 50, 540, 500, 13)]
    # The View menu button and its menu (opened twice: Export…, then Import…).
    out.append(f'<rect x="908" y="70" width="44" height="38" rx="9" fill="{SOFT}" stroke="{EDGE}"/>'
               + ''.join(f'<path d="M920 {80 + i * 9} H940" stroke="{INK}" stroke-width="2.4" stroke-linecap="round"/>' for i in range(3)))
    menu = (f'<rect x="750" y="116" width="202" height="100" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
            f'<text x="772" y="151" font-size="20" fill="{INK}">Import…</text><text x="772" y="196" font-size="20" fill="{INK}">Export…</text>')
    hover = lambda y, a, b: (f'<rect x="756" y="{y}" width="190" height="40" rx="8" fill="{TINT}" opacity="0">' + during(loop, (a, b), fade=0.08) + '</rect>')
    out.append('<g>' + menu.replace('<text', hover(170, 2.35, 2.75) + hover(124, 8.35, 8.75) + '<text', 1)
               + during(loop, (1.5, 2.75), (7.75, 8.75)) + '</g>')
    # Export: the dialog with its formats and layer switches.
    dx, dy = 460, 116
    rows = ['Annotated PDF', 'Notes as PDF', 'Markdown', 'BibTeX', 'Obsidian vault']
    dlg = [f'<rect x="{dx}" y="{dy}" width="480" height="432" rx="16" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#shadow)"/>'
           f'<text x="{dx + 32}" y="{dy + 46}" font-size="24" font-weight="600" fill="{INK}">Export</text>'
           f'<path d="M{dx + 32} {dy + 66} H{dx + 448}" stroke="{EDGE}" stroke-width="1.5"/>'
           f'<rect x="{dx + 20}" y="{dy + 78}" width="440" height="42" rx="9" fill="{TINT}" opacity="0">' + show(loop, 3.9, 6.9) + '</rect>']
    for i, name in enumerate(rows):
        cy = dy + 99 + i * 44
        dlg.append(f'<circle cx="{dx + 46}" cy="{cy}" r="9" fill="none" stroke="{BAR_DARK}" stroke-width="2"/>'
                   f'<text x="{dx + 68}" y="{cy + 7}" font-size="20" fill="{INK}">{name}</text>')
    dlg.append(f'<circle cx="{dx + 46}" cy="{dy + 99}" r="5" fill="{AMBER}" opacity="0">' + show(loop, 3.9, 6.9) + '</circle>')
    dlg.append(f'<path d="M{dx + 32} {dy + 330} H{dx + 448}" stroke="{EDGE}" stroke-width="1.5"/>'
               + toggle(dx + 32, dy + 346, 'highlights') + toggle(dx + 172, dy + 346, 'notes') + toggle(dx + 280, dy + 346, 'bundle files')
               + button(dx + 348, dy + 380, 'Export', primary=True))
    out.append('<g>' + ''.join(dlg) + during(loop, (2.85, 6.9)) + '</g>')
    # The file flies out to the right.
    out.append(f'<g>{file_icon(866, 486)}' + move(loop, 330, -250, 5.6, 6.4) + show(loop, 5.6, fade=0.1) + '</g>')
    # Import: four sources arrive from the left.
    out.append(f'<g><text x="60" y="150" font-size="22" font-weight="600" fill="{INK}">Import…</text>' + show(loop, 8.75) + '</g>')
    for i, name in enumerate(['Zotero', 'Obsidian', 'Notion', 'PDF annotations']):
        cy = 214 + i * 76
        t = 8.9 + i * 0.25
        w = int(len(name) * 18 * 0.56 + 26)
        out.append(f'<g>{chip(60, cy - 17, name)}' + show(loop, t - 0.15) + '</g>')
        x1 = 60 + w + 14
        out.append('<g>' + stroke(f'M{x1} {cy} H414', loop, t, t + 0.45, AMBER, 3, 400) + show(loop, t - 0.05, fade=0.05) + '</g>')
        out.append(f'<g><path d="M404 {cy - 8} L416 {cy} L404 {cy + 8}" fill="none" stroke="{AMBER}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
                   + show(loop, t + 0.4, fade=0.1) + '</g>')
    out.append(pointer([(700, 330), (700, 330), (930, 89), (930, 89), (905, 190), (905, 190), (dx + 46, dy + 99), (dx + 46, dy + 99),
                        (dx + 436, dy + 400), (dx + 436, dy + 400), (930, 89), (930, 89), (905, 145), (905, 145), (700, 330)],
                       loop, [0, 0.5, 1.2, 1.5, 2.3, 2.6, 3.5, 4.1, 5.0, 5.7, 7.4, 7.8, 8.3, 8.7, 9.6],
                       press=[1.3, 2.4, 3.8, 5.4, 7.5, 8.4]))
    return frame('Import and export from the View menu',
                 'On a page, the View menu opens and Export… is chosen. The dialog lists Annotated PDF, Notes as PDF, Markdown, '
                 'BibTeX and Obsidian vault, with switches for highlights, notes and bundle files; Annotated PDF is picked and '
                 'a PDF file flies out. Then Import… is chosen and arrows arrive from Zotero, Obsidian, Notion and PDF annotations.',
                 ''.join(out))


SCENES = [
    ('collab', collab),
    ('sync', sync),
    ('connector', connector),
    ('assistant', assistant),
    ('export', export),
]

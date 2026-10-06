"""Reading a paper: highlight, draw, type on the page, notebooks, links, translation."""
from scene import (AMBER, BAR, BAR_DARK, BLUE, CARD, CHIP, CHIP_INK, EDGE, INK, MONO, MUTED, RED, SELECT,
                   SOFT, YELLOW, anim, block, bullet, button, caption, frame, hide, key, line_y, panel, paper,
                   path_length, path_points, pointer, show, strip, stroke, typed)


def annotate():
    """One line highlighted, one note typed under it, one stroke drawn: nothing else."""
    loop = 9
    px, py = 100, 100
    sel_y = line_y(py, 5)
    body = []
    # The selection sweeps one line, then it is yellow.
    body.append(f'<rect x="150" y="{sel_y}" width="0" height="24" rx="4" fill="{SELECT}" opacity="0.6">'
                + anim('width', '0;0;420;420;0;0', loop, 0, 0.5, 1.6, 2.0, 2.01, loop) + '</rect>')
    body.append(f'<rect x="150" y="{sel_y}" width="420" height="24" rx="4" fill="{YELLOW}" opacity="0">'
                + anim('opacity', '0;0;0.85;0.85;0', loop, 0, 2.0, 2.15, loop - 0.3, loop) + '</rect>')
    body.append(pointer([(150, sel_y + 12), (150, sel_y + 12), (570, sel_y + 12), (570, sel_y + 12)], loop, [0, 0.5, 1.6, 2.2]))
    # The highlight lands in the notes as a block; a comment is typed under it.
    body.append('<g>'
                f'<path d="M572 {sel_y + 12} C660 {sel_y + 12} 660 250 740 250" fill="none" stroke="{AMBER}" stroke-width="2.5" stroke-dasharray="2 9" stroke-linecap="round"/>'
                f'<circle cx="770" cy="250" r="5" fill="{AMBER}"/>'
                + block(790, 218, 470, 64, YELLOW)
                + f'<text x="812" y="246" font-family="Georgia, serif" font-size="20" font-style="italic" fill="{MUTED}">“decoherence is dominated by</text>'
                f'<text x="812" y="272" font-family="Georgia, serif" font-size="20" font-style="italic" fill="{MUTED}">intermediate-state scattering”</text>'
                + show(loop, 2.2) + '</g>')
    body.append('<g>' + bullet(800, 316) + typed('Key claim.', 822, 323, loop, 2.8, 4.2, 22) + show(loop, 2.6) + '</g>')
    # A circle drawn around the figure becomes an ink block.
    body.append(stroke('M232 404 C300 372 470 368 500 412 C528 458 320 488 232 462 C196 450 200 420 232 404', loop, 5.2, 6.8))
    body.append('<g>' + bullet(800, 386) + block(822, 364, 180, 46)
                + f'<path d="M842 394 C854 372 874 372 884 384 C892 394 872 402 862 396" fill="none" stroke="{AMBER}" stroke-width="3" stroke-linecap="round"/>'
                f'<text x="902" y="393" font-size="19" fill="{MUTED}">ink · p. 3</text>'
                + show(loop, 7.0) + '</g>')
    scene = (paper(px, py, 500, 440, 5)
             + f'<rect x="200" y="360" width="360" height="130" rx="8" fill="none" stroke="#d6d3cb" stroke-width="2"/>'
             f'<path d="M230 470 C300 400 380 392 520 398" stroke="#b9b6ae" stroke-width="3" fill="none"/>'
             + panel(740, 100, 560, 440, 'Notes')
             + ''.join(body))
    return frame('Highlight a line, note it, draw on the page',
                 'One line of a paper is selected and turns yellow; it appears as a block in the notes and a comment is '
                 'typed under it. Then a circle is drawn around the figure and becomes an ink block.', scene)



def _trace(d, start, end, n=12):
    """Pointer waypoints and times that follow a stroke() of `d` drawn from start to end."""
    pts = path_points(d, n)
    return pts, [start + (end - start) * k / n for k in range(n + 1)]


def _figure(x, y, w, h):
    """A paper's figure: a frame and one curve."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="none" stroke="#d6d3cb" stroke-width="2"/>'
            f'<path d="M{x + 30} {y + h - 22} C{x + 100} {y + 50} {x + w * 0.55:.0f} {y + 36} {x + w - 30} {y + 40}" '
            f'stroke="#b9b6ae" stroke-width="3" fill="none"/>')


def pen():
    """The pen is picked, a phrase is circled and an arrow drawn to the figure; the lasso
    picks up the arrow and it is recoloured red. The notes hold the ink as one block."""
    loop = 12
    tools, at = strip(60, 150, ['pen', 'highlighter', 'eraser', 'lasso', 'text', 'undo'], loop,
                      picks=[('pen', 1.05, 6.3), ('lasso', 6.35, None)])
    circle = 'M298 236 C302 220 410 216 428 232 C444 248 404 260 354 259 C304 258 284 248 302 228'
    arrow = 'M432 250 C478 258 488 298 468 334'
    head = 'M481 323 L468 335 L466 318'
    lasso = 'M420 268 C432 230 500 236 506 284 C512 332 492 360 462 356 C432 352 414 304 420 268'
    body = [stroke(circle, loop, 1.9, 3.1, length=path_length(circle) + 4),
            stroke(arrow, loop, 3.5, 4.2, length=path_length(arrow) + 4),
            stroke(head, loop, 4.35, 4.6, length=path_length(head) + 4)]
    # The arrow, recoloured.
    body.append(f'<g fill="none" stroke="{RED}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" opacity="0">'
                f'<path d="{arrow}"/><path d="{head}"/>' + show(loop, 9.0) + '</g>')
    # The lasso: a dashed loop, revealed along its length through a mask.
    lasso_len = path_length(lasso) + 4
    body.append(f'<mask id="lassoReveal" maskUnits="userSpaceOnUse" x="0" y="0" width="1400" height="620">'
                + stroke(lasso, loop, 6.9, 8.0, color='#ffffff', width=8, length=lasso_len) + '</mask>'
                f'<g opacity="0"><path d="{lasso}" fill="none" stroke="{MUTED}" stroke-width="2" stroke-dasharray="7 6" mask="url(#lassoReveal)"/>'
                + show(loop, 6.85, 9.6) + '</g>')
    # The selection's colours; red is clicked.
    swatches = ''.join(f'<circle cx="{548 + i * 34}" cy="300" r="11" fill="{c}"/>' for i, c in enumerate([INK, AMBER, RED, BLUE]))
    body.append(f'<g opacity="0"><rect x="526" y="278" width="152" height="44" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
                f'{swatches}' + show(loop, 8.2, 9.3) + '</g>')
    # The notes: one ink block holding both strokes.
    thumb = f'<g transform="translate(846 258) scale(0.62) translate(-290 -214)" fill="none" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
    ink = (thumb + f'<g stroke="{AMBER}"><path d="{circle}"/><path d="{arrow}"/><path d="{head}"/></g>'
           f'<g stroke="{RED}" opacity="0"><path d="{arrow}"/><path d="{head}"/>' + show(loop, 9.3) + '</g></g>')
    body.append('<g opacity="0">' + bullet(800, 290) + block(822, 240, 330, 100) + ink
                + f'<text x="1006" y="297" font-size="19" fill="{MUTED}">ink · p. 2</text>' + show(loop, 5.0) + '</g>')
    pts, ts = [(640, 420), (at['pen'][0], at['pen'][1]), at['pen']], [0, 0.8, 1.0]
    p, t = _trace(circle, 1.9, 3.1)
    pts += [p[0], *p]; ts += [1.7, *t]
    p, t = _trace(arrow, 3.5, 4.2, 6)
    pts += [p[0], *p]; ts += [3.35, *t]
    p, t = _trace(head, 4.35, 4.6, 2)
    pts += p; ts += t
    pts += [at['lasso'], at['lasso']]; ts += [6.0, 6.3]
    p, t = _trace(lasso, 6.9, 8.0)
    pts += [p[0], *p]; ts += [6.75, *t]
    pts += [(616, 300), (616, 300), (640, 420)]; ts += [8.7, 9.0, 9.8]
    scene = (paper(160, 80, 480, 460, 4) + _figure(260, 340, 300, 150) + tools
             + panel(740, 80, 560, 460, 'Notes') + bullet(800, 206) + f'<rect x="822" y="201" width="300" height="10" rx="5" fill="{BAR}"/>'
             + ''.join(body) + pointer(pts, loop, ts, press=[1.0, 6.3, 8.85]))
    return frame('Draw on the page with the pen',
                 'The pen is picked from the tool strip beside the paper. A phrase is circled and an arrow drawn from it '
                 'to a figure; both strokes appear in the notes as one ink block. Then the lasso is picked, a dashed loop '
                 'is drawn around the arrow, and red is chosen: the arrow turns red, on the page and in the notes.', scene)



def text_box():
    """The Text tool is picked, a click on the page places a box, a sentence is typed into
    it; selected, it shows its edge and the right-edge handle, which widens it. The notes
    hold it as a block with a T marker."""
    loop = 10
    tools, at = strip(60, 150, ['pen', 'highlighter', 'eraser', 'lasso', 'text', 'undo'], loop,
                      picks=[('text', 1.05, None)])
    words = 'Check the error bars.'
    bx, by, bw, bh = 200, 316, 262, 46
    body = []
    # Editing: a dashed outline while the box is being typed in.
    body.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6" fill="none" stroke="{MUTED}" stroke-width="1.5" '
                f'stroke-dasharray="5 5" opacity="0">' + show(loop, 1.85, 5.4) + '</rect>')
    # Selected: a thin edge and the handle on its right, dragged wider.
    body.append(f'<g opacity="0"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6" fill="none" stroke="{BLUE}" stroke-width="1.5">'
                + anim('width', f'{bw};{bw};{bw + 60};{bw + 60};{bw}', loop, 0, 6.3, 7.0, loop - 0.15, loop) + '</rect>'
                f'<rect x="{bx + bw - 5}" y="{by + bh / 2 - 12}" width="10" height="24" rx="4" fill="{CARD}" stroke="{BLUE}" stroke-width="1.5">'
                + anim('x', f'{bx + bw - 5};{bx + bw - 5};{bx + bw + 55};{bx + bw + 55};{bx + bw - 5}', loop, 0, 6.3, 7.0, loop - 0.15, loop) + '</rect>'
                + show(loop, 5.45) + '</g>')
    body.append(typed(words, bx + 12, by + 31, loop, 2.2, 4.4, 24))
    # The notes: the box as a block with its T marker.
    body.append('<g opacity="0">' + bullet(800, 278) + block(822, 250, 400, 56)
                + f'<rect x="838" y="264" width="28" height="28" rx="7" fill="{CHIP}"/>'
                f'<text x="852" y="285" text-anchor="middle" font-size="19" font-weight="700" fill="{CHIP_INK}">T</text>'
                f'<text x="882" y="286" font-size="21" fill="{INK}">{words}</text>' + show(loop, 4.8) + '</g>')
    lines = ''.join(f'<rect x="210" y="{y}" width="{w}" height="9" rx="4.5" fill="{BAR}"/>'
                    for y, w in [(410, 380), (434, 380), (458, 330), (482, 250)])
    pts = [(640, 430), at['text'], at['text'], (bx + 12, by + 23), (bx + 12, by + 23), (bx + 40, by + 92), (bx + 40, by + 92),
           (bx + 120, by + 23), (bx + 120, by + 23), (bx + bw, by + bh / 2), (bx + bw, by + bh / 2),
           (bx + bw + 60, by + bh / 2), (bx + bw + 60, by + bh / 2), (640, 430)]
    ts = [0, 0.8, 1.0, 1.6, 1.8, 2.2, 4.7, 5.2, 5.45, 5.95, 6.3, 7.0, 7.3, 8.2]
    scene = (paper(160, 80, 480, 460, 4) + lines + tools
             + panel(740, 80, 560, 460, 'Notes') + bullet(800, 206) + f'<rect x="822" y="201" width="300" height="10" rx="5" fill="{BAR}"/>'
             + ''.join(body) + pointer(pts, loop, ts, press=[1.0, 1.8, 5.4]))
    return frame('Type on the page with the Text tool',
                 'T is picked from the tool strip, a click on the page places a text box, and a sentence is typed into it. '
                 'The box appears in the notes as a block with a T marker. Clicked, the box shows a thin edge and a handle '
                 'on its right edge, which is dragged to widen it.', scene)


def _scribble(x, y, words, seed=0, size=1.3):
    """Handwriting along a baseline at (x, y), one path per word: `words` lists letter counts. Each letter is
    one turn of a slanted cycloid: a tall loop (an l), a small loop (an e) or an arch (a u),
    in a fixed rhythm, with a gap between words."""
    from math import cos, pi, sin
    d, k = [], seed
    for n in words:
        pts = []
        for i in range(n):
            tall, loopy = [(24, 3.4), (11, 2.6), (11, 0.8), (24, 3.4), (11, 0.8), (11, 2.6), (11, 0.8)][k % 7]
            k += 1
            for j in range(13):
                t = 2 * pi * (i + j / 12)
                up = size * tall * (1 - cos(t)) / 2
                pts.append((x + size * (2.4 * t + loopy * sin(t)) + 0.35 * up, y - up))
        d.append('M' + ' L'.join(f'{a:.1f} {b:.1f}' for a, b in pts))
        x += size * 2.4 * 2 * pi * n + 26
    return d


def notebook():
    """/note typed in a block turns it into a sheet of ruled paper; a pen writes on it,
    and writing near the bottom brings the next sheet."""
    loop = 11
    sx, sw, sh = 322, 740, 236
    s1, s2 = 192, 454

    def sheet(y):
        rules = ''.join(f'<path d="M{sx + 24} {y + 48 + 40 * i} H{sx + sw - 24}" stroke="#d6e0ec" stroke-width="1.5"/>' for i in range(5))
        return (f'<rect x="{sx}" y="{y}" width="{sw}" height="{sh}" rx="6" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
                f'<path d="M{sx + 70} {y + 14} V{y + sh - 14}" stroke="#f1cdc8" stroke-width="1.5"/>' + rules)

    def slide(dy, start, end):
        return (f'<animateTransform attributeName="transform" type="translate" values="0 {dy};0 {dy};0 0;0 0" '
                f'keyTimes="0;{start / loop:.4f};{end / loop:.4f};1" dur="{loop}s" repeatCount="indefinite"/>')

    first = _scribble(sx + 100, s1 + 48, [5, 3, 6])
    second = _scribble(sx + 100, s1 + 88, [4, 7], 2)
    low = _scribble(sx + 100, s1 + 208, [6, 2, 5], 4)
    body = ['<g>' + bullet(300, 211) + typed('/note', 322, 219, loop, 0.6, 1.4, 22, family=MONO) + hide(loop, 2.2) + '</g>',
            '<g opacity="0">' + key(424, 193, 'Enter') + show(loop, 1.7, 2.05) + '</g>',
            '<g opacity="0">' + sheet(s1) + slide(16, 2.2, 2.6) + show(loop, 2.2, fade=0.3) + '</g>',
            '<g clip-path="url(#notesBody)"><g opacity="0">' + sheet(s2) + slide(90, 7.5, 8.1) + show(loop, 7.5, fade=0.3) + '</g></g>']
    pts, ts = [(980, 520), (980, 520)], [0, 2.4]
    # Each word is its own stroke (dashing restarts at every subpath), timed by its length
    # with a short lift of the pen between words.
    for words, a, b in [(first, 3.0, 4.3), (second, 4.6, 5.6), (low, 6.1, 7.2)]:
        lengths = [path_length(d) for d in words]
        rate = (b - a - 0.12 * (len(words) - 1)) / sum(lengths)
        t = a
        for d, n in zip(words, lengths):
            body.append(stroke(d, loop, t, t + n * rate, width=3, length=n + 4))
            p_, t_ = _trace(d, t, t + n * rate, 10)
            pts += [p_[0], *p_]
            ts += [t - (0.25 if t == a else 0.1), *t_]
            t += n * rate + 0.12
    pts += [(980, 520)]
    ts += [8.3]
    scene = (f'<clipPath id="notesBody"><rect x="252" y="122" width="896" height="461" rx="12"/></clipPath>'
             + panel(250, 40, 900, 545, 'Notes') + bullet(300, 160) + f'<rect x="322" y="155" width="340" height="10" rx="5" fill="{BAR}"/>'
             + ''.join(body) + pointer(pts, loop, ts))
    return frame('A notebook sheet among the notes',
                 '/note is typed in an empty note block, and the block becomes a sheet of ruled paper. A pen writes two '
                 'lines on it; writing near the bottom of the sheet brings a second sheet in below, so there is always '
                 'paper below.', scene)


def links():
    """A citation is clicked and fetched: the cited paper opens over the first. Back
    brings the first paper back at the same spot."""
    loop = 10
    px, pw = 250, 900
    cite_y = 290
    kt = lambda *ts: ';'.join(f'{t / loop:.4f}' for t in ts)
    lines = []
    for i in range(9):
        y = 140 + i * 30
        if y == cite_y:
            lines.append(f'<rect x="{px + 50}" y="{y}" width="470" height="9" rx="4.5" fill="{BAR}"/>'
                         f'<text x="{px + 532}" y="{y + 11}" font-size="21" font-weight="600" fill="{BLUE}">[12]</text>'
                         f'<rect x="{px + 588}" y="{y}" width="{pw - 638}" height="9" rx="4.5" fill="{BAR}"/>')
        else:
            frac = [1, 0.96, 1, 0.82, 1, 0.9, 1][i % 7]
            lines.append(f'<rect x="{px + 50}" y="{y}" width="{(pw - 100) * frac:.0f}" height="9" rx="4.5" fill="{BAR}"/>')
    # Back at the same spot: the cited line glows once, under its text.
    glow = (f'<rect x="{px + 40}" y="{cite_y - 9}" width="{pw - 80}" height="28" rx="6" fill="{YELLOW}" opacity="0">'
            + anim('opacity', '0;0;0.7;0.7;0;0', loop, 0, 6.4, 6.6, 7.3, 7.9, loop) + '</rect>')
    first = (f'<rect x="{px}" y="110" width="{pw}" height="490" rx="12" fill="{CARD}" filter="url(#shadow)"/>'
             + glow + ''.join(lines) + _figure(px + 200, 420, 500, 150))
    # The popover under the marker.
    ox, oy = px + 520, cite_y + 26
    pop = (f'<g opacity="0"><rect x="{ox}" y="{oy}" width="250" height="104" rx="12" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
           f'<rect x="{ox + 6}" y="{oy + 6}" width="238" height="44" rx="8" fill="{SOFT}" opacity="0">' + show(loop, 2.2, 2.7) + '</rect>'
           f'<text x="{ox + 22}" y="{oy + 35}" font-size="19" fill="{INK}">Fetch into Gamma</text>'
           f'<text x="{ox + 22}" y="{oy + 83}" font-size="19" fill="{INK}">Open in browser</text>'
           + show(loop, 1.5, 2.7) + '</g>')
    # The fetched paper slides in over the first one, and out again on Back.
    widths = [1, 1, 0.88, 1, 0.94, 1, 0.76, 1, 0.9, 1, 0.8]
    second = (f'<g opacity="0"><rect x="{px}" y="110" width="{pw}" height="490" rx="12" fill="{CARD}" filter="url(#shadow)"/>'
              f'<rect x="{px + 50}" y="164" width="480" height="14" rx="4" fill="{BAR_DARK}"/>'
              f'<rect x="{px + 50}" y="190" width="320" height="9" rx="4" fill="{BAR_DARK}"/>'
              + ''.join(f'<rect x="{px + 50}" y="{240 + i * 30}" width="{(pw - 100) * f:.0f}" height="9" rx="4.5" fill="{BAR}"/>'
                        for i, f in enumerate(widths))
              + f'<animateTransform attributeName="transform" type="translate" values="560 0;560 0;0 0;0 0;560 0;560 0" '
                f'keyTimes="{kt(0, 2.8, 3.3, 5.75, 6.25, loop)}" dur="{loop}s" repeatCount="indefinite"/>'
              + anim('opacity', '0;0;1;1;0;0', loop, 0, 2.8, 2.95, 6.05, 6.25, loop) + '</g>')
    # The top bar: Back, and the tabs; the active tab's underline follows.
    def tab(x, w):
        return (f'<rect x="{x}" y="34" width="210" height="40" rx="9" fill="{SOFT}"/>'
                f'<rect x="{x + 22}" y="49" width="{w}" height="10" rx="5" fill="{BAR_DARK}"/>')
    bar = (f'<rect x="100" y="22" width="1200" height="64" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
           + button(118, 33, '← Back')
           + tab(250, 140) + '<g opacity="0">' + tab(476, 120) + show(loop, 2.8) + '</g>'
           f'<rect x="262" y="78" width="186" height="4" rx="2" fill="{AMBER}">'
           f'<animateTransform attributeName="transform" type="translate" values="0 0;0 0;226 0;226 0;0 0;0 0" '
           f'keyTimes="{kt(0, 2.8, 3.1, 5.75, 6.05, loop)}" dur="{loop}s" repeatCount="indefinite"/></rect>')
    pts = [(640, 380), (px + 554, cite_y + 5), (px + 554, cite_y + 5), (ox + 110, oy + 28), (ox + 110, oy + 28),
           (900, 470), (166, 54), (166, 54), (640, 380)]
    ts = [0, 1.1, 1.45, 2.1, 2.65, 3.6, 5.3, 5.75, 6.8]
    scene = bar + first + pop + second + pointer(pts, loop, ts, press=[1.35, 2.55, 5.65])
    return frame('Follow a citation and come back',
                 'A citation marker [12] in a paper is clicked; a popover offers Fetch into Gamma and Open in browser. '
                 'Fetch is clicked and the cited paper opens over the first in a new tab. Then Back in the top bar is '
                 'clicked and the first paper returns at the same spot.', scene)


def translate():
    """The 文A button translates the page in place, paragraph by paragraph, the figure
    untouched; holding Alt peeks at the original."""
    loop = 10
    tint, tint_dark = '#c9d6e8', '#7f93b0'
    tools, at = strip(300, 170, ['minus', 'plus', 'fit', 'pen', 'translate'], loop, picks=[('translate', 1.25, None)])
    x0, y0, w, h = 400, 50, 640, 540
    lx, lw = x0 + 50, w - 100

    def bars(rows, fill):
        return ''.join(f'<rect x="{x}" y="{y}" width="{bw:.0f}" height="{bh}" rx="4.5" fill="{fill}"/>' for x, y, bw, bh in rows)

    title = [(lx, 100, 360, 14), (lx, 126, 250, 9)]
    title_t = [(lx, 100, 300, 14), (lx, 126, 210, 9)]
    p1 = [(lx + 134, 170, lw - 134, 9), (lx, 194, lw, 9), (lx, 218, lw * 0.92, 9), (lx, 242, lw * 0.6, 9)]
    p1_t = [(lx + 104, 170, lw - 104, 9), (lx, 194, lw * 0.9, 9), (lx, 218, lw * 0.7, 9)]
    p2 = [(lx, 450, lw, 9), (lx, 474, lw * 0.95, 9), (lx, 498, lw, 9), (lx, 522, lw * 0.5, 9)]
    p2_t = [(lx, 450, lw * 0.94, 9), (lx, 474, lw * 0.86, 9), (lx, 498, lw * 0.4, 9)]
    page = (f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="12" fill="{CARD}" filter="url(#shadow)"/>'
            + bars(title, BAR_DARK) + bars(p1 + p2, BAR)
            + f'<text x="{lx}" y="180" font-size="18" font-weight="500" fill="{INK}">Rydberg atoms</text>'
            + _figure(lx + 70, 278, lw - 140, 146))

    def over(rows, fill, top, bottom, start, extra=''):
        cover = f'<rect x="{lx - 6}" y="{top}" width="{lw + 12}" height="{bottom - top}" fill="{CARD}"/>'
        return ('<g opacity="0">' + cover + bars(rows, fill) + extra
                + anim('opacity', '0;0;1;1;0;0;1;1;0;0', loop, 0, start, start + 0.3, 5.55, 5.7, 6.85, 7.0, loop - 0.3, loop - 0.15, loop)
                + '</g>')

    body = [over(title_t, tint_dark, 92, 140, 1.6),
            over(p1_t, tint, 162, 256, 2.2, f'<text x="{lx}" y="180" font-size="18" font-weight="500" fill="{INK}">里德堡原子</text>'),
            over(p2_t, tint, 442, 536, 2.8)]
    body.append('<g opacity="0">' + key(930, 268, 'Alt') + show(loop, 5.4, 6.95) + '</g>')
    pts = [(900, 560), at['translate'], at['translate'], (950, 250), (950, 250), (900, 560)]
    ts = [0, 0.95, 1.2, 2.4, 7.3, 8.3]
    scene = page + tools + ''.join(body) + pointer(pts, loop, ts, press=[1.15])
    return frame('Translate a paper in place',
                 'The 文A button in the zoom column beside the paper is clicked. The title and each paragraph are redrawn '
                 'one after another in the reader’s language, “Rydberg atoms” becoming “里德堡原子”, while the figure '
                 'stays put. Holding Alt shows the original for a moment.', scene)


SCENES = [
    ('annotate', annotate),
    ('pen', pen),
    ('text-box', text_box),
    ('notebook', notebook),
    ('links', links),
    ('translate', translate),
]

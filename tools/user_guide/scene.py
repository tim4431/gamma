"""Shared pieces of the user guide's animations: the branding palette, the paper,
the notes panel, chips and buttons, the pointer, typing, and the SMIL helpers.

Every animation is one looping SVG (SMIL only, so it plays inside a plain <img> on
GitHub and on the site), light only, drawn on a 1400 x 620 canvas unless a scene
asks for another. Typed text goes through branding.typewriter() so the caret lands
on the last letter in any system font. Every keyTimes list ends at 1."""
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/branding'))
from branding import FONT, MONO, typewriter  # noqa: E402

ASSETS = ROOT / 'docs/user_guide/assets'
MATH = "'Cambria Math', Cambria, Georgia, 'Times New Roman', serif"

# The palette, from the branding scenes.
BG = '#f6f4ef'        # warm paper background
CARD = '#ffffff'      # a window or a page
EDGE = '#e3e0d8'      # card edges and rules
SOFT = '#f2f0ea'      # a block, a field, a chip
INK = '#1a1a18'       # text
MUTED = '#6b6a65'     # secondary text
BAR = '#cfccc4'       # body lines of a paper
BAR_DARK = '#8d8a82'  # a paper's title bars, bullets
AMBER = '#e8a020'     # the accent: pointer, strokes, active things
YELLOW = '#ffe28f'    # a highlight
SELECT = '#9bcdff'    # a text selection
BLUE = '#3a7bd5'      # callouts, links, a second person
GREEN = '#3f9a5c'     # success, a third person
RED = '#d9534f'
CHIP = '#ecdfc4'      # a folder / label chip
CHIP_INK = '#5a4a24'

W, H = 1400, 620


def keytimes(loop, *ts):
    return ';'.join(f'{t / loop:.4f}' for t in ts)


def anim(attr, values, loop, *ts, calc=None):
    """<animate> through `values` (semicolon-separated) at the seconds `ts`; the last
    moment must be `loop` so keyTimes ends at 1."""
    extra = f' calcMode="{calc}"' if calc else ''
    return f'<animate attributeName="{attr}" values="{values}" keyTimes="{keytimes(loop, *ts)}" dur="{loop}s" repeatCount="indefinite"{extra}/>'


def show(loop, start, end=None, fade=0.15):
    """Opacity 0 -> 1 at `start`, back to 0 at `end` (default: just before the loop restarts)."""
    if end is None or end >= loop - 0.3:
        return anim('opacity', '0;0;1;1;0;0', loop, 0, start, start + fade, loop - 0.3, loop - 0.15, loop)
    return anim('opacity', '0;0;1;1;0;0', loop, 0, start, start + fade, end, end + fade, loop)


def hide(loop, start, end=None, fade=0.15):
    """The opposite: visible until `start`, then gone (back at `end`, or at the restart)."""
    if end is None or end >= loop - 0.3:
        return anim('opacity', '1;1;0;0;1', loop, 0, start, start + fade, loop - 0.3, loop)
    return anim('opacity', '1;1;0;0;1;1', loop, 0, start, start + fade, end, end + fade, loop)


def move(loop, dx, dy, start, end, back=True):
    """A translate from (0,0) to (dx,dy) between `start` and `end`, undone at the restart."""
    values = f'0 0;0 0;{dx} {dy};{dx} {dy};0 0' if back else f'0 0;0 0;{dx} {dy};{dx} {dy};{dx} {dy}'
    return (f'<animateTransform attributeName="transform" type="translate" values="{values}" '
            f'keyTimes="{keytimes(loop, 0, start, end, loop - 0.3, loop)}" dur="{loop}s" repeatCount="indefinite"/>')


def typed(text, x, y, loop, start, end, size=22, family=FONT, fill=INK, label=None):
    return typewriter(text, x, y, loop, start, end, size=size, family=family, fill=fill, label=label)


def pointer(points, loop, times, press=()):
    """The amber cursor dot travelling through (x, y) waypoints at the given seconds.
    `press` lists moments where a click ring pulses."""
    if times[-1] < loop:  # keyTimes must end at 1: hold the last waypoint, then jump back
        points, times = [*points, points[-1], points[0]], [*times, loop - 0.01, loop]
    values = ';'.join(f'{x} {y}' for x, y in points)
    rings = ''
    for t in press:
        rings += (f'<circle r="9" fill="none" stroke="{AMBER}" stroke-width="2" opacity="0">'
                  + anim('r', '9;9;22;22', loop, 0, t, t + 0.35, loop)
                  + anim('opacity', '0;0;0.8;0;0', loop, 0, t, t + 0.05, t + 0.35, loop) + '</circle>')
    return (f'<g><circle r="10.5" fill="#ffffff" opacity="0.9"/><circle r="9" fill="{AMBER}"/><circle r="4" fill="#ffffff"/>{rings}'
            f'<animateTransform attributeName="transform" type="translate" values="{values}" '
            f'keyTimes="{keytimes(loop, *times)}" dur="{loop}s" repeatCount="indefinite"/></g>')


def paper(x, y, w, h, lines, seed=0, title=True):
    """A paper page: title bars and body lines of varied length."""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{CARD}" filter="url(#shadow)"/>']
    top = y + 54
    if title:
        out += [f'<rect x="{x + 50}" y="{top}" width="{w * 0.55:.0f}" height="14" rx="4" fill="{BAR_DARK}"/>',
                f'<rect x="{x + 50}" y="{top + 26}" width="{w * 0.4:.0f}" height="9" rx="4" fill="{BAR_DARK}"/>']
        top += 76
    for i in range(lines):
        length = [1, 1, 0.86, 1, 0.94, 1, 0.7][(i + seed) % 7]
        out.append(f'<rect x="{x + 50}" y="{top + i * 24}" width="{(w - 100) * length:.0f}" height="9" rx="4.5" fill="{BAR}"/>')
    return '\n'.join(out)


def line_y(y, i, title=True):
    """The top of a 24 px selection box over body line `i` of a paper() placed at `y`."""
    return y + 54 + (76 if title else 0) + i * 24 - 7


def panel(x, y, w, h, title, body=''):
    """A white window with a title row and a rule under it."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#shadow)"/>'
            f'<text x="{x + 36}" y="{y + 56}" font-size="24" font-weight="600" fill="{INK}">{title}</text>'
            f'<path d="M{x + 36} {y + 80} H{x + w - 36}" stroke="{EDGE}" stroke-width="1.5"/>{body}')


def block(x, y, w, h, color=None):
    """A note block: a soft rounded box, with an optional colour bar on its left."""
    out = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{SOFT}"/>'
    if color:
        out += f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{color}"/>'
    return out


def bullet(x, y):
    return f'<circle cx="{x}" cy="{y}" r="5" fill="{BAR_DARK}"/>'


def chip(x, y, text, fill=CHIP, ink=CHIP_INK, size=18):
    w = int(len(text) * size * 0.56 + 26)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{size + 16}" rx="{(size + 16) / 2}" fill="{fill}"/>'
            f'<text x="{x + w / 2:.0f}" y="{y + size + 2}" text-anchor="middle" font-size="{size}" font-weight="500" fill="{ink}">{text}</text>')


def button(x, y, text, primary=False, size=19):
    w = int(len(text) * size * 0.56 + 36)
    fill, ink, stroke = (AMBER, '#ffffff', 'none') if primary else (CARD, INK, EDGE)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{size + 22}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{x + w / 2:.0f}" y="{y + size + 5}" text-anchor="middle" font-size="{size}" font-weight="600" fill="{ink}">{text}</text>')


def field(x, y, w, h=52, placeholder=''):
    """A text field; a placeholder in muted text when given."""
    out = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{SOFT}" stroke="{EDGE}"/>'
    if placeholder:
        out += f'<text x="{x + 20}" y="{y + h / 2 + 7:.0f}" font-size="20" fill="{MUTED}">{placeholder}</text>'
    return out


def stroke(d, loop, start, end, color=AMBER, width=4, length=900, end_at=None):
    """A pen stroke drawn along `d` between `start` and `end` (a dash-offset sweep), gone at the restart."""
    off = end_at if end_at is not None else loop - 0.3
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" '
            f'stroke-dasharray="{length}" stroke-dashoffset="{length}">'
            + anim('stroke-dashoffset', f'{length};{length};0;0;{length}', loop, 0, start, end, off, loop)
            + anim('opacity', '0;0;1;1;0', loop, 0, start - 0.05, start, off, loop) + '</path>')


def caption(text, x=W / 2, y=H - 26):
    return f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="middle" font-size="19" fill="{MUTED}">{text}</text>'


def frame(title, desc, body, w=W, h=H):
    """The whole document: defs (a card's #shadow, and #lift for small things such as a
    popover or a tool column), the warm background with the amber wave, then `body`."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">{desc}</desc>
  <defs>
    <filter id="shadow" x="-10%" y="-10%" width="125%" height="125%">
      <feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#000000" flood-opacity="0.10"/>
    </filter>
    <filter id="lift" x="-60%" y="-40%" width="220%" height="200%">
      <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#000000" flood-opacity="0.10"/>
    </filter>
  </defs>
  <rect width="{w}" height="{h}" fill="{BG}"/>
  <g fill="none" stroke="{AMBER}" stroke-width="3" opacity="0.2" transform="scale({w / 1600:.4f} {h / 1000:.4f})">
    <path d="M-60 880 C220 1100 560 1000 820 940 S1300 900 1660 1010"/>
  </g>
  <g font-family="{FONT}">
{body}
  </g>
</svg>
'''


def write(stem, svg):
    """Write docs/user_guide/assets/<stem>.svg, checking it is well-formed and every
    keyTimes list ends at 1 (a browser drops the animation otherwise)."""
    ASSETS.mkdir(parents=True, exist_ok=True)
    target = ASSETS / f'{stem}.svg'
    target.write_text(svg, encoding='utf-8', newline='\n')
    ET.parse(target)
    for kt in re.findall(r'keyTimes="([^"]*)"', svg):
        parts = kt.split(';')
        if float(parts[-1]) != 1 or float(parts[0]) != 0 or any(float(a) > float(b) for a, b in zip(parts, parts[1:])):
            raise SystemExit(f'{stem}: keyTimes must run from 0 to 1 and never decrease: {kt}')
    print(target.relative_to(ROOT).as_posix())


def key(x, y, text, size=18):
    """A keycap with the name of a key or a chord ("Tab", "Shift+Enter"); wrap it in a
    <g> with show() for when it is pressed."""
    w = int(len(text) * size * 0.6 + 28)
    h = size + 18
    return (f'<rect x="{x}" y="{y + 3}" width="{w}" height="{h}" rx="9" fill="{EDGE}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5"/>'
            f'<text x="{x + w / 2:.0f}" y="{y + h / 2 + size * 0.36:.0f}" text-anchor="middle" font-size="{size}" font-weight="600" fill="{INK}">{text}</text>')


def _polyline(d, steps=32):
    """The points along an absolute M/L/H/V/C/Q/Z path, each curve cut into `steps`."""
    tokens = re.findall(r'[MLHVCQZ]|-?\d*\.?\d+', d)
    pts, cmd, i, start = [], None, 0, (0, 0)
    num = lambda: float(tokens[i])
    while i < len(tokens):
        if tokens[i] in 'MLHVCQZ':
            cmd = tokens[i]
            i += 1
            if cmd == 'Z':
                pts.append(start)
            continue
        here = pts[-1] if pts else (0, 0)
        if cmd in 'ML':
            p = (num(), float(tokens[i + 1]))
            i += 2
            pts.append(p)
            if cmd == 'M':
                start, cmd = p, 'L'
        elif cmd in 'HV':
            pts.append((num(), here[1]) if cmd == 'H' else (here[0], num()))
            i += 1
        else:
            k = 3 if cmd == 'C' else 2
            ctrl = [(float(tokens[i + 2 * j]), float(tokens[i + 2 * j + 1])) for j in range(k)]
            i += 2 * k
            ps = [here, *ctrl]
            for s in range(1, steps + 1):
                t = s / steps
                q = ps
                while len(q) > 1:  # de Casteljau
                    q = [((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in zip(q, q[1:])]
                pts.append(q[0])
    return pts


def path_length(d):
    """The length of a path written with absolute M/L/H/V/C/Q/Z commands (for stroke())."""
    pts = _polyline(d)
    return sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:]))


def path_points(d, n):
    """n + 1 points evenly spaced along the path, so a pointer moving through them at even
    times keeps pace with a stroke() drawing the same path."""
    pts = _polyline(d)
    seg = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:])]
    total, out, j, run = sum(seg), [pts[0]], 0, 0.0
    for k in range(1, n + 1):
        want = total * k / n
        while j < len(seg) - 1 and run + seg[j] < want:
            run += seg[j]
            j += 1
        f = (want - run) / seg[j] if seg[j] else 0
        a, b = pts[j], pts[j + 1]
        out.append((round(a[0] + f * (b[0] - a[0]), 1), round(a[1] + f * (b[1] - a[1]), 1)))
    return out


_GLYPHS = {
    'pen': f'<g transform="rotate(45)"><rect x="-4" y="-13" width="8" height="19" rx="2" fill="none" stroke="{INK}" stroke-width="2"/>'
           f'<path d="M-4 6 L0 13 L4 6 Z" fill="{INK}"/></g>',
    'highlighter': f'<g transform="rotate(45)"><rect x="-5.5" y="-13" width="11" height="15" rx="2" fill="{YELLOW}" stroke="{INK}" stroke-width="1.8"/>'
                   f'<path d="M-3.5 2 V11 L3.5 8 V2 Z" fill="{INK}"/></g>',
    'eraser': f'<g transform="rotate(-35)"><rect x="-12" y="-6.5" width="24" height="13" rx="3" fill="none" stroke="{INK}" stroke-width="2"/>'
              f'<rect x="-12" y="-6.5" width="9" height="13" rx="3" fill="{INK}"/></g>',
    'lasso': f'<ellipse cx="1" cy="-3" rx="11" ry="7.5" fill="none" stroke="{INK}" stroke-width="2" stroke-dasharray="3.5 3"/>'
             f'<path d="M-5 4 C-7 9 -3 12 1 13" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>',
    'text': f'<text y="8" text-anchor="middle" font-size="24" font-weight="600" fill="{INK}">T</text>',
    'undo': f'<path d="M-9 -3 H3 C8 -3 11 1 11 4 C11 8 8 11 3 11 H-3 M-4 -8 L-9 -3 L-4 2" fill="none" stroke="{INK}" '
            'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    'minus': f'<path d="M-9 0 H9" stroke="{INK}" stroke-width="2.4" stroke-linecap="round"/>',
    'plus': f'<path d="M-9 0 H9 M0 -9 V9" stroke="{INK}" stroke-width="2.4" stroke-linecap="round"/>',
    'fit': f'<path d="M-11 0 H11 M-6 -5 L-11 0 L-6 5 M6 -5 L11 0 L6 5" fill="none" stroke="{INK}" stroke-width="2.2" '
           'stroke-linecap="round" stroke-linejoin="round"/>',
    'translate': f'<text y="7" text-anchor="middle" font-size="19" font-weight="600" fill="{INK}">文A</text>',
}


def strip(x, y, tools, loop=None, picks=()):
    """A vertical tool column (the viewer's zoom column, or the pen's tool strip) of
    `tools` named in _GLYPHS. `picks` = [(tool, start, end)] lights a tool up while it is
    armed (end None: until the restart). Returns (svg, {tool: (cx, cy)})."""
    cell, w = 56, 60
    centres = {t: (x + w / 2, y + 6 + cell / 2 + i * cell) for i, t in enumerate(tools)}
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{len(tools) * cell + 12}" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="1.5" filter="url(#lift)"/>']
    for tool, start, end in picks:
        cx, cy = centres[tool]
        out.append(f'<rect x="{cx - 23}" y="{cy - 23}" width="46" height="46" rx="10" fill="#fbefd6" stroke="{AMBER}" stroke-width="1.5" opacity="0">'
                   + show(loop, start, end) + '</rect>')
    for tool, (cx, cy) in centres.items():
        out.append(f'<g transform="translate({cx:.0f} {cy:.0f})">{_GLYPHS[tool]}</g>')
    return ''.join(out), centres


_MARK = (ROOT / 'design/brand/marks/favicon.svg').read_text(encoding='utf-8')
_MARK = re.sub(r'<!--.*?-->|\s+id="[^"]*"', '', re.search(r'<svg\b[^>]*>(.*)</svg>', _MARK, re.S).group(1), flags=re.S)
_MARK = re.sub(r'>\s+<', '><', _MARK.strip())


def mark(x, y, size=32):
    """The Gamma mark (the app icon) as a size x size square at (x, y): an extension's
    toolbar badge, the corner of a Gamma window."""
    return f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 32 32">{_MARK}</svg>'

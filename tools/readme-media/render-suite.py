"""Re-render freshly captured README cases at 25 fps, as small animated WebP images.

Raw captures and timing manifests stay in ignored artifacts/readme-media/suite
(the connector's take, recorded by record-connector.mjs on take.mjs, in
artifacts/readme-media/connector). Notes, library, search and the connector
are retina captures rendered by compose.py; metadata, reference-links and the
AI previews are the earlier WebM recordings.
"""
import argparse
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess

from imageio_ffmpeg import get_ffmpeg_exe
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFilter
from compose import Capture, Camera, FULL, Take, focus, publish_demo, quiet
from media_output import ROOT, FRAME, concat_segments, encode_webp, publish
from render_feature_demos import CASES as FEATURES

FF = get_ffmpeg_exe()
SUITE = ROOT / 'artifacts/readme-media/suite'
OUT = ROOT / 'docs/assets/demos'
NAMES = ['notes', 'library', 'search', 'metadata', 'agent', 'download-and-chat', 'reference-links', 'connector']


def run(*args):
    subprocess.run([FF, '-v', 'error', '-y', *map(str, args)], check=True)


def read(directory, name):
    return json.loads((directory / name).read_text(encoding='utf-8'))


def duration(source):
    log = subprocess.run([FF, '-hide_banner', '-i', str(source)], capture_output=True, text=True).stderr
    m = re.search(r'Duration: (\d+):(\d+):([\d.]+)', log)
    if not m:
        raise ValueError(f'No video duration: {source}')
    return int(m[1])*3600 + int(m[2])*60 + float(m[3])


class Browser:
    """The connector take's three screencasts as one capture-like timeline under a browser toolbar.

    `pieces` are (state, capture, start, end) in that capture's seconds, laid
    back to back; `t(state, local)` is the timeline second of a piece's
    capture second, which the edit and the camera are written in. States: the
    APS page, a beat with the pointer drawn on the toolbar icon, the popup
    hung under the icon over the page, the paper in Gamma. Frames are the
    1440 x 900 card: the recorder's toolbar screenshot above the 860 px page,
    and in the popup state the popup's own capture cut to its sampled height,
    placed where Chrome hangs it (right-aligned under the icon). The toolbar's
    badge turns to the saved check once the popup says so.
    """
    def __init__(self, take, pieces):
        timing, self.marks = take.timing, take.marks
        self.toolbar = timing['toolbar']
        self.heights = [(t, h) for t, h in timing['heights'] if h]
        self.popup_at = (timing['popup']['x'], timing['popup']['y'])
        self.popup_w = timing['popup']['width']
        self.icon = timing['icon']
        self.scale = pieces[0][1].scale
        self.pieces, self.starts, t = pieces, [], 0.0
        for _, _, a, b in pieces:
            self.starts.append(t)
            t += b - a
        self.duration = t
        self.bars = {k: PIL.Image.open(take.dir / f'toolbar-{k}.png').convert('RGB') for k in ('page', 'hover', 'open', 'saved', 'gamma')}
        self.dir = take.dir   # what publish_demo reports as the capture

    def t(self, state, local):
        """Timeline seconds of capture second `local` in piece `state`."""
        k = [p[0] for p in self.pieces].index(state)
        _, _, a, b = self.pieces[k]
        # Parenthesised so a piece's first frame lands on its start exactly, not
        # an ulp before it, in the previous piece.
        return self.starts[k] + (min(max(local, a), b) - a)

    def index(self, t):
        k = max(i for i, s in enumerate(self.starts) if s <= max(t, 0)) if t > 0 else 0
        state, capture, a, _ = self.pieces[k]
        local = a + t - self.starts[k]
        if state != 'open':
            return (k, capture.index(local), 0, state)
        height = next((h for s, h in reversed(self.heights) if s <= local), self.heights[0][1])
        return (k, capture.index(local), height, 'saved' if local >= self.marks['bSaved'] else 'open')

    @lru_cache(maxsize=8)
    def image(self, key):
        k, i, height, bar = key
        state, capture, _, _ = self.pieces[k]
        s = self.scale
        card = PIL.Image.new('RGB', (1440 * s, 900 * s), 'white')
        page = capture.image(i)
        if state in ('hover', 'open'):
            # The APS page as the pointer left it for the toolbar. The page's
            # drawn arrow still rests on its top row under the icon (its
            # screencast sent no frame for the arrow's hiding): that strip
            # comes from the first frame, the same still header without it.
            aps = self.pieces[0][1]
            page = aps.image(aps.index(self.marks['aHidden'] + 0.2)).copy()
            x0, x1 = (round(v * s) for v in (self.icon['x'] - 60, self.icon['x'] + self.icon['width'] + 30))
            page.paste(aps.image(0).crop((x0, 0, x1, 40 * s)), (x0, 0))
        card.paste(page, (0, self.toolbar * s))
        card.paste(self.bars[bar], (0, 0))
        if state == 'open':
            popup = capture.image(i).crop((0, 0, self.popup_w * s, height * s))
            x, y = (v * s for v in self.popup_at)
            radius = 8 * s
            mask = PIL.Image.new('L', popup.size, 0)
            PIL.ImageDraw.Draw(mask).rounded_rectangle((0, 0, popup.width - 1, popup.height - 1), radius, fill=255)
            shadow = PIL.Image.new('L', card.size, 0)
            PIL.ImageDraw.Draw(shadow).rounded_rectangle((x, y + 4 * s, x + popup.width, y + popup.height + 4 * s), radius, fill=70)
            card = PIL.Image.composite(PIL.Image.new('RGB', card.size, (20, 24, 32)), card, shadow.filter(PIL.ImageFilter.GaussianBlur(10 * s)))
            card.paste(popup, (x, y), mask)
            PIL.ImageDraw.Draw(card).rounded_rectangle((x, y, x + popup.width - 1, y + popup.height - 1), radius, outline=(208, 214, 223), width=s)
        return card


def connector(_suite=None):
    # record-connector.mjs's kept take, in artifacts/readme-media/connector
    # (not the suite/ folder render() passes): timing.json exists only when
    # the paper was saved with its PDF, in Quantum/Neutral atoms, labeled,
    # and still showed both after a reload.
    take = Take(ROOT / 'artifacts/readme-media/connector')
    m, timing = take.marks, take.timing
    page, popup, gamma = (Capture(take.dir / timing['pieces'][k]) for k in ('page', 'popup', 'gamma'))
    # The paper in Gamma is shown once page 1 is painted with its chips up and
    # the metadata look-up that opening it starts has settled (no spinner, no
    # status pill), and the pointer rests under the Open in Gamma spot.
    shown = max(m['cSettled'], m.get('cParked', 0) + 0.1)
    # The popup comes up 0.85 s before the pointer heads for the folder: a
    # beat to read the title and authors (it is still before that).
    opened = max(m['bReady'], m['bFolderGo'] - 0.85)
    # The press on Save plays up to the frame before the progress row shows:
    # its "saving to your library…" lasts about 0.1 s before the PDF fetch's
    # words, too short to read, so the cut goes from the pressed button
    # straight to Saved.
    pressed = max(m['bSave'] + 0.06, m['bProgress'] - 0.02)
    # Gamma holds 1.3 s in the popup framing (the title and both chips read),
    # then 0.5 s back to the whole window, which holds 1.2 s before the loop:
    # the one view of the paper open in Gamma, PDF and notes side by side.
    HOLD = 1.3
    gamma_end = min(m['cEnd'], shown + HOLD + 0.5 + 1.2)
    b = Browser(take, [
        ('page', page, 0.0, m['aIcon']),
        ('hover', page, m['aHidden'], m['aHidden'] + 0.2),
        ('open', popup, opened, m['bEnd']),
        ('gamma', gamma, shown, gamma_end),
    ])
    T = b.t
    POINTER = 1.25
    segments = [
        # The APS article, the badge on the Connector's icon: held still for
        # the eye to find the paper and the toolbar.
        (T('page', max(0.1, m['aGlide'] - 0.6)), T('page', m['aGlide'])),
        # Up to the icon (pointer travel, sped up), and the icon under it.
        (T('page', m['aGlide']), T('page', m['aIcon']), POINTER),
        (T('hover', m['aHidden']), T('hover', m['aHidden'] + 0.2)),
        # The popup, with the paper's title and authors: a beat to read them.
        (T('open', opened), T('open', m['bFolderGo'])),
        # The folder menu and Quantum/Neutral atoms, then into Labels.
        (T('open', m['bFolderGo']), T('open', m['bType']), POINTER),
        # The label typed at its own speed; the suggestions close.
        (T('open', m['bType']), T('open', m['bTyped'])),
        (T('open', m['bTyped']), T('open', m['bFilled']), POINTER),
        # The filled popup holds about 0.85 s (the pointer rests on Save),
        # then Save is pressed and starts saving.
        (T('open', m['bFilled']), T('open', pressed)),
        # A hard cut past the save's wait (the server, then the browser,
        # fetching the PDF): the pointer has not moved, so the popup just
        # turns into "Saved to your library." with the badge now a check; a
        # dissolve here would ghost the form under it. It holds 0.8 s.
        (T('open', max(m['bSaved'], m['bOpenGo'] - 0.8)), T('open', m['bOpenGo']), 1, 0),
        # On to Open in Gamma, and its press.
        (T('open', m['bOpenGo']), T('open', m['bPress']), POINTER),
        (T('open', m['bPress']), T('open', m['bEnd'])),
        # Past the new tab's load: the pieces lie back to back, so this joins
        # the press with no dissolve, a hard cut like the browser's own switch
        # to the new tab (a dissolve would ghost the popup over the notes).
        # Gamma comes in at the popup's framing, the notes column's title with
        # its folder and label chips and the pointer just below where Open in
        # Gamma was; that holds 1.3 s (HOLD), then the camera pulls back to
        # the whole window, which holds before the loop dissolves to the APS
        # page (whole window to whole window).
        (T('gamma', shown), T('gamma', gamma_end)),
    ]
    # The popup's framing: the popup and the icon above it, zoomed in during
    # the pointer's glide to the icon. The Gamma tab cuts in at that same
    # framing, where the notes column's title, chips and the PDF's right half
    # read at README size; after the hold the camera goes back to the whole
    # window for the loop. Two moves.
    height = max(h for _, h in b.heights)
    box = {'x': b.popup_at[0], 'y': timing['icon']['y'], 'width': b.popup_w, 'height': b.popup_at[1] + height - timing['icon']['y']}
    g = T('gamma', shown)
    camera = (Camera()
              .move(T('page', m['aGlide']), focus(box, margin=30, max_zoom=1.6), 0.5)
              .move(g + HOLD, FULL, 0.5))
    return publish_demo('connector', b, segments, camera, take.dir, loop_fade=0.3, quality=70)


def notes(directory):
    m = read(directory, 'notes_marks.json')
    capture, f = Capture(m['frames']), m['framing']
    # The note fills the width at 130% interface size, so the typing stays in
    # the full view; the camera closes in on the equation and the new sheet
    # while the pen sketches, and pulls back for the end.
    camera = (Camera()
              .move(m['sheetAt'] + 0.3, focus(f['equation'], f['sketch'], margin=30), 0.6)
              .move(m['m1'] - 1.3, FULL, 0.6))
    return publish_demo('notes', capture, quiet(capture, max(0, m['m0'] - 0.25), m['m1']), camera, directory)


def library(directory):
    m = read(directory, 'library_marks.json')
    capture, f = Capture(m['frames']), m['framing']
    # Close in on the search panel while it fills and narrows; pull back as
    # the paper opens (the frame changes anyway), then settle on the match.
    camera = (Camera()
              .move(m['m0'] + 0.6, focus(f['popover'], margin=30), 0.75)
              .move(m['mOpen'] + 0.35, FULL, 0.75)
              .move(m['mMark'] + 0.6, focus(f['mark'], margin=150, max_zoom=1.5), 0.75))
    return publish_demo('library', capture, quiet(capture, m['m0'] - 0.25, m['tEnd'] - 0.8), camera, directory)


def search(directory):
    m = read(directory, 'search_marks.json')
    capture, f = Capture(m['frames']), m['framing']
    # Close in on the find bar and the match it marks, pan to the palette, and pull
    # back as the label's papers open (the page changes there anyway). The end
    # differs from the start everywhere, so the loop's dissolve is kept short.
    camera = (Camera()
              .move(m['m0'] + 0.3, focus(f['find'], f['match'], margin=60), 0.5)
              .move(m['palette'] + 0.1, focus(f['palette'], margin=40), 0.5)
              .move(m['label'] + 0.1, FULL, 0.5))
    return publish_demo('search', capture, quiet(capture, m['m0'] - 0.6, m['end']), camera, directory, loop_fade=0.3)


def shot(name, directory):
    filenames = {'metadata': 'video_meta_path.txt', 'agent': 'video_agent.txt',
                 'download-and-chat': 'video_path.txt', 'reference-links': 'video_links_path.txt'}
    source = Path((directory / filenames[name]).read_text().strip())
    if name == 'metadata':
        m = read(directory, 'meta_zoom.json')
        # Fixed detail crop keeps both popovers and the Share control readable.
        return source, m['m0'], m['tEnd']-0.5, 'crop=848:530:592:0'
    if name == 'agent':
        m = read(directory, 'agent_marks.json')
        return source, m['m0']-0.25, m['mEnd']-1, None
    if name == 'reference-links':
        m = read(directory, 'links_zoom.json')
        cx = (m['R1']['x'] + m['R2']['x']) / 2
        cy = (m['R1']['y'] + m['R2']['y']) / 2
        # Leave enough room on the right for the centered Fetch modal.
        x = max(120, min(480, round(cx-480))) // 2 * 2
        y = max(0, min(300, round(cy-300))) // 2 * 2
        return source, m['m0']-0.25, m['tEnd'], f'crop=960:600:{x}:{y}'
    m = read(directory, 'hero-marks.json')
    return source, m['m0']-0.25, min(duration(source), m['end']), None


def render(name):
    directory = SUITE / name
    if name in RETINA:
        return RETINA[name](directory)
    source, start, end, crop = shot(name, directory)
    if end <= start:
        raise ValueError(f'Invalid timeline: {name}')
    # Keep up to 1.4 s of long static holds. No interpolation or blanket speedup:
    # pointer movement, typing, and streaming retain their real capture cadence.
    log = subprocess.run([FF, '-hide_banner', '-i', str(source), '-vf', 'freezedetect=n=0.0003:d=2.5',
                          '-an', '-f', 'null', '-'], capture_output=True, text=True, check=True).stderr
    cuts, frozen = [], None
    for kind, value in re.findall(r'freeze_(start|end): ([\d.]+)', log):
        t = float(value)
        if kind == 'start':
            frozen = t
        elif frozen is not None:
            a, b = max(start, frozen)+0.7, min(end, t)-0.7
            if b-a > 1:
                cuts.append((a, b))
            frozen = None
    segments, cursor = [], start
    for a, b in cuts:
        if a > cursor and b < end:
            segments.append((cursor, a)); cursor = b
    segments.append((cursor, end))
    parts = [f'[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS' for a, b in segments]
    tail = 'fps=25' + (f',{crop}' if crop else '') + f',scale=1440:-2:flags=lanczos,{FRAME},setsar=1'
    master = directory / 'master.mkv'
    concat_segments(master, [source], parts, tail)
    output = directory / 'rendered.webp'
    # This shot contains full-page scrolling; a smaller raster and quality 75
    # keep it compact while retaining the actual pointer/streaming cadence.
    width, quality, effort = (1040, 75, 4) if name in ('download-and-chat', 'reference-links') else (1120, 85, 6)
    report = {'name': name, 'fps': 25, 'width': width, 'quality': quality, 'effort': effort,
              **encode_webp(master, output, f'fps=25,scale={width}:-2:flags=lanczos', quality, effort),
              'segments': segments, 'source': str(source), 'crop': crop}
    (directory / 'render.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    # The agent and download-and-chat captures feed no README slot; they stay
    # scratch previews (the README's AI story is render_feature_demos.py).
    target = directory / 'preview.webp' if name in ('agent', 'download-and-chat') else OUT / f'demo-{name}.webp'
    publish(output, target)
    print(json.dumps(report), flush=True)


RETINA = {'notes': notes, 'library': library, 'search': search, 'connector': connector}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='+', choices=NAMES + [*FEATURES, 'all'])
    args = parser.parse_args()
    published = [n for n in NAMES if n not in ('agent', 'download-and-chat')] + [*FEATURES]
    for name in published if 'all' in args.cases else args.cases:
        if name in FEATURES:
            FEATURES[name]()
        else:
            render(name)

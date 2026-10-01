"""Re-render freshly captured README cases at 25 fps, as small animated WebP images.

Raw captures and timing manifests stay in ignored artifacts/readme-media/suite.
Notes, library, search and the connector are retina captures rendered by
compose.py; metadata, reference-links and the AI previews are the earlier WebM
recordings.
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
from compose import Capture, Camera, FULL, focus, publish_demo, quiet
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
    """The connector's three captures as one capture-like timeline under a browser toolbar.

    `pieces` are (state, capture, start, end) in that capture's seconds, played
    back to back: the APS page, a beat with the pointer on the toolbar icon,
    the popup over the page, the paper in Gamma. Frames are the 1440 x 900
    card: the recorder's toolbar screenshot above the 860 px page, and in the
    popup piece the popup's own capture cut to its height, hung under the icon.
    """
    def __init__(self, directory, marks, heights, toolbar, pieces):
        self.dir, self.marks, self.toolbar = directory, marks, toolbar
        self.heights = [(t, h) for t, h in heights if h]
        self.scale = pieces[0][1].scale
        self.pieces, self.starts, t = pieces, [], 0.0
        for _, _, a, b in pieces:
            self.starts.append(t)
            t += b - a
        self.duration = t
        self.bars = {k: PIL.Image.open(directory / f'toolbar-{k}.png').convert('RGB') for k in ('page', 'hover', 'open', 'gamma')}
        # The popup's resting place: right-aligned under the icon, as Chrome hangs it.
        icon = marks['icon']
        self.popup_at = (round(icon['x'] + icon['width'] + 4 - 360), toolbar + 4)

    def at(self, name):
        """Timeline seconds where piece `name` starts."""
        return self.starts[[p[0] for p in self.pieces].index(name)]

    def index(self, t):
        k = max(i for i, s in enumerate(self.starts) if s <= max(t, 0)) if t > 0 else 0
        state, capture, a, _ = self.pieces[k]
        local = a + t - self.starts[k]
        height = next((h for s, h in reversed(self.heights) if s <= local), self.heights[0][1]) if state == 'open' else 0
        return (k, capture.index(local), height)

    @lru_cache(maxsize=8)
    def image(self, key):
        k, i, height = key
        state, capture, _, _ = self.pieces[k]
        s = self.scale
        card = PIL.Image.new('RGB', (1440 * s, 900 * s), 'white')
        page = self.pieces[0][1].image(self.pieces[0][1].index(self.marks['aHidden'] + 0.2)) if state == 'open' else capture.image(i)
        card.paste(page, (0, self.toolbar * s))
        card.paste(self.bars[state], (0, 0))
        if state == 'open':
            popup = capture.image(i).crop((0, 0, 360 * s, height * s))
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


def connector(directory):
    m = read(directory, 'connector_marks.json')
    marks, verified = m['marks'], m.get('verified', {})
    if not (verified.get('saved') and verified.get('pdf') and verified.get('folder') and verified.get('labels')):
        raise ValueError('Capture the paper saved with its PDF, a folder and a label first')
    a, b, c = (Capture(directory / s) for s in 'abc')
    browser = Browser(directory, marks, m['heights'], m['toolbar'], [
        ('page', a, max(0, marks['a0'] - 0.3), marks['a1']),
        ('hover', a, marks['aHidden'], marks['aHidden'] + 0.35),
        ('open', b, marks['bReady'] + 0.1, marks['b1'] + 0.35),
        ('gamma', c, max(0, marks['cReady'] - 0.5), marks['c1']),
    ])
    # Real speed throughout, the save's wait included. The camera closes in
    # on the popup as it opens and pulls back as its link is clicked; the
    # paper opening in Gamma dissolves in, in the full view.
    cut = browser.at('gamma')
    popup = {'x': browser.popup_at[0], 'y': browser.popup_at[1], 'width': 360, 'height': max(h for _, h in browser.heights)}
    camera = (Camera()
              .move(browser.at('hover') - 0.25, focus(popup, margin=30), 0.5)
              .move(cut - 0.6, FULL, 0.5))
    return publish_demo('connector', browser, [(0, cut), (cut, browser.duration)], camera, directory, loop_fade=0.4)


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

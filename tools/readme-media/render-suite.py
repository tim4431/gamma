"""Re-render freshly captured README cases at 25 fps, as small animated WebP images.

Raw captures and timing manifests stay in ignored artifacts/readme-media/suite.
Notes and library are retina captures rendered by compose.py; the other cases
are the earlier WebM recordings.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from imageio_ffmpeg import get_ffmpeg_exe
from compose import Capture, Camera, FULL, focus, publish_demo, quiet
from media_output import ROOT, FRAME, concat_segments, encode_webp, publish

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


def connector(directory):
    m = read(directory, 'conn_marks.json')
    a0, a1 = max(0, m['a0'] - 0.2), m['a1']
    b0, b1 = max(0, m['b0'] - 0.2), m['b2'] + 0.1
    c0 = max(0, m['cReady'] + 0.3)
    # The real popup is composited over its actual arXiv tab, as in the original
    # recipe. Only the popup is enlarged; the arXiv background stays fixed.
    bg = directory / 'background.png'
    run('-ss', a1-0.1, '-i', m['videoA'], '-frames:v', '1', bg)
    h = (m['popupH'] + 1) // 2 * 2
    enc = ['-an', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'yuv420p']
    a, b, c = [directory / f'part-{s}.mkv' for s in 'abc']
    run('-ss', a0, '-t', a1-a0, '-i', m['videoA'], '-vf', 'fps=25,setsar=1', *enc, a)
    run('-loop', '1', '-framerate', '25', '-i', bg, '-ss', b0, '-t', b1-b0, '-i', m['videoB'],
        '-filter_complex', f'[1:v]fps=25,crop=360:{h}:0:0,scale=504:-2:flags=lanczos,pad=iw+4:ih+4:2:2:0xd0d6df[p];[0:v][p]overlay=908:20:shortest=1,setsar=1',
        '-t', b1-b0, *enc, b)
    run('-ss', c0, '-t', min(2.8, m['c1']-c0), '-i', m['videoC'], '-vf', 'fps=25,setsar=1', *enc, c)
    master = directory / 'composited.mkv'
    run('-i', a, '-i', b, '-i', c, '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0', *enc, master)
    return master, 0, duration(master), None


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
    if name == 'connector':
        return connector(directory)
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
    # scratch previews (the README's AI story is render-feature-demos.py).
    target = directory / 'preview.webp' if name in ('agent', 'download-and-chat') else OUT / f'demo-{name}.webp'
    publish(output, target)
    print(json.dumps(report), flush=True)


RETINA = {'notes': notes, 'library': library, 'search': search}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    features = ['annotate-and-ink', 'native-agentic', 'agentic-notes']   # render-feature-demos.py
    parser.add_argument('cases', nargs='+', choices=NAMES+features+['all'])
    args = parser.parse_args()
    published = [n for n in NAMES if n not in ('agent', 'download-and-chat')] + features
    for name in published if 'all' in args.cases else args.cases:
        if name in features:
            subprocess.run([sys.executable, str(Path(__file__).with_name('render-feature-demos.py')), name], check=True)
        else:
            render(name)

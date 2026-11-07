"""The website's cuts of the README demos, written to sites/site/media/: the front
page's two shorter clips and the stills of its three clips, which it shows until a
clip comes near, and for good without script or under prefers-reduced-motion.

Run it after re-recording one of these demos; the site's build copies the results
as they are. A cut keeps the demo's frames byte for byte. Only its first frame is
encoded again, whole, since `encode_master` frames carry just the tiles that changed.
"""
from pathlib import Path

from PIL import Image

from media_output import ROOT, _chunk, _still, check_webp

DEMOS = ROOT / 'docs/assets/demos'
OUT = ROOT / 'sites/site/media'
# Cut → the demo, its first and last frames, and how long the last one holds (ms).
CUTS = {
    'demo-ink-marks': ('demo-ink-import', 98, 215, 600),       # the paper, past the import wizard
    'demo-notes-math': ('demo-notes', 209, 394, 1800),         # the equation, then the sketch
}
# Still → the demo and the frame of its payoff (each demo's tail fades back to its start).
STILLS = {
    'demo-ink-still': ('demo-ink-import', 215),
    'demo-notes-still': ('demo-notes', 394),
    'demo-agentic-still': ('demo-native-agentic', 288),
}


def chunks(path):
    data, offset = Path(path).read_bytes(), 12
    while offset < len(data):
        kind, size = data[offset:offset+4], int.from_bytes(data[offset+4:offset+8], 'little')
        yield kind, data[offset+8:offset+8+size]
        offset += 8 + size + size % 2


def frame(path, index):
    image = Image.open(path)
    image.seek(index)
    return image.convert('RGB')


def cut(name, demo, first, last, hold):
    source, target = DEMOS / f'{demo}.webp', OUT / f'{name}.webp'
    parts = list(chunks(source))
    frames = [payload for kind, payload in parts if kind == b'ANMF']
    data, (width, height) = _still(frame(source, first), 85, 6)
    position = bytes(6) + (width - 1).to_bytes(3, 'little') + (height - 1).to_bytes(3, 'little')
    # The first frame is whole; the rest draw over it as they did in the demo.
    kept = [position + frames[first][12:16] + data, *frames[first+1:last], frames[last][:12] + hold.to_bytes(3, 'little') + frames[last][15:]]
    body = b'WEBP' + b''.join(_chunk(kind, payload) for kind, payload in parts if kind in (b'VP8X', b'ANIM'))
    body += b''.join(_chunk(b'ANMF', payload) for payload in kept)
    target.write_bytes(b'RIFF' + len(body).to_bytes(4, 'little') + body)
    return check_webp(target)


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name, spec in CUTS.items():
        print(name, cut(name, *spec))
    for name, (demo, index) in STILLS.items():
        frame(DEMOS / f'{demo}.webp', index).save(OUT / f'{name}.webp', 'WEBP', quality=82, method=6)
        print(name, (OUT / f'{name}.webp').stat().st_size // 1024, 'KB')

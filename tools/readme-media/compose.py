"""Compose a retina capture into the framed README animation.

A capture is the JPEG screencast `startCapture()` writes (runtime.mjs): 2x
frames of the 1440 x 900 app with timestamps. `render_master()` places it on
the warm-paper card, points an eased camera at it, dissolves across cuts and
back into the first frame at the loop, and writes a lossless master at the
output size. Camera and edit times are capture seconds, the recorders' marks.
"""
from bisect import bisect_right
from functools import lru_cache
import json
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from imageio_ffmpeg import get_ffmpeg_exe

VW, VH = 1440, 900          # CSS size of every capture
PAPER, EDGE = (246, 244, 239), (227, 224, 216)


class Capture:
    def __init__(self, directory):
        self.dir = Path(directory)
        meta = json.loads((self.dir / 'frames.json').read_text(encoding='utf-8'))
        self.scale = meta['scale']
        self.times = [f['t'] for f in meta['frames']]
        self.files = [f['file'] for f in meta['frames']]

    def index(self, t):
        """The frame on screen at capture time t."""
        return max(0, bisect_right(self.times, t) - 1)

    @lru_cache(maxsize=6)
    def image(self, i):
        with Image.open(self.dir / self.files[i]) as im:
            return im.convert('RGB')


def view(cx, cy, zoom):
    """A camera state: the CSS rect (x, y, w, h) around (cx, cy) at `zoom`, kept inside the app."""
    zoom = max(1.0, zoom)
    w, h = VW / zoom, VH / zoom
    x = min(max(cx - w/2, 0), VW - w)
    y = min(max(cy - h/2, 0), VH - h)
    return (x, y, w, h)


FULL = view(VW/2, VH/2, 1)


def focus(*boxes, margin=40, max_zoom=1.6):
    """The closest view that shows every box ({x, y, width, height} in CSS px) with a margin."""
    x0 = min(b['x'] for b in boxes) - margin
    y0 = min(b['y'] for b in boxes) - margin
    x1 = max(b['x'] + b['width'] for b in boxes) + margin
    y1 = max(b['y'] + b['height'] for b in boxes) + margin
    zoom = min(max_zoom, VW / (x1 - x0), VH / (y1 - y0))
    return view((x0 + x1) / 2, (y0 + y1) / 2, zoom)


def ease(u):
    """Ease-in-out with a gentle start and a long settle, like a damped camera."""
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (u * (6*u - 15) + 10)


class Camera:
    """Keyframed camera: `move(t, rect, seconds)` glides to `rect` starting at capture time t."""
    def __init__(self, start=FULL):
        self.start = start
        self.moves = []

    def move(self, t, rect, seconds=0.9):
        self.moves.append((t, rect, seconds))
        self.moves.sort(key=lambda m: m[0])
        return self

    def at(self, t):
        rect = self.start
        for t0, target, seconds in self.moves:
            if t < t0:
                break
            u = ease((t - t0) / seconds) if seconds > 0 else 1
            rect = blend_rect(rect, target, u)
        return rect


def blend_rect(a, b, u):
    if u >= 1:
        return b
    # Zoom changes geometrically so it reads at a constant rate; the centre follows.
    w = math.exp(math.log(a[2]) * (1-u) + math.log(b[2]) * u)
    cx = (a[0] + a[2]/2) * (1-u) + (b[0] + b[2]/2) * u
    cy = (a[1] + a[3]/2) * (1-u) + (b[1] + b[3]/2) * u
    return view(cx, cy, VW / w)


class Frame:
    """The paper canvas with the soft card shadow, and the card's rounded mask and edge."""
    def __init__(self, width):
        self.width = width = width // 2 * 2
        self.height = height = round(width * 9 / 16) // 2 * 2
        margin = round(height * 0.045)
        self.card = (round((height - 2*margin) * VW / VH) // 2 * 2, (height - 2*margin) // 2 * 2)
        cw, ch = self.card
        self.origin = ((width - cw) // 2, (height - ch) // 2)
        k = width / 1920       # the branding illustrations' shadow, drawn at 1920 px
        radius, ss = max(6, round(14 * k)), 4
        mask = Image.new('L', (cw*ss, ch*ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw*ss - 1, ch*ss - 1), radius*ss, fill=255)
        self.mask = mask.resize(self.card, Image.LANCZOS)
        edge = Image.new('L', (cw*ss, ch*ss), 0)
        ImageDraw.Draw(edge).rounded_rectangle((0, 0, cw*ss - 1, ch*ss - 1), radius*ss, outline=255, width=max(1, round(1.5*k))*ss)
        self.edge = Image.new('RGB', self.card, EDGE)
        self.edge_mask = ImageChops.multiply(edge.resize(self.card, Image.LANCZOS), self.mask)
        shadow = Image.new('L', (width, height), 0)
        ox, oy = self.origin
        ImageDraw.Draw(shadow).rounded_rectangle((ox, oy + round(18*k), ox + cw, oy + ch + round(18*k)), radius, fill=round(255*0.14))
        shadow = shadow.filter(ImageFilter.GaussianBlur(18*k))
        self.base = Image.composite(Image.new('RGB', (width, height), (30, 30, 28)), Image.new('RGB', (width, height), PAPER), shadow)

    def compose(self, card):
        out = self.base.copy()
        out.paste(card, self.origin, self.mask)
        out.paste(self.edge, self.origin, self.edge_mask)
        return out


def mix(layers):
    """Weighted average of (weight, image) pairs."""
    total, acc = 0.0, None
    for weight, image in layers:
        if weight <= 0:
            continue
        total += weight
        acc = image if acc is None else Image.blend(acc, image, weight / total)
    return acc


def render_master(capture, segments, camera, master, width=1600, fps=25, fade=0.3, loop_fade=0.6):
    """Write `master` (lossless FFV1) from capture-time `segments` [(start, end), ...].

    Consecutive segments dissolve over `fade` seconds; the last frame dissolves
    into the first over `loop_fade`, so the loop has no jump. Camera moves get
    motion blur: each frame averages the camera across a 180-degree shutter.
    Returns the output duration in seconds.
    """
    frame = Frame(width)
    cw, ch = frame.card
    step = 1 / fps

    def card(t, rect):
        x, y, w, h = (v * capture.scale for v in rect)
        return capture.image(capture.index(t)).resize(frame.card, Image.LANCZOS, box=(x, y, x + w, y + h))

    def blurred(t):
        """(cache key, output px the camera travels in half a frame, renderer)"""
        a, b = camera.at(t - step/4), camera.at(t + step/4)
        travel = max(abs(p - q) for p, q in zip(a, b)) * cw / min(a[2], b[2])
        samples = min(16, max(1, math.ceil(travel / 1.5)))
        if samples == 1:
            return (capture.index(t), camera.at(t)), travel, lambda: card(t, camera.at(t))
        rects = [camera.at(t + step/2 * (j / (samples-1) - 0.5)) for j in range(samples)]
        # Detail the eye cannot follow mid-move costs the most to encode: soften
        # with speed, on top of the directional blur. Rests stay sharp.
        soften = min(3.0, travel / 4) * width / 1600

        def render():
            image = mix((1, card(t, r)) for r in rects)
            return image.filter(ImageFilter.GaussianBlur(soften)) if soften >= 0.3 else image
        return (capture.index(t), tuple(rects)), travel, render

    # Each output frame is a list of (weight, capture time) layers.
    timeline = []
    for i, (start, end) in enumerate(segments):
        times = [start + k*step for k in range(max(1, round((end - start) * fps)))]
        overlap = min(round(fade * fps), len(times) // 2, len(timeline) // 2)
        base = len(timeline) - overlap
        for k, t in enumerate(times):
            if k < overlap:
                u = ease((k + 1) / (overlap + 1))
                timeline[base + k] = [(w * (1-u), s) for w, s in timeline[base + k]] + [(u, t)]
            else:
                timeline.append([(1.0, t)])
    last, first = timeline[-1][-1][1], timeline[0][0][1]
    n = round(loop_fade * fps)
    timeline += [[(1 - ease((k+1)/(n+1)), last), (ease((k+1)/(n+1)), first)] for k in range(n)]

    proc = subprocess.Popen([get_ffmpeg_exe(), '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                             '-s', f'{frame.width}x{frame.height}', '-r', str(fps), '-i', '-',
                             '-an', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgr0', str(master)], stdin=subprocess.PIPE)
    previous_key, data, motion = None, None, []
    try:
        for layers in timeline:
            parts = [(w, *blurred(t)) for w, t in layers if w > 0]
            key = tuple((round(w, 4), k) for w, k, _, _ in parts)
            if key != previous_key:
                data = frame.compose(mix((w, make()) for w, _, _, make in parts)).tobytes()
                previous_key = key
            proc.stdin.write(data)
            # How much of the frame is in flux: camera travel, or a dissolve.
            motion.append(round(max(travel for _, _, travel, _ in parts) + (8.0 if len(parts) > 1 else 0), 2))
    finally:
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError('ffmpeg could not write the master')
    Path(master).with_suffix('.json').write_text(json.dumps({'fps': fps, 'size': [frame.width, frame.height], 'motion': motion}), encoding='utf-8')
    return len(timeline) / fps

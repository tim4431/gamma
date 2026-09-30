"""Lossless assembly and small animated WebP delivery, shared by the README renderers."""
import json
import os
from pathlib import Path
import subprocess
import time

from imageio_ffmpeg import get_ffmpeg_exe

ROOT = Path(__file__).resolve().parents[2]
# The 1x WebM cases' frame (metadata, reference-links, connector): warm paper,
# a fine card edge, a 16:9 canvas. Retina captures are framed by compose.Frame.
FRAME = ('pad=iw+4:ih+4:2:2:color=0xe3e0d8,'
         'pad=1728:972:(ow-iw)/2:(oh-ih)/2:color=0xf6f4ef')


def concat_segments(master, sources, parts, tail):
    """Write a lossless master: each entry of `parts` is one segment's filter chain
    (`[i:v]trim=…`), concatenated in order and finished with the `tail` filters."""
    graph = ';'.join(f'{part}[s{i}]' for i, part in enumerate(parts)) + ';'
    graph += ''.join(f'[s{i}]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=1:a=0,{tail}[v]'
    inputs = [arg for source in sources for arg in ('-i', str(source))]
    subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-y', *inputs, '-filter_complex', graph,
                    '-map', '[v]', '-an', '-c:v', 'ffv1', '-level', '3', str(master)], check=True)


def encode_webp(source, target, filters='fps=25,scale=1120:-2:flags=lanczos', quality=85, effort=6):
    target = Path(target)
    subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-y', '-i', str(source),
                    '-vf', filters, '-an', '-c:v', 'libwebp_anim', '-lossless', '0',
                    '-quality', str(quality), '-compression_level', str(effort), '-loop', '0', str(target)], check=True)
    return check_webp(target)


def encode_master(master, target, quality=75, motion_quality=40, effort=6, tile=16, threshold=1):
    """Encode a compose.py master as an animated WebP, tile by tile.

    Each frame re-encodes only the 16 px tiles whose pixels drifted more than
    `threshold` from what that tile was last encoded from, or that were last
    encoded at a lower quality; the rest of its rectangle is transparent, so
    the previous frame shows through. (libwebp's own animation encoder compares
    each frame with the previous one, so slow changes such as a settling camera
    or a dissolve accumulate on screen as ghosts.) Frames the sidecar JSON marks
    as moving drop towards `motion_quality`: they are softened and on screen
    for 40 ms, and full-frame changes are what an animated WebP pays for.
    """
    import numpy as np
    from PIL import Image
    meta = json.loads(Path(master).with_suffix('.json').read_text(encoding='utf-8'))
    (width, height), fps, motion = meta['size'], meta['fps'], meta['motion']
    rows, cols = -(-height // tile), -(-width // tile)
    proc = subprocess.Popen([get_ffmpeg_exe(), '-v', 'error', '-i', str(master), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                            stdout=subprocess.PIPE)
    frames, reference, encoded_at, count = [], None, None, 0
    while len(data := proc.stdout.read(width * height * 3)) == width * height * 3:
        current = np.frombuffer(data, np.uint8).reshape(height, width, 3)
        m = motion[count] if count < len(motion) else 0
        q = round(quality - (quality - motion_quality) * min(1.0, m / 6))
        start = round(count * 1000 / fps)
        count += 1
        if reference is None:
            reference, encoded_at = current.copy(), np.full((rows, cols), q)
            frames.append([0, 0, _still(Image.fromarray(current), q, effort), start])
            continue
        drift = np.zeros((rows * tile, cols * tile), np.int16)
        drift[:height, :width] = np.abs(current.astype(np.int16) - reference).max(axis=2)
        # A tile last encoded lower, even a little (the quality ramp as the camera
        # settles), is encoded again: a lone patch's flat colour lands a few levels
        # off its neighbours' and shows as a faint box.
        dirty = (drift.reshape(rows, tile, cols, tile).max(axis=(1, 3)) > threshold) | (encoded_at < q)
        if not dirty.any():
            continue
        ys, xs = np.nonzero(dirty)
        r0, r1, c0, c1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        x0, y0, x1, y1 = c0 * tile, r0 * tile, min(width, c1 * tile), min(height, r1 * tile)
        region = dirty[r0:r1, c0:c1]
        mask = np.kron(region, np.ones((tile, tile), bool))[:y1 - y0, :x1 - x0]
        patch = current[y0:y1, x0:x1]
        image = Image.fromarray(patch) if region.all() else Image.fromarray(np.dstack([patch, mask * np.uint8(255)]), 'RGBA')
        frames.append([x0, y0, _still(image, q, effort), start])
        reference[y0:y1, x0:x1][mask] = patch[mask]
        encoded_at[r0:r1, c0:c1][region] = q
    if proc.wait() or count < 2:
        raise RuntimeError(f'Could not read the master {master}')
    total = round(count * 1000 / fps)
    chunks = [_chunk(b'VP8X', bytes([0x12, 0, 0, 0]) + (width - 1).to_bytes(3, 'little') + (height - 1).to_bytes(3, 'little')),
              _chunk(b'ANIM', bytes([0xef, 0xf4, 0xf6, 0xff, 0, 0]))]   # paper background (BGRA), loop forever
    for i, (x, y, (data, (w, h)), start) in enumerate(frames):
        duration = (frames[i + 1][3] if i + 1 < len(frames) else total) - start
        header = b''.join(int(v).to_bytes(3, 'little') for v in (x // 2, y // 2, w - 1, h - 1, duration))
        chunks.append(_chunk(b'ANMF', header + bytes(1) + data))   # alpha-blend, keep the canvas
    body = b'WEBP' + b''.join(chunks)
    Path(target).write_bytes(b'RIFF' + len(body).to_bytes(4, 'little') + body)
    return check_webp(target)


def _still(image, quality, effort):
    """The ALPH/VP8 chunks of one lossy WebP still, and its size."""
    from io import BytesIO
    buffer = BytesIO()
    image.save(buffer, 'WEBP', quality=quality, method=effort)
    data, offset, chunks = buffer.getvalue(), 12, b''
    while offset < len(data):
        kind, size = data[offset:offset + 4], int.from_bytes(data[offset + 4:offset + 8], 'little')
        if kind in (b'ALPH', b'VP8 ', b'VP8L'):
            chunks += data[offset:offset + 8 + size + size % 2]
        offset += 8 + size + size % 2
    return chunks, image.size


def _chunk(kind, payload):
    return kind + len(payload).to_bytes(4, 'little') + payload + bytes(len(payload) % 2)


def check_webp(target):
    data = Path(target).read_bytes()
    if data[:4] != b'RIFF' or data[8:12] != b'WEBP' or int.from_bytes(data[4:8], 'little') + 8 != len(data):
        raise ValueError('Invalid WebP container')
    offset, frames, milliseconds, loops = 12, 0, 0, None
    while offset < len(data):
        kind, size = data[offset:offset+4], int.from_bytes(data[offset+4:offset+8], 'little')
        chunk = data[offset+8:offset+8+size]
        if len(chunk) != size:
            raise ValueError('Truncated WebP chunk')
        if kind == b'ANIM':
            loops = int.from_bytes(chunk[4:6], 'little')
        if kind == b'ANMF':
            frames += 1
            milliseconds += int.from_bytes(chunk[12:15], 'little')
        offset += 8 + size + size % 2
    if frames < 2 or loops != 0 or milliseconds <= 0:
        raise ValueError('Expected a looping animated WebP')
    if len(data) > 5 * 1024**2:
        raise ValueError('WebP exceeds 5 MiB; shorten the edit or tighten its crop')
    return {'frames': frames, 'seconds': round(milliseconds/1000, 2), 'webp_mib': round(len(data)/1024**2, 2)}


def publish(source, target):
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(8):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.25)

"""Lossless assembly and small animated WebP delivery, shared by the README renderers."""
import json
import os
from pathlib import Path
import subprocess
import time

from imageio_ffmpeg import get_ffmpeg_exe

ROOT = Path(__file__).resolve().parents[2]
# Match the illustrations: warm paper, a fine card edge, and a 16:9 canvas.
# Captures stay at 1440 px wide; detail crops are centered without stretching.
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


def encode_master(master, target, quality=80, motion_quality=45, effort=6):
    """Encode a compose.py master. Frames its sidecar JSON marks as moving (camera
    travel or a dissolve) use `motion_quality`: they are softened and on screen
    for 40 ms, and full-frame changes are what an animated WebP pays for."""
    from PIL import Image, _webp   # Pillow's streaming encoder; its public save() holds every frame
    meta = json.loads(Path(master).with_suffix('.json').read_text(encoding='utf-8'))
    (width, height), fps, motion = meta['size'], meta['fps'], meta['motion']
    paper = 0xff000000 | 0xf6f4ef
    encoder = _webp.WebPAnimEncoder((width, height), paper, 0, False, 0, 0, False, False)
    proc = subprocess.Popen([get_ffmpeg_exe(), '-v', 'error', '-i', str(master), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                            stdout=subprocess.PIPE)
    count, size = 0, width * height * 3
    while len(data := proc.stdout.read(size)) == size:
        q = motion_quality if count < len(motion) and motion[count] >= 0.5 else quality
        encoder.add(Image.frombytes('RGB', (width, height), data).getim(), round(count * 1000 / fps), False, float(q), 100.0, effort)
        count += 1
    if proc.wait() or count < 2:
        raise RuntimeError(f'Could not read the master {master}')
    encoder.add(None, round(count * 1000 / fps), False, float(quality), 100.0, 0)
    Path(target).write_bytes(encoder.assemble(b'', b'', b''))
    return check_webp(target)


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

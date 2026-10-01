"""Render the ink-only capture (record-ink.mjs without --annotate) as a looping animated WebP."""
import argparse
import json
from pathlib import Path

from compose import Capture, Camera, render_master
from media_output import ROOT, encode_master, publish

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scratch', type=Path, default=ROOT / 'artifacts/readme-media')
parser.add_argument('--out', type=Path, default=ROOT / 'docs/assets/demos')
args = parser.parse_args()
timeline = json.loads((args.scratch / 'ink-timeline.json').read_text(encoding='utf-8'))
m = timeline['marks']
master = args.scratch / 'ink-master.mkv'
render_master(Capture(timeline['frames']), [(m['start'], m['end'])], Camera(), master)
output = args.scratch / 'rendered-ink.webp'
report = {'name': 'ink', 'fps': 25, 'width': 1600, **encode_master(master, output)}
(args.scratch / 'ink-render.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
publish(output, args.out / 'demo-ink.webp')
print(json.dumps(report))

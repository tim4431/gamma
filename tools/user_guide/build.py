"""Generate the user guide's animations: every scene of every scenes_*.py module in
this directory, written to docs/user_guide/assets/<stem>.svg.

    backend/venv/Scripts/python.exe tools/user_guide/build.py            # all
    backend/venv/Scripts/python.exe tools/user_guide/build.py annotate   # one stem

A module lists its scenes as SCENES = [(stem, build), ...], each build() returning
the SVG text (see scene.py for the shared pieces)."""
from importlib import import_module
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scene import write  # noqa: E402

only = set(sys.argv[1:])
modules = sorted(p.stem for p in HERE.glob('scenes_*.py'))
seen = set()
for name in modules:
    for stem, build in import_module(name).SCENES:
        if stem in seen:
            raise SystemExit(f'{name}: scene {stem} is defined twice')
        seen.add(stem)
        if only and stem not in only:
            continue
        write(stem, build())
missing = only - seen
if missing:
    raise SystemExit(f'No such scene: {", ".join(sorted(missing))}')

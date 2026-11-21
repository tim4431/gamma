"""Per-paper NED of each scored reader on the real crops: python -I by_paper.py"""
import collections, json
import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import ROOT
s3 = ROOT
rows = {}
for f in sorted((s3 / "scores").glob("real-*.json")):
    e = f.stem[5:]
    per = collections.defaultdict(list)
    for r in json.loads(f.read_text(encoding="utf-8"))["rows"]:
        per[r["id"].split("-")[0]].append(r)
    rows[e] = per
papers = sorted({p for per in rows.values() for p in per})
print(f"{'reader':24s}" + "".join(f"{p:>16s}" for p in papers))
for e, per in rows.items():
    cells = []
    for p in papers:
        rs = per.get(p, [])
        cells.append(f"{sum(r['ned'] for r in rs)/max(1,len(rs)):.3f} ({sum(r['exact'] for r in rs)}/{len(rs)})" if rs else "-")
    print(f"{e:24s}" + "".join(f"{c:>16s}" for c in cells))

"""Summarise $OCR_STUDY_DIR/stage2/vlm_raw (pageocr.py): per engine and page seconds, tokens, caps, and simple oddity checks
(running header / page number missing from a page answer, lines repeated 3+ times, tables, figure text).
No scoring against the truth: the header and page number are taken from paddle-regions' own header/number blocks.

    python -I pageocr_summary.py
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

RAW = common.S3.parent / "stage2" / "vlm_raw"
PAGES = common.CODE.parents[1] / "stage2" / "pages.json"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def page_text(d):
    if "regions" in d:
        return "\n".join(r["text"] for r in d["regions"])
    return d["text"]


def main():
    engines = sorted(p.name for p in RAW.iterdir() if p.is_dir())
    pages = [p["id"] for p in json.loads(PAGES.read_text(encoding="utf-8"))["pages"]]
    ref = {}
    for p in pages:
        f = RAW / "paddle-regions" / f"{p}.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            ref[p] = [(r["cls"], r["text"]) for r in d["regions"] if r["cls"] in ("header", "number", "footer")]
    print("| engine | pages | total s | mean s/page | max s | tokens out (sum) | capped pages |")
    print("|---|---|---|---|---|---|---|")
    odd = collections.defaultdict(list)
    for e in engines:
        rows = []
        for p in pages:
            f = RAW / e / f"{p}.json"
            if not f.exists():
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            rows.append((p, d))
            t = page_text(d)
            if d.get("first_attempt"):
                odd[e].append(f"{p}: hit 8192, rerun with 16384 -> {d['finish_reason']} ({d['tokens_out']} tok)")
            elif d.get("finish_reason") == "length":
                odd[e].append(f"{p}: stopped on the token cap ({d['tokens_out']} tok)")
            if d.get("n_length"):
                odd[e].append(f"{p}: {d['n_length']} region(s) on the token cap")
            lines = [ln.strip() for ln in t.splitlines() if len(ln.strip()) > 10]
            rep = [(ln, c) for ln, c in collections.Counter(lines).items() if c >= 3]
            if rep:
                odd[e].append(f"{p}: repeated lines " + "; ".join(f"{c}x {ln[:50]!r}" for ln, c in rep[:3]))
            if "regions" not in d:
                miss = [f"{c} {txt.strip()[:40]!r}" for c, txt in ref.get(p, []) if norm(txt)[:12] and norm(txt)[:12] not in norm(t)]
                if miss:
                    odd[e].append(f"{p}: missing " + ", ".join(miss))
        secs = [d["seconds"] for _, d in rows]
        toks = sum(d.get("tokens_out") or 0 for _, d in rows)
        capped = sum(1 for _, d in rows if d.get("finish_reason") == "length" or d.get("first_attempt"))
        print(f"| {e} | {len(rows)} | {sum(secs):.1f} | {sum(secs) / len(secs):.2f} | {max(secs):.1f} | {toks} | {capped} |")
    for e, items in odd.items():
        print(f"\n{e}:")
        for it in items:
            print("  " + it)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    main()

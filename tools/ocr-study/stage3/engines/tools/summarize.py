"""Collect the smoke-test _timing.json files under $OCR_STUDY_DIR/stage3/det and stage3/rec into markdown tables."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

S3 = common.S3


def mb(n):
    return f"{n / 1e6:.0f} MB"


def main():
    print("| detector | size | 1 thread s/page | default s/page | load s (default) |")
    print("|---|---|---|---|---|")
    for t in sorted((S3 / "det").glob("*/_timing.json")):
        d = json.loads(t.read_text(encoding="utf-8"))
        m = d["modes"]
        size = sum(d["files"].values())
        print(f"| {d['model']} | {mb(size)} | {m.get('t1', {}).get('mean_s_per_page', '-')} | {m.get('tdefault', {}).get('mean_s_per_page', '-')} | {m.get('tdefault', {}).get('load_s', '-')} |")
    print()
    print("| recognizer | size | 1 thread s/crop (mean / max) | default s/crop (mean / max) | GPU s/crop (mean / max) | mean tokens out |")
    print("|---|---|---|---|---|---|")
    for t in sorted((S3 / "rec").glob("*/_timing.json")):
        d = json.loads(t.read_text(encoding="utf-8"))
        m = d["modes"]
        name = d.get("engine") or f"{d['model']}-{d['device']}" + ("-page" if t.parent.name.endswith("-page") else "")
        size = d.get("size_bytes") or sum(d.get("files", {}).values())

        def cell(k):
            if k not in m:
                return "-"
            v = m[k]
            mean = v.get("mean_s_per_crop", v.get("mean_s"))
            mx = v.get("max_s_per_crop", v.get("max_s"))
            return f"{mean:.2f} / {mx:.2f}"

        toks = "/".join(str(v.get("mean_tokens_out")) for v in m.values() if "mean_tokens_out" in v) or "-"
        print(f"| {name} | {mb(size)} | {cell('t1')} | {cell('tdefault')} | {cell('gpu')} | {toks} |")


if __name__ == "__main__":
    main()

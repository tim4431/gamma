"""The frontier pass: a hosted vision model reads each synthetic scan crop once.

  python -I frontier.py groups [--n 6]     write frontier_groups.json (n groups, every n-th crop)
  python -I frontier.py prompt <k>         print the prompt for group k (FRONTIER_PROMPT.md filled in)
  python -I frontier.py split              rec_synth/claude-opus-5-5/_batch<k>.json -> <id>.json records

Each group goes to one agent with its prompt; the agent reads only its crops and
writes one _batch<k>.json mapping each id to its LaTeX. `split` turns the batches
into the {latex} records score_latex.py reads, and refuses a batch that misses an
id of its group. audit_readers.py then checks from the agents' transcripts that
they read their crops and nothing else."""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

GROUPS = common.ROOT / "frontier_groups.json"
OUT = common.ROOT / "rec_synth" / "claude-opus-5-5"
PROMPT = common.HERE / "FRONTIER_PROMPT.md"

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
g = sub.add_parser("groups")
g.add_argument("--n", type=int, default=6)
p = sub.add_parser("prompt")
p.add_argument("k", type=int)
sub.add_parser("split")
a = ap.parse_args()

if a.cmd == "groups":
    ids = sorted(f.stem for f in (common.SYNTH / "scan").glob("eq*.png"))
    groups = [ids[k::a.n] for k in range(a.n)]
    GROUPS.write_text(json.dumps(groups), encoding="utf-8")
    print(len(ids), "crops in", a.n, "groups:", [len(x) for x in groups])
elif a.cmd == "prompt":
    groups = json.loads(GROUPS.read_text(encoding="utf-8"))
    text = PROMPT.read_text(encoding="utf-8").split("\n---\n", 1)[1]
    print(text.replace("{SCAN}", str(common.SYNTH / "scan")).replace("{OUT}", str(OUT))
          .replace("{ROOT}", str(common.ROOT)).replace("{K}", str(a.k)).replace("{IDS}", ", ".join(groups[a.k])))
else:
    groups = json.loads(GROUPS.read_text(encoding="utf-8"))
    n = 0
    for k, ids in enumerate(groups):
        batch = json.loads((OUT / f"_batch{k}.json").read_text(encoding="utf-8"))
        missing = [i for i in ids if i not in batch]
        if missing:
            sys.exit(f"_batch{k}.json misses {missing}")
        for i in ids:
            (OUT / f"{i}.json").write_text(json.dumps({"latex": batch[i]}), encoding="utf-8")
            n += 1
    print(n, "records written")

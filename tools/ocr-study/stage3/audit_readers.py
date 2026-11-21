"""Which tools the frontier readers called, and on what: python -I audit_readers.py <workflow transcript dir>"""
import collections, json, pathlib, re, sys
d = pathlib.Path(sys.argv[1])
calls, other = collections.Counter(), set()
for f in d.glob("agent-*.jsonl"):
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        c = (rec.get("message") or {}).get("content")
        for b in c if isinstance(c, list) else []:
            if b.get("type") != "tool_use":
                continue
            p = pathlib.PureWindowsPath(str(b["input"].get("file_path", "")))
            if b["name"] == "Read" and p.parent.name == "scan" and p.parent.parent.name == "synth" and re.fullmatch(r"eq\d{4}\.png", p.name):
                calls["crop read"] += 1
            elif b["name"] == "Write" and p.parent.name == "claude-opus-5-5" and re.fullmatch(r"_batch\d\.json", p.name):
                calls["batch write"] += 1
            elif b["name"] == "SubagentHandback":
                calls["handback"] += 1
            else:
                other.add((b["name"], json.dumps(b["input"])[:200]))
print(dict(calls), "other:", other)

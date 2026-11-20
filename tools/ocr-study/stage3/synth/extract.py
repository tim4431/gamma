"""Stage 1: read arXiv e-prints (as tar/gzip data only), expand simple macros,
extract display equations, write work/candidates.json and meta/papers.json
(all under $OCR_STUDY_DIR/stage3/synth)."""
import gzip, io, json, re, sys, tarfile
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import SYNTH  # noqa: E402

BASE = SYNTH
PAPERS = ["cond-mat/9806038", "0704.3011", "cond-mat/0506438", "0811.1338",
          "0807.3358", "1303.0733", "0810.4729", "nucl-th/9705009"]


def fname(pid):
    return pid.replace("/", "_")


def read_sources(path):
    raw = gzip.decompress(path.read_bytes())
    texts = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.lower().endswith(".tex"):
                    texts.append((m.name, tf.extractfile(m).read()))
    except tarfile.ReadError:
        texts.append(("main.tex", raw))
    out = []
    for name, b in texts:
        try:
            s = b.decode("utf-8")
        except UnicodeDecodeError:
            s = b.decode("latin-1")
        out.append((name, s))
    return out


def strip_comments(s):
    lines = []
    for line in s.splitlines():
        # a % preceded by an odd number of backslashes is literal
        i = 0
        cut = None
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        lines.append(line if cut is None else line[:cut])
    return "\n".join(lines)


def group(s, i):
    """s[i] == '{' -> (content, index after closing brace)"""
    assert s[i] == "{"
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced")


def skipws(s, i):
    while i < len(s) and s[i] in " \t\n":
        i += 1
    return i


def parse_macros(s):
    macros = {}  # name -> (nargs, body)
    for m in re.finditer(r"\\(newcommand|renewcommand|providecommand)\*?\s*", s):
        i = m.end()
        try:
            if s[i] == "{":
                name, i = group(s, i)
                name = name.strip()
            else:
                mm = re.match(r"\\[A-Za-z]+", s[i:])
                if not mm:
                    continue
                name = mm.group(0)
                i += len(name)
            i = skipws(s, i)
            nargs = 0
            if i < len(s) and s[i] == "[":
                k = s.index("]", i)
                nargs = int(s[i + 1:k])
                i = skipws(s, k + 1)
                if i < len(s) and s[i] == "[":
                    continue  # optional-argument macros: not handled
            if s[i] != "{":
                continue
            body, _ = group(s, i)
            if re.fullmatch(r"\\[A-Za-z]+", name):
                macros[name] = (nargs, body)
        except (ValueError, IndexError):
            continue
    for m in re.finditer(r"\\def\s*(\\[A-Za-z]+)((?:#\d)*)\s*\{", s):
        try:
            body, _ = group(s, m.end() - 1)
        except ValueError:
            continue
        macros[m.group(1)] = (len(m.group(2)) // 2, body)
    for m in re.finditer(r"\\DeclareMathOperator\*?\s*\{(\\[A-Za-z]+)\}\s*\{", s):
        try:
            body, _ = group(s, m.end() - 1)
        except ValueError:
            continue
        macros[m.group(1)] = (0, r"\operatorname{" + body + "}")
    # drop macros whose body is structural / unsafe for equation text
    bad = re.compile(r"\\(def|newcommand|begin|end|section|input|include|"
                     r"catcode|expandafter|ifx|else|fi|futurelet|csname|"
                     r"usepackage|special|write|immediate|open|read)\b|#\d.*#\d#")
    return {k: v for k, v in macros.items() if not bad.search(v[1])}


BUILTIN_KEEP = {"\\vec", "\\bar", "\\hat", "\\tilde", "\\dot", "\\ddot"}


def expand(eq, macros):
    for _ in range(12):
        changed = False
        out = []
        i = 0
        while i < len(eq):
            m = re.compile(r"\\[A-Za-z]+").match(eq, i)
            if not m:
                out.append(eq[i])
                if eq[i] == "\\" and i + 1 < len(eq):
                    out.append(eq[i + 1])
                    i += 2
                else:
                    i += 1
                continue
            name = m.group(0)
            j = m.end()
            if name in macros:
                nargs, body = macros[name]
                args = []
                k = j
                ok = True
                for _a in range(nargs):
                    k = skipws(eq, k)
                    if k >= len(eq):
                        ok = False
                        break
                    if eq[k] == "{":
                        try:
                            a, k = group(eq, k)
                        except ValueError:
                            ok = False
                            break
                    elif eq[k] == "\\":
                        mm = re.compile(r"\\([A-Za-z]+|.)").match(eq, k)
                        a, k = mm.group(0), mm.end()
                    else:
                        a, k = eq[k], k + 1
                    args.append(a)
                if not ok:
                    return None
                rep = body
                for n, a in enumerate(args, 1):
                    rep = rep.replace("#%d" % n, a)
                # keep a following letter from fusing with a control word
                if re.search(r"\\[A-Za-z]+$", rep) and k < len(eq) and eq[k].isalpha():
                    rep += " "
                out.append(rep)
                i = k
                changed = True
            else:
                out.append(name)
                i = j
        eq = "".join(out)
        if not changed:
            return eq
    return None  # recursive macro


def clean_eq(e):
    e = re.sub(r"\\label\s*\{[^{}]*\}", "", e)
    e = re.sub(r"\\tag\*?\s*\{[^{}]*\}", "", e)
    e = re.sub(r"\\(nonumber|notag|xspace)\b", "", e)
    e = re.sub(r"\\ensuremath\b", "", e)
    e = re.sub(r"[ \t]*\n[ \t]*", " ", e)
    e = re.sub(r"\s+", " ", e).strip()
    return e


def split_rows(body):
    rows, depth, envd, cur, i = [], 0, 0, [], 0
    while i < len(body):
        if body.startswith("\\begin", i):
            envd += 1
        elif body.startswith("\\end", i):
            envd -= 1
        c = body[i]
        if c == "\\" and body.startswith("\\\\", i) and depth == 0 and envd == 0:
            rows.append("".join(cur))
            cur = []
            i += 2
            # optional [skip] after \\
            m = re.match(r"\s*\[[^\]]*\]", body[i:])
            if m:
                i += m.end()
            continue
        if c == "\\":
            cur.append(body[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        cur.append(c)
        i += 1
    rows.append("".join(cur))
    return rows


def top_level_amp(row):
    """Remove alignment & at brace depth 0 and outside nested environments."""
    out, depth, envd, i = [], 0, 0, 0
    while i < len(row):
        if row.startswith("\\begin", i):
            envd += 1
        elif row.startswith("\\end", i):
            envd -= 1
        c = row[i]
        if c == "\\":
            out.append(row[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if c == "&" and depth == 0 and envd == 0:
            out.append(" ")
        else:
            out.append(c)
        i += 1
    return "".join(out)


ENV = re.compile(r"\\begin\{(equation\*?|displaymath|align\*?|eqnarray\*?|gather\*?)\}(.*?)\\end\{\1\}", re.S)


def extract(doc):
    found = []  # (kind, body)
    for m in ENV.finditer(doc):
        found.append((m.group(1), m.group(2)))
    for m in re.finditer(r"(?<!\\)\\\[(.*?)\\\]", doc, re.S):
        found.append(("brackets", m.group(1)))
    for m in re.finditer(r"\$\$(.*?)\$\$", doc, re.S):
        found.append(("dollars", m.group(1)))
    eqs = []
    for kind, body in found:
        base = kind.rstrip("*")
        if base in ("align", "eqnarray", "gather"):
            rows = [r for r in split_rows(body)]
            rows_s = [clean_eq(r) for r in rows]
            for n, r in enumerate(rows_s):
                if not r:
                    continue
                if r.startswith("&") and base != "gather":
                    continue  # continuation row
                nxt = next((x for x in rows_s[n + 1:] if x), "")
                if nxt.startswith("&") and base != "gather":
                    continue  # truncated head of a multi-row expression
                eqs.append((kind, top_level_amp(r).strip()))
        else:
            b = clean_eq(body)
            if not b:
                continue
            if len(split_rows(b)) > 1:
                continue  # multi-line inside equation (e.g. split): skip
            if "&" in top_level_amp(b) and "&" in b and "\\begin" not in b:
                continue
            eqs.append((kind, b))
    return eqs


def paper_meta(pid):
    xml = (BASE / "meta" / (fname(pid) + ".xml")).read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    ns = {"a": "http://arxiv.org/OAI/arXiv/"}
    rec = root.find(".//a:arXiv", ns)
    title = " ".join(rec.find("a:title", ns).text.split())
    authors = []
    for a in rec.findall("a:authors/a:author", ns):
        fn = a.find("a:forenames", ns)
        ks = a.find("a:keyname", ns)
        authors.append(((fn.text + " ") if fn is not None else "") + ks.text)
    lic = rec.find("a:license", ns)
    cats = rec.find("a:categories", ns).text
    created = rec.find("a:created", ns).text
    yymm = pid.split("/")[-1][:4]
    submitted = ("19" if yymm[0] == "9" else "20") + yymm[:2] + "-" + yymm[2:]
    return {"id": pid, "title": title, "authors": authors, "categories": cats,
            "first_submitted": submitted, "oai_created": created,
            "license": lic.text if lic is not None else None,
            "license_note": ("as listed on arXiv" if lic is not None else
                             "no licence listed on arXiv (submitted before arXiv recorded an explicit licence choice; "
                             "distributed by arXiv under its default terms of the time; copyright remains with the authors/publisher)"),
            "abs_url": "https://arxiv.org/abs/" + pid}


def main():
    cands, papers = [], []
    seen = set()
    for pid in PAPERS:
        srcs = read_sources(BASE / "dl" / fname(pid) / "eprint.bin")
        doc = "\n".join(strip_comments(s) for _, s in srcs)
        # environment shortcuts such as \def\be{\begin{equation}}
        for name, be, env in re.findall(
                r"\\(?:def|newcommand)\s*\{?(\\[A-Za-z]+)\}?\s*\{\s*\\(begin|end)\{([a-z*]+)\}\s*\}", doc):
            doc = re.sub(re.escape(name) + r"(?![A-Za-z])",
                         lambda _m: "\\%s{%s}" % (be, env), doc)
        macros = parse_macros(doc)
        eqs = extract(doc)
        n_raw, n_drop_exp, n_dup = len(eqs), 0, 0
        k = 0
        for kind, e in eqs:
            x = expand(e, macros)
            if x is None:
                n_drop_exp += 1
                continue
            x = re.sub(r"\s+", " ", x).strip()
            x = re.sub(r"(\\\\|\\[,;! ]|\s)+$", "", x).strip()
            if len(x) < 4:
                continue
            key = re.sub(r"\s+", "", x)
            if key in seen:
                n_dup += 1
                continue
            seen.add(key)
            k += 1
            cands.append({"cid": "%s-%04d" % (fname(pid), k), "paper": pid,
                          "kind": kind, "source": e, "latex": x})
        meta = paper_meta(pid)
        meta.update({"tex_files": [n for n, _ in srcs], "macros_found": len(macros),
                     "display_rows_found": n_raw, "dropped_bad_macro": n_drop_exp,
                     "dropped_duplicate": n_dup, "candidates": k})
        papers.append(meta)
        print(pid, meta["title"][:50], "rows", n_raw, "macros", len(macros), "cand", k)
    (BASE / "work").mkdir(exist_ok=True)
    (BASE / "work" / "candidates.json").write_text(json.dumps(cands, indent=1), encoding="utf-8")
    (BASE / "meta" / "papers.json").write_text(json.dumps(papers, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

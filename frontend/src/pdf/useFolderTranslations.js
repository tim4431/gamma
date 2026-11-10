// The Export dialog's side of a folder's translated annotated PDF
// (pdf/folderTranslation.js, docs/dev/translation.md "A folder's export"):
// the folder's papers read once as the dialog opens on its annotated PDF, so
// the dialog can count those that go translated, and the same read handed
// to Export. App owns the settings; this owns the read.
//
// `folder`: the folder the dialog shows its annotated PDF for, "" or null
// otherwise. `key`: the settings' translation key, a change of which reads
// the papers again. `papersOf(folder)`: the papers that may go translated;
// `layoutOf(paper, signal)`: one paper's {lang, pages}, or null.
// Returns `translated` ({papers, counting} while some may, then the number
// that do; null for none) and `translations(folder)`, the job's translations:
// the read under way for that folder and key, else a fresh one.
import { useEffect, useRef, useState } from "react";
import { folderTranslations } from "./folderTranslation.js";

export function useFolderTranslations({ folder, key, papersOf, layoutOf }) {
  const readRef = useRef(null); // {folder, key, done: Promise<translations>, ctl}
  const [read, setRead] = useState(null); // {folder, papers, counting}
  const fns = useRef({ papersOf, layoutOf });
  fns.current = { papersOf, layoutOf };
  useEffect(() => {
    if (!folder) return;
    const papers = fns.current.papersOf(folder);
    if (!papers.length) return;
    const ctl = new AbortController();
    const done = folderTranslations(papers, { layoutOf: (p, s) => fns.current.layoutOf(p, s), signal: ctl.signal });
    readRef.current = { folder, key, done, ctl };
    setRead({ folder, papers: papers.length, counting: true });
    done.then((list) => { if (!ctl.signal.aborted) setRead({ folder, papers: list.length, counting: false }); });
    return () => {
      ctl.abort();
      if (readRef.current?.ctl === ctl) readRef.current = null;
      setRead(null);
    };
  }, [folder, key]);
  function translations(of) {
    const at = readRef.current;
    if (at?.folder === of && at.key === key) return at.done;
    const papers = fns.current.papersOf(of);
    return papers.length ? folderTranslations(papers, { layoutOf: (p, s) => fns.current.layoutOf(p, s) }) : Promise.resolve([]);
  }
  const translated = read && read.folder === folder && read.papers ? { papers: read.papers, counting: read.counting } : null;
  return { translated, translations };
}

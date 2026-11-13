// A relative link in a note — `[the method](sections/method.tex)` — that
// the vault left as written: a path typed in Gamma, a file not tracked yet,
// a note of a work folder that does not pull. In a work folder's note
// (PathLinkContext, its `pageId` the note's) a
// click asks the server which page the path names, relative to the note's
// directory and then from the work folder's top, and opens it; a path that
// names none says so, and one that names several opens the first the
// server lists and says how many. Elsewhere it is the plain link it always
// was. A Ctrl/⌘ click is left to the browser.
import React, { useContext } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { PathLinkContext } from "./pathLinkContext.js";

// The path as the person typed it: a link target is percent-encoded
// (`my%20fig.pdf`), which reads badly in a sentence.
function shown(href) {
  try {
    return decodeURIComponent(href);
  } catch {
    return href;
  }
}

// The links of the blocks inside, which are of the page `pageId` (an
// embedded block of another note: BlockTree.jsx BlockEmbedCard): resolved
// only when that is the note the context is for, else plain links.
export function PathLinkScope({ pageId, children }) {
  const ctx = useContext(PathLinkContext);
  return <PathLinkContext.Provider value={ctx && pageId && ctx.pageId === pageId ? ctx : null}>{children}</PathLinkContext.Provider>;
}

export function PathLink({ href, children }) {
  const ctx = useContext(PathLinkContext);
  if (!ctx) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
  const follow = async (e) => {
    if (e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    e.stopPropagation();
    let rows = [];
    try {
      rows = await ctx.resolve(href);
    } catch {
      rows = [];
    }
    if (!rows[0]?.id) {
      ctx.say(t("No tracked file at “{path}”.", { path: shown(href) }));
      return;
    }
    ctx.open(rows[0].id);
    if (rows.length > 1) {
      ctx.say(tn("{n} file is at “{path}”; this is the first.", "{n} files are at “{path}”; this is the first.", rows.length,
        { path: shown(href) }));
    }
  };
  return <a className="pathLink" href={href} onClick={follow}>{children}</a>;
}

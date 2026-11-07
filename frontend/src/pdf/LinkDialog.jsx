// The link dialog: what a PDF passage links to (docs/dev/pdf_citations.md
// "Manual reference links"), from a selection's link button or a link
// region's "Change link". A page picker (library/PagePicker.jsx): the field
// takes a DOI, an arXiv id or a web address, and "Link to …" leads the list
// while it reads as one (isLinkInput), or else searches the pages as Ctrl+P
// does. With nothing typed the list starts with Remove link for a passage
// that links somewhere already (`linked`), then the reference point copied
// from another page's highlight (`refPoint`), then the pages the passage
// names as Best matches (scorePaperMatch: its DOI or arXiv id, authors,
// title words), then the recent pages and the rest. Remove link stays while
// the field holds a link, and a URL link opens with its address in the field
// (`url`). `onLink(target)` takes {url}, {pageId, blockId?} or, to remove
// the link, {}.
import React, { useCallback, useMemo } from "react";
import { HighlightIcon, LinkIcon, UnlinkIcon } from "../shared/ui/Icons";
import PagePicker from "../library/PagePicker";
import { isLinkInput, normalizeLinkInput, scorePaperMatch } from "../library/libraryUtils";
import { t } from "../shared/i18n/i18n.js";

const BEST_SCORE = 6; // a title word scores 2, an author 4, a DOI or arXiv id 20
const MAX_BEST = 5;
const clip = (text, n = 60) => (text.length > n ? `${text.slice(0, n)}…` : text);

export default function LinkDialog({ quote, url = "", linked, pages, exclude, tree, recentViews, openTabs, refPoint, onLink, onClose }) {
  const best = useMemo(() => pages
    .filter((page) => !exclude?.has(page.id))
    .map((page) => ({ id: page.id, score: scorePaperMatch(quote, page) }))
    .filter((r) => r.score >= BEST_SCORE)
    .sort((a, b) => b.score - a.score)
    .slice(0, MAX_BEST)
    .map((r) => r.id), [pages, exclude, quote]);
  const actions = useCallback((q) => {
    const rows = [];
    const link = isLinkInput(q);
    if (link) {
      const to = normalizeLinkInput(q);
      rows.push({ key: "url", section: "link", icon: LinkIcon, title: t("Link to {url}", { url: to }),
        run: () => onLink({ url: to }) });
    }
    if (linked && (link || !q)) {
      rows.push({ key: "remove", section: "link", icon: UnlinkIcon, title: t("Remove link"),
        detail: t("Turn this back into a plain highlight"), run: () => onLink({}) });
    }
    if (!q && refPoint) {
      rows.push({ key: "ref", section: "ref", icon: HighlightIcon, title: refPoint.pageTitle,
        detail: `“${clip(refPoint.quote)}”`, tag: t("highlight"),
        run: () => onLink({ pageId: refPoint.pageId, blockId: refPoint.blockId }) });
    }
    return rows;
  }, [linked, refPoint, onLink]);
  return (
    <PagePicker
      label={linked ? t("Change reference link") : t("Link reference to a page")}
      placeholder={t("Paste a DOI, arXiv id or URL, or search your pages")}
      initialQuery={url}
      note={quote ? t("“{quote}” on the PDF links to what you pick.", { quote: clip(quote) })
        : t("The passage on the PDF links to what you pick.")}
      hint={t("↑↓ choose · Enter link · Esc close")}
      pages={pages}
      exclude={exclude}
      tree={tree}
      recentViews={recentViews}
      openTabs={openTabs}
      first={best}
      firstTitle={t("Best matches")}
      actions={actions}
      sections={{ ref: t("Copied reference point") }}
      onPick={(page) => onLink({ pageId: page.id })}
      onClose={onClose}
    />
  );
}

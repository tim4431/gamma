// The link dialog: what a PDF passage links to (docs/dev/pdf_citations.md
// "Manual reference links"), from a selection's link button or a link
// region's "Change link". A page picker (library/PagePicker.jsx): the field
// takes a DOI, an arXiv id or a web address, and "Link to …" leads the list
// while it reads as one (isLinkInput), or else searches the pages as Ctrl+P
// does. A Gamma link in the field (a copied highlight's or page's, whatever
// host it names) that `placeOf` finds in this library leads instead, as the
// highlight or page it names, which may be on this same paper: the passage
// then links there, not to the address. With nothing typed the list starts
// with Remove link for a passage that links somewhere already (`linked`),
// then the reference point copied from a highlight (`refPoint`), then the
// pages the passage names as Best matches (scorePaperMatch: its DOI or
// arXiv id, authors, title words), then the recent pages and the rest.
// Remove link stays while the field holds a link, and a URL link opens with
// its address in the field (`url`). `self` is the highlight being linked,
// never its own target. `onLink(target)` takes {url}, {pageId, blockId?}
// or, to remove the link, {}.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileTextIcon, HighlightIcon, LinkIcon, UnlinkIcon } from "../shared/ui/Icons";
import PagePicker from "../library/PagePicker";
import { isLinkInput, normalizeLinkInput, scorePaperMatch } from "../library/libraryUtils";
import { gammaLinkId, parseGammaLink } from "../shared/model/gammaLinks.js";
import { t } from "../shared/i18n/i18n.js";

const BEST_SCORE = 6; // a title word scores 2, an author 4, a DOI or arXiv id 20
const MAX_BEST = 5;
const clip = (text, n = 60) => (text.length > n ? `${text.slice(0, n)}…` : text);
// The Gamma link a field holds, and the key its looked-up place is kept by.
const gammaLinkOf = (q) => parseGammaLink(q.trim(), window.location.origin);
const placeKey = (link) => (link ? `${link.kind === "block" ? "block" : "page"}:${gammaLinkId(link)}` : "");

export default function LinkDialog({ quote, url = "", linked, self, pages, exclude, tree, recentViews, openTabs, refPoint, placeOf, onLink, onClose }) {
  const best = useMemo(() => pages
    .filter((page) => !exclude?.has(page.id))
    .map((page) => ({ id: page.id, score: scorePaperMatch(quote, page) }))
    .filter((r) => r.score >= BEST_SCORE)
    .sort((a, b) => b.score - a.score)
    .slice(0, MAX_BEST)
    .map((r) => r.id), [pages, exclude, quote]);
  // The places of the Gamma links typed so far, by placeKey: {pageId,
  // blockId?, title, quote?, highlight?}, or null where this library has none.
  const [query, setQuery] = useState(url);
  const [places, setPlaces] = useState({});
  const asked = useRef(new Set());
  const link = useMemo(() => gammaLinkOf(query), [query]);
  useEffect(() => {
    const key = placeKey(link);
    if (!key || !placeOf || asked.current.has(key)) return;
    asked.current.add(key);
    const keep = (place) => setPlaces((prev) => ({ ...prev, [key]: place }));
    placeOf(link).then(keep, () => keep(null));
  }, [link, placeOf]);
  const actions = useCallback((q) => {
    const rows = [];
    const typed = isLinkInput(q);
    const place = places[placeKey(gammaLinkOf(q))];
    // a highlight anywhere but the one being linked; a page but this one
    const usable = place && (place.blockId ? place.blockId !== self : !exclude?.has(place.pageId));
    if (usable) {
      rows.push({ key: "place", section: "link", icon: place.blockId ? HighlightIcon : FileTextIcon, title: place.title,
        detail: place.quote ? `“${clip(place.quote)}”` : undefined, tag: place.highlight ? t("highlight") : undefined,
        run: () => onLink(place.blockId ? { pageId: place.pageId, blockId: place.blockId } : { pageId: place.pageId }) });
    } else if (typed) {
      const to = normalizeLinkInput(q);
      rows.push({ key: "url", section: "link", icon: LinkIcon, title: t("Link to {url}", { url: to }),
        run: () => onLink({ url: to }) });
    }
    if (linked && (typed || !q)) {
      rows.push({ key: "remove", section: "link", icon: UnlinkIcon, title: t("Remove link"),
        detail: t("Turn this back into a plain highlight"), run: () => onLink({}) });
    }
    if (!q && refPoint) {
      rows.push({ key: "ref", section: "ref", icon: HighlightIcon, title: refPoint.pageTitle,
        detail: `“${clip(refPoint.quote)}”`, tag: t("highlight"),
        run: () => onLink({ pageId: refPoint.pageId, blockId: refPoint.blockId }) });
    }
    return rows;
  }, [linked, self, exclude, places, refPoint, onLink]);
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
      onQuery={setQuery}
      onPick={(page) => onLink({ pageId: page.id })}
      onClose={onClose}
    />
  );
}

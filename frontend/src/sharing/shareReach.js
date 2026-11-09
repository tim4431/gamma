// Who a page is open to beyond the workspace, in words (docs/dev/home_library.md
// "Shares"): the share links that open a page from around it (a page it is
// inside, a folder its top is filed in: GET /api/share-settings/{id}'s
// `via`), and what a move into a page exposes the moved pages to (GET
// /api/pages/{id}/reach: `{shares, published}`), said before Move into
// page… or a page dropped on a page goes ahead.
import { t, tn } from "../shared/i18n/i18n.js";

// A share entry's holder: the page or the folder whose link it is.
const holderOf = (share) => share.title || (share.folder ? t("a folder") : t("Untitled"));

// Who a share's general access lets in, as a sentence's subject.
function whoOf(audience) {
  return audience === "anyone" ? t("anyone with the link")
    : audience === "users" ? t("anyone signed in") : t("the people invited to it");
}

// "read" or "edit" by the share's role (the invited keep their own).
const verbOf = (share) => (share.role === "edit" && share.audience !== "list" ? t("edit") : t("read"));

// The line the Share popover shows under a page another link opens (its
// `via`, nearest first): the nearest one names its holder and what it
// lets people do; "" when none does.
export function viaLine(via) {
  const [first, ...more] = via || [];
  if (!first) return "";
  const vars = { holder: holderOf(first), who: whoOf(first.audience), verb: verbOf(first) };
  const line = first.folder
    ? t("The link of the folder “{holder}” opens this page too: {who} can {verb} it.", vars)
    : t("The link of “{holder}” opens this page too, as a page inside it: {who} can {verb} it.", vars);
  return more.length ? `${line} ${tn("{n} more link opens it as well.", "{n} more links open it as well.", more.length)}` : line;
}

// What a move of `moving` (page ids) into the page whose `reach` was read
// exposes them to: the shares and the publication that do not reach them
// already (`within(id, entry)`: the page `id` is inside the entry's page,
// or filed in its folder, now) — {shares, published}, both empty when the
// move changes nothing about who can open the pages.
export function moveExposure(reach, moving, within) {
  const fresh = (entry) => moving.some((id) => !within(id, entry));
  const published = reach?.published && fresh(reach.published) ? reach.published : null;
  return { shares: (reach?.shares || []).filter(fresh), published };
}

// That exposure as the confirm's message for `n` moved pages; "" when
// there is nothing to say.
export function moveExposureMessage(exposure, n) {
  const lines = [];
  const [first, ...more] = exposure?.shares || [];
  if (first) {
    const vars = { holder: holderOf(first), who: whoOf(first.audience), verb: verbOf(first) };
    lines.push(tn("The link of “{holder}” opens it: {who} can {verb} the page you move, too.",
      "The link of “{holder}” opens it: {who} can {verb} the {n} pages you move, too.", n, vars));
    if (more.length) lines.push(tn("{n} more link opens it as well.", "{n} more links open it as well.", more.length));
  }
  if (exposure?.published) {
    lines.push(tn("“{holder}” is published to Gamma Cloud: the page you move goes there at the next sync and counts against your plan.",
      "“{holder}” is published to Gamma Cloud: the {n} pages you move go there at the next sync and count against your plan.",
      n, { holder: holderOf(exposure.published) }));
  }
  return lines.join(" ");
}

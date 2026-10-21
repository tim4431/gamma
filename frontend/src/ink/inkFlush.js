// What a flush of the drafts sends, group by group, and which of it goes
// with fetch `keepalive` (docs/dev/handwriting.md "Fences"). Pure, so a
// node test pins it: the e2e cannot drive a pagehide. App's flushInk starts
// every group's requests at once — nothing is sure to run once a closing
// page is gone, so a loop waiting for one group before the next would send
// the first group alone — and asks planFlush which go with keepalive, in
// the room the page's keepalive ledger leaves handwriting
// (shared/lib/keepalive.js: the lowest priority, since the draft journal
// keeps what a lost request carried).

// groups: the dirty groups in the flush's order, each {id, remove,
// uploadBytes, putBytes, beside, inFlight} — `remove` the group goes (a
// DELETE, no body); `uploadBytes` / `putBytes` the UTF-8 length of its
// upload's body and of its block PUT's; `beside` its PUT may go beside the
// upload (on pagehide, the upload named by its hash, no merge pending);
// `inFlight` what of this very drawing an earlier flush has on its way
// with keepalive, 'upload', 'put' or 'delete', else null. `budget`: the
// body bytes keepalive may carry now (the ledger's available('low')).
// → [{id, send, keepalive}] in the same order. `send`: 'delete'; 'both',
// the upload and the PUT (side by side when `beside`, else the PUT after
// the upload's answer); 'put', the PUT alone, beside the upload on its way;
// or null — its DELETE or its PUT is on its way, or its upload is and the
// PUT cannot go beside it (that flush sends the PUT if the page lives, and
// the draft journal brings the drawing back if it does not). `keepalive`:
// whether the group's requests fit in what is left of the budget, groups
// taken in order; one that does not fit goes as plain fetches, which a
// closing page may cut off (the journal is their safety net), and a smaller
// one after it may still fit.
export function planFlush(groups, budget) {
  let left = budget;
  return groups.map((g) => {
    const send = g.remove ? (g.inFlight === "delete" ? null : "delete")
      : g.inFlight === "put" ? null
        : g.inFlight === "upload" ? (g.beside ? "put" : null)
          : "both";
    const bytes = send === "both" ? g.uploadBytes + g.putBytes : send === "put" ? g.putBytes : 0;
    const keepalive = send !== null && bytes <= left;
    if (keepalive) left -= bytes;
    return { id: g.id, send, keepalive };
  });
}

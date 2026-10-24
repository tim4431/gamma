// The Recently deleted list asked for ahead of its dialog
// (RecentlyDeleted.jsx, docs/dev/home_library.md "Recently deleted"): the
// pointer or the focus reaching the trash button asks for it together
// with the dialog's module, so a click lands on the dialog with its pages.
// In the startup bundle, with the button.
import { API, apiJson } from "../shared/lib/utils";

// An answer asked this recently is the dialog's list as it is; an older one
// shows while the dialog asks again.
const FRESH_MS = 2000;
let ahead = null; // {at, list: a promise of the answer (null when refused), value: the answer once in}

export function prefetchTrash() {
  if (ahead && performance.now() - ahead.at < FRESH_MS) return;
  const entry = { at: performance.now(), value: null };
  entry.list = apiJson(`${API}/trash`).then((value) => { entry.value = value; return value; }, () => null);
  ahead = entry;
}

// What was asked ahead, `{list, value, fresh}`, or null. The dialog drops
// it as it closes (dropTrash), so what it changed is never shown from here.
export function aheadTrash() {
  return ahead && { list: ahead.list, value: ahead.value, fresh: performance.now() - ahead.at < FRESH_MS };
}

export function dropTrash() {
  ahead = null;
}

// The display name a visitor without an account carries on a share link
// (docs/dev/collab.md): shown in presence and stored as `link:<name>` in the
// page's op log. Generated once per browser, Google-Docs style, and kept in
// localStorage so the same person keeps the same name across visits and
// pages; the share view's topbar lets them change it.
const KEY = "gamma-link-name";
export const LINK_NAME_MAX = 40; // mirrored in gamma/auth.py LINK_NAME_MAX

const ADJECTIVES = ["Curious", "Quiet", "Bright", "Gentle", "Swift", "Clever", "Brave", "Calm",
  "Merry", "Keen", "Bold", "Witty", "Nimble", "Patient", "Lively", "Sunny"];
const ANIMALS = ["Otter", "Heron", "Fox", "Owl", "Lynx", "Panda", "Koala", "Falcon",
  "Badger", "Dolphin", "Marten", "Puffin", "Ibis", "Gecko", "Lemur", "Beaver"];

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

export function randomLinkName() {
  return `${pick(ADJECTIVES)} ${pick(ANIMALS)}`;
}

// What Python counts as whitespace once the control characters are gone
// (str.isspace): JS's \s adds U+FEFF and lacks U+0085.
const SPACES = /[ \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g;

// The server's cleaning (gamma/auth.py link_name; tests/shared/linkname.json
// holds both to the same answers): control characters out, whitespace
// collapsed, capped at LINK_NAME_MAX code points, so an emoji is never cut
// in half. Empty when nothing is left, so callers can keep the old name.
export function cleanLinkName(raw) {
  // biome-ignore lint/suspicious/noControlCharactersInRegex: strips the C0 controls and DEL, as the server does
  const text = String(raw || "").replace(/[\x00-\x1f\x7f]/g, "").replace(SPACES, " ").replace(/^ | $/g, "");
  return Array.from(text).slice(0, LINK_NAME_MAX).join("").replace(/ $/, "");
}

export function saveLinkName(name) {
  try { localStorage.setItem(KEY, name); } catch {}
}

// The stored name, minting and storing one on the first visit.
export function loadLinkName() {
  let stored = "";
  try { stored = cleanLinkName(localStorage.getItem(KEY)); } catch {}
  if (stored) return stored;
  const fresh = randomLinkName();
  saveLinkName(fresh);
  return fresh;
}

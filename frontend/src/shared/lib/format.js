// Plain formatting helpers, free of the browser and of React so the pure
// modules (and their node tests) can use them; shared/lib/utils.js
// re-exports them for everyone else.

// A byte count the way people read it: "812 B", "37 KB", "4.2 MB", "3.1 GB".
export function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}

// Text as HTML that shows it as it is: &, < and > escaped (for an element's
// text; an attribute's value needs its quotes escaped too).
export const escapeHtml = (text) => String(text ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

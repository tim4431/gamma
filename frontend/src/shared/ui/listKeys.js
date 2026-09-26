// ↑/↓ between a search box and the results listed under it (Settings'
// search, the move-to-page filter, the chat's page picker): ↓ from the box
// focuses the first result, ↑/↓ walk the results, ↑ from the first goes back
// to the box. `items` are the focusable results in order; a result that is a
// button or a checkbox already answers Enter / Space itself. Returns true
// when the key was handled.
export function stepList(event, box, items) {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return false;
  if (event.nativeEvent?.isComposing || event.altKey || event.ctrlKey || event.metaKey) return false;
  const down = event.key === "ArrowDown";
  const active = document.activeElement;
  const at = items.indexOf(active);
  let next = null;
  if (active === box) next = down ? items[0] : null;
  else if (at >= 0) next = !down && at === 0 ? box : items[at + (down ? 1 : -1)] || active;
  if (!next) return false;
  event.preventDefault();
  next.focus();
  return true;
}

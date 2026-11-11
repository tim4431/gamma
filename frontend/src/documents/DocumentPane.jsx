// The viewer the view names (documentView.js), drawn by the host's render
// function for it; the fallback when none mounts. A viewer a host does not
// render is the fallback.
export default function DocumentPane({ view, render, fallback = null }) {
  const draw = view.viewer ? render?.[view.viewer] : null;
  return draw ? draw() : fallback;
}

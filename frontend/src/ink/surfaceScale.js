// A screen distance in a surface's page units (docs/dev/handwriting.md
// "Client"): Duplicate sets its copies DUPLICATE_PX css px right of and
// below what they copy, at whatever zoom the surface is shown, from the
// selection menu's button and from Ctrl+D alike. The layer that shows a
// surface — its page's own InkLayer, not the zoom window's copy, which
// shows it at another scale — registers how wide the surface stands on the
// screen, and a caller asks the surface by its key (a PDF page's number, a
// sheet's id), never the document. Pure but for that registry.
export const DUPLICATE_PX = 12;

// surface → () => {css, width}: how many css px wide it stands, how many page units wide it is
const surfaces = new Map();

// `px` css px in page units, on a surface `css` px wide on the screen and
// `width` units wide; `px` itself (a scale of one) when either is unknown.
export const pxToUnits = (px, css, width) => (css > 0 && width > 0 ? px * width / css : px);

// The layer showing `surface` measures it from now on, `measure() → {css,
// width}` read at each ask; → the call that stops it, which leaves alone a
// layer that registered the surface since.
export function measureSurface(surface, measure) {
  surfaces.set(surface, measure);
  return () => { if (surfaces.get(surface) === measure) surfaces.delete(surface); };
}

// `px` css px in `surface`'s page units, at its zoom now.
export function surfaceUnits(surface, px) {
  const m = surfaces.get(surface)?.();
  return pxToUnits(px, m?.css, m?.width);
}

// Duplicate's offset on `surface`: {dx, dy} in its page units.
export function duplicateOffset(surface) {
  const d = surfaceUnits(surface, DUPLICATE_PX);
  return { dx: d, dy: d };
}

// Boxes for Mermaid's layout when the browser's own getBBox() goes stale
// (docs/dev/diagrams.md, "Mermaid"). Mermaid measures a label inside a
// foreignObject sized up to 16384px, then shrinks the box and shifts its
// group, and lays the diagram out from the node's getBBox(). Some Chrome
// installations keep answering with the geometry the elements had when they
// were first measured: every node grows by the measuring box, and the
// diagram is drawn many times too large and scaled down to dots.
// staleSvgGeometry() detects that once per page; withComputedBBox() then
// answers getBBox() inside Mermaid's render container from what the DOM
// itself says: x/y/width/height of foreignObjects and simple shapes, each
// child's transform list, and the browser's box only for paths, text and
// other leaves that are drawn once.

const SVG_NS = "http://www.w3.org/2000/svg";
const NON_GRAPHIC = new Set(["defs", "style", "title", "desc", "marker", "clipPath", "mask", "pattern",
  "linearGradient", "radialGradient", "filter", "metadata", "script", "symbol"]);

const num = (el, name) => parseFloat(el.getAttribute(name)) || 0;
const plain = (r) => ({ x: r.x, y: r.y, width: r.width, height: r.height });

// An element's transform attribute as one matrix, read from the DOM's
// transform list (never consolidate(), which rewrites the attribute).
function matrixOf(el) {
  const list = el.transform?.baseVal;
  if (!list?.numberOfItems) return null;
  let m = new DOMMatrix();
  for (let i = 0; i < list.numberOfItems; i++) m = m.multiply(DOMMatrix.fromMatrix(list.getItem(i).matrix));
  return m;
}

function mapBox(b, m) {
  const pts = [[b.x, b.y], [b.x + b.width, b.y], [b.x, b.y + b.height], [b.x + b.width, b.y + b.height]]
    .map(([x, y]) => m.transformPoint(new DOMPoint(x, y)));
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y };
}

// getBBox() of el in its own user space, as the browser computes it when its
// geometry is current: a container unites its graphic children's boxes (in
// their transforms), skipping empty ones.
export function computedBBox(el, nativeGetBBox) {
  switch (el.localName) {
    case "use":
      if (!el.hasAttribute("width")) return plain(nativeGetBBox.call(el));
    // falls through
    case "foreignObject": case "rect": case "image":
      return { x: num(el, "x"), y: num(el, "y"), width: num(el, "width"), height: num(el, "height") };
    case "circle": {
      const r = num(el, "r");
      return { x: num(el, "cx") - r, y: num(el, "cy") - r, width: 2 * r, height: 2 * r };
    }
    case "ellipse": {
      const rx = num(el, "rx"), ry = num(el, "ry");
      return { x: num(el, "cx") - rx, y: num(el, "cy") - ry, width: 2 * rx, height: 2 * ry };
    }
    case "g": case "a": case "switch": case "svg": {
      let box = null;
      for (const child of el.children) {
        if (!(child instanceof SVGGraphicsElement) || NON_GRAPHIC.has(child.localName)) continue;
        // A nested <svg> is its own viewport: the browser's box.
        const own = child.localName === "svg" ? plain(nativeGetBBox.call(child)) : computedBBox(child, nativeGetBBox);
        if (!own.width && !own.height) continue;
        const m = matrixOf(child);
        const b = m ? mapBox(own, m) : own;
        if (!box) { box = b; continue; }
        const x = Math.min(box.x, b.x), y = Math.min(box.y, b.y);
        box = { x, y, width: Math.max(box.x + box.width, b.x + b.width) - x, height: Math.max(box.y + box.height, b.y + b.height) - y };
      }
      return box || { x: 0, y: 0, width: 0, height: 0 };
    }
    default:
      return plain(nativeGetBBox.call(el));
  }
}

// Does a group's getBBox() follow its label's foreignObject being resized
// and the label group being shifted after a first measurement, the way
// Mermaid's node measurement needs? Probed once, off screen.
let stale = null;
export function staleSvgGeometry() {
  if (stale !== null) return stale;
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;left:-100000px;top:0;visibility:hidden;pointer-events:none";
  const svg = document.createElementNS(SVG_NS, "svg");
  const node = svg.appendChild(document.createElementNS(SVG_NS, "g"));
  const label = node.appendChild(document.createElementNS(SVG_NS, "g"));
  const fo = label.appendChild(document.createElementNS(SVG_NS, "foreignObject"));
  fo.setAttribute("width", "1200px");
  fo.setAttribute("height", "1200px");
  fo.appendChild(document.createElement("div")).textContent = "x";
  host.appendChild(svg);
  document.body.appendChild(host);
  try {
    node.getBBox();
    fo.setAttribute("width", "120");
    fo.setAttribute("height", "24");
    label.setAttribute("transform", "translate(-60, -12)");
    const b = node.getBBox();
    stale = Math.abs(b.width - 120) > 1 || Math.abs(b.x + 60) > 1;
  } finally {
    host.remove();
  }
  return stale;
}

// Runs run() (a Mermaid render into host) with getBBox() answered by
// computedBBox() for the elements inside host; everything else, and calls
// with getBBox options, keep the browser's own.
export async function withComputedBBox(host, run) {
  const nativeGetBBox = SVGGraphicsElement.prototype.getBBox;
  SVGGraphicsElement.prototype.getBBox = function getBBox(...args) {
    if (args.length || !host.contains(this)) return nativeGetBBox.apply(this, args);
    const b = computedBBox(this, nativeGetBBox);
    return new DOMRect(b.x, b.y, b.width, b.height);
  };
  try {
    return await run();
  } finally {
    SVGGraphicsElement.prototype.getBBox = nativeGetBBox;
  }
}

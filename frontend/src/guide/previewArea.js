import { anchorElement } from "./anchors.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The rendered text nodes of a page's text layer, with their screen boxes.
function layerNodes(page) {
  const layer = page.querySelector('[data-guide="pdf.textLayer"]');
  if (!layer) return null;
  const nodes = [];
  const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    nodes.push({ text: node.textContent, box: range.getBoundingClientRect() });
  }
  return nodes;
}

// The demo paper's attention formula: the line holding "Attention(" and
// "softmax", without its equation number.
function formulaBox(nodes, expectedY) {
  const start = nodes.filter((n) => /Attention/i.test(n.text))
    .sort((a, b) => Math.abs(a.box.top - expectedY) - Math.abs(b.box.top - expectedY))[0];
  if (!start) return null;
  const baseline = start.box.top + start.box.height / 2;
  const parts = nodes.filter((n) => n.box.width > 0 && n.box.left >= start.box.left - 3
    && Math.abs(n.box.top + n.box.height / 2 - baseline) < start.box.height * 1.8
    && !/^\s*\(\d+\)\s*$/.test(n.text)
    && (n === start || n.text.trim().length < 20 || /softmax/i.test(n.text)));
  if (!parts.some((n) => /softmax/i.test(n.text))) return null;
  return {
    left: Math.min(...parts.map((n) => n.box.left)) - 8,
    top: Math.min(...parts.map((n) => n.box.top)) - 8,
    right: Math.max(...parts.map((n) => n.box.right)) + 8,
    bottom: Math.max(...parts.map((n) => n.box.bottom)) + 8,
  };
}

// The space a figure takes above its caption. A text layer holds no
// figures, only their labels, so the figure is the gap in the running text:
// from the caption line up to the last prose line above it in the caption's
// column (a line wider than a third of the page with some length to it),
// as wide as the labels and the caption line, at least 40% of the page.
function figureBox(nodes, captionY, pageRect) {
  const caption = nodes.filter((n) => /^\s*(Figure|Fig\.)\s*\d/i.test(n.text))
    .sort((a, b) => Math.abs(a.box.top - captionY) - Math.abs(b.box.top - captionY))[0];
  if (!caption) return null;
  const middle = (n) => (n.box.top + n.box.bottom) / 2;
  const line = nodes.filter((n) => Math.abs(middle(n) - middle(caption)) < caption.box.height * 0.6);
  const lineLeft = Math.min(...line.map((n) => n.box.left));
  const lineRight = Math.max(...line.map((n) => n.box.right));
  const bottom = caption.box.top - 4;
  const prose = nodes.filter((n) => n.box.bottom <= bottom - 2 && n.box.width > pageRect.width / 3
    && n.text.trim().length > 30 && n.box.right > lineLeft && n.box.left < lineRight);
  const top = Math.max(pageRect.top + pageRect.height * 0.06, ...prose.map((n) => n.box.bottom + 4));
  if (bottom - top < 40) return null;
  const inside = [...line, ...nodes.filter((n) => n.box.top >= top && n.box.bottom <= bottom)];
  let left = Math.min(...inside.map((n) => n.box.left));
  let right = Math.max(...inside.map((n) => n.box.right));
  const minWidth = pageRect.width * 0.4;
  if (right - left < minWidth) { const centre = (left + right) / 2; left = centre - minWidth / 2; right = centre + minWidth / 2; }
  return { left: Math.max(pageRect.left + 8, left - 8), top, right: Math.min(pageRect.right - 8, right + 8), bottom };
}

// Exercise the viewer's real rectangle-drag path, then cancel before release
// would capture an image or create a pending annotation. `find` names what
// to box: the demo paper's formula ({page, rects, pageH}, the first tour's
// findEquation) or, with `figure: true`, a figure caption whose figure is
// framed (the AI chat tour's findFigure); without one, a box near the top of
// the page in view. `context` releases the drag, so the snapshot goes to the
// chat; it stays for the tour's next step and goes when the tour ends
// (`services.snapshotDemo` / `onTourEnd`), unless it was sent.
export async function previewArea(live, cancelled, onCleanup, { find, context = false, services = {} } = {}) {
  const target = await find?.();
  if (cancelled()) return;
  const viewerElement = anchorElement("pdf.viewer");
  const viewer = viewerElement?.getBoundingClientRect();
  if (!viewer) return;
  const pages = [...document.querySelectorAll('[data-guide="pdf.page"]')];
  const targetPage = target && pages.find((el) => Number(el.dataset.page) === target.page);
  const page = targetPage || pages.find((el) => {
    const r = el.getBoundingClientRect();
    return r.bottom > viewer.top + 120 && r.top < viewer.bottom - 260;
  });
  if (!page) return;
  let box = null;
  if (targetPage) {
    const initial = page.getBoundingClientRect();
    const hit = target.rects[0];
    // A formula near the top third of the view; a caption low, so the
    // figure above it is in view.
    viewerElement.scrollTop += initial.top + hit.y1 * initial.height / target.pageH - viewer.top
      - viewer.height * (target.figure ? 0.78 : 0.3);
    const deadline = performance.now() + 8000;
    while (!box && performance.now() < deadline) {
      if (cancelled()) return;
      const nodes = layerNodes(page);
      if (nodes) {
        const pageRect = page.getBoundingClientRect();
        const expectedY = pageRect.top + hit.y1 * pageRect.height / target.pageH;
        box = target.figure ? figureBox(nodes, expectedY, pageRect) : formulaBox(nodes, expectedY);
      }
      if (!box) await sleep(100);
    }
  }
  const r = page.getBoundingClientRect();
  const x = box?.left ?? Math.max(r.left, viewer.left) + 45;
  const y = box ? Math.max(box.top, viewer.top + 8) : Math.max(r.top, viewer.top) + 80;
  const width = box ? box.right - x : Math.min(240, r.right - x - 30, viewer.right - x - 30);
  const height = box ? Math.min(box.bottom, viewer.bottom - 8) - y : Math.min(110, r.bottom - y - 30, viewer.bottom - y - 220);
  if (width < 30 || height < 30) return;
  const pointerId = 971;
  let dragging = false;
  const emit = (type, clientX = x, clientY = y, el = document) => el.dispatchEvent(new PointerEvent(type, {
    bubbles: true, cancelable: true, pointerId, pointerType: "mouse", isPrimary: true, button: 0,
    buttons: 1, ctrlKey: true, clientX, clientY,
  }));
  const clear = () => {
    if (dragging) { dragging = false; emit("pointercancel"); }
  };
  onCleanup(clear);
  const check = () => { if (cancelled()) throw new Error("cancelled"); };
  const show = (progress, pressed) => live({ anchor: "pdf.viewer", cursor: {
    x: x + width * progress, y: y + height * progress,
    pressed, dragging: pressed, modifier: "Ctrl",
  } });
  try {
    show(0, false);
    await sleep(650); check();
    dragging = true;
    emit("pointerdown", x, y, page);
    const frames = matchMedia("(prefers-reduced-motion: reduce)").matches ? 1 : 40;
    for (let i = 1; i <= frames; i++) {
      check();
      const progress = i / frames;
      emit("pointermove", x + width * progress, y + height * progress);
      show(progress, true);
      await sleep(frames === 1 ? 0 : 30);
    }
    await sleep(1000); check();
    if (context) {
      const takeBack = services.snapshotDemo?.();
      if (takeBack) services.onTourEnd?.(takeBack);
      emit("pointerup", x + width, y + height);
      dragging = false;
      await sleep(200); check();
      document.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      await sleep(500);
    }
  } finally { clear(); }
}

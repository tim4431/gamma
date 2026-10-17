import { mermaidMath } from "./diagramMarkdown.js";
import { staleSvgGeometry, withComputedBBox } from "./svgGeometry.js";

let library;
let queue = Promise.resolve();
let nextId = 0;

// Mermaid holds global configuration: serialize initialization and rendering so
// simultaneous diagrams/theme changes cannot borrow another render's settings.
export function renderMermaid(source, theme, cancelled = () => false) {
  const task = queue.then(async () => {
    if (cancelled()) return null;
    if (source.length > 50000) throw new Error("Diagram exceeds the 50,000 character limit.");
    library ||= import("mermaid").then((m) => m.default).catch((error) => {
      library = null;
      throw error;
    });
    const mermaid = await library;
    if (cancelled()) return null;
    mermaid.initialize({
      startOnLoad: false, securityLevel: "strict", theme,
      // Mermaid's KaTeX/MathML labels need foreignObject, including edge math.
      // Strict mode still sanitizes labels; HTML layout is not loose security.
      htmlLabels: true, suppressErrorRendering: true,
      maxTextSize: 50000, maxEdges: 500,
      secure: ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges", "suppressErrorRendering", "htmlLabels"],
    });
    const host = document.createElement("div");
    host.style.cssText = "position:absolute;left:-100000px;top:0;visibility:hidden;pointer-events:none";
    host.setAttribute("aria-hidden", "true");
    document.body.appendChild(host);
    try {
      // Where the browser's getBBox() keeps stale label geometry, Mermaid
      // would lay every node out as large as its measuring box.
      const render = () => mermaid.render(`gamma-mermaid-${++nextId}`, mermaidMath(source), host);
      const { svg } = await (staleSvgGeometry() ? withComputedBBox(host, render) : render());
      if (cancelled()) return null;
      // Mermaid writes its SVG as HTML: a label's <br>, <img> or &nbsp; is no
      // XML, and an XML parse would put the browser's error page into the
      // drawing. Read it as the page does, as HTML (inert: nothing loads or
      // runs in a parsed document), and write it back as XML, so the preview,
      // the download and the zoom get one well-formed SVG document.
      const root = new DOMParser().parseFromString(svg, "text/html").body.querySelector("svg");
      if (!root) throw new Error("Mermaid returned no SVG.");
      // Strict mode disables callbacks, but Mermaid still emits URL anchors.
      // Keep previews and downloads inert, including diagrams from AI replies.
      for (const link of root.querySelectorAll("a")) link.replaceWith(...link.childNodes);
      return new XMLSerializer().serializeToString(root);
    } finally {
      host.remove();
    }
  });
  queue = task.catch(() => {});
  return task;
}

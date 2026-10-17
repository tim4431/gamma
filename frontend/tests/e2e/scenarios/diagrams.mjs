import { readFile } from "node:fs/promises";
import { closeEditor, newPageViaUi } from "./notes.mjs";
import { fakeAiModels } from "../harness.mjs";

const flow = 'flowchart LR\n  A["Start $a|b$ \\(x\\)"] --> B[Finish]';
const sequence = "sequenceDiagram\n  Alice->>Bob: Hello\n  Bob-->>Alice: Hi";
// Note-style `$…$` next to Mermaid's own `$$…$$`: both typeset.
const quantum = String.raw`flowchart LR
  S(("$S_z$")) -. "$\chi$" .- A(("$$a_1$$"))
  A <-->|"$J$"| B(("$$a_2: |\alpha\rangle$$"))`;
// A subgraph around math labels: the layout that broke where getBBox()
// kept a label's measuring box.
const nested = String.raw`flowchart TD
  H["$$H = \chi S_z n_1$$"] --> C
  subgraph LIN["Linear fields"]
    C["$$\dot\alpha = -i M \alpha$$"] --> R["$$\rho_s$$"]
  end`;
const fence = (source) => "```mermaid\n" + source + "\n```";
// A figure Mermaid cannot draw (two energy levels and a transition), with
// the script, handler and page-wide style a drawing must not get through.
const levels = [
  '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" onload="window.svgRan = 1">',
  "  <script>window.svgRan = 2</script>",
  "  <style>.level { stroke: rgb(0, 0, 0); stroke-width: 2 }</style>",
  '  <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z"/></marker></defs>',
  '  <line class="level" x1="40" y1="160" x2="160" y2="160"/>',
  '  <line class="level" x1="40" y1="40" x2="160" y2="40"/>',
  '  <line class="jump" x1="100" y1="155" x2="100" y2="48" stroke="rgb(200, 0, 0)" marker-end="url(#arrow)"/>',
  '  <text x="170" y="165">E0</text><text x="170" y="45">E1</text>',
  "</svg>",
].join("\n");
const svgFence = (source) => "```svg\n" + source + "\n```";

// Drags a figure's right grip by dx pixels (negative: narrower).
async function dragGrip(page, figure, dx) {
  await figure.hover();
  const box = await figure.locator(".mdResizeGrip.right").boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2, { steps: 4 });
  await page.mouse.up();
}

// The chat's next reply is whatever the step pushes through
// window.chatStream.push(delta), until window.chatStream.finish().
async function scriptedChat(alice, browser) {
  const ctx = await alice.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
  await fakeAiModels(ctx);
  await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
  await ctx.addInitScript(() => {
    localStorage.setItem("gamma-ai-login-check", "off");
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (String(input).endsWith("/api/ai/chat")) return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          window.chatStream = {
            push: (delta) => controller.enqueue(new TextEncoder().encode(JSON.stringify({ delta }) + "\n")),
            finish: () => controller.close(),
          };
        },
      }), { headers: { "Content-Type": "application/x-ndjson" } }));
      return original(input, init);
    };
  });
  return ctx;
}

// What a selection over the whole element copies as text/plain.
const selectionCopy = (el) => {
  const range = document.createRange(); range.selectNode(el);
  const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
  const clipboardData = new DataTransfer();
  el.dispatchEvent(new ClipboardEvent("copy", { bubbles: true, clipboardData }));
  selection.removeAllRanges();
  return clipboardData.getData("text/plain");
};

// An svg fence's image: drawn (decoded, with a size) and what it shows.
const SVG_URL = "data:image/svg+xml;charset=utf-8,";
const drawn = (img) => img.evaluate((el) => el.complete && el.naturalWidth > 0);
async function imageMarkup(img) {
  const src = await img.getAttribute("src");
  if (!src.startsWith(SVG_URL)) throw new Error(`not an SVG image: ${src.slice(0, 60)}`);
  return decodeURIComponent(src.slice(SVG_URL.length));
}

// The clipboard's one item, by type: text as text, a PNG as its pixel size
// and how many of its pixels differ from its corner's (0: a blank image).
const readClipboard = async () => {
  const [item] = await navigator.clipboard.read();
  const out = {};
  for (const type of item.types) {
    const blob = await item.getType(type);
    if (type !== "image/png") { out[type] = await blob.text(); continue; }
    const bitmap = await createImageBitmap(blob);
    const canvas = Object.assign(document.createElement("canvas"), { width: bitmap.width, height: bitmap.height });
    const g = canvas.getContext("2d");
    g.drawImage(bitmap, 0, 0);
    const px = g.getImageData(0, 0, bitmap.width, bitmap.height).data;
    let inked = 0;
    for (let i = 4; i < px.length; i += 4) if (px[i] !== px[0] || px[i + 1] !== px[1] || px[i + 2] !== px[2] || px[i + 3] !== px[3]) inked += 1;
    out[type] = { size: `${bitmap.width}x${bitmap.height}`, inked };
  }
  return out;
};

const setTheme = (page, theme) => page.evaluate((value) => {
  Object.assign(document.documentElement.dataset, { scheme: value, theme: value });
}, theme);

export async function diagramScenarios(env) {
  const { server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage } = env;
  // The open lightbox's picture: one SVG document, drawn, that fills the
  // window (94% of its width or 90% of its height, shared/ui/Lightbox.jsx)
  // at the figure's shape. Returns its markup.
  async function zoomedDrawing(page, ratio) {
    const img = page.locator(".lightbox img");
    await img.waitFor();
    await until(() => drawn(img), { what: "the zoomed drawing loads" });
    const box = await img.boundingBox();
    const { width, height } = page.viewportSize();
    assert(Math.abs(box.width - width * 0.94) < 2 || Math.abs(box.height - height * 0.9) < 2,
      `the zoomed drawing fills the window: ${box.width}x${box.height} in ${width}x${height}`);
    if (ratio) assert(Math.abs(box.width / box.height - ratio) < 0.02, `the drawing keeps its shape: ${box.width}x${box.height}`);
    return imageMarkup(img);
  }
  await step("diagrams: mermaid in notes, slash command, source, SVG, themes and Markdown round trip", async () => {
    const imported = await alice.upload("/api/import/markdown", Buffer.from(
      [fence(flow), fence(sequence), fence("this is not a diagram"), "```js\nconst normal = 1;\n```"].join("\n\n"),
    ), "Mermaid.md", "text/markdown");
    const ctx = await alice.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${imported.block_id}`);
    try {
      await until(async () => await page.locator(".diagramSvg > svg").count() === 2);
      await page.locator(".diagramError").waitFor();
      assert((await page.locator(".diagramError").innerText()).includes("Could not render diagram"));
      assertEq(await page.locator(".codeBlock code").innerText(), "const normal = 1;");
      const diagrams = page.locator(".diagramCard");
      const first = diagrams.first();
      assertEq(await first.getAttribute("data-diagram-source"), flow);
      assertEq(await first.getAttribute("data-diagram-kind"), "mermaid");
      const ids = await page.locator(".diagramSvg > svg").evaluateAll((els) => els.map((el) => el.id));
      assertEq(new Set(ids).size, 2, "SVG IDs are unique");
      await first.hover(); // the toolbar is a hover strip, like an image's
      await first.getByRole("button", { name: "Copy source", exact: true }).click();
      assertEq((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n"), flow);
      assertEq(await page.locator(".blockEditorCm").count(), 0, "toolbar does not start editing");
      await first.getByRole("button", { name: "Source", exact: true }).click();
      assertEq(await first.locator(".diagramSource").innerText(), flow);
      await first.getByRole("button", { name: "Source", exact: true }).click();
      const downloadEvent = page.waitForEvent("download");
      await first.getByRole("button", { name: "Download SVG", exact: true }).click();
      const download = await downloadEvent;
      const svg = await readFile(await download.path(), "utf8");
      assert(svg.includes("<svg") && svg.includes("Finish"), "SVG download contains the diagram");
      // Zoom, like a note image's: the figure fills the window in a
      // lightbox; a press on it closes it and reaches nothing underneath.
      const shape = await first.locator(".diagramSvg > svg").boundingBox();
      await first.getByRole("button", { name: "Zoom", exact: true }).click();
      assert((await zoomedDrawing(page, shape.width / shape.height)).includes("Finish"), "the zoomed figure keeps its labels");
      await page.locator(".lightbox").click();
      await page.locator(".lightbox").waitFor({ state: "detached" });
      assertEq(await page.locator(".blockEditorCm").count(), 0, "closing the zoom does not start editing");

      const initialTheme = await page.evaluate(() => document.documentElement.dataset.theme);
      for (const theme of initialTheme === "light" ? ["dark", "light"] : ["light", "dark"]) {
        const oldId = await first.locator(".diagramSvg > svg").getAttribute("id");
        await setTheme(page, theme);
        await until(async () => {
          const id = await first.locator(".diagramSvg > svg").getAttribute("id").catch(() => null);
          return id && id !== oldId;
        });
        assertEq(await first.getAttribute("data-diagram-theme"), theme === "light" ? "default" : "dark");
      }
      assertEq(await first.evaluate(selectionCopy), fence(flow), "selection copy recovers the Markdown fence");
      const exported = await alice.api(`/api/pages/${imported.block_id}/export?mode=readable`, { raw: true });
      const again = await alice.upload("/api/import/markdown", Buffer.from(await exported.text()), "Roundtrip.md", "text/markdown");
      const tree = await alice.api(`/api/blocks/${again.block_id}/subtree`);
      const contents = (block) => [block.content, ...(block.children || []).flatMap(contents)];
      assert(contents(tree.block).includes(fence(flow)), "Markdown export/import preserves source");

      if (process.env.GAMMA_DIAGRAM_SCREENSHOT) await page.screenshot({ path: process.env.GAMMA_DIAGRAM_SCREENSHOT });
      await page.goto(`${server.base}/?ws=${alice.ws}`);
      const starter = await newPageViaUi(page, "Mermaid starter");
      await page.keyboard.type("/mermaid");
      await page.getByRole("button", { name: /Mermaid diagram/ }).click();
      await page.waitForFunction(() => window.getSelection()?.toString() === "Start", null, { timeout: 5000 });
      await page.keyboard.insertText("Begin");
      await closeEditor(page);
      await page.locator(".diagramPreview").filter({ hasText: "Begin" }).waitFor();
      // The diagram is an object: a click selects it (no editor), a
      // double-click opens its source, and the menu does the same.
      await page.locator(".diagramPreview").click();
      await page.locator(".mdObject-diagram.mdObjectSelected").waitFor();
      assertEq(await page.locator(".blockEditorCm").count(), 0, "a click on the diagram selects it instead of opening the editor");
      assertEq(await page.locator(".lightbox").count(), 0, "a click in an editable note selects, the toolbar zooms");
      await page.locator(".diagramPreview").dblclick();
      await page.locator(".blockEditorCm .cm-content").waitFor();
      assert((await page.locator(".blockEditorCm .cm-content").innerText()).includes("flowchart"),
        "a double-click opens the editor on the diagram's source");
      await closeEditor(page);
      await page.locator(".diagramPreview").filter({ hasText: "Begin" }).waitFor();
      await page.locator(".diagramPreview").click({ button: "right" });
      await page.getByRole("button", { name: "Edit markdown source" }).click();
      await page.locator(".blockEditorCm .cm-content").waitFor();
      await page.keyboard.press("ControlOrMeta+a");
      await page.keyboard.insertText(fence("flowchart LR\n  A[Edited] --> B[Saved]"));
      await closeEditor(page);
      await page.locator(".diagramPreview").filter({ hasText: "Edited" }).waitFor();
      await page.reload();
      await page.locator(".diagramPreview").filter({ hasText: "Edited" }).waitFor();

      // Resize: the image grip's drag writes `width=N` into the fence's info
      // string, the figure follows it, double-click clears it again.
      const figure = page.locator(".diagramFigure");
      const grip = figure.locator(".mdResizeGrip.right");
      const startW = (await figure.boundingBox()).width;
      await dragGrip(page, figure, -80);
      const stored = async () => {
        const tree = await alice.api(`/api/blocks/${starter}/subtree`);
        return (tree.block.children || []).map((b) => b.content).find((c) => c.includes("mermaid")) || "";
      };
      await until(async () => /```mermaid width=\d+/.test(await stored()));
      const width = Number(/width=(\d+)/.exec(await stored())[1]);
      assert(width < startW && width >= 60, `dragged width ${width} is narrower than ${startW}`);
      await until(async () => Math.abs((await figure.boundingBox()).width - width) < 2, "figure takes the stored width");
      await page.reload();
      await page.locator(".diagramPreview").filter({ hasText: "Edited" }).waitFor();
      await until(async () => Math.abs((await figure.boundingBox()).width - width) < 2, "stored width survives a reload");
      await figure.hover();
      await grip.dblclick();
      await until(async () => !/width=/.test(await stored()));
      assertEq(await page.locator(".blockEditorCm").count(), 0, "resizing never opens the editor");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("diagrams: mermaid in chat waits for closing fence and recovers from invalid diagrams", async () => {
    const ctx = await scriptedChat(alice, browser);
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      // The conversation's load clears the composer: type after it settled.
      await page.waitForLoadState("networkidle");
      await input.fill("Draw a diagram"); await input.press("Enter");
      await page.waitForFunction(() => !!window.chatStream);
      await page.evaluate((source) => window.chatStream.push("```mermaid\n" + source), flow);
      await page.getByText("Waiting for the diagram to finish…", { exact: true }).waitFor();
      assertEq(await page.locator(".diagramSvg > svg").count(), 0);
      assertEq(await page.locator(".diagramSource").innerText(), flow);
      await page.evaluate(() => window.chatStream.push("\n```\n\n"));
      await page.locator(".diagramSvg > svg").waitFor();
      const firstId = await page.locator(".diagramSvg > svg").getAttribute("id");
      const untrusted = '%%{init: {"securityLevel":"loose","htmlLabels":false}}%%\nflowchart LR\nA["<img src=x onerror=alert(1)>"] --> B[Safe]\nclick A href "https://example.com"';
      await page.evaluate((text) => { window.chatStream.push(text); window.chatStream.finish(); },
        [fence("invalid diagram"), fence(sequence), fence(untrusted)].join("\n\n"));
      await until(async () => await page.locator(".diagramSvg > svg").count() === 3);
      await page.locator(".diagramError").waitFor();
      const untrustedPreview = page.locator(".diagramPreview").last();
      assertEq(await untrustedPreview.locator("a, script, [onerror], [onclick]").count(), 0,
        "diagram directives cannot enable links or unsafe HTML");
      // A label's HTML (that <img>) comes out of Mermaid as HTML, not XML:
      // the figure still draws whole, with no XML error in it, and zooms.
      assert(!(await untrustedPreview.innerText()).includes("This page contains the following errors"), "no XML error in the figure");
      assertEq(await untrustedPreview.locator("parsererror").count(), 0);
      const untrustedSvg = untrustedPreview.locator(".diagramSvg > svg");
      const untrustedShape = await untrustedSvg.boundingBox();
      await untrustedSvg.click();
      assert((await zoomedDrawing(page, untrustedShape.width / untrustedShape.height)).includes("Safe"), "the figure zooms whole");
      await page.keyboard.press("Escape");
      await page.locator(".lightbox").waitFor({ state: "detached" });
      assertEq(await page.locator(".diagramSvg > svg").first().getAttribute("id"), firstId, "streaming leaves completed SVG mounted");
      // A reply's figure zooms on a click, on the card's background.
      const figure = page.locator(".diagramSvg > svg").first();
      const shape = await figure.boundingBox();
      await figure.click();
      const zoomed = await zoomedDrawing(page, shape.width / shape.height);
      assert(zoomed.includes("Finish") && /<svg[^>]*background-color: rgb/.test(zoomed), "the zoomed flowchart on its page's colour");      await page.keyboard.press("Escape");
      await page.locator(".lightbox").waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("diagrams: LaTeX in Mermaid node and edge labels survives SVG download", async () => {
    const imported = await alice.upload("/api/import/markdown", Buffer.from(fence(quantum)), "Quantum diagram.md", "text/markdown");
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${imported.block_id}`);
    try {
      const diagram = page.locator(".diagramCard");
      await until(async () => await diagram.locator("math").count() === 5);
      assertEq(await diagram.locator(".node math").count(), 3, "node labels contain typeset math");
      assertEq(await diagram.locator(".edgeLabel math").count(), 2, "edge labels contain typeset math");
      assertEq(await diagram.getAttribute("data-diagram-source"), quantum, "math source is preserved");
      const labels = (await diagram.locator("math").allTextContents()).join(" ");
      assert(labels.includes("χ") && labels.includes("α") && labels.includes("J"), "LaTeX commands become math symbols");
      assertEq(await diagram.locator("math msub").count(), 3, "subscripts are typeset");
      assert(await diagram.locator("math").evaluateAll((els) => els.every((el) => {
        const box = el.getBoundingClientRect(); return box.width > 0 && box.height > 0;
      })), "math labels have visible dimensions");
      const downloadEvent = page.waitForEvent("download");
      await diagram.hover();
      await diagram.getByRole("button", { name: "Download SVG", exact: true }).click();
      const svg = await readFile(await (await downloadEvent).path(), "utf8");
      assertEq(await page.evaluate((source) => new DOMParser().parseFromString(source, "image/svg+xml").querySelectorAll("math").length, svg), 5);
      if (process.env.GAMMA_DIAGRAM_SCREENSHOT) await diagram.screenshot({ path: process.env.GAMMA_DIAGRAM_SCREENSHOT });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("diagrams: Mermaid lays out where getBBox() keeps a label's measuring box", async () => {
    const imported = await alice.upload("/api/import/markdown", Buffer.from(fence(nested)), "Stale geometry.md", "text/markdown");
    // The geometry some Chrome installations report: a foreignObject, and
    // every group around one, still covers the 1200px box Mermaid measured
    // the label in (lib/svgGeometry.js).
    const staleGeometry = () => {
      const native = SVGGraphicsElement.prototype.getBBox;
      SVGGraphicsElement.prototype.getBBox = function (...args) {
        const box = native.apply(this, args);
        if (this.localName !== "foreignObject" && !this.querySelector("foreignObject")) return box;
        const x = Math.min(box.x, 0), y = Math.min(box.y, 0);
        return new DOMRect(x, y, Math.max(box.x + box.width, 1200) - x, Math.max(box.y + box.height, 1200) - y);
      };
    };
    const viewBox = async (ctx) => {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${imported.block_id}`);
      const svg = page.locator(".diagramSvg > svg");
      await svg.waitFor();
      const box = (await svg.getAttribute("viewBox")).split(" ").map(Number);
      assertNoProblems(page);
      return box;
    };
    const healthy = await alice.context(browser);
    const stale = await alice.context(browser);
    await stale.addInitScript(staleGeometry);
    try {
      const expected = await viewBox(healthy);
      const got = await viewBox(stale);
      assert(got.every((v, i) => Math.abs(v - expected[i]) < 1), `the layout matches a healthy browser's: ${got} vs ${expected}`);
    } finally { await healthy.close(); await stale.close(); }
  });

  await step("diagrams: an svg figure in a note is a sanitized image, white, sized and shares the index", async () => {
    // One block (a list item's continuation lines) holding three diagrams:
    // a drawing, a flowchart and markup that is no drawing.
    const indent = (text) => text.split("\n").map((line) => "  " + line).join("\n");
    const figures = "- Figures\n" + indent([svgFence(levels), fence(flow), svgFence("<div>hi</div>")].join("\n"));
    const imported = await alice.upload("/api/import/markdown", Buffer.from(figures), "Figures.md", "text/markdown");
    const stored = async () => {
      const tree = await alice.api(`/api/blocks/${imported.block_id}/subtree`);
      return (tree.block.children || []).map((b) => b.content).find((c) => c.startsWith("Figures")) || "";
    };
    // The block's fence openers, joined: "```svg | ```mermaid | ```svg".
    const openers = async () => (await stored()).split("\n").filter((line) => /^```\w/.test(line)).join(" | ");
    assertEq(await openers(), "```svg | ```mermaid | ```svg", "the three fences share one block");
    const ctx = await alice.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${imported.block_id}`);
    try {
      await until(async () => await page.locator(".diagramFigure").count() === 2);
      const error = page.locator(".diagramError");
      await error.waitFor();
      assertEq(await error.count(), 1);
      assert((await error.innerText()).includes("Not an SVG document."), "the error names what is wrong");
      assertEq(await page.locator(".diagramCard").evaluateAll((els) => els.map((el) => el.dataset.diagramKind).join(" ")), "svg mermaid svg");
      const card = page.locator('.diagramCard[data-diagram-kind="svg"]').first();
      assertEq(await card.getAttribute("data-diagram-source"), levels);
      assertEq(await card.getAttribute("data-diagram-theme"), null, "a drawing has no theme");

      // The drawing is an image, a document of its own: it draws, and none
      // of its markup is in the page.
      const img = card.getByRole("img", { name: "SVG diagram", exact: true });
      await until(() => drawn(img), { what: "the drawing's image loads" });
      assertEq(await img.evaluate((el) => `${el.naturalWidth}x${el.naturalHeight}`), "320x200");
      const markup = await imageMarkup(img);
      assert(markup.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'), "a standalone document");
      assert(!/script|onload|svgRan/.test(markup), "the image is the sanitized drawing");
      assert(markup.includes('<marker id="arrow"') && markup.includes("url(#arrow)") && markup.includes("<style>.level"), "ids and style stay as drawn");
      assertEq(await card.locator(".diagramFigure svg").count(), 0, "no inline drawing");
      assertEq(await page.locator("[id='arrow']").count(), 0, "its ids are not the page's");
      assertEq(await page.evaluate(() => window.svgRan), undefined, "the drawing's script never ran");
      assertEq(await page.evaluate(() => {
        const probe = document.body.appendChild(Object.assign(document.createElement("div"), { className: "level" }));
        const width = getComputedStyle(probe).strokeWidth;
        probe.remove();
        return width;
      }), "1px", "the drawing's style does not reach the page");

      // A white page in every theme, and no re-render when the theme flips.
      for (const theme of ["dark", "light"]) {
        await setTheme(page, theme);
        assertEq(await card.locator(".diagramPreview").evaluate((el) => getComputedStyle(el).backgroundColor), "rgb(255, 255, 255)", `white in ${theme}`);
        assertEq(await imageMarkup(img), markup);
      }

      await card.hover();
      await card.getByRole("button", { name: "Copy source", exact: true }).click();
      assertEq((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n"), levels, "copy source is the original text");
      const downloadEvent = page.waitForEvent("download");
      await card.getByRole("button", { name: "Download SVG", exact: true }).click();
      const download = await downloadEvent;
      assertEq(download.suggestedFilename(), "diagram.svg");
      const file = await readFile(await download.path(), "utf8");
      assertEq(file, markup, "the download is the image's drawing");

      // The menu's Copy image: each figure as shown, an SVG at its size on
      // its page plus a PNG twice that, the flowchart's labels drawn in it.
      const copyImageOf = async (picture) => {
        await page.evaluate(() => navigator.clipboard.writeText("before"));
        await picture.click({ button: "right" });
        await page.getByRole("button", { name: "Copy image", exact: true }).click();
        let copied = null;
        await until(async () => !(copied = await page.evaluate(readClipboard))["text/plain"], { what: "the figure reached the clipboard" });
        return copied;
      };
      const drawing = await copyImageOf(img);
      assertEq(drawing["image/png"]?.size, "640x400", "the drawing's PNG is twice its shown size");
      assert(drawing["image/png"].inked > 0, "the PNG is drawn");
      const drawnSvg = drawing["image/svg+xml"] || "";
      assert(/<svg[^>]* width="320" height="200"/.test(drawnSvg) && drawnSvg.includes("background-color: rgb(255, 255, 255)"),
        `the SVG at its shown size on its white page: ${drawnSvg.slice(0, 200)}`);
      assert(drawnSvg.includes('<marker id="arrow"') && !/script|onload/.test(drawnSvg), "the sanitized drawing");
      const flowSvg = page.locator('.diagramCard[data-diagram-kind="mermaid"] .diagramSvg > svg');
      const flowBox = await flowSvg.boundingBox();
      const flowchart = await copyImageOf(flowSvg);
      assertEq(flowchart["image/png"]?.size, `${Math.floor(flowBox.width * 2)}x${Math.floor(flowBox.height * 2)}`, "the flowchart's PNG is twice its shown size");
      assert(flowchart["image/png"].inked > 0, "the flowchart's PNG is drawn");
      const flowRoot = /<svg[^>]*>/.exec(flowchart["image/svg+xml"] || "")?.[0] || "";
      assert(flowRoot.includes(` width="${Math.round(flowBox.width)}"`) && !flowRoot.includes("max-width"),
        `the flowchart's SVG at its shown size: ${flowRoot}`);
      assert(flowchart["image/svg+xml"].includes("Finish"), "with its labels");
      await page.getByText("Copied image", { exact: true }).waitFor();

      // The drawing hugs its own width; its grip writes width=N on its own
      // fence, the flowchart's grip on the flowchart's: one index for both kinds.
      const svgFigure = card.locator(".diagramFigure");
      assert(Math.abs((await svgFigure.boundingBox()).width - 320) < 2, "the figure takes the drawing's width");
      await dragGrip(page, svgFigure, -60);
      await until(async () => /^```svg width=\d+ \|/.test(await openers()));
      const svgWidth = Number(/width=(\d+)/.exec(await openers())[1]);
      assert(svgWidth < 320 && svgWidth >= 60, `dragged width ${svgWidth}`);
      assertEq(await openers(), `\`\`\`svg width=${svgWidth} | \`\`\`mermaid | \`\`\`svg`, "the other fences keep their size");
      await until(async () => Math.abs((await svgFigure.boundingBox()).width - svgWidth) < 2, "the drawing takes the stored width");
      const mermaidFigure = page.locator('.diagramCard[data-diagram-kind="mermaid"] .diagramFigure');
      await dragGrip(page, mermaidFigure, -40);
      await until(async () => /\| ```mermaid width=\d+ \|/.test(await openers()));
      assert(new RegExp(`^\`\`\`svg width=${svgWidth} \\| \`\`\`mermaid width=\\d+ \\| \`\`\`svg$`).test(await openers()),
        "the flowchart's grip sized the flowchart only");
      assertEq(await page.locator(".blockEditorCm").count(), 0, "resizing never opens the editor");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("diagrams: an svg figure streams into chat and copies back as its fence", async () => {
    const ctx = await scriptedChat(alice, browser);
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await page.waitForLoadState("networkidle");
      await input.fill("Draw the energy levels"); await input.press("Enter");
      await page.waitForFunction(() => !!window.chatStream);
      const half = Math.floor(levels.length / 2);
      await page.evaluate((text) => window.chatStream.push(text), "Two figures:\n\n```svg\n" + levels.slice(0, half));
      await page.getByText("Waiting for the diagram to finish…", { exact: true }).waitFor();
      const images = page.locator("img.diagramImg");
      assertEq(await images.count(), 0, "an open svg fence is not drawn");
      await page.evaluate((text) => window.chatStream.push(text), levels.slice(half) + "\n```\n\n");
      await images.first().waitFor();
      await page.evaluate((text) => { window.chatStream.push(text); window.chatStream.finish(); }, svgFence(levels) + "\n\nDone.");
      await until(async () => await images.count() === 2);
      // Both drawings define <marker id="arrow">: each in its own image.
      for (const img of await images.all()) await until(() => drawn(img), { what: "a reply's drawing loads" });
      assertEq(await page.locator("[id='arrow']").count(), 0);
      assertEq(await page.locator(".diagramFigure svg").count(), 0);
      assertEq(await page.evaluate(() => window.svgRan), undefined);
      // A click zooms the drawing: the same sanitized drawing on its white page.
      await images.first().click();
      const zoomed = await zoomedDrawing(page, 320 / 200);
      assert(zoomed.includes('<marker id="arrow"') && zoomed.includes("background-color: rgb(255, 255, 255)") && !/script|onload/.test(zoomed),
        `the zoomed drawing is the sanitized one, white: ${zoomed.slice(0, 200)}`);      await page.locator(".lightbox").click();
      await page.locator(".lightbox").waitFor({ state: "detached" });
      const copied = await page.locator(".diagramCard").first().locator("xpath=..").evaluate(selectionCopy);
      assert(copied.startsWith("Two figures:") && copied.endsWith("Done.") && copied.split(`\n${svgFence(levels)}\n`).length === 3,
        `a copied reply keeps its svg fences: ${JSON.stringify(copied)}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}

// Files and documents (docs/dev/block_centric.md, stage 4): any file dropped
// on a block row or on the page body becomes a file chip; a PDF chip promotes
// to a DOCUMENT page (right-click → "Add to library" → by-doc, no re-upload)
// filed in the project's folder, and offers "Open page" afterwards; a
// document page's PDF is replaced by a new version; the upload endpoint takes
// lab files and refuses executables. Then documents of any format
// (docs/dev/documents.md "Writing a document"): a PNG, a .tex and a .bin
// dropped on the library are pages showing them, one page per stored file;
// the paperclip's Upload a file… on a note page; a .json chip's Add to
// library; a dropped directory brings its files and leaves its .git out.
import { PNG_1PX, editRow, tree } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

// Drop `files` ([{name, type, bytes}]) on the first element `selector`
// matches, the way the browser would: one DataTransfer carrying real File
// objects, dragover then drop. Synthetic items have no webkitGetAsEntry, so
// the app's folder branch is skipped and the plain-files path runs.
export async function dropFiles(page, selector, files) {
  const payload = files.map((f) => ({ name: f.name, type: f.type, b64: Buffer.from(f.bytes).toString("base64") }));
  const dt = await page.evaluateHandle((items) => {
    const dt = new DataTransfer();
    for (const it of items) {
      const bin = atob(it.b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      dt.items.add(new File([arr], it.name, { type: it.type }));
    }
    return dt;
  }, payload);
  const el = page.locator(selector).first();
  await el.dispatchEvent("dragover", { dataTransfer: dt });
  await el.dispatchEvent("drop", { dataTransfer: dt });
}

// Drop a directory named `name` holding `files` ([{path, type, bytes}], the
// paths below it) on `selector`, as the browser hands one over: one item
// whose webkitGetAsEntry is the directory's entry, its files read through
// the entry API (shared/lib/dropEntries.js). A synthetic item has no entry,
// so the entries are stood in for while the drop runs.
async function dropFolder(page, selector, name, files) {
  const payload = files.map((f) => ({ path: f.path, type: f.type || "", b64: Buffer.from(f.bytes).toString("base64") }));
  await page.evaluate(({ selector, name, items }) => {
    const dir = (dirName) => ({
      isDirectory: true, isFile: false, name: dirName, children: [],
      createReader() {
        let read = false;
        return { readEntries: (ok) => { const batch = read ? [] : this.children; read = true; ok(batch); } };
      },
    });
    const root = dir(name);
    for (const it of items) {
      const parts = it.path.split("/");
      let at = root;
      for (const part of parts.slice(0, -1)) {
        let next = at.children.find((c) => c.isDirectory && c.name === part);
        if (!next) { next = dir(part); at.children.push(next); }
        at = next;
      }
      const file = new File([Uint8Array.from(atob(it.b64), (c) => c.charCodeAt(0))], parts[parts.length - 1], { type: it.type });
      at.children.push({ isFile: true, isDirectory: false, name: file.name, file: (ok) => ok(file) });
    }
    const dt = new DataTransfer();
    dt.items.add(new File([], name));
    const proto = DataTransferItem.prototype;
    const native = proto.webkitGetAsEntry;
    proto.webkitGetAsEntry = () => root;
    try {
      const el = document.querySelector(selector);
      for (const type of ["dragenter", "dragover", "drop"]) el.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
    } finally {
      proto.webkitGetAsEntry = native;
    }
  }, { selector, name, items: payload });
}

export async function fileScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  const account = alice;
  let ctx, page, projectId, docPageId, rydberg;
  const pdf = makePdf([["Supplementary material for the Rydberg paper"]]);

  await step("files: a drop on a block row makes file chips — a notebook and a PDF", async () => {
    rydberg = await account.folder("Projects/Rydberg");
    const created = await account.api("/api/pages", { method: "POST", body: { title: "Lab project", folders: [rydberg] } });
    projectId = created.id;
    await account.api("/api/blocks", { method: "POST", body: { parent_id: projectId, content: "Data goes here" } });
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${projectId}&ws=${account.ws}`);
    await page.waitForSelector(".blockRow", { timeout: 15000 });
    await dropFiles(page, ".blockRowWrap", [
      { name: "sim.nb", type: "", bytes: Buffer.from("Notebook[{Cell[1]}]") },
      { name: "supplement.pdf", type: "application/pdf", bytes: pdf },
    ]);
    await until(async () => (await page.$$(".blockRow .fileChip")).length === 2, { what: "two file chips" });
    // the row's text change saves through the (debounced) op path
    await until(async () => /supplement\.pdf/.test((await tree(account, projectId))[0]?.content || ""), { what: "chips saved" });
    const t = await tree(account, projectId);
    assertEq(t.length, 1, "still one block");
    assert(/\[sim\.nb\]\(\/api\/uploads\/[0-9a-f]+\.nb\)/.test(t[0].content), `notebook chip stored: ${t[0].content}`);
    assert(/\[supplement\.pdf\]\(\/api\/uploads\/[0-9a-f]+\.pdf\)/.test(t[0].content), `pdf chip stored: ${t[0].content}`);
    // every chip looks the same: no inline action anywhere
    assertEq((await page.$$(".fileChip button")).length, 0, "no inline actions");
    // the PDF has no document page yet → its right-click menu offers to make one
    await page.locator(".fileChip", { hasText: "supplement.pdf" }).click({ button: "right" });
    await page.locator(".ctxMenuItem", { hasText: "Add to library" }).waitFor({ timeout: 8000 });
    await page.keyboard.press("Escape");
    await page.locator(".fileChip", { hasText: "sim.nb" }).click({ button: "right" });
    await page.locator(".ctxMenuItem", { hasText: "Download" }).waitFor();
    assertEq((await page.$$(".ctxMenuItem:has-text('Add to library')")).length, 1, "a notebook can become a page too: a file of any format is one");
    assertEq((await page.$$(".ctxMenuItem")).length, 2, "the page and the download are the whole menu for a notebook");
    await page.keyboard.press("Escape");
    assertEq((await page.$$(".fileChipOpen")).length, 0, "no open-page buttons before any promotion");
    // both uploads are listed as finished background tasks
    await page.click("button[aria-label='Background tasks']");
    await page.locator(".taskRow", { hasText: "supplement.pdf" }).waitFor({ timeout: 5000 });
    assert((await page.$$(".taskRow .taskStatus.done")).length >= 2, "both uploads done");
    await page.keyboard.press("Escape");
    assertNoProblems(page);
  });

  await step("files: a drop on the page body appends blocks at the end", async () => {
    await dropFiles(page, ".app", [
      { name: "fit.mat", type: "", bytes: Buffer.from("MATLAB 5.0 MAT-file") },
      { name: "README", type: "", bytes: Buffer.from("no extension") },
    ]);
    await until(async () => (await tree(account, projectId)).length === 3, { what: "two new blocks saved" });
    const t = await tree(account, projectId);
    assert(/^\[fit\.mat\]\(\/api\/uploads\/[0-9a-f]+\.mat\)$/.test(t[1].content), `mat block: ${t[1].content}`);
    assert(/^\[README\]\(\/api\/uploads\/[0-9a-f]+\.bin\)$/.test(t[2].content), `extension-less block: ${t[2].content}`);
    assertEq((await page.$$(".blockRow .fileChip")).length, 4, "four chips rendered");
    assertNoProblems(page);
  });

  await step("files: 'Add to library' makes the document page in the same folder", async () => {
    await page.locator(".fileChip", { hasText: "supplement.pdf" }).click({ button: "right" });
    await page.click(".ctxMenuItem:has-text('Add to library')");
    await waitForPdf(page, 1);
    await until(async () => (await page.textContent(".titleText")) === "supplement.pdf", { what: "title = file name" });
    docPageId = new URL(page.url()).searchParams.get("block");
    assert(docPageId && docPageId !== projectId, "navigated to a new page");
    const doc = await account.api(`/api/blocks/${docPageId}`);
    assertEq(doc.parent_id, "root", "a root page");
    assertEq(JSON.stringify(doc.properties.folders), JSON.stringify([rydberg]), "filed with the project");
    assert(doc.properties.doc?.hash, "carries the PDF as its document");
    // the header paperclip is the document's own popover
    await page.click("button[aria-label='Document']");
    await page.waitForSelector(".attachPopover .popoverTitle:has-text('Document')");
    await page.keyboard.press("Escape");
    // the same hash again is the same page (no duplicate)
    const again = await account.api(`/api/blocks/by-doc/${doc.properties.doc.hash}`, { method: "POST", body: { default_title: "" } });
    assertEq(again.id, docPageId, "by-doc finds the page");
    assertNoProblems(page);
  });

  await step("files: back on the project page the PDF chip shows an open-page button that opens it", async () => {
    await page.goto(`${server.base}/?page=${projectId}&ws=${account.ws}`);
    const chip = page.locator(".fileChip", { hasText: "supplement.pdf" });
    await chip.locator(".fileChipOpen").waitFor({ timeout: 15000 });
    assertEq((await page.$$(".fileChipOpen")).length, 1, "only the promoted PDF has the button");
    await chip.click({ button: "right" });
    await page.locator(".ctxMenuItem", { hasText: "Open page" }).waitFor({ timeout: 8000 });
    assertEq((await page.$$(".ctxMenuItem:has-text('Add to library')")).length, 0, "no promotion offered any more");
    await page.keyboard.press("Escape");
    await chip.locator(".fileChipOpen").click();
    await until(async () => new URL(page.url()).searchParams.get("block") === docPageId, { what: "document page opened" });
    await waitForPdf(page, 1);
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: a duplicated document page shows the PDF but leaves it one page — the original", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}&folder=${rydberg}`);
    const original = await account.api(`/api/blocks/${docPageId}`);
    await account.api(`/api/blocks/${docPageId}`, { method: "PUT", body: { properties: { ...original.properties, notion_id: "a".repeat(32) } } });
    await page.locator(".fileRow", { hasText: "supplement.pdf" }).first().click({ button: "right" });
    await page.locator(".ctxMenuItem", { hasText: "Duplicate" }).click();
    const copyRow = page.locator(".fileRow", { hasText: "supplement.pdf (copy)" });
    await copyRow.waitFor({ timeout: 8000 });
    const pages = (await account.api("/api/blocks/root/children")).children;
    const copy = pages.find((p) => p.content === "supplement.pdf (copy)");
    assert(copy && !("doc_id" in copy.properties), "the copy carries no flat document keys");
    assert(copy.properties.doc?.copy === true, "the copy shows the PDF as a copy, which it does not own");
    assert(!copy.properties.notion_id, "nor a Notion id: an import of that page would find the copy");
    assertEq(copy.properties.doc.hash, original.properties.doc.hash, "it shows the same file");
    const byDoc = await account.api(`/api/blocks/by-doc/${original.properties.doc.hash}`);
    assertEq(byDoc.id, docPageId, "the PDF's page is still the original");
    await copyRow.dblclick();
    await until(async () => new URL(page.url()).searchParams.get("block") === copy.id, { what: "the copy opened" });
    await waitForPdf(page, 1);
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: a markdown chip's 'Add to library' imports it as a note page; the file stays", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${projectId}&ws=${account.ws}`);
    await page.waitForSelector(".blockRow", { timeout: 15000 });
    const md = Buffer.from("# Setup\n\n- first point\n- second point\n");
    await dropFiles(page, ".app", [{ name: "Squeezing notes.md", type: "text/markdown", bytes: md }]);
    const chip = page.locator(".fileChip", { hasText: "Squeezing notes.md" });
    await chip.waitFor({ timeout: 8000 });
    await until(async () => (await tree(account, projectId)).some((b) => /Squeezing notes\.md/.test(b.content)), { what: "md chip saved" });
    await chip.click({ button: "right" });
    await page.click(".ctxMenuItem:has-text('Add to library')");
    await until(async () => (await page.textContent(".titleText")) === "Squeezing notes", { what: "note page titled after the file" });
    const noteId = new URL(page.url()).searchParams.get("block");
    const note = await account.api(`/api/blocks/${noteId}`);
    assertEq(JSON.stringify(note.properties.folders), JSON.stringify([rydberg]), "filed with the project");
    assert(!note.properties.doc, "a note page, not a document page");
    assert((await page.$$(".pdfViewer")).length === 0, "no viewer for a note page");
    await page.locator(".blockRow", { hasText: "first point" }).waitFor();
    // editing the note leaves the file as it was
    const mdUrl = (await tree(account, projectId)).find((b) => /Squeezing notes\.md/.test(b.content)).content.match(/\((\/api\/uploads\/[^)]+)\)/)[1];
    await account.api(`/api/blocks/${noteId}`, { method: "PUT", body: { content: "Squeezing notes (edited)" } });
    const served = await account.api(mdUrl, { raw: true });
    assertEq(await served.text(), md.toString(), "file untouched");
    // back on the project page: the chip has the open button, and it goes to the note
    await page.goto(`${server.base}/?page=${projectId}&ws=${account.ws}`);
    await chip.locator(".fileChipOpen").waitFor({ timeout: 15000 });
    await chip.locator(".fileChipOpen").click();
    await until(async () => new URL(page.url()).searchParams.get("block") === noteId, { what: "note page opened from the chip" });
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: a PDF pasted into the editor becomes a file chip at the caret", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${projectId}&ws=${account.ws}`);
    await page.waitForSelector(".blockRow", { timeout: 15000 });
    await editRow(page, "Data goes here");
    const dt = await page.evaluateHandle((b64) => {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const dt = new DataTransfer();
      dt.items.add(new File([arr], "pasted.pdf", { type: "application/pdf" }));
      return dt;
    }, Buffer.from(pdf).toString("base64"));
    // Playwright's dispatchEvent knows no ClipboardEvent — build it in the page.
    await page.evaluate((dt) => {
      document.querySelector(".blockEditorCm .cm-content")
        .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    }, dt);
    await until(async () => /\[pasted\.pdf\]\(\/api\/uploads\/[0-9a-f]+\.pdf\)/.test((await tree(account, projectId))[0]?.content || ""), { what: "pasted chip saved" });
    await page.evaluate(() => document.activeElement?.blur());
    await until(async () => (await page.$$(".blockRow .fileChip")).length === 6, { what: "six chips rendered" });
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: the upload endpoint takes lab files and refuses executables", async () => {
    const nb = await account.upload("/api/upload-file", Buffer.from("x"), "model.nb", "application/octet-stream");
    assert(nb.url.endsWith(".nb") && nb.name === "model.nb", `nb stored: ${JSON.stringify(nb)}`);
    const r = await account.api(nb.url, { raw: true });
    assertEq(r.status, 200, "served");
    assert((r.headers.get("content-disposition") || "").startsWith("attachment"), "downloads rather than renders");
    const exe = await account.upload("/api/upload-file", Buffer.from("MZ"), "setup.exe", "application/octet-stream").catch((e) => e);
    assertEq(exe.status, 400, "executables are refused");
    assert(/executable/.test(exe.data?.detail || ""), `refusal names the reason: ${exe.data?.detail}`);
  });

  await step("tasks: a slow upload shows its progress bar and stops from the popover", async () => {
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 10, downloadThroughput: -1, uploadThroughput: (512 * 1024) / 8 });
    const big = makePdf([["Slow upload"]], { padBytes: 3 * 1024 * 1024 });
    await page.click("button[aria-label='Add']");
    const chooser = page.waitForEvent("filechooser");
    await page.locator(".addPopover .ctxMenuItem", { hasText: "Upload files" }).click();
    await (await chooser).setFiles([{ name: "slow-upload.pdf", mimeType: "application/pdf", buffer: Buffer.from(big) }]);
    await page.click("button[aria-label='Background tasks']");
    const row = page.locator(".taskRow", { hasText: "slow-upload.pdf" });
    await row.locator(".taskBar").waitFor({ timeout: 10000 });
    await until(async () => /\d+(\.\d+)? [KM]B \/ /.test(await row.innerText()), { what: "byte progress on the row" });
    await row.getByRole("button", { name: "Stop slow-upload.pdf", exact: true }).click();
    await until(async () => (await row.innerText()).includes("stopped"), { what: "the row reads stopped" });
    assertEq(await row.locator(".taskBar").count(), 0, "a stopped row has no bar");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await sleep(500);
    assertNoProblems(page);
    await ctx.close();
  });

  // A PDF past one part's size (shared/lib/uploadParts.js PART_BYTES, 32 MiB)
  // goes up in parts — a proxy in front of the server caps a request's body —
  // and opens like any other upload; the task row reports the whole's bytes.
  await step("tasks: a PDF past one part's size goes up in parts and opens like any other", async () => {
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    const parts = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith("/api/uploads/parts")) parts.push(`${request.method()} ${url.pathname.replace(/\/[A-Za-z0-9_-]{20,}/, "/<token>")}`);
      else if (url.pathname === "/api/uploads" && request.method() === "POST") parts.push("POST /api/uploads");
    });
    const big = makePdf([["Uploaded in parts"], ["Page two of the big one"]], { padBytes: 33 * 1024 * 1024 });
    await page.click("button[aria-label='Add']");
    const chooser = page.waitForEvent("filechooser");
    await page.locator(".addPopover .ctxMenuItem", { hasText: "Upload files" }).click();
    await (await chooser).setFiles([{ name: "in-parts.pdf", mimeType: "application/pdf", buffer: Buffer.from(big) }]);
    await waitForPdf(page, 1);
    const text = await page.textContent(`[data-page="1"] .textLayer`);
    assert(text.includes("Uploaded in parts"), `the viewer shows the assembled file: ${text.slice(0, 60)}`);
    assertEq(parts.filter((p) => p === "POST /api/uploads").length, 0, "never one request for the whole");
    assertEq(parts[0], "POST /api/uploads/parts", `opened first: ${parts.join(", ")}`);
    assertEq(parts.filter((p) => p === "POST /api/uploads/parts/<token>").length, 2, `two parts of 32 MiB: ${parts.join(", ")}`);
    assertEq(parts[parts.length - 1], "POST /api/uploads/parts/<token>/finish", `finished last: ${parts.join(", ")}`);
    await page.click("button[aria-label='Background tasks']");
    const row = page.locator(".taskRow.done", { hasText: "in-parts.pdf" }); // not the metadata lookup's row
    await until(async () => /33(\.\d+)? MB/.test(await row.innerText()), { what: "the upload's row reports the whole file's size" });
    assertNoProblems(page);
    await ctx.close();
  });

  // Several PDFs at once: their lookups are one metadata job on the server
  // (docs/dev/tasks.md), not a lookup per paper in the tab. The suite's
  // registries are off and the account has no AI, so each finds nothing.
  await step("files: an upload of several PDFs looks their metadata up as one background job", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    await page.waitForSelector(".folderNewBtn");
    await page.click("button[aria-label='Add']");
    const chooser = page.waitForEvent("filechooser");
    await page.locator(".addPopover .ctxMenuItem", { hasText: "Upload files" }).click();
    await (await chooser).setFiles(["First batch paper", "Second batch paper"].map((title, i) => ({
      name: `batch-${i + 1}.pdf`, mimeType: "application/pdf", buffer: Buffer.from(makePdf([[title]])) })));
    await page.click("button[aria-label='Background tasks']");
    await page.locator(".taskRow", { hasText: "Metadata lookup for 2 papers" }).locator(".taskStatus.done").waitFor({ timeout: 30000 });
    assertEq((await page.$$(".taskRow:has-text('Metadata —')")).length, 0, "no lookup row per paper");
    const job = (await account.api("/api/jobs")).jobs.find((j) => j.kind === "metadata");
    assertEq(JSON.stringify((await account.api(`/api/jobs/${job.id}`)).result), JSON.stringify({ papers: 2, fetched: 0, failed: 2 }));
    assertNoProblems(page);
    await ctx.close();
  });

  // A new version of a document page's PDF (a paper compiled again from its
  // TeX): the Document popover's "Replace the file…" swaps the file; the page
  // keeps its id, title and highlights, the server's relocate-highlights job
  // moves the highlight to where its text is in the new file
  // (docs/dev/highlight_relocation.md), and another tab on the page follows.
  await step("files: 'Replace the file…' swaps a document page's file, keeps its title and highlights, and the highlight follows its text", async () => {
    const up = await account.upload("/api/uploads", makePdf([["Draft one of the thesis"]]), "thesis.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up.doc_id}`, {
      method: "POST", body: { default_title: "", source_url: up.source_url, original_filename: "thesis.pdf" } });
    // a highlight over the whole line, its quote that line, as the viewer stores it (a quote that is only
    // a short part of the text under the rectangles says they were not measured for it: _agrees)
    const rect = { x1: 72, y1: 60, x2: 300, y2: 80 };
    const position = { pageNumber: 1, width: 612, height: 792, boundingRect: rect, rects: [rect] };
    const hl = await account.api("/api/blocks", { method: "POST", body: {
      parent_id: doc.id, content: "a note on draft one", properties: { quote: "Draft one of the thesis", pdf_position: position } } });
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.locator(`[data-page="1"] [data-hl-id="${hl.id}"]`).first().waitFor();
    const other = await openPage(ctx, `${server.base}/?page=${doc.id}&ws=${account.ws}`); // a second tab
    await waitForPdf(other, 1);
    // every line the status pill shows, so the job's summary is seen
    // whichever of it and "PDF replaced." comes first
    await page.evaluate(() => {
      window.pillLines = [];
      new MutationObserver(() => {
        const text = document.querySelector(".statusPill")?.textContent;
        if (text && text !== window.pillLines.at(-1)) window.pillLines.push(text);
      }).observe(document.body, { subtree: true, childList: true, characterData: true });
    });

    await page.click("button[aria-label='Document']");
    const chooser = page.waitForEvent("filechooser");
    await page.locator(".attachPopover .ctxMenuItem", { hasText: "Replace the file…" }).click();
    await (await chooser).setFiles({ name: "thesis-v2.pdf", mimeType: "application/pdf",
      buffer: Buffer.from(makePdf([["Draft two of the thesis"], ["A second page"]])) });
    await waitForPdf(page, 2);
    await until(async () => (await page.textContent('[data-page="1"] .textLayer')).includes("Draft two"),
      { what: "the viewer shows the new file" });

    const after = await account.api(`/api/blocks/${doc.id}`);
    assert(after.properties.doc?.hash && after.properties.doc.hash !== up.doc_id, "the page carries the new file");
    assertEq(after.content, "thesis.pdf", "the title stays the first file's name");
    assertEq(after.properties.doc?.name, "thesis-v2.pdf", "the file name is the new file's");
    // the job the replacement started finds the highlight's text in the new file and says so: one word
    // changed, so it is matched roughly, drawn dashed for the person to check
    const summary = "Highlights placed in the PDF: 0 moved, 1 matched roughly or estimated, 0 stayed in place, 0 not found. "
      + "Hover the dashed highlight to compare and accept it.";
    await until(() => page.evaluate((s) => window.pillLines.some((line) => line.includes(s)), summary),
      { what: "the relocation's summary", timeout: 20000 });
    const placed = await until(async () => {
      const b = await account.api(`/api/blocks/${hl.id}`);
      const p = b.properties.pdf_position;
      return JSON.stringify(p) !== JSON.stringify(position) && !p.lost && !p.doc_id && p.approx && typeof p.match === "number"
        && (b.properties.quote || "").includes("Draft two") ? b : null;
    }, { what: "the highlight moved to its text in the new file", timeout: 20000 });
    assertEq(placed.content, "a note on draft one", "the highlight keeps its note");
    await page.locator(`[data-page="1"] [data-hl-id="${hl.id}"]`).first().waitFor(); // drawn over the new file
    // the other tab hears of it over the page's socket and shows the new file
    await waitForPdf(other, 2);
    await until(async () => (await other.textContent('[data-page="1"] .textLayer')).includes("Draft two"),
      { what: "the other tab shows the new file" });
    assertNoProblems(page);
    assertNoProblems(other);
    await ctx.close();
  });

  // A file of another format than PDF or Markdown dropped on the library:
  // uploaded as any file and made a page by its stored name (POST /pages/
  // by-file), which opens, its document shown in its format's pane.
  const dropOnLibrary = async (page, file) => {
    await page.goto(`${server.base}/?ws=${account.ws}`);
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    await dropFiles(page, ".app", [file]);
    await until(() => !!new URL(page.url()).searchParams.get("block"), { timeout: 20000, what: `${file.name}'s page opens` });
    return new URL(page.url()).searchParams.get("block");
  };

  await step("files: a PNG, a .tex and a .bin dropped on the library become pages of their format, and the same file again opens the same page", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    const cases = [
      { file: { name: "chart.png", type: "image/png", bytes: PNG_1PX }, format: "image", ext: ".png", pane: ".imgViewer img" },
      { file: { name: "notes.tex", type: "", bytes: Buffer.from("\\documentclass{article}\n\\begin{document}Notes\\end{document}\n") },
        format: "text", ext: ".tex", pane: ".docTextView .cm-content" },
      { file: { name: "weights.bin", type: "application/octet-stream", bytes: Buffer.from([0, 1, 2, 3, 255, 254]) }, format: "binary", ext: ".bin", pane: ".filePane" },
    ];
    for (const { file, format, ext, pane } of cases) {
      const id = await dropOnLibrary(page, file);
      await page.locator(pane).waitFor({ timeout: 20000 });
      const made = await account.api(`/api/blocks/${id}`);
      const doc = made.properties.doc;
      assertEq(JSON.stringify([doc.format, doc.at, doc.ext, doc.size]), JSON.stringify([format, "store", ext, file.bytes.length]), `${file.name}'s document`);
      assert(doc.hash && doc.name === file.name, `${file.name}: a stored file named for it`);
      assertEq(made.content, file.name, `${file.name}: the page is titled by the file`);
      assertEq(made.parent_id, "root", "a library page");
      assertEq(await dropOnLibrary(page, file), id, `${file.name} dropped again opens the same page`);
      const owners = (await account.api("/api/blocks/root/children")).children.filter((p) => p.properties?.doc?.hash === doc.hash);
      assertEq(owners.length, 1, "one page per stored file");
    }
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: a slash in a dropped file's name is part of it: a PDF, a note and a .tex are titled whole, filed in no folder, and the PDF again opens its page", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    const foldersBefore = JSON.stringify((await account.api("/api/blocks/folders/subtree")).block);
    const cases = [
      { file: { name: "AC/DC live.pdf", type: "application/pdf", bytes: makePdf([["A slash in the file's name"]]) }, title: "AC/DC live.pdf" },
      { file: { name: "I/O notes.md", type: "text/markdown", bytes: Buffer.from("Inputs and outputs.\n") }, title: "I/O notes" },
      { file: { name: "A/B test.tex", type: "", bytes: Buffer.from("\\section{A/B}\n") }, title: "A/B test.tex" },
    ];
    for (const { file, title } of cases) {
      const id = await dropOnLibrary(page, file);
      await until(async () => (await page.textContent(".titleText")) === title, { what: `${file.name}: the title shown` });
      const made = await account.api(`/api/blocks/${id}`);
      assertEq(made.content, title, `${file.name}: the page's title`);
      assertEq(JSON.stringify(made.properties.folders ?? []), "[]", `${file.name}: filed in no folder`);
      assertEq(made.properties.doc?.name ?? made.properties.original_filename, file.name, `${file.name}: the name kept whole`);
      if (file.type === "application/pdf") assertEq(await dropOnLibrary(page, file), id, "the same PDF again opens its page");
    }
    assertEq(JSON.stringify((await account.api("/api/blocks/folders/subtree")).block), foldersBefore, "no folder made of a name's part");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: 'Upload a file…' makes a file the open note page's document; the paperclip then offers to replace or detach the file", async () => {
    const note = await account.api("/api/pages", { method: "POST", body: { title: "Settings notes" } });
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${note.id}&ws=${account.ws}`);
    await page.locator(".titleText").waitFor({ timeout: 15000 });
    await page.click("button[aria-label='Attach document']");
    const chooser = page.waitForEvent("filechooser");
    await page.locator(".attachPopover .ctxMenuItem", { hasText: "Upload a file…" }).click();
    const text = JSON.stringify({ seed: 7, rate: 0.01 }, null, 2);
    await (await chooser).setFiles({ name: "settings.json", mimeType: "application/json", buffer: Buffer.from(text) });
    const content = page.locator(".docTextView .cm-content");
    await content.waitFor({ timeout: 20000 });
    await until(async () => (await content.innerText()).includes('"seed"'), { what: "the file's text in the code view" });
    const doc = (await account.api(`/api/blocks/${note.id}`)).properties.doc;
    assertEq(JSON.stringify([doc.format, doc.at, doc.ext, doc.name]), JSON.stringify(["text", "store", ".json", "settings.json"]), "the note page's document");
    await page.click("button[aria-label='Document']");
    const menu = page.locator(".attachPopover");
    await menu.locator(".ctxMenuItem", { hasText: "Replace the file…" }).waitFor();
    await menu.locator(".ctxMenuItem", { hasText: "Detach the file…" }).waitFor();
    for (const gone of ["Upload a file…", "Find highlights in this PDF"]) {
      assertEq(await menu.locator(".ctxMenuItem", { hasText: gone }).count(), 0, `no ${gone}`);
    }
    await page.keyboard.press("Escape");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: a chip's 'Add to library' on a .json makes its page in the code view, the same page for the same stored file", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${projectId}&ws=${account.ws}`);
    await page.waitForSelector(".blockRow", { timeout: 15000 });
    await dropFiles(page, ".app", [{ name: "params.json", type: "application/json", bytes: Buffer.from(JSON.stringify({ runs: [1, 2, 3] })) }]);
    const chip = page.locator(".fileChip", { hasText: "params.json" });
    await chip.waitFor({ timeout: 8000 });
    await until(async () => (await tree(account, projectId)).some((b) => /params\.json/.test(b.content)), { what: "the chip saved" });
    const stored = (await tree(account, projectId)).find((b) => /params\.json/.test(b.content)).content.match(/\/api\/uploads\/([0-9a-f]+\.json)\)/)[1];
    await chip.click({ button: "right" });
    await page.click(".ctxMenuItem:has-text('Add to library')");
    await until(() => { const id = new URL(page.url()).searchParams.get("block"); return id && id !== projectId; }, { what: "the file's page opens" });
    const pageId = new URL(page.url()).searchParams.get("block");
    await page.locator(".docTextView .cm-content").waitFor({ timeout: 20000 });
    const made = await account.api(`/api/blocks/${pageId}`);
    assertEq(made.content, "params.json", "titled by the file");
    assertEq(JSON.stringify([made.properties.doc?.format, made.properties.doc?.at, made.properties.doc?.ext]), JSON.stringify(["text", "store", ".json"]),
      "its document is the stored file");
    assertEq(JSON.stringify(made.properties.folders), JSON.stringify([rydberg]), "filed with the project");
    const again = await account.api("/api/pages/by-file", { method: "POST", body: { file: stored } });
    assertEq(again.page.id, pageId, "by-file finds the page");
    assertEq(again.created, false, "and makes no second one");
    // back on the project page: the chip offers Open page and shows the button
    await page.goto(`${server.base}/?page=${projectId}&ws=${account.ws}`);
    await chip.locator(".fileChipOpen").waitFor({ timeout: 15000 });
    await chip.click({ button: "right" });
    await page.locator(".ctxMenuItem", { hasText: "Open page" }).waitFor({ timeout: 8000 });
    assertEq((await page.$$(".ctxMenuItem:has-text('Add to library')")).length, 0, "no promotion offered any more");
    await page.keyboard.press("Escape");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("files: a folder dropped on the library brings its files as pages in its folder, leaving its .git out", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    // a 1×1 GIF: bytes of its own, since the PNG dropped above has its page already (one page per stored file)
    const gif = Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64");
    await dropFolder(page, ".app", "thesis", [
      { path: "main.tex", bytes: Buffer.from("\\documentclass{book}\n") },
      { path: "figures/setup.gif", type: "image/gif", bytes: gif },
      { path: "README.md", type: "text/markdown", bytes: Buffer.from("# Thesis\n\nThe draft.\n") },
      { path: ".git/HEAD", bytes: Buffer.from("ref: refs/heads/main\n") },
      { path: ".git/config", bytes: Buffer.from("[core]\n") },
    ]);
    // a folder of the tree by name, under the folder named `under` when given
    const folderNamed = async (name, under = null) => {
      const walk = (node, parent) => (node.content === name && (!under || parent?.content === under) ? node
        : (node.children || []).map((c) => walk(c, node)).find(Boolean));
      return walk((await account.api("/api/blocks/folders/subtree")).block, null);
    };
    // the library's pages filed in the folder (a work folder alone lists its own)
    const inFolder = async (folder) => (await account.api("/api/blocks/root/children")).children.filter((p) => (p.properties?.folders || []).includes(folder.id));
    const thesis = await until(() => folderNamed("thesis"), { timeout: 20000, what: "the dropped folder's folder" });
    await until(async () => (await inFolder(thesis)).length === 2, { timeout: 30000, what: "main.tex and the README in it" });
    assertEq(JSON.stringify((await inFolder(thesis)).map((p) => p.content).sort()), JSON.stringify(["README", "main.tex"]), "the source as a file page, the README as a note");
    const figures = await until(() => folderNamed("figures", "thesis"), { timeout: 20000, what: "its figures folder" });
    const figure = await until(async () => (await inFolder(figures)).find((p) => p.content === "setup.gif") || null, { timeout: 20000, what: "the figure's page" });
    assertEq((await account.api(`/api/blocks/${figure.id}`)).properties.doc?.format, "image", "the figure is a picture page");
    const all = (await account.api("/api/blocks/root/children")).children;
    assertEq(all.filter((p) => ["HEAD", "config"].includes(p.content)).length, 0, "nothing of .git");
    assert(!(await folderNamed(".git")), "no .git folder");
    assertNoProblems(page);
    await ctx.close();
  });
}

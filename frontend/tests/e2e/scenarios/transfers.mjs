import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { ROOT } from "../harness.mjs";
import { newPageViaUi } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

// Windows clipboards hand back CRLF; compare against the text as shown.
const CRLF = String.fromCharCode(13, 10);
const LF = String.fromCharCode(10);

export async function transferScenarios({ server, browser, alice, bob, makePdf, step, assert, assertEq, assertNoProblems, openPage, until, flags }) {
  async function setup(viewport) {
    const ctx = await alice.context(browser, viewport ? { viewport } : {});
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    return { ctx, page: await openPage(ctx, server.base) };
  }
  async function openDialog(page, name) {
    // The View menu — on a phone its rows are in the bottom bar's More sheet.
    const view = page.locator('[data-popover="menu"] > button');
    await (await view.count() ? view : page.locator('[data-guide="header.account"]')).click();
    await page.getByRole("button", { name: `${name}…`, exact: true }).click();
    // The dialogs are a chunk fetched on first use (docs/dev/frontend-refactor.md,
    // "Lazy boundaries"): a click right after the menu opened can beat it.
    const dialog = page.getByRole("dialog", { name, exact: true });
    await dialog.waitFor();
    return dialog;
  }
  const choice = (dialog, name) => dialog.getByRole("button", { name, exact: true });
  const toggle = (dialog, name) => dialog.getByRole("checkbox", { name, exact: true });
  const python = process.env.GAMMA_E2E_PYTHON || path.join(ROOT, "backend", "venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

  await step("transfer: Zotero ZIP review, cancellation, import report and repeat import", async () => {
    const { ctx, page } = await setup();
    try {
      const fixture = path.join(server.dir, "review.zip");
      // Use the backend's synthetic fixture: one real PDF, one absent PDF,
      // a nested collection and an explicitly empty attachment directory.
      execFileSync(python, ["-c", `
import sys, zipfile
sys.path.insert(0, 'backend/tests')
from test_zotero_import import RDF, _annotated_pdf
rdf = RDF.replace('s41586-000-00000-0', 'e2e-zotero-review')
rdf = rdf.replace('<dc:title>Proximal Policy Optimization</dc:title>', '<dc:title>Missing PDF example</dc:title><link:link rdf:resource="#missing"/>')
rdf = rdf.replace('</rdf:RDF>', '<z:Attachment rdf:about="#missing"><z:path rdf:resource="files/99/missing.pdf"/></z:Attachment></rdf:RDF>')
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr('Review/library.rdf', rdf)
    z.writestr('Review/files/3/Vaswani - 2017 - Attention.pdf', _annotated_pdf(b'E2E review PDF'))
    z.writestr('Review/files/99/', '')
`, fixture], { cwd: ROOT });
      const selectZip = async () => {
        const dialog = await openDialog(page, "Import");
        await choice(dialog, "Zotero library").click();
        await choice(dialog, "Next").click();
        const chooser = page.waitForEvent("filechooser");
        await choice(dialog, "or choose a .zip").click();
        await (await chooser).setFiles(fixture);
        const review = page.getByRole("dialog", { name: "Review Zotero import", exact: true });
        await review.getByRole("heading", { name: "Library after import", exact: true }).waitFor();
        return review;
      };
      await page.waitForSelector(".folderNewBtn");
      let imports = 0;
      let uploads = 0;
      page.on("request", request => {
        if (request.method() !== "POST") return;
        const path = new URL(request.url()).pathname;
        if (path === "/api/jobs/import") imports++; // the import itself is a background job
        if (path === "/api/import/review") uploads++;
      });
      let review = await selectZip();
      assert(await review.getByText("Empty folder", { exact: true }).isVisible());
      const target = review.getByRole("region", { name: "Library after import", exact: true });
      assert(await target.getByText("ML", { exact: true }).isVisible());
      assert(await target.getByText("Transformers", { exact: true }).isVisible());
      assert(await target.getByText("Missing PDF example", { exact: true }).isVisible());
      assertEq((await target.locator(".importKind").allTextContents()).join(","), "PDF,Page");
      assert((await review.locator(".importWarnings").innerText()).includes("PDF missing from ZIP"));
      assertEq(imports, 0, "preview does not import");
      await choice(review, "Missing").click();
      assertEq(await target.locator(".importTreeFile").count(), 1);
      assert((await review.innerText()).includes("2 of 2 items selected"), "filtering keeps hidden selections");
      await choice(review, "Deselect all").click();
      assert(await choice(review, "Import to library").isDisabled());
      await review.getByRole("checkbox", { name: "Import Missing PDF example", exact: true }).check();
      await choice(review, "Selected").click();
      assertEq(await target.locator(".importTreeFile").count(), 1);
      await choice(review, "All").click();
      await choice(review, "Select all").click();
      if (flags.keep) await page.screenshot({ path: `${server.dir}/zotero-review.png` });
      await choice(review, "Cancel").click();
      assertEq(imports, 0, "cancelling leaves the library unchanged");
      review = await selectZip();
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      assertEq(imports, 1);
      assertEq(uploads, 2, "each review uploads once; committing reuses the upload");
      assert((await report.locator(".importWarnings").innerText()).includes("PDF missing from ZIP"));
      await choice(report, "Done").click();
      review = await selectZip();
      assertEq(await review.getByText("Update existing page", { exact: false }).count(), 2);
      await page.setViewportSize({ width: 390, height: 844 });
      assert(!(await review.evaluate(el => el.scrollWidth > el.clientWidth + 1)), "review fits mobile");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/zotero-review-mobile.png` });
      await choice(review, "Cancel").click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: a picked Zotero export folder is packed and reviewed like its zip, and a dropped .zip too", async () => {
    const { ctx, page } = await setup();
    try {
      // The export as Zotero leaves it on disk, one PDF not written yet: a
      // picked folder lists no empty folders, so files/99 is just absent.
      const folder = path.join(server.dir, "Folder Review");
      execFileSync(python, ["-c", `
import os, sys
sys.path.insert(0, 'backend/tests')
from test_zotero_import import RDF, _annotated_pdf
root = sys.argv[1]
rdf = RDF.replace('s41586-000-00000-0', 'e2e-zotero-folder').replace('Attention is all you need', 'Folder PDF example')
rdf = rdf.replace('<dc:title>Proximal Policy Optimization</dc:title>', '<dc:title>Unwritten PDF example</dc:title><link:link rdf:resource="#unwritten"/>')
rdf = rdf.replace('</rdf:RDF>', '<z:Attachment rdf:about="#unwritten"><z:path rdf:resource="files/99/unwritten.pdf"/></z:Attachment></rdf:RDF>')
os.makedirs(os.path.join(root, 'files', '3'))
open(os.path.join(root, 'library.rdf'), 'w', encoding='utf-8').write(rdf)
open(os.path.join(root, 'files', '3', 'Vaswani - 2017 - Attention.pdf'), 'wb').write(_annotated_pdf(b'E2E folder PDF'))
`, folder], { cwd: ROOT });
      await page.waitForSelector(".folderNewBtn");
      let dialog = await openDialog(page, "Import");
      await choice(dialog, "Zotero library").click();
      await choice(dialog, "Next").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose folder…").click();
      await (await chooser).setFiles(folder);
      let review = page.getByRole("dialog", { name: "Review Zotero import", exact: true });
      await review.getByRole("heading", { name: "Library after import", exact: true }).waitFor();
      assert(await review.getByRole("heading", { name: "Inside the folder", exact: true }).isVisible());
      assert((await review.locator(".importReviewHeader").innerText()).includes("Folder Review"));
      assert(await review.getByRole("region", { name: "ZIP contents", exact: true }).getByText("library.rdf", { exact: true }).isVisible());
      const warnings = await review.locator(".importWarnings").innerText();
      assert(warnings.includes("Zotero export not finished") && warnings.includes("(Zotero had not written it yet)"), warnings);
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      const imported = report.getByRole("region", { name: "Library after import", exact: true });
      assert(await imported.getByText("Folder PDF example", { exact: true }).isVisible());
      await choice(report, "Done").click();
      // The same export zipped and dropped on the step: reviewed like a
      // picked .zip, and the library behind never takes the drop.
      const zip = path.join(server.dir, "Folder Review.zip");
      execFileSync(python, ["-c", "import shutil, sys; shutil.make_archive(sys.argv[1][:-4], 'zip', sys.argv[2], 'Folder Review')", zip, server.dir]);
      let uploads = 0;
      page.on("request", request => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/uploads") uploads++; });
      dialog = await openDialog(page, "Import");
      await choice(dialog, "Zotero library").click();
      await choice(dialog, "Next").click();
      const transfer = await page.evaluateHandle(bytes => {
        const data = new DataTransfer();
        data.items.add(new File([new Uint8Array(bytes)], "Folder Review.zip", { type: "application/zip" }));
        return data;
      }, [...fs.readFileSync(zip)]);
      const area = dialog.locator(".importDropArea");
      await area.dispatchEvent("dragover", { dataTransfer: transfer });
      assert(await dialog.locator(".importDrop.over").isVisible(), "the drop line lights up");
      if (flags.keep) await dialog.screenshot({ path: `${server.dir}/zotero-drop.png` });
      await area.dispatchEvent("drop", { dataTransfer: transfer });
      review = page.getByRole("dialog", { name: "Review Zotero import", exact: true });
      await review.getByRole("heading", { name: "Inside the ZIP", exact: true }).waitFor();
      await review.getByRole("heading", { name: "Library after import", exact: true }).waitFor();
      assert((await review.locator(".importReviewHeader").innerText()).includes("Folder Review.zip"));
      assert(await review.getByText("Update existing page", { exact: false }).count() >= 1, "the dropped export updates what the folder made");
      await choice(review, "Cancel").click();
      assertEq(uploads, 0, "the drop stays in the dialog");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: an export past one request's size goes up in parts and imports", async () => {
    const { ctx, page } = await setup();
    try {
      // A proxy in front of the server caps a request's body (Cloudflare at
      // 100 MB): past 32 MiB the review's upload goes in parts.
      const folder = path.join(server.dir, "Parts Review");
      execFileSync(python, ["-c", `
import os, sys
sys.path.insert(0, 'backend/tests')
from test_zotero_import import RDF, _annotated_pdf
root = sys.argv[1]
rdf = RDF.replace('s41586-000-00000-0', 'e2e-zotero-parts').replace('Attention is all you need', 'Parts PDF example')
os.makedirs(os.path.join(root, 'files', '3'))
os.makedirs(os.path.join(root, 'files', '5'))
open(os.path.join(root, 'library.rdf'), 'w', encoding='utf-8').write(rdf)
open(os.path.join(root, 'files', '3', 'Vaswani - 2017 - Attention.pdf'), 'wb').write(_annotated_pdf(b'E2E parts PDF'))
open(os.path.join(root, 'files', '5', 'dataset.bin'), 'wb').write(os.urandom(40 << 20))
`, folder], { cwd: ROOT });
      await page.waitForSelector(".folderNewBtn");
      const posts = [];
      page.on("request", request => { if (request.method() === "POST") posts.push(new URL(request.url()).pathname); });
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Zotero library").click();
      await choice(dialog, "Next").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose folder…").click();
      await (await chooser).setFiles(folder);
      const review = page.getByRole("dialog", { name: "Review Zotero import", exact: true });
      await review.getByRole("heading", { name: "Library after import", exact: true }).waitFor();
      assertEq(posts.filter((p) => /^\/api\/import\/review\/parts\/[0-9a-f]+$/.test(p)).length, 2, "40 MB goes up as two parts");
      assert(!posts.includes("/api/import/review"), "never as one request");
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      assert(await report.getByRole("region", { name: "Library after import", exact: true })
        .getByText("Parts PDF example", { exact: true }).isVisible());
      await choice(report, "Done").click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: a Zotero item's other attachments arrive on its page, under Attachments", async () => {
    const { ctx, page } = await setup();
    try {
      const fixture = path.join(server.dir, "attachments.zip");
      // The item's PDF is the page's document; a CSV and a web link go
      // under its "Attachments" block, as in Zotero's item tree.
      execFileSync(python, ["-c", `
import sys, zipfile
sys.path.insert(0, 'backend/tests')
from test_zotero_import import RDF, _annotated_pdf, _file_att, _link_att, _with_attachments
rdf = RDF.replace('s41586-000-00000-0', 'e2e-zotero-attachments').replace('Attention is all you need', 'Attachments example')
rdf = _with_attachments(rdf, [_file_att('#data', 'files/4/data.csv', 'text/csv'),
                              _link_att('#link', 'https://example.org/publisher', 'Publisher page')])
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr('Attach/library.rdf', rdf)
    z.writestr('Attach/files/3/Vaswani - 2017 - Attention.pdf', _annotated_pdf(b'E2E attachments PDF'))
    z.writestr('Attach/files/4/data.csv', 'a,b\\n1,2\\n')
`, fixture], { cwd: ROOT });
      await page.waitForSelector(".folderNewBtn");
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Zotero library").click();
      await choice(dialog, "Next").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "or choose a .zip").click();
      await (await chooser).setFiles(fixture);
      const review = page.getByRole("dialog", { name: "Review Zotero import", exact: true });
      const target = review.getByRole("region", { name: "Library after import", exact: true });
      await target.waitFor();
      const row = target.locator(".importTreeFile", { hasText: "Attachments example" });
      assert((await row.innerText()).includes("2 attachments"), await row.innerText());
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      assert((await report.innerText()).includes("2 attachments"), "the report counts them");
      await choice(report, "Done").click();
      const pages = (await alice.api("/api/blocks/root/children")).children;
      const imported = pages.find((p) => p.content === "Attachments example");
      await page.goto(`${server.base}/?page=${imported.id}&ws=${alice.ws}`);
      await page.locator(".blockRow .fileChip", { hasText: "data.csv" }).waitFor();
      assert(await page.getByRole("heading", { name: "Attachments", exact: true }).isVisible());
      assert(await page.locator(".blockRow a", { hasText: "Publisher page" }).isVisible());
      if (flags.keep) await page.screenshot({ path: `${server.dir}/zotero-attachments.png` });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: shared Markdown upload progress, selection and persistent summary", async () => {
    const { ctx, page } = await setup();
    try {
      await page.waitForSelector(".folderNewBtn");
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      // `*`: the upload's URL carries the workspace (?ws=…).
      await page.route("**/api/import/review*", async route => {
        const response = await route.fetch();
        await Promise.race([gate, new Promise(resolve => setTimeout(resolve, 3000))]);
        await route.fulfill({ response });
      });
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Markdown notes").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose file…").click();
      await (await chooser).setFiles({ name: "reviewed.md", mimeType: "text/markdown", buffer: Buffer.from("---\ntitle: Reviewed Markdown\nfolder: Imported notes\n---\nA selected note.") });
      const review = page.getByRole("dialog", { name: "Review Markdown import", exact: true });
      await review.getByRole("progressbar").waitFor();
      // The upload phase ends as soon as the bytes are sent; the gate above
      // holds only the answer, so a slow runner may already be scanning.
      const busyText = await review.innerText();
      assert(["Uploading for review", "Checking files and library destinations"].some((label) => busyText.includes(label)),
        "the review shows its busy phase while the upload is answered");
      release();
      await review.getByRole("checkbox", { name: "Import Reviewed Markdown", exact: true }).waitFor();
      await choice(review, "Deselect all").click();
      assert(await choice(review, "Import to library").isDisabled());
      await review.getByRole("checkbox", { name: "Select folder Imported notes", exact: true }).check();
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      assert((await report.innerText()).includes("1 new page"));
      assert(await report.getByText("Reviewed Markdown", { exact: true }).isVisible());
      await choice(report, "Done").click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // No cap on a zip's notes; a review of more than 300 (ImportTree's
  // OPEN_UP_TO) starts with its folders closed and makes a folder's rows
  // only once it opens.
  await step("transfer: a vault of hundreds of notes reviews with its folders closed and imports whole", async () => {
    const { ctx, page } = await setup();
    try {
      const vault = path.join(server.dir, "Big vault.zip");
      execFileSync(python, ["-c", `
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED) as z:
    for i in range(400):
        z.writestr(f'Big vault/Topic {i % 4}/Vault note {i}.md', f'- point of vault note {i}\\n')
`, vault], { cwd: ROOT });
      await page.waitForSelector(".folderNewBtn");
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Markdown notes").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose file…").click();
      await (await chooser).setFiles(vault);
      const review = page.getByRole("dialog", { name: "Review Markdown import", exact: true });
      const target = review.getByRole("region", { name: "Library after import", exact: true });
      await target.getByText("Topic 1", { exact: true }).waitFor();
      assert((await review.innerText()).includes("400 of 400 items selected"));
      assertEq(await review.locator(".importTreeFile").count(), 0, "both trees start closed");
      await target.getByText("Topic 1", { exact: true }).click();
      await until(async () => (await target.locator(".importTreeFile").count()) === 100, { what: "an opened folder shows its notes" });
      await target.getByRole("checkbox", { name: "Select folder Topic 2", exact: true }).uncheck();
      assert((await review.innerText()).includes("300 of 400 items selected"));
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      assert((await report.innerText()).includes("300 new pages"));
      await choice(report, "Done").click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: Gamma export review imports only the selected page without reloading", async () => {
    const donorA = await bob.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Selected Gamma review" } });
    const donorB = await bob.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Excluded Gamma review" } });
    const backup = await bob.api("/api/export", { raw: true });
    const buffer = Buffer.from(await backup.arrayBuffer());
    const { ctx, page } = await setup();
    try {
      await page.waitForSelector(".folderNewBtn");
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Gamma export (.zip)").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose .zip…").click();
      await (await chooser).setFiles({ name: "gamma-review.zip", mimeType: "application/zip", buffer });
      const review = page.getByRole("dialog", { name: "Review Gamma import", exact: true });
      await review.getByRole("checkbox", { name: "Import Selected Gamma review", exact: true }).waitFor();
      await choice(review, "Deselect all").click();
      await review.getByRole("checkbox", { name: "Import Selected Gamma review", exact: true }).check();
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      assert((await report.innerText()).includes("1 new page"));
      assertEq(await report.getByText("Excluded Gamma review", { exact: true }).count(), 0);
      assertEq((await alice.api(`/api/blocks/${donorA.id}`)).content, "Selected Gamma review");
      assertEq((await alice.api(`/api/blocks/${donorB.id}`, { raw: true })).status, 404);
      await choice(report, "Done").click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: a Notability note imports as a page with its ink, text box, picture and recording", async () => {
    const { ctx, page } = await setup();
    try {
      // The backend's writer of what Notability writes (backend/tests/notability_fixtures.py):
      // a PDF page then a paper page, a pen stroke and a highlighter, a text
      // box and a picture on page 1, a dot on page 2, and a recording. And
      // an .ntb, Notability Cloud's format, which the import refuses.
      const note = path.join(server.dir, "E2E Notability note.note");
      const ntb = path.join(server.dir, "Cloud note.ntb");
      execFileSync(python, ["-c", `
import sys
sys.path.insert(0, 'backend/tests')
from notability_fixtures import ntb, sample_note
open(sys.argv[1], 'wb').write(sample_note())
open(sys.argv[2], 'wb').write(ntb())
`, note, ntb], { cwd: ROOT });
      await page.waitForSelector(".folderNewBtn");
      const pickNotes = async (file) => {
        const dialog = await openDialog(page, "Import");
        await choice(dialog, "Notability notes").click();
        const chooser = page.waitForEvent("filechooser");
        await choice(dialog, "Choose files…").click();
        await (await chooser).setFiles(file);
        return page.getByRole("dialog", { name: "Review Notability import", exact: true });
      };
      // The .ntb: the server's refusal says how to export "Note" instead, and
      // sending the same file again is not offered.
      let review = await pickNotes(ntb);
      await review.getByRole("alert").filter({ hasText: "NTB" }).waitFor();
      assertEq(await choice(review, "Retry preview").count(), 0, "a refused file is not offered again");
      assert(!(await review.innerText()).includes("Choose what to import"), "nothing to choose from");
      await choice(review, "Cancel").click();
      await review.waitFor({ state: "detached" });
      assertNoProblems(page, [/^POST \/api\/import\/review\S* -> 400$/]);

      review = await pickNotes(note);
      const target = review.getByRole("region", { name: "Library after import", exact: true });
      await target.getByText("E2E Notability note", { exact: true }).waitFor();
      assertEq((await target.locator(".importKind").allTextContents()).join(","), "PDF");
      assert(await review.getByRole("heading", { name: "Source file", exact: true }).isVisible());
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      const summary = await report.locator(".importReviewHeader").innerText();
      assert(summary.includes("1 new page") && summary.includes("1 recording"), summary);
      await choice(report, "Done").click();

      // The page carries one PDF, the note's PDF page and its paper page; an
      // ink group per page; the boxes on page 1; the recording.
      const imported = (await alice.api("/api/blocks/root/children")).children.find((p) => p.content === "E2E Notability note");
      assert(imported?.properties?.doc_id, "the note keeps a PDF");
      const blocks = (await alice.api(`/api/blocks/${imported.id}/subtree`)).block.children;
      const groups = blocks.filter((b) => b.properties?.ink_url);
      assertEq(groups.length, 2, "an ink group per page");
      const files = await Promise.all(groups.map((b) => alice.api(b.properties.ink_url)));
      for (const file of files) assertEq(`${file.format} ${file.version} ${file.space.kind}`, "gamma-ink 2 pdf-page");
      assert(files.find((f) => f.space.page === 1)?.strokes.some((s) => s.tool === "highlighter"), "the highlighter on page 1");
      assert(files.flatMap((f) => f.strokes).some((s) => (s.ch || "").includes("w")), "a stroke whose width varies along it");
      const boxes = blocks.filter((b) => b.properties?.text_box && b.properties.pdf_page === 1);
      assert(boxes.some((b) => b.content.includes("Typed **box**")), "the text box");
      assert(boxes.some((b) => b.content.startsWith("![")), "the picture's box");
      const recording = blocks.find((b) => b.properties?.recording);
      assertEq(recording?.properties.recording.segments[0].mime, "audio/mp4");

      // Picked again, twice over: the files go up as one .zip, the second
      // copy renamed in it, and the note already in the library is skipped.
      const copy = { name: path.basename(note), mimeType: "application/octet-stream", buffer: fs.readFileSync(note) };
      review = await pickNotes([copy, copy]);
      await review.getByRole("heading", { name: "Selected files", exact: true }).waitFor();
      const source = review.getByRole("region", { name: "ZIP contents", exact: true });
      for (const name of ["E2E Notability note.note", "E2E Notability note (2).note"]) {
        assert(await source.getByText(name, { exact: true }).isVisible(), name);
      }
      await until(async () => (await review.getByText("Already in library", { exact: true }).count()) === 2, { what: "both copies skipped" });
      await choice(review, "Cancel").click();
      await review.waitFor({ state: "detached" });

      // On screen: both pages, the ink drawn, the picture as wide as its box's text.
      await page.goto(`${server.base}/?page=${imported.id}&ws=${alice.ws}`);
      await until(async () => (await page.locator(".pdfPageWrap").count()) === 2, { what: "the note's two pages" });
      await until(async () => (await page.locator('[data-page="1"] .inkLayer path').count()) >= 2, { what: "the pen and the highlighter drawn" });
      const picture = page.locator('[data-page="1"] .textBox img.mdImg');
      await picture.waitFor();
      await until(() => picture.evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the picture loaded" });
      const fit = await picture.evaluate((img) => {
        const body = img.closest(".textBoxBody");
        const style = getComputedStyle(body);
        return { picture: img.getBoundingClientRect().width,
          text: body.getBoundingClientRect().width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) };
      });
      assert(Math.abs(fit.picture - fit.text) < 1, `the picture fills its box's text width: ${JSON.stringify(fit)}`);
      if (flags.keep) await page.screenshot({ path: `${server.dir}/notability-note.png` });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: format selection, live options, back navigation and a real notes PDF", async () => {
    const { ctx, page } = await setup();
    try {
      await newPageViaUi(page, "Export preview example");
      const dialog = await openDialog(page, "Export");
      assert(await choice(dialog, "Close Export").isVisible());
      assertEq(await choice(dialog, "Cancel").count(), 0);
      await page.keyboard.press("Shift+Tab");
      assert(await dialog.evaluate((el) => el.contains(document.activeElement)), "reverse tab from the heading stays in the dialog");
      assertEq(await choice(dialog, "PDF").getAttribute("aria-pressed"), "true");
      // a note page has no paper: no "This paper" formats, the others offered
      assertEq(await dialog.getByRole("group", { name: "This paper choices", exact: true }).count(), 0);
      for (const type of ["Notes", "Library"]) {
        assert(await dialog.getByRole("group", { name: `${type} choices`, exact: true }).getByRole("button").count() > 0, `${type} formats offered`);
      }
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/export-formats.png` });
      await choice(dialog, "Markdown").dblclick();
      assert(await dialog.getByRole("heading", { name: "Markdown", exact: true }).isVisible());
      assertEq(await choice(dialog, "Back").count(), 0);
      await choice(dialog, "1. Choose a format").click();
      assertEq(await choice(dialog, "Markdown").getAttribute("aria-pressed"), "true");
      await choice(dialog, "PDF").click();
      await choice(dialog, "Next").click();
      await toggle(dialog, "Highlights").uncheck();
      assertEq(await dialog.locator('[data-preview="highlights"]').count(), 0);
      await toggle(dialog, "Notes").uncheck();
      assertEq(await dialog.locator('[data-preview="notes"]').count(), 0);
      await choice(dialog, "1. Choose a format").focus();
      await page.keyboard.press("Enter");
      await choice(dialog, "Next").click();
      assertEq(await toggle(dialog, "Notes").isChecked(), false);
      await toggle(dialog, "Highlights").check();
      await toggle(dialog, "Notes").check();
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/export-page-preview.png` });
      const request = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/jobs/export");
      const download = page.waitForEvent("download");
      await choice(dialog, "Export").click();
      const body = (await request).postDataJSON();
      assertEq(body.mode, "notes-pdf");
      assertEq(body.highlights, true);
      assertEq(body.notes, true);
      const file = await download; // the dialog watches the job: its file comes by itself
      assertEq(fs.readFileSync(await file.path()).subarray(0, 5).toString(), "%PDF-");
      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      // the finished step counts the pages (the job's result, fetched once it ended)
      await dialog.getByText("1 page exported.", { exact: true }).waitFor();
      await choice(dialog, "Done").click();
      await dialog.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: required content, bundled files, and mobile keyboard dismissal", async () => {
    const { ctx, page } = await setup({ width: 390, height: 844 });
    try {
      await newPageViaUi(page, "Mobile export example");
      let dialog = await openDialog(page, "Export");
      await choice(dialog, "Gamma").click();
      assertEq(await choice(dialog, "Next").count(), 0);
      assertEq(await dialog.getByRole("checkbox").count(), 0);
      const gammaDownload = page.waitForEvent("download");
      await choice(dialog, "Export").click();
      assertEq(fs.readFileSync(await (await gammaDownload).path()).subarray(0, 2).toString(), "PK");
      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      assert(!(await dialog.evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "the finished step fits mobile");
      await choice(dialog, "Done").click();
      await dialog.waitFor({ state: "detached" });
      dialog = await openDialog(page, "Export");
      await choice(dialog, "Logseq").click();
      await choice(dialog, "Next").click();
      assertEq(await dialog.getByRole("checkbox").count(), 1);
      assertEq(await dialog.locator('[data-preview="highlights"]').count(), 1);
      assertEq(await dialog.locator('[data-preview="notes"]').count(), 1);
      assert(!(await toggle(dialog, "Bundle the files").isDisabled()));
      await choice(dialog, "1. Choose a format").click();
      await choice(dialog, "Obsidian").click();
      await choice(dialog, "Next").click();
      await toggle(dialog, "Bundle the files").uncheck();
      assertEq(await dialog.locator('[data-preview="linked-files"]').count(), 1);
      await toggle(dialog, "Bundle the files").check();
      assertEq(await dialog.locator('[data-preview="bundled-files"]').count(), 1);
      assert(!(await dialog.evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "dialog fits mobile");
      await dialog.locator(".transferStep").evaluate((el) => { el.scrollTop = 0; });
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/export-mobile.png` });
      await choice(dialog, "Export").focus();
      await page.keyboard.press("Tab");
      assert(await dialog.evaluate((el) => el.contains(document.activeElement)), "focus stays in the dialog");
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: PDF export and import previews retain source-specific behavior", async () => {
    const { ctx, page } = await setup();
    try {
      const pdf = makePdf([["Import and export preview"]]);
      const up = await alice.upload("/api/uploads", pdf, "preview.pdf", "application/pdf");
      const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Preview paper", source_url: up.source_url } });
      await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
      await waitForPdf(page);
      let dialog = await openDialog(page, "Export");
      await choice(dialog, "Annotated PDF").click();
      await choice(dialog, "Next").click();
      assertEq(await dialog.locator('[data-preview="original"]').count(), 1);
      await toggle(dialog, "Notes").uncheck();
      assertEq(await dialog.locator('[data-preview="notes"]').count(), 0);
      await choice(dialog, "Close Export").click();
      dialog = await openDialog(page, "Import");
      assertEq(await dialog.getByRole("group", { name: "Import from" }).getByRole("button").count(), 6);
      assertEq(await choice(dialog, "Annotations in this PDF").getAttribute("aria-pressed"), "true");
      for (const [type, count] of [["This paper", 2], ["Notes", 2], ["Library", 2]]) {
        assertEq(await dialog.getByRole("group", { name: `${type} choices`, exact: true }).getByRole("button").count(), count);
      }
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/import-sources.png` });
      await choice(dialog, "Next").click();
      await toggle(dialog, "Strip the originals").check();
      assertEq(await dialog.locator('[data-preview="highlights"]').count(), 0);
      assertEq(await dialog.locator('[data-preview="imported-annotations"]').count(), 1);
      await choice(dialog, "1. Choose a source").click();
      assertEq(await choice(dialog, "Annotations in this PDF").getAttribute("aria-pressed"), "true");
      await choice(dialog, "Next").click();
      assert(await toggle(dialog, "Strip the originals").isChecked(), "breadcrumb preserves the import options");
      await toggle(dialog, "Strip the originals").uncheck();
      assertEq(await dialog.locator('[data-preview="highlights"]').count(), 1);
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/import-preview.png` });
      await choice(dialog, "1. Choose a source").click();
      const markdownChooser = page.waitForEvent("filechooser");
      await choice(dialog, "Markdown notes").dblclick();
      assertEq(await (await markdownChooser).element().getAttribute("accept"), ".md,.markdown,.zip,text/markdown,application/zip");
      await dialog.waitFor({ state: "detached" });
      dialog = await openDialog(page, "Import");
      await choice(dialog, "Zotero library").click();
      await choice(dialog, "Next").click();
      assert(await dialog.getByText("Export from Zotero", { exact: true }).isVisible());
      const folderChooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose folder…").click();
      assert(await (await folderChooser).element().evaluate(input => input.webkitdirectory), "the folder button picks a folder");
      await dialog.waitFor({ state: "detached" });
      dialog = await openDialog(page, "Import");
      await choice(dialog, "Zotero library").click();
      await choice(dialog, "Next").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "or choose a .zip").click();
      assertEq(await (await chooser).element().getAttribute("accept"), ".zip,application/zip");
      await dialog.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: direct actions use the activated format and import source", async () => {
    const { ctx, page } = await setup();
    try {
      await newPageViaUi(page, "Direct transfer example");
      let dialog = await openDialog(page, "Export");
      await choice(dialog, "PDF").click();
      const request = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/jobs/export");
      const download = page.waitForEvent("download");
      await choice(dialog, "Gamma").dblclick();
      assertEq((await request).postDataJSON().mode, "gamma");
      assertEq(fs.readFileSync(await (await download).path()).subarray(0, 2).toString(), "PK");
      await choice(dialog, "Done").click();
      await dialog.waitFor({ state: "detached" });
      for (const [name, accept, multiple] of [["Gamma export (.zip)", ".zip,application/zip", false], ["Logseq highlights", ".pdf,.edn,.md", true],
        ["Notability notes", ".note,.zip,application/zip", true]]) {
        dialog = await openDialog(page, "Import");
        const chooser = page.waitForEvent("filechooser");
        if (multiple) {
          await choice(dialog, name).click();
          assertEq(await choice(dialog, "Next").count(), 0);
          await choice(dialog, "Choose files…").click();
        } else await choice(dialog, name).dblclick();
        const fileChooser = await chooser;
        assertEq(await fileChooser.element().getAttribute("accept"), accept);
        assertEq(fileChooser.isMultiple(), multiple);
        await dialog.waitFor({ state: "detached" });
      }
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("transfer: a folder's papers export as annotated PDFs in one zip, in the background, reopened from the tray", async () => {
    const folder = "E2E annotated";
    const folderId = await alice.folder(folder);
    for (const [title, where] of [["Folder paper one", folder], ["Folder paper two", `${folder}/Sub`]]) {
      const up = await alice.upload("/api/uploads", makePdf([[title]]), `${title}.pdf`, "application/pdf");
      const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: title, source_url: up.source_url } });
      await alice.file(created.id, { folders: [where] });
    }
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Folder note page", properties: { folders: [folderId] } } });
    const { ctx, page } = await setup();
    try {
      // The server's job is quick: its listing is held at "running" so the
      // window can be closed and opened again while it works.
      let hold = true;
      await page.route(/\/api\/jobs(\?.*)?$/, async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        for (const job of body.jobs || []) {
          if (hold && job.kind === "export" && job.params.folder === folderId) {
            Object.assign(job, { state: "running", finished_at: "", artifact: null, stoppable: true,
              progress: { done: 1, total: 3, unit: "pages", item: "Folder paper two" } });
          }
        }
        await route.fulfill({ response, json: body });
      });
      await page.goto(`${server.base}/?folder=${folderId}&ws=${alice.ws}`);
      await page.waitForSelector(".folderNewBtn");
      const view = page.locator('[data-popover="menu"] > button');
      await (await view.count() ? view : page.locator('[data-guide="header.account"]')).click();
      await page.getByRole("button", { name: "Export…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: `Export “${folder}”`, exact: true });
      await dialog.getByRole("group", { name: "Papers choices", exact: true }).waitFor();
      await choice(dialog, "Annotated PDF").click();
      await choice(dialog, "Next").click();
      await toggle(dialog, "Highlights").check();
      await toggle(dialog, "Notes").uncheck();
      assert((await dialog.innerText()).includes("Pages without a PDF are left out"), "the summary says what a folder export skips");
      const started = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/jobs/export");
      await choice(dialog, "Export").click();
      const job = await (await started).json();
      assertEq(job.params.mode, "annotated-pdf");
      assertEq(job.params.folder, folderId);
      await dialog.getByRole("heading", { name: "Exporting…", exact: true }).waitFor();
      await dialog.getByText("1 of 3 pages · Folder paper two", { exact: true }).waitFor();
      if (flags.keep) await page.screenshot({ path: `${server.dir}/export-running.png` });
      // Closed mid-way: the export goes on in Background tasks, where its row stops or opens it.
      await choice(dialog, "Close").click();
      await dialog.waitFor({ state: "detached" });
      await page.click("button[aria-label='Background tasks']");
      const title = `Export “${folder}” as Annotated PDF`;
      const row = page.locator(".taskRow", { hasText: title });
      await row.waitFor();
      assert(await row.getByRole("button", { name: `Stop ${title}`, exact: true }).isVisible(), "a running export can be stopped");
      assert((await row.innerText()).includes("1 of 3 pages"), "the row shows how far it got");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/tasks-running.png` });
      await row.getByRole("button", { name: `Open ${title}`, exact: true }).click();
      const again = page.getByRole("dialog", { name: `Export “${folder}”`, exact: true });
      await again.getByRole("heading", { name: "Exporting…", exact: true }).waitFor();
      // Once ready, the file comes by itself to the window watching it.
      const download = page.waitForEvent("download");
      hold = false;
      const file = await download;
      assertEq(file.suggestedFilename(), `${folder}-annotated.zip`);
      await again.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      const names = JSON.parse(execFileSync(python, ["-c", "import json, sys, zipfile; print(json.dumps(sorted(zipfile.ZipFile(sys.argv[1]).namelist())))",
        await file.path()]).toString());
      assertEq(names.join(","), "Folder paper one.pdf,Sub/Folder paper two.pdf");
      // the heading turns as the job ends; what was exported and left out comes with a read of the ended job after it
      await until(() => again.innerText().then((text) => text.includes("2 pages exported.") && text.includes("1 page left out") && text.includes("Folder note page")),
        { what: "the finished step listing the page without a PDF" });
      if (flags.keep) await page.screenshot({ path: `${server.dir}/export-ready.png` });
      await choice(again, "Done").click();
      // The tray keeps the finished export with its file, and removes it on request.
      await page.click("button[aria-label='Background tasks']");
      await row.getByRole("button", { name: `Download ${title}`, exact: true }).waitFor();
      await row.getByRole("button", { name: `Remove ${title}`, exact: true }).click();
      await row.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
  await step("transfer: BibTeX previews the real bibliography, copies it, and resolves clashing keys", async () => {
    const folder = "E2E bibtex";
    // Two papers by the same author in the same year: their generated keys
    // collide, which is the case the bibliography has to resolve.
    const pages = {};
    for (const [title, where] of [["Bib paper one", folder], ["Bib paper two", `${folder}/Sub`],
                                  ["Bib paper three", folder]]) {
      const made = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } });
      const meta = { title, authors: ["Ada Lovelace"], year: "1843", venue: "Notes", source: "crossref" };
      // meta + bibtex together, the way a metadata lookup leaves a page.
      const entry = `@article{lovelace1843,
  title = {${title}},
  author = {Ada Lovelace},
  journal = {Notes},
  year = {1843}
}`;
      await alice.api(`/api/blocks/${made.id}`, { method: "PUT", body: { properties: { meta, bibtex: entry } } });
      await alice.file(made.id, { folders: [where] });
      pages[title] = made;
    }
    // A page with nothing to cite: the export leaves it out and says so.
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Bib note page" } })
      .then((made) => alice.file(made.id, { folders: [folder] }));
    const folderId = await alice.folder(folder);
    const ctx = await alice.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    try {
      const page = await openPage(ctx, server.base);

      // 1. Pin a citation key on one paper, in the metadata popover.
      await page.goto(`${server.base}/?page=${pages["Bib paper one"].id}&ws=${alice.ws}`);
      await page.click("button[aria-label='Paper metadata']");
      const key = page.getByRole("textbox", { name: "Citation key", exact: true });
      await key.waitFor();
      assertEq(await key.getAttribute("placeholder"), "lovelace1843", "the row shows the generated key it would use");
      assertEq(await key.inputValue(), "", "nothing is pinned yet");
      await key.fill("lovelace:analytical");
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/meta-cite-key.png` });
      await page.getByRole("button", { name: "Save metadata", exact: true }).click();
      await until(async () => (await alice.api(`/api/blocks/${pages["Bib paper one"].id}`)).properties.cite_key === "lovelace:analytical",
        "the pinned key is saved on the page");

      // 2. One page's own bibliography: the same two panes, one paper, and
      //    no Copy all to copy (its entry's own button is the way).
      const one = await openDialog(page, "Export");
      await choice(one, "BibTeX").click();
      await choice(one, "Next").click();
      await one.getByRole("region", { name: "Papers", exact: true }).waitFor();
      assertEq(await one.locator(".bibPaperRow").count(), 1);
      assert((await one.innerText()).includes("1 paper can be cited."));
      assert((await one.locator(".bibtexPreview").innerText()).includes("@article{lovelace:analytical,"));
      assertEq(await choice(one, "Copy all").count(), 0, "one entry needs no Copy all");
      await choice(one, "Close Export").click();
      await one.waitFor({ state: "detached" });

      // 3. Export the folder as one bibliography. The folder is shared, so
      //    the review can show the link that keeps serving its .bib.
      const { token: bibShare } = await alice.api(`/api/share/folder/${folderId}`, { method: "POST" });
      await page.goto(`${server.base}/?folder=${folderId}&ws=${alice.ws}`);
      await page.waitForSelector(".folderNewBtn");
      // A folder's dialog is named after it, so openDialog's exact "Export" misses.
      const view = page.locator('[data-popover="menu"] > button');
      await (await view.count() ? view : page.locator('[data-guide="header.account"]')).click();
      await page.getByRole("button", { name: "Export…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: `Export “${folder}”`, exact: true });
      await dialog.getByRole("group", { name: "Papers choices", exact: true }).waitFor();
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/export-bibtex-cards.png` });
      await choice(dialog, "BibTeX").click();
      await choice(dialog, "Next").click();

      // 4. The two panes: every citable paper on the left, the picked one's
      //    entry on the right, and the page it could not cite listed too.
      const papers = dialog.getByRole("region", { name: "Papers", exact: true });
      await papers.waitFor();
      const rows = papers.locator(".bibPaperRow");
      await rows.first().waitFor();
      assertEq((await rows.allInnerTexts()).map((row) => row.split(LF).join(" / ")).sort().join(" | "),
        [
          "Bib note page / page has no paper metadata",
          "Bib paper one / lovelace:analytical",
          "Bib paper three / lovelace1843",
          "Bib paper two / lovelace1843a",
        ].join(" | "),
        "each paper shows the key the file will cite it by; the uncitable page says why");
      assert((await dialog.innerText()).includes("3 papers can be cited."));
      // The first entry is shown without picking anything.
      const entry = dialog.getByRole("region", { name: "BibTeX entry", exact: true });
      assert((await entry.innerText()).includes("@article{lovelace1843,"), "the first paper's entry is shown");
      if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/export-bibtex.png` });

      // 5. Picking another paper shows its entry; Copy takes just that one.
      await papers.getByRole("button", { name: /Bib paper one/ }).click();
      const picked = await entry.locator(".bibtexPreview").innerText();
      assert(picked.includes("@article{lovelace:analytical,"), `the pinned key is used verbatim: ${picked}`);
      assert(picked.includes("Bib paper one") && !picked.includes("Bib paper two"), "one entry at a time");
      await entry.getByRole("button", { name: "Copy this entry", exact: true }).click();
      const plain = (text) => text.split(CRLF).join(LF).trim();
      assertEq(plain(await page.evaluate(() => navigator.clipboard.readText())), plain(picked),
        "the clipboard holds that entry");
      // Copy all takes the whole bibliography, suffixed keys and all.
      await choice(dialog, "Copy all").click();
      const everything = plain(await page.evaluate(() => navigator.clipboard.readText()));
      for (const key of ["lovelace1843,", "lovelace1843a,", "lovelace:analytical,"]) {
        assert(everything.includes(`@article{${key}`), `${key} is in the bibliography: ${everything}`);
      }
      assert(!everything.includes("Bib note page"), "a page with no metadata is not an entry");

      // 5b. The shared folder's bibliography link, under "Keep this .bib up
      //     to date": the export URL with the share token, in a CopyField
      //     whose button copies it.
      await dialog.getByText("Keep this .bib up to date", { exact: true }).click();
      const link = dialog.getByRole("textbox", { name: "Bibliography link", exact: true });
      await link.waitFor();
      const bibUrl = new URL(await link.inputValue());
      assertEq(bibUrl.pathname, `/api/folders/${folderId}/export`, "the folder's export endpoint");
      assertEq(bibUrl.searchParams.get("mode"), "bibtex", "in bibtex mode");
      assertEq(bibUrl.searchParams.get("share"), bibShare, "through the folder's share token");
      assert((await dialog.innerText()).includes("Anyone with the link can read the folder"), "the folder wording");
      if (flags.keep) await dialog.screenshot({ animations: "disabled", path: `${server.dir}/export-bibtex-link.png` });
      await dialog.getByRole("button", { name: "Copy the bibliography link", exact: true }).click();
      assertEq(await page.evaluate(() => navigator.clipboard.readText()), bibUrl.href, "the clipboard holds the link");
      await dialog.locator(".integrationCopy.on").waitFor();

      // 6. And the download is the file itself.
      const request = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/jobs/export");
      const download = page.waitForEvent("download");
      await choice(dialog, "Export").click();
      assertEq((await request).postDataJSON().mode, "bibtex");
      const file = await download;
      assertEq(file.suggestedFilename(), `${folder}.bib`);
      const written = fs.readFileSync(await file.path(), "utf-8");
      assert(written.startsWith("% 3 entries from E2E bibtex, exported from Gamma"), `the .bib names what it holds: ${written.slice(0, 80)}`);

      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      // The ended job's result (what was exported, what was left out) is
      // fetched once the heading says it is ready, so it shows a moment later.
      await dialog.getByText("3 pages exported.", { exact: true }).waitFor();
      const done = await dialog.innerText();
      assert(done.includes("1 page left out") && done.includes("Bib note page"),
        `the finished step lists the page it could not cite: ${done}`);
      assert(done.includes("Put it beside your .tex file"), "and says what to do with the file");
      assertEq(plain(written), everything, "the file is what Copy all copied");

      // 7. The finished step's breadcrumb walks back to the review, and on
      //    to the format cards — a second thought needs no reopening.
      await choice(dialog, "2. Review").click();
      await dialog.getByRole("heading", { name: "BibTeX", exact: true }).waitFor();
      await choice(dialog, "1. Choose a format").click();
      await dialog.getByRole("group", { name: "Papers choices", exact: true }).waitFor();
      // The close button is named after the dialog, and a folder's title is
      // the folder — Escape is the same exit and shorter to say.
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });

      // 8. A page with no paper record has no bibliography to offer.
      const bare = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Bib bare page" } });
      await page.goto(`${server.base}/?page=${bare.id}&ws=${alice.ws}`);
      // openDialog decides between the View menu and the phone sheet by
      // counting, so wait for the menu this viewport has.
      await page.waitForSelector('[data-popover="menu"] > button');
      const other = await openDialog(page, "Export");
      assertEq(await choice(other, "BibTeX").count(), 0, "no record, no BibTeX card");
      await choice(other, "Close Export").click();
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/share-settings/folder/${folderId}`, { method: "DELETE" }).catch(() => {});
    }
  });

}

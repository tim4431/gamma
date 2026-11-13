// A block's Properties (editor/BlockProperties.jsx, the rows from
// editor/blockProps.js): the read-only popover that says what a block is and
// every key it stores. From the ⋮⋮ handle menu it opens in place under the
// row, named as a dialog: a link region's colour, place (with its mini-map)
// and link; each row copies its stored value, and its menu its key path and
// SQL path; the JSON view highlights the raw object, copies the whole block
// as the API has it, keeps the surface's height and top (the row stays in
// sight) and is remembered; a change made through the API shows live,
// saved; a note's unlisted key shows under Other keys, its Page row steps
// into the page's properties (its filing) and Back returns; a link row
// opens its page, which closes the popover; Escape and a press outside
// close it. The other ways in: the highlight's menu on the PDF, the tab's
// menu, the palette, a library card's menu (the server's whole record, not
// the listing's summary, so nothing reads as unsaved; Enter and Delete in
// the popover leave the library's selection alone) and a folder's. On a
// phone it fits the screen, its copy buttons show at rest and a held finger
// opens a row's menu; in Chinese the handle menu's row reads 属性. With
// --keep it leaves screenshots (block-properties-*.png) in the run's temp
// dir. The rules behind it are frontend/tests/blockProps.test.mjs.
import { Account, arrived, assertMenuRoles, assertOpensInPlace, live, wanted } from "../harness.mjs";
import { PNG_1PX } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

export async function blockPropertiesScenarios({ server, browser, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }) {
  if (!wanted("props")) return;
  await server.manage("create-user", "props-user", "props-pw");
  const user = await new Account(server, "props-user", "props-pw").login();

  // Page A: a PDF filed in Lab / Clocks and labelled to-read, with paper
  // metadata; page B, which A's link region links to. On A: the link region
  // (three lines on page 1) with a note under it that carries an unlisted
  // key, a text box and a picture.
  const up = await user.upload("/api/uploads", makePdf([["Properties paper line one", "line two", "line three"]]), "props.pdf", "application/pdf");
  const A = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Paper A", source_url: up.source_url, original_filename: "props.pdf" } });
  await user.api(`/api/blocks/${A.id}`, { method: "PUT", body: { content: "Paper A" } }); // its title, not the file's name
  const B = await user.api("/api/pages", { method: "POST", body: { title: "Page B" } });
  await user.file(A.id, { folders: ["Lab/Clocks"], labels: ["to-read"] });
  await user.api("/api/metadata/update", { method: "POST", body: { block_id: A.id,
    meta: { title: "Ramsey fringes in optical clocks", authors: ["Ada Lovelace", "Niels Bohr"], year: "2024", venue: "Phys. Rev. A" } } });
  const rects = [0, 1, 2].map((i) => ({ x1: 72, y1: 60 + i * 26, x2: 400, y2: 80 + i * 26 }));
  const position = { pageNumber: 1, width: 612, height: 792, boundingRect: { x1: 72, y1: 60, x2: 400, y2: 132 }, rects };
  const hl = await user.api("/api/blocks", { method: "POST", body: { parent_id: A.id, content: "a highlight note",
    properties: { quote: "Properties paper line one", color: "#ffe5648c", pdf_position: position, link_page_id: B.id } } });
  const note = await user.api("/api/blocks", { method: "POST", body: { parent_id: hl.id, content: "a child note",
    properties: { t0: Date.now() - 2 * 3600e3, x_custom: { a: 1 } } } });
  const { url: pictureUrl } = await user.upload("/api/upload-image", PNG_1PX, "dot.png", "image/png");
  await user.api(`/api/pages/${A.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "propsBox", parent: A.id, content: "Hello box", props: { text_box: { x: 40, y: 400, size: 14 }, pdf_page: 1 } },
    { op: "insert", id: "propsPic", parent: A.id, content: "", props: { picture: { url: pictureUrl, x: 320, y: 300, w: 120, h: 80 }, pdf_page: 1 } },
  ] } });
  const pageUrl = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
  const CLIP = { permissions: ["clipboard-read", "clipboard-write"] };

  const pop = (page) => page.locator(live(".propsPopover"));
  const propRow = (page, key) => pop(page).locator(`.propsRow[data-key="${key}"]`).first();
  const valueOf = (page, key) => propRow(page, key).locator(".metaVal").innerText();
  const title = (page) => pop(page).locator(".propsTitle").innerText();
  const wrapOf = (page, id) => page.locator(`.sortableBlockWrap[data-block-id="${id}"]`).first();
  const clipboard = (page) => page.evaluate(() => navigator.clipboard.readText());
  const closed = (page, what = "the popover closes") => until(async () => (await pop(page).count()) === 0, { what });
  const shot = async (page, name) => {
    if (flags.keep) await page.screenshot({ path: `${server.dir}/block-properties-${name}.png`, animations: "disabled" });
  };
  // The ⋮⋮ handle's menu on block `id`'s row; its items.
  const handleMenu = async (page, id, { finger = false } = {}) => {
    const wrap = wrapOf(page, id);
    if (finger) await wrap.locator(".dragHandle").first().tap();
    else {
      await wrap.hover();
      await wrap.locator(".dragHandle").first().click();
    }
    const menu = page.locator(live(".ctxMenu")).first();
    await menu.waitFor();
    return assertMenuRoles(menu, "the handle menu");
  };
  // Properties from block `id`'s handle menu, the popover arrived.
  const openFromHandle = async (page, id) => {
    await handleMenu(page, id);
    await page.getByRole("menuitem", { name: "Properties", exact: true }).click();
    await pop(page).waitFor();
    await arrived(pop(page));
  };
  // The popover's box once it has stopped changing (a view's body grown, the
  // surface placed again).
  const settled = async (page) => {
    let last = null;
    return until(async () => {
      const box = await pop(page).boundingBox();
      const same = last && ["x", "y", "width", "height"].every((k) => Math.abs(box[k] - last[k]) < 0.5);
      last = box;
      return same ? box : null;
    }, { every: 150, what: "the popover settled" });
  };
  // Back to Details when an earlier step left the JSON view chosen.
  const toDetails = async (page) => {
    if ((await pop(page).getAttribute("data-view")) !== "json") return;
    await pop(page).locator(".propsBar .segGroup").getByRole("button", { name: "Details", exact: true }).click();
    await pop(page).locator(".propsSection").first().waitFor();
  };
  // Waits for the server's dict: the Block section's times are read.
  const loaded = (page) => until(async () => /\d/.test(await valueOf(page, "created_at").catch(() => "")), { what: "the server's fields" });
  const onPaperA = async (page) => {
    await page.goto(pageUrl(A.id));
    await waitForPdf(page, 1);
    await wrapOf(page, note.id).waitFor();
  };

  let ctx, page;
  try {
    ctx = await user.context(browser, CLIP);
    page = await openPage(ctx);

    await step("props: the handle menu's Properties opens in place under the row, a dialog naming the link region's colour, place and mini-map", async () => {
      await onPaperA(page);
      const items = (await handleMenu(page, hl.id)).map((item) => item.text);
      const at = items.indexOf("Properties");
      assert(at > 0 && items[at - 1] === "Copy as markdown", `Properties right after Copy as markdown: ${items.join(" | ")}`);
      await assertOpensInPlace(page, ".propsPopover", () => page.getByRole("menuitem", { name: "Properties", exact: true }).click(), { what: "the properties popover" });
      await arrived(pop(page));
      assertEq(await page.getByRole("dialog", { name: "Block properties", exact: true }).count(), 1, "a dialog named Block properties");
      assertEq(await title(page), "Link region", "the kind");
      assert((await valueOf(page, "color")).includes("Yellow"), `the colour: ${await valueOf(page, "color")}`);
      assert((await valueOf(page, "pdf_position")).includes("Page 1 · 3 lines"), `the place: ${await valueOf(page, "pdf_position")}`);
      assertEq(await pop(page).locator("svg.propsMiniMap .propsMiniRect").count(), 3, "the mini-map's line boxes");
      const box = await pop(page).boundingBox();
      const handle = await wrapOf(page, hl.id).locator(".dragHandle").first().boundingBox();
      const rowBox = await wrapOf(page, hl.id).locator(".blockRow").first().boundingBox();
      assert(box.y >= handle.y + handle.height, `the popover hangs under the handle: popover top ${box.y}, handle bottom ${handle.y + handle.height}`);
      assert(box.y >= rowBox.y + rowBox.height - 1, `the row stays in sight: popover top ${box.y}, row bottom ${rowBox.y + rowBox.height}`);
      // by their left edges, or moved left as far as the window's edge asks
      const right = await page.evaluate(() => window.innerWidth - 8);
      assert(Math.abs(box.x - handle.x) <= 1 || (box.x < handle.x && Math.abs(box.x + box.width - right) <= 1),
        `aligned by left edges, or kept inside the window: popover ${box.x}..${box.x + box.width}, handle ${handle.x}`);
      await loaded(page);
      await shot(page, "light");
      assertNoProblems(page);
    });

    await step("props: a row's copy button copies its stored value; its menu copies the key path and the SQL path", async () => {
      await propRow(page, "id").hover();
      await propRow(page, "id").locator(".propsCopy").click();
      await until(async () => (await clipboard(page)) === hl.id, { what: "the id copied" });
      await propRow(page, "color").hover();
      await propRow(page, "color").locator(".propsCopy").click();
      await until(async () => (await clipboard(page)) === "#ffe5648c", { what: "the stored colour copied" });
      assertEq(await propRow(page, "color").locator(".propsCopy.on").count(), 1, "the button shows it copied, in place");
      const viaMenu = async (item) => {
        await propRow(page, "pdf_position").locator(".metaKey").click({ button: "right" });
        const menu = page.locator(live(".ctxMenu.propsRowMenu"));
        await menu.waitFor();
        assertEq(await menu.locator(".propsMenuKey").innerText(), "properties.pdf_position", "the menu names the key path");
        await menu.getByRole("menuitem", { name: item, exact: true }).click();
        await menu.waitFor({ state: "detached" });
      };
      await viaMenu("Copy key path");
      await until(async () => (await clipboard(page)) === "properties.pdf_position", { what: "the key path copied" });
      await viaMenu("Copy SQL path");
      await until(async () => (await clipboard(page)) === "json_extract(properties, '$.pdf_position')", { what: "the SQL path copied" });
      // the row's "⋯" opens the same menu, named after its row
      await propRow(page, "color").hover();
      await propRow(page, "color").getByRole("button", { name: "Actions: Colour", exact: true }).click();
      const byButton = page.locator(live(".ctxMenu.propsRowMenu"));
      await byButton.waitFor();
      assertEq(await byButton.locator(".propsMenuKey").innerText(), "properties.color", "the ⋯ opens the row's menu");
      await page.keyboard.press("Escape");
      await byButton.waitFor({ state: "detached" });
      assertEq(await pop(page).count(), 1, "the row menu leaves the popover open");
      assertNoProblems(page);
    });

    await step("props: JSON highlights the raw object, copies the whole block as the API has it, keeps the height and is remembered", async () => {
      const bar = pop(page).locator(".propsBar .segGroup");
      const before = await settled(page);
      await bar.getByRole("button", { name: "JSON", exact: true }).click();
      await pop(page).locator("pre.propsJson .hljs-attr").first().waitFor();
      const attrs = await pop(page).locator("pre.propsJson .hljs-attr").allInnerTexts();
      assert(attrs.includes('"pdf_position"'), `the keys are highlighted: ${attrs.join(", ")}`);
      await pop(page).locator(".propsJsonView .segGroup").getByRole("button", { name: "Whole block", exact: true }).click();
      const json = await settled(page);
      await pop(page).locator(".propsCopyJson").click();
      let last = "";
      const copied = JSON.parse(await until(async () => {
        last = await clipboard(page);
        return /^\{\s+"id"/.test(last) ? last : null; // (Windows' clipboard reads back CRLF)
      }, { what: "the whole block copied" }).catch((e) => { throw new Error(`${e.message}; the clipboard: ${last.slice(0, 200)}`); }));
      const api = await user.api(`/api/blocks/${hl.id}`);
      assertEq(Object.keys(copied).join(","), "id,kind,parent_id,page_id,position,created_at,updated_at,content,properties", "the block dict's fields, in order");
      for (const key of Object.keys(copied)) assertEq(JSON.stringify(copied[key]), JSON.stringify(api[key]), `the copy's ${key} is the API's`);
      await bar.getByRole("button", { name: "Details", exact: true }).click();
      await propRow(page, "color").waitFor();
      const after = await settled(page);
      // the body only grows: JSON is at least as tall, and Details back keeps the box JSON had
      assert(json.height >= before.height - 1, `JSON does not shrink the surface: ${json.height} < ${before.height}`);
      assert(Math.abs(json.y - before.y) <= 1, `JSON grows downward, the row left in sight: top ${json.y} vs ${before.y}`);
      for (const k of ["x", "y", "width", "height"]) assert(Math.abs(after[k] - json[k]) <= 1, `Details back keeps the box: ${k} ${after[k]} vs ${json[k]}`);
      // the view is remembered by the next opening
      await bar.getByRole("button", { name: "JSON", exact: true }).click();
      await page.keyboard.press("Escape");
      await closed(page);
      await openFromHandle(page, hl.id);
      assertEq(await pop(page).getAttribute("data-view"), "json", "it reopens in JSON");
      await pop(page).locator("pre.propsJson").waitFor();
      await shot(page, "json");
      await pop(page).locator(".propsBar .segGroup").getByRole("button", { name: "Details", exact: true }).click();
      await propRow(page, "color").waitFor();
      assertNoProblems(page);
    });

    await step("props: a change made through the API shows live, and Last edited reads saved", async () => {
      await toDetails(page);
      await user.api(`/api/blocks/${hl.id}`, { method: "PUT", body: { properties: { color: "#9be38a8c" } } });
      await until(async () => (await valueOf(page, "color")).includes("Green"), { what: "the colour row turned green" });
      await until(async () => / saved$/.test((await valueOf(page, "updated_at")).trim()), { timeout: 10000, what: "Last edited reads saved" });
      assertEq(await pop(page).locator(".propsStatus [data-chip]").count(), 0, "no sync chip once saved");
      assertNoProblems(page);
    });

    await step("props: a note's unlisted key shows under Other keys; its Page row steps into the page's filing and Back returns; a link row opens its page", async () => {
      await page.keyboard.press("Escape");
      await closed(page);
      await openFromHandle(page, note.id);
      await toDetails(page);
      assertEq(await title(page), "Note", "the note's kind");
      const other = pop(page).locator('.propsSection[data-section="other"]');
      assertEq(await other.locator(".propsDisclose").getAttribute("aria-expanded"), "true", "Other keys starts open");
      const custom = other.locator('.propsRow[data-key="x_custom"]');
      assert((await custom.innerText()).includes("Not in the catalog"), `x_custom is tagged: ${await custom.innerText()}`);
      assert(/ago/.test(await valueOf(page, "t0")) && !/Invalid/.test(await valueOf(page, "t0")), `Written: ${await valueOf(page, "t0")}`);
      await loaded(page);
      await propRow(page, "page_id").hover();
      await propRow(page, "page_id").locator(".propsStep").click();
      await until(async () => (await title(page)) === "Page", { what: "the page's properties" });
      const filing = pop(page).locator('.propsSection[data-section="filing"]');
      await filing.waitFor();
      const filed = await filing.innerText();
      assert(filed.includes("Clocks") && filed.includes("to-read"), `the page's folder and label: ${filed}`);
      assert(await pop(page).locator('.propsSection[data-section="paper"]').innerText().then((s) => s.includes("Ramsey fringes")), "the paper record");
      await pop(page).locator('.propsSection[data-section="document"]').waitFor();
      await shot(page, "page");
      await pop(page).getByRole("button", { name: "Back", exact: true }).click();
      await until(async () => (await title(page)) === "Note", { what: "Back to the note" });
      assertEq(await pop(page).locator(".propsBack").count(), 0, "no Back at the first block");
      // the link region's "Links to page" opens B; another page closes the popover
      await page.keyboard.press("Escape");
      await closed(page);
      await openFromHandle(page, hl.id);
      await propRow(page, "link_page_id").hover();
      await propRow(page, "link_page_id").getByRole("button", { name: "Open page: Links to page", exact: true }).click();
      await until(() => new URL(page.url()).searchParams.get("block") === B.id || new URL(page.url()).searchParams.get("page") === B.id, { what: "page B opened" });
      await closed(page, "the popover closes with its page");
      assertNoProblems(page);
    });

    await step("props: Escape closes it, and so does a press outside", async () => {
      await onPaperA(page);
      await openFromHandle(page, note.id);
      assert(await pop(page).evaluate((el) => el.contains(document.activeElement)), "the focus is in the dialog");
      await page.keyboard.press("Escape");
      await closed(page);
      await openFromHandle(page, note.id);
      const pdf = await page.locator('[data-page="1"]').boundingBox();
      await page.mouse.click(pdf.x + pdf.width / 2, pdf.y + pdf.height - 40);
      await closed(page, "a press outside closes it");
      assertNoProblems(page);
    });

    await step("props: the highlight's menu on the PDF, the tab's menu, the palette, a library card's menu and a folder's open it too", async () => {
      // the highlight on the PDF: its menu's plain Properties row
      await page.locator(`[data-page="1"] [data-hl-id="${hl.id}"]`).first().click({ button: "right" });
      const hlMenu = page.locator(live(".ctxMenu")).first();
      await hlMenu.waitFor();
      const rows = (await hlMenu.locator(":scope > .ctxMenuItem").allInnerTexts()).map((s) => s.trim());
      const at = rows.indexOf("Properties");
      assert(at > 0 && rows[at - 1] === "Comment" && rows[at + 1] === "Delete", `Properties between Comment and Delete: ${rows.join(" | ")}`);
      await hlMenu.locator(":scope > .ctxMenuItem", { hasText: /^Properties$/ }).click();
      await pop(page).waitFor();
      assertEq(await title(page), "Link region", "the highlight's properties");
      await page.keyboard.press("Escape");
      await closed(page);
      // the tab
      await page.locator(".tabStrip .tab", { hasText: "Paper A" }).first().click({ button: "right" });
      await page.getByRole("menuitem", { name: "Page properties", exact: true }).click();
      await pop(page).waitFor();
      await until(async () => (await title(page)) === "Page", { what: "the tab's page properties" });
      await page.keyboard.press("Escape");
      await closed(page);
      // the palette, on the row last edited
      await wrapOf(page, note.id).locator(".blockBody").first().click();
      await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
      await page.keyboard.press("Control+Shift+P");
      const palette = page.getByRole("textbox", { name: "Type a command" });
      await palette.waitFor();
      await palette.fill(">Block properties");
      await until(async () => /Block properties/.test(await page.locator('[role="option"][aria-selected="true"]').textContent().catch(() => "")), { what: "the command selected" });
      await page.keyboard.press("Enter");
      await pop(page).waitFor();
      assertEq(await title(page), "Note", "the focused row's properties");
      await until(() => pop(page).evaluate((el) => el.contains(document.activeElement)), { what: "the focus in the dialog" });
      await page.keyboard.press("Escape");
      await closed(page);
      // a library card
      await page.locator(".homeBtn").click();
      const card = page.locator(".pageCardTitle", { hasText: "Paper A" }).first();
      await card.waitFor({ timeout: 15000 });
      await card.click({ button: "right" });
      await page.getByRole("menuitem", { name: "Properties", exact: true }).click();
      await pop(page).waitFor();
      await until(async () => (await pop(page).locator('.propsRow[data-key="doc.name"]').count()) === 1, { what: "the Document section" });
      assert((await valueOf(page, "doc.name")).includes("props.pdf"), "the file name");
      // the server's whole record, not the listing's summary of it: nothing reads as unsaved
      assertEq(await valueOf(page, "kind"), "Page", "the kind");
      assertEq(await pop(page).locator(".propsStatus [data-chip]").count(), 0, "no sync chip on a library page");
      assertEq(await pop(page).locator('.propsRow[data-key="bibtex"]').count(), 1, "a key the listing leaves out");
      await shot(page, "library");
      // the right-click selected the card: Enter and Delete in the popover stay there
      await pop(page).focus();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Delete");
      await sleep(400); // what must not happen: a page opening, a confirmation
      assertEq(new URL(page.url()).searchParams.get("page"), null, "Enter opened no page");
      assertEq(await page.locator(live('[role="dialog"], [role="alertdialog"]')).count(), 1, "Delete asked nothing: the popover is the one dialog");
      assertEq(await pop(page).count(), 1, "the popover stays open");
      await page.keyboard.press("Escape");
      await closed(page);
      // a folder's menu
      await page.locator(".fileList .folderRow", { hasText: "Lab" }).first().click({ button: "right" });
      await page.getByRole("menuitem", { name: "Properties", exact: true }).click();
      await pop(page).waitFor();
      await until(async () => (await title(page)) === "Folder", { what: "the folder's properties" });
      await page.keyboard.press("Escape");
      await closed(page);
      assertNoProblems(page);
    });

    await step("props: a text box and a picture show their own sections; dark theme screenshots", async () => {
      await onPaperA(page);
      await openFromHandle(page, "propsBox");
      await toDetails(page);
      assertEq(await title(page), "Text box", "the text box's kind");
      await pop(page).locator('.propsSection[data-section="text_box"]').waitFor();
      await shot(page, "textbox");
      await page.keyboard.press("Escape");
      await closed(page);
      await openFromHandle(page, "propsPic");
      assertEq(await title(page), "Picture", "the picture's kind");
      await pop(page).locator('.propsSection[data-section="picture"] .propsThumb').waitFor();
      await shot(page, "picture");
      await page.keyboard.press("Escape");
      await closed(page);
      assertNoProblems(page);
      if (!flags.keep) return;
      const dark = await user.context(browser, { ...CLIP, colorScheme: "dark" });
      try {
        const p = await openPage(dark);
        await onPaperA(p);
        await openFromHandle(p, hl.id);
        await loaded(p);
        await shot(p, "dark");
        assertNoProblems(p);
      } finally { await dark.close(); }
    });
  } finally {
    await ctx?.close();
  }

  // (the finger is Chromium's, through CDP)
  if (browser.browserType().name() === "chromium") await step("props: on a phone it fits the screen, its copy buttons show at rest, and a held finger opens a row's menu", async () => {
    const phone = await user.context(browser, { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    try {
      const p = await openPage(phone, pageUrl(A.id));
      await p.waitForSelector(".app.phoneUI .phoneBottomBar");
      await p.getByRole("button", { name: "Notes", exact: true }).tap();
      await wrapOf(p, note.id).waitFor();
      await handleMenu(p, note.id, { finger: true });
      await p.getByRole("menuitem", { name: "Properties", exact: true }).tap();
      await pop(p).waitFor();
      await arrived(pop(p));
      const box = await pop(p).boundingBox();
      assert(box.x >= 8 - 0.5 && box.x + box.width <= 382 + 0.5, `within the screen's gutters: ${box.x}..${box.x + box.width}`);
      assert(await p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "no sideways scroll");
      assertEq(await propRow(p, "t0").locator(".propsRowBtns").evaluate((el) => getComputedStyle(el).opacity), "1", "the copy buttons show at rest");
      await shot(p, "phone");
      const key = await propRow(p, "t0").locator(".metaKey").boundingBox();
      const cdp = await phone.newCDPSession(p);
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: key.x + 10, y: key.y + key.height / 2 }] });
      await sleep(650); // the gesture IS the wait: a finger held still
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      const menu = p.locator(live(".ctxMenu.propsRowMenu"));
      await menu.waitFor();
      assertEq(await menu.locator(".propsMenuKey").innerText(), "properties.t0", "the menu names the key path");
      assertEq(await pop(p).count(), 1, "the popover stays open under its row menu");
      assertNoProblems(p);
    } finally { await phone.close(); }
  });

  await step("props: in Chinese the handle menu's row reads 属性", async () => {
    const zh = await user.context(browser, { locale: "zh-CN" });
    try {
      const p = await openPage(zh, pageUrl(A.id));
      await waitForPdf(p, 1);
      await wrapOf(p, note.id).waitFor();
      const items = (await handleMenu(p, note.id)).map((item) => item.text);
      assert(items.includes("属性"), `the menu's rows: ${items.join(" | ")}`);
      await p.getByRole("menuitem", { name: "属性", exact: true }).click();
      await pop(p).waitFor();
      assertEq(await pop(p).getAttribute("aria-label"), "块属性", "the dialog's name");
      assertNoProblems(p);
    } finally { await zh.close(); }
  });
}

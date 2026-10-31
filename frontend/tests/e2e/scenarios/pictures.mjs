// Pictures on the page (docs/dev/pictures.md): a photo or an image placed on
// a PDF page or a sheet of paper as an object. The strip's Picture button
// opens the file picker and the picked PNG lands on the page in view at its
// natural aspect, centred and selected: a block with `picture` (plus
// `pdf_page` on a PDF page, under its sheet on a sheet) whose content is the
// caption. Then the picture as an object: a drag moves it, the handles
// resize it (Alt stretches) and turn it (snapping to a right angle), the
// menu flips it, a crop keeps part of it where it was (Escape cancels one),
// and Ctrl+Z / Ctrl+Y undo and redo each; Copy image puts it on the
// clipboard as it shows, Reset brings back the whole image upright after a
// crop, a flip and a turn, Fit to page fills the page's width and a turn
// then keeps the turned frame on the page; a reload keeps it; a paste of an
// image and a drop of one place more; Duplicate puts
// a copy 12 px aside, and Bring to front / Send to back reorder two
// overlapping pictures on the page and in the notes; the arrows nudge a
// selected picture and Delete deletes it, but not while its caption is typed
// in its row; a stylus moves a selected picture and turns it; Escape during
// a handle drag takes back the drag alone, and a press elsewhere mid-drag
// leaves nothing of it; a text box over the handles takes none of their
// presses; an upload that lands late leaves the text box opened meanwhile
// open; the notes show it as a card and a marker that show it on its page;
// a finger drags only a selected picture and scrolls over another, and two
// fingers pinch over one without moving it; a read-only visitor sees it
// through the share token and a click shows its note, and an edit share's
// visitor drops one on the page and sees it at once; a highlighter across a
// picture multiplies into it on a light page, a flipped one and in Gamma
// Dark, and where the picture is transparent does as on the page around it.
// The same on a sheet in the notebook view, where on dark paper a
// highlighter over it multiplies too, and a drop on the sheet drawn among
// the notes outlines no row. Last,
// two people on one picture: a move made while the other crops it and types
// its caption keeps all three, a crop that moved the frame keeps the part
// kept where it is on the picture moved meanwhile
// (tests/shared/picturemerge.json), and the mover's undo keeps the other's
// crop. The rules behind it are
// frontend/tests/picture.test.mjs, pictureEdits.test.mjs, blockHistory.test.mjs,
// collabSession.test.mjs and backend/tests/test_picture.py,
// test_picture_merge.py.
import { crc32 } from "../../../src/shared/lib/crc32.js";
import { normalizePaper } from "../../../src/notebook/notebook.js";
import { cropTo, mergePicture, normalizePicture, turnedBox } from "../../../src/markup/picture.js";
import zlib from "node:zlib";
import { closeEditor } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";
import { stylus } from "./textBoxes.mjs";

// A PNG of `width` × `height` pixels, each row's pixel x `pixel(x)`: [r,
// g, b], or [r, g, b, a] for a PNG with transparency.
function encodePng(width, height, pixel) {
  const channels = pixel(0).length;
  const row = width * channels + 1;
  const raw = Buffer.alloc(row * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) raw.set(pixel(x), y * row + 1 + x * channels);
  }
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, "latin1"), data]);
    const out = Buffer.alloc(body.length + 8);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), body.length + 4);
    return out;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(width, 0);
  head.writeUInt32BE(height, 4);
  head[8] = 8; // bit depth
  head[9] = channels === 4 ? 6 : 2; // RGBA or RGB
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", head),
    chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

// A PNG of `width` × `height` pixels, its left half red and its right half
// blue (so a flip shows), RGB without transparency.
const png = (width, height) => encodePng(width, height, (x) => (x < width / 2 ? [220, 40, 0] : [0, 40, 220]));

// A file from the bytes in the page: dropped on `selector` at x, y (css px),
// or pasted with nothing focused.
async function dropFile(page, selector, bytes, name, x, y) {
  await page.evaluate(({ selector, b64, name, x, y }) => {
    const dt = new DataTransfer();
    dt.items.add(new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], name, { type: "image/png" }));
    const el = document.querySelector(selector);
    for (const type of ["dragenter", "dragover", "drop"]) {
      el.dispatchEvent(new DragEvent(type, { dataTransfer: dt, clientX: x, clientY: y, bubbles: true, cancelable: true }));
    }
  }, { selector, b64: bytes.toString("base64"), name, x, y });
}
async function pasteFile(page, bytes, name) {
  await page.evaluate(({ b64, name }) => {
    const dt = new DataTransfer();
    dt.items.add(new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], name, { type: "image/png" }));
    document.activeElement?.blur?.();
    document.body.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
  }, { b64: bytes.toString("base64"), name });
}

// What the screen shows in `clip` (css px), from a screenshot: its darkest
// and lightest pixels (by r + g + b), how many pixels are tinted (one
// channel 40 below another) and the median of each channel (ink.mjs reads
// the screen the same way).
async function screenPixels(page, clip) {
  const image = await page.screenshot({ clip });
  return page.evaluate(async (b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = Object.assign(document.createElement("canvas"), { width: bitmap.width, height: bitmap.height });
    const g = canvas.getContext("2d");
    g.drawImage(bitmap, 0, 0);
    const px = g.getImageData(0, 0, bitmap.width, bitmap.height).data;
    let darkest = null, lightest = null, tinted = 0;
    const channels = [[], [], []];
    for (let i = 0; i < px.length; i += 4) {
      const rgb = [px[i], px[i + 1], px[i + 2]], sum = rgb[0] + rgb[1] + rgb[2];
      if (!darkest || sum < darkest.sum) darkest = { sum, rgb };
      if (!lightest || sum > lightest.sum) lightest = { sum, rgb };
      if (Math.max(...rgb) - Math.min(...rgb) >= 40) tinted += 1;
      rgb.forEach((v, c) => channels[c].push(v));
    }
    const median = channels.map((list) => list.sort((a, b) => a - b)[list.length >> 1]);
    return { darkest: darkest.rgb, lightest: lightest.rgb, tinted, median };
  }, image.toString("base64"));
}

// The colour highlighter `stroke` makes of `rgb` under it: multiplied into
// it at its opacity, or with `plain` drawn over it at its opacity.
function highlighted(stroke, rgb, plain = false) {
  const a = stroke.opacity ?? 1, hex = stroke.color.replace("#", "");
  return rgb.map((v, i) => {
    const h = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
    return plain ? v * (1 - a) + h * a : v * (1 - a + (a * h) / 255);
  });
}
const sameColour = (got, want, within = 10) => got.every((v, i) => Math.abs(v - want[i]) <= within);

// The strip's highlighter armed (the strip open); its keys listen from the
// effect after it shows, so a press that comes first is pressed again.
async function armHighlighter(page) {
  const armed = async () => ((await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label").catch(() => "")) || "")
    .includes("highlighter");
  for (let i = 0; i < 5 && !(await armed()); i++) {
    await page.keyboard.press("h");
    await page.waitForTimeout(200);
  }
  return armed();
}

// The clipboard's picture: its pixel size and the colour ("red" or "blue")
// a tenth in from its left and its right edge, halfway down; null when the
// clipboard holds no PNG.
const clipboardPicture = (page) => page.evaluate(async () => {
  const item = (await navigator.clipboard.read()).find((i) => i.types.includes("image/png"));
  if (!item) return null;
  const bitmap = await createImageBitmap(await item.getType("image/png"));
  const canvas = Object.assign(document.createElement("canvas"), { width: bitmap.width, height: bitmap.height });
  const g = canvas.getContext("2d");
  g.drawImage(bitmap, 0, 0);
  const at = (fx) => {
    const [r, , b] = g.getImageData(Math.floor(bitmap.width * fx), Math.floor(bitmap.height / 2), 1, 1).data;
    return r > b ? "red" : "blue";
  };
  return { width: bitmap.width, height: bitmap.height, left: at(0.1), right: at(0.9) };
});

// A point (css px, whole) where picture `id` is what a press there lands
// on: the middle of its frame, else the nearest such point of a grid over
// it (another picture may cover part of it); null when none is.
const spotOn = (page, id) => page.evaluate((id) => {
  const el = document.querySelector(`[data-picture-id="${id}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect(), spots = [];
  for (let i = 1; i < 10; i++) for (let j = 1; j < 10; j++) spots.push([i / 10, j / 10]);
  spots.sort((a, b) => Math.hypot(a[0] - 0.5, a[1] - 0.5) - Math.hypot(b[0] - 0.5, b[1] - 0.5));
  for (const [fx, fy] of spots) {
    const x = Math.round(r.left + r.width * fx), y = Math.round(r.top + r.height * fy);
    if (document.elementFromPoint(x, y)?.closest(".pagePicture") === el) return { x, y };
  }
  return null;
}, id);

export async function pictureScenarios({ server, browser, alice, bob, makePdf, step, until, assert, assertEq, assertNoProblems, openPage, flags }) {
  const account = alice;
  let ctx, page, pageId, firstId;
  const subtree = async (id) => (await account.api(`/api/blocks/${id}/subtree`)).block;
  const picturesIn = (list) => (list || []).flatMap((b) => [...(b.properties?.picture ? [b] : []), ...picturesIn(b.children)]);
  // A picture's block on the server, on the page `root`.
  const stored = async (id, root = pageId) => picturesIn((await subtree(root)).children).find((b) => b.id === id);
  const near = (a, b, what, within = 1) => assert(Math.abs(a - b) < within, `${what}: ${a}, not ${b}`);
  const picEl = (id) => page.locator(`[data-picture-id="${id}"]`);
  const menu = () => page.getByRole("toolbar", { name: "Edit picture", exact: true });
  // The menu's button `name`, the More row opened first when the button is
  // in it and the row is closed (it stays open for the session).
  const menuButton = async (name) => {
    const button = menu().getByRole("button", { name, exact: true });
    if (!(await button.count())) await menu().getByRole("button", { name: "More", exact: true }).click();
    return button;
  };
  const status = async () => (await page.locator(".statusPill").allInnerTexts().catch(() => [])).join(" | ");
  const shot = async (name) => { if (flags.keep) await page.screenshot({ path: `${server.dir}/${name}.png` }); };
  const centre = (r) => ({ x: r.x + r.width / 2, y: r.y + r.height / 2 });
  // A mouse drag from `from` by dx, dy css px.
  const drag = async (from, dx, dy, steps = 10) => {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(from.x + dx, from.y + dy, { steps });
    await page.mouse.up();
  };
  const dragBy = async (locator, dx, dy) => drag(centre(await locator.boundingBox()), dx, dy);
  // The Picture button with a PNG for the file picker.
  const pickPicture = async (bytes, name = "picked.png", scope = ".pdfInkBar") => {
    const chooser = page.waitForEvent("filechooser");
    await page.click(`${scope} button[aria-label='Picture']`);
    await (await chooser).setFiles({ name, mimeType: "image/png", buffer: bytes });
  };
  // The picture `id` until `test(picture)` holds on the server.
  const storedWhen = (id, test, what, root = pageId) => until(async () => {
    const p = (await stored(id, root))?.properties.picture;
    return p && test(p) ? p : null;
  }, { what });
  const pause = () => page.waitForTimeout(1100); // edits a second apart are steps of their own

  await step("picture: the strip's Picture button places a PNG on the PDF page in view, at its aspect, centred, selected", async () => {
    const pdf = makePdf([["A page with a picture", "and some text"]]);
    const up = await account.upload("/api/uploads", pdf, "pictures.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Picture paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser, { permissions: ["clipboard-read", "clipboard-write"] }); // Copy image's clipboard
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await pickPicture(png(200, 100));
    await page.waitForSelector('[data-page="1"] .pagePicture.selected', { timeout: 10000 });
    const [block] = await until(async () => {
      const found = picturesIn((await subtree(pageId)).children);
      return found.length === 1 ? found : null;
    }, { what: "the picture's block on the server" });
    firstId = block.id;
    const pic = block.properties.picture;
    assertEq(block.properties.pdf_page, 1, "on PDF page 1");
    assertEq(block.content, "", "no caption yet");
    assert(/^\/api\/uploads\/[0-9A-Za-z_-]+\.png$/.test(pic.url), `an upload of its own (${pic.url})`);
    assert(Number.isFinite(block.properties.t0), "stamped with the moment it was placed");
    assertEq(JSON.stringify([pic.x, pic.y, pic.w, pic.h, pic.angle]), JSON.stringify([206, 346, 200, 100, 0]),
      "its pixels as points, centred on the Letter page, upright");
    assertEq(JSON.stringify(pic.crop), JSON.stringify({ x: 0, y: 0, w: 1, h: 1 }), "the whole image");
    await menu().waitFor({ timeout: 5000 });
    for (const name of ["Crop", "Flip horizontal", "Flip vertical", "Duplicate", "More", "Delete"]) {
      assert(await menu().getByRole("button", { name, exact: true }).isVisible(), `the menu's ${name}`);
    }
    assert(await page.$(`[data-picture-id="${firstId}"] img[src*="/api/uploads/"]`), "the image drawn on the page");
    assert(await page.$(".pdfInkBar button[aria-label='Undo']") && !(await page.$(".pdfInkBar button[aria-label='Undo ink']")),
      "with a picture selected the strip's Undo is the block history's");
    await shot("picture-placed");
    assertNoProblems(page);
  });

  await step("picture: a drag moves it, the arrows nudge it; Ctrl+Z and Ctrl+Y undo and redo the move", async () => {
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const before = (await stored(firstId)).properties.picture;
    await dragBy(picEl(firstId), 60, 40);
    const moved = await storedWhen(firstId, (p) => p.x !== before.x, "the moved picture on the server");
    near(moved.x, before.x + 60 / k, "moved right as far as the drag");
    near(moved.y, before.y + 40 / k, "and down");
    assert(await page.$(`[data-picture-id="${firstId}"].selected`), "still selected after the drag");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.x === before.x && p.y === before.y, "the move undone");
    assert((await status()).includes("Undone"), `the pill says so (${await status()})`);
    await page.keyboard.press("Control+y");
    await storedWhen(firstId, (p) => p.x === moved.x && p.y === moved.y, "the move redone");
    await pause();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("Shift+ArrowUp");
    const round = (v) => Math.round(v * 100) / 100;
    await storedWhen(firstId, (p) => p.x === round(moved.x - 1) && p.y === round(moved.y - 10), "nudged 1 pt left and 10 pt up");
    assertNoProblems(page);
  });

  await step("picture: the corner handle resizes it about its centre, its aspect kept; with Alt it stretches; undo and redo", async () => {
    await pause();
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const before = (await stored(firstId)).properties.picture;
    const mid = centre(await picEl(firstId).boundingBox());
    const handle = centre(await page.locator(".pictureHandle.inkTransform-resize").boundingBox());
    const to = { x: handle.x + 40, y: handle.y + 20 };
    const f = Math.hypot(to.x - mid.x, to.y - mid.y) / Math.hypot(handle.x - mid.x, handle.y - mid.y);
    await drag(handle, 40, 20);
    const sized = await storedWhen(firstId, (p) => p.w !== before.w, "the resized picture on the server");
    near(sized.w, before.w * f, "as much larger as the pointer went out", 1.5);
    near(sized.w / sized.h, 2, "its aspect kept", 0.02);
    near(sized.x + sized.w / 2, before.x + before.w / 2, "about its centre");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.w === before.w && p.h === before.h, "the resize undone");
    await page.keyboard.press("Control+Shift+z");
    await storedWhen(firstId, (p) => p.w === sized.w, "the resize redone");
    await pause();
    const grip = centre(await page.locator(".pictureHandle.inkTransform-resize").boundingBox());
    await page.keyboard.down("Alt");
    await drag(grip, 30, 0);
    await page.keyboard.up("Alt");
    const stretched = await storedWhen(firstId, (p) => p.w !== sized.w, "the stretched picture on the server");
    near(stretched.w, sized.w + 60 / k, "wider by the drag on either side", 1.5);
    near(stretched.h, sized.h, "as tall as it was");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.w === sized.w, "the stretch undone");
    assertNoProblems(page);
  });

  await step("picture: the top corner handle turns it, the right angle's magnet taking it; undo and redo", async () => {
    await pause();
    const mid = centre(await picEl(firstId).boundingBox());
    const handle = centre(await page.locator(".pictureHandle.inkTransform-rotate").boundingBox());
    // a quarter turn clockwise about the centre, two degrees short
    const a = (88 * Math.PI) / 180, dx = handle.x - mid.x, dy = handle.y - mid.y;
    const to = { x: mid.x + dx * Math.cos(a) - dy * Math.sin(a), y: mid.y + dx * Math.sin(a) + dy * Math.cos(a) };
    await page.mouse.move(handle.x, handle.y);
    await page.mouse.down();
    for (let i = 1; i <= 12; i++) {
      const b = (a * i) / 12;
      await page.mouse.move(mid.x + dx * Math.cos(b) - dy * Math.sin(b), mid.y + dx * Math.sin(b) + dy * Math.cos(b));
    }
    await page.mouse.move(to.x, to.y);
    await page.mouse.up();
    const turned = await storedWhen(firstId, (p) => p.angle !== 0, "the turned picture on the server");
    assertEq(turned.angle, 90, "snapped to the quarter turn");
    assert((await picEl(firstId).getAttribute("style")).includes("rotate(90deg)"), "the frame turned on the page");
    await shot("picture-turned");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.angle === 0, "the turn undone");
    await page.keyboard.press("Control+y");
    await storedWhen(firstId, (p) => p.angle === 90, "the turn redone");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.angle === 0, "and undone again");
    assertNoProblems(page);
  });

  await step("picture: the menu flips it; undo and redo the flip", async () => {
    await pause();
    await menu().getByRole("button", { name: "Flip horizontal", exact: true }).click();
    await storedWhen(firstId, (p) => p.flip_h === true, "the flip on the server");
    const flip = () => picEl(firstId).locator(".pagePictureFlip").evaluate((el) => el.style.transform);
    await until(async () => (await flip()) === "scale(-1, 1)", { what: "the picture mirrored on the page" });
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.flip_h === false, "the flip undone");
    await page.keyboard.press("Control+y");
    await storedWhen(firstId, (p) => p.flip_h === true, "the flip redone");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.flip_h === false, "and undone again");
    assertNoProblems(page);
  });

  await step("picture: a crop keeps the part kept where it was; Escape cancels one; undo and redo the crop", async () => {
    await pause();
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const before = (await stored(firstId)).properties.picture;
    // a double click crops too; Escape leaves it as it was
    await picEl(firstId).dblclick();
    await page.waitForSelector(".pictureCrop .pictureCrop-se", { timeout: 5000 });
    await dragBy(page.locator(".pictureCrop-se"), -40, -20);
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".pictureCrop")), { what: "crop mode closed" });
    await page.waitForTimeout(600); // a negative check: nothing to wait for
    assertEq(JSON.stringify((await stored(firstId)).properties.picture), JSON.stringify(before), "a cancelled crop writes nothing");
    assert(await page.$(`[data-picture-id="${firstId}"].selected:not(.cropping)`), "the picture drawn again, still selected");
    const r = await picEl(firstId).boundingBox();
    near(r.width / k, before.w, "as large as it was");
    // the menu's Crop, a drag of the bottom-right corner, Enter
    await menu().getByRole("button", { name: "Crop", exact: true }).click();
    await page.waitForSelector(".pictureCrop .pictureCrop-se", { timeout: 5000 });
    assert(!(await menu().count()), "the crop's own bar in the menu's place");
    await dragBy(page.locator(".pictureCrop-se"), -50, -25);
    await shot("picture-cropping");
    await page.keyboard.press("Enter");
    const cropped = await storedWhen(firstId, (p) => p.crop.w < 1, "the crop on the server");
    near(cropped.crop.w, 1 - 50 / k / before.w, "the width kept", 0.01);
    near(cropped.crop.h, 1 - 25 / k / before.h, "the height kept", 0.01);
    assertEq(cropped.crop.x, 0, "from the left edge");
    near(cropped.w, before.w * cropped.crop.w, "the frame shrinks to the part kept, at the same scale");
    near(cropped.x, before.x, "its left edge where it was");
    near(cropped.y, before.y, "its top too");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.crop.w === 1 && p.w === before.w, "the crop undone");
    await page.keyboard.press("Control+y");
    await storedWhen(firstId, (p) => p.crop.w === cropped.crop.w, "the crop redone");
    assertNoProblems(page);
  });

  await step("picture: Copy image puts it on the clipboard as it shows; Reset after a crop, a flip and a turn brings the whole image back upright; Fit to page fills the page's width, and a turn then shrinks it to keep its turned frame on the page; undo each", async () => {
    await pause();
    await menu().getByRole("button", { name: "Flip horizontal", exact: true }).click();
    const flipped = await storedWhen(firstId, (p) => p.flip_h, "the flip on the server");
    // The part kept at the file's own pixels, mirrored as on the page: the
    // image's blue right half on the left now.
    await menu().getByRole("button", { name: "Copy image", exact: true }).click();
    await until(async () => (await status()).includes("Copied image"), { what: "the copy's pill" });
    const copied = await until(() => clipboardPicture(page), { what: "a PNG on the clipboard" });
    const c = flipped.crop, px = Math.max((c.w * 200) / flipped.w, (c.h * 100) / flipped.h); // pixels per point
    near(copied.width, flipped.w * px, "as wide as the part kept, in the file's pixels", 1.01);
    near(copied.height, flipped.h * px, "as tall", 1.01);
    assertEq(`${copied.left} ${copied.right}`, "blue red", "mirrored as it shows");
    // A turn by a focused handle's arrow key: 15°.
    await pause();
    await page.locator(".pictureHandle.inkTransform-rotate").focus();
    await page.keyboard.press("ArrowRight");
    const before = await storedWhen(firstId, (p) => p.angle === 15, "the turn on the server");
    await pause();
    await (await menuButton("Reset")).click();
    const reset = await storedWhen(firstId, (p) => p.angle === 0, "the reset on the server");
    assertEq(JSON.stringify(reset.crop), JSON.stringify({ x: 0, y: 0, w: 1, h: 1 }), "the whole image");
    assert(!reset.flip_h && !reset.flip_v, "unflipped");
    assertEq(reset.w, before.w, "as wide as it was");
    near(reset.h, before.w / 2, "at the file's own proportions", 0.02);
    near(reset.x + reset.w / 2, before.x + before.w / 2, "about the same centre", 0.02);
    near(reset.y + reset.h / 2, before.y + before.h / 2, "down too", 0.02);
    assert((await picEl(firstId).getAttribute("style")).indexOf("rotate(") < 0, "upright on the page");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => JSON.stringify(p) === JSON.stringify(before), "the reset undone: cropped, flipped and turned again");
    assert((await status()).includes("Undone: picture change"), `the pill names it (${await status()})`);
    await page.keyboard.press("Control+y");
    await storedWhen(firstId, (p) => JSON.stringify(p) === JSON.stringify(reset), "the reset redone");
    await pause();
    await (await menuButton("Fit to page")).click();
    const fit = await storedWhen(firstId, (p) => p.w !== reset.w, "the fit on the server");
    assertEq(JSON.stringify([fit.x, fit.w]), JSON.stringify([24, 564]), "as wide as the page less 24 pt each side");
    near(fit.h, 282, "its proportions kept", 0.1);
    near(fit.y + fit.h / 2, 396, "centred down the page", 0.1);
    // Turned 15° as wide as that, its turned frame would overhang the page:
    // the turn shrinks it until the frame fits.
    await pause();
    await page.locator(".pictureHandle.inkTransform-rotate").focus();
    await page.keyboard.press("ArrowRight");
    const tilted = await storedWhen(firstId, (p) => p.angle === 15, "the turn of the fitted picture on the server");
    const [x0, y0, x1, y1] = turnedBox(tilted);
    assert(x0 > -0.02 && y0 > -0.02 && x1 < 612.02 && y1 < 792.02, `its turned frame on the page: ${[x0, y0, x1, y1]}`);
    assert(tilted.w < fit.w, "shrunk to fit");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => JSON.stringify(p) === JSON.stringify(fit), "the turn undone");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => JSON.stringify(p) === JSON.stringify(reset), "the fit undone");
    assertNoProblems(page);
  });

  await step("picture: a reload keeps it as it was left", async () => {
    const pic = (await stored(firstId)).properties.picture;
    await page.reload();
    await waitForPdf(page, 1);
    await picEl(firstId).waitFor({ timeout: 15000 });
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const r = await picEl(firstId).boundingBox(), pg = await page.locator('[data-page="1"]').boundingBox();
    near((r.x - pg.x) / k, pic.x, "where it was");
    near(r.width / k, pic.w, "as large");
    assert(await picEl(firstId).locator("img").evaluate((img) => img.complete && img.naturalWidth === 200), "its image loaded");
    assertNoProblems(page);
  });

  let pastedId;
  await step("picture: a paste with an image on the clipboard places it at the page's centre; a drop at the drop point", async () => {
    await pasteFile(page, png(120, 120), "pasted.png");
    const pasted = await until(async () => picturesIn((await subtree(pageId)).children).find((b) => b.id !== firstId),
      { what: "the pasted picture on the server" });
    pastedId = pasted.id;
    const p = pasted.properties.picture;
    assertEq(JSON.stringify([p.x, p.y, p.w, p.h]), JSON.stringify([246, 336, 120, 120]), "at the page's centre");
    await page.waitForSelector(`[data-picture-id="${pastedId}"].selected`, { timeout: 5000 });
    assert(!(await page.$(`[data-picture-id="${firstId}"].selected`)), "one picture selected at a time");
    const pg = await page.locator('[data-page="1"]').boundingBox(), k = pg.width / 612;
    const blocks0 = (await subtree(pageId)).children.length;
    await dropFile(page, '[data-page="1"]', png(60, 40), "dropped.png", pg.x + 150 * k, pg.y + 600 * k);
    const dropped = await until(async () => picturesIn((await subtree(pageId)).children).find((b) => ![firstId, pastedId].includes(b.id)),
      { what: "the dropped picture on the server" });
    const d = dropped.properties.picture;
    near(d.x + d.w / 2, 150, "centred on the drop point");
    near(d.y + d.h / 2, 600, "down too");
    assertEq((await subtree(pageId)).children.length, blocks0 + 1, "one block for it, not a file block too");
    assert(!(await page.$(".app.dragOver")), "the drop overlay is gone");
    // Send to back on the last one puts it first among the page's pictures.
    await page.keyboard.press("Escape");
    await page.locator(`[data-picture-id="${dropped.id}"]`).click();
    await menu().waitFor({ timeout: 5000 });
    await (await menuButton("Send to back")).click();
    await until(async () => picturesIn((await subtree(pageId)).children)[0]?.id === dropped.id, { what: "sent to the back" });
    await page.keyboard.press("Delete");
    await until(async () => !(await stored(dropped.id)), { what: "the dropped picture deleted" });
    await page.keyboard.press("Control+z");
    await until(async () => !!(await stored(dropped.id)), { what: "and back with Ctrl+Z" });
    await account.api(`/api/blocks/${dropped.id}`, { method: "DELETE" });
    assertNoProblems(page);
  });

  await step("picture: Duplicate puts a copy 12 px right and down, selected; Send to back and Bring to front reorder two overlapping pictures on the page and in the notes; the arrows nudge the copy and Delete deletes it", async () => {
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    await page.keyboard.press("Escape");
    await picEl(pastedId).click(); // the one on top
    await menu().waitFor({ timeout: 5000 });
    const src = (await stored(pastedId)).properties.picture;
    await menu().getByRole("button", { name: "Duplicate", exact: true }).click();
    const copy = await until(async () => picturesIn((await subtree(pageId)).children).find((b) => ![firstId, pastedId].includes(b.id)),
      { what: "the copy on the server" });
    const c = copy.properties.picture;
    near(c.x, src.x + 12 / k, "12 px right", 0.02);
    near(c.y, src.y + 12 / k, "and down", 0.02);
    assertEq(JSON.stringify([c.url, c.w, c.h, copy.properties.pdf_page]), JSON.stringify([src.url, src.w, src.h, 1]),
      "the same file, as large, on the same page");
    await page.waitForSelector(`[data-picture-id="${copy.id}"].selected`, { timeout: 5000 });
    assert(!(await page.$(`[data-picture-id="${pastedId}"].selected`)), "the copy selected in its place");
    // The order of the page's pictures on the server, on the page and in
    // the notes; and which of the two takes a press where they overlap.
    const ids = [firstId, pastedId, copy.id];
    const order = async () => ({
      server: picturesIn((await subtree(pageId)).children).map((b) => b.id).join(" "),
      page: (await page.locator('[data-page="1"] .pictureLayer .pagePicture').evaluateAll((els) => els.map((el) => el.dataset.pictureId))).join(" "),
      notes: (await page.locator(".blockRowWrap[data-block-id]").evaluateAll((els, ids) =>
        els.map((el) => el.dataset.blockId).filter((id) => ids.includes(id)), ids)).join(" "),
    });
    const r = await picEl(copy.id).boundingBox(), both = { x: r.x + 4, y: r.y + 4 };
    const onTop = () => page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest(".pagePicture")?.dataset.pictureId, both);
    const inOrder = async (want, what) => {
      const o = await until(async () => {
        const o = await order();
        return o.server === want.join(" ") ? o : null;
      }, { what });
      assertEq(o.page, o.server, `${what}: on the page`);
      assertEq(o.notes, o.server, `${what}: in the notes`);
    };
    await inOrder([firstId, pastedId, copy.id], "the copy right after the picture it copies");
    assertEq(await onTop(), copy.id, "the copy over it");
    await (await menuButton("Send to back")).click();
    await inOrder([copy.id, firstId, pastedId], "the copy sent to the back");
    assertEq(await onTop(), pastedId, "the picture it copies over it now");
    await (await menuButton("Bring to front")).click();
    await inOrder([firstId, pastedId, copy.id], "the copy brought to the front");
    assertEq(await onTop(), copy.id, "the copy over it again");
    assert(await page.$(`[data-picture-id="${copy.id}"].selected`), "still selected");
    await page.keyboard.press("ArrowDown");
    await storedWhen(copy.id, (p) => p.y === Math.round((c.y + 1) * 100) / 100 && p.x === c.x, "the copy nudged 1 pt down");
    await page.keyboard.press("Delete");
    await until(async () => !(await stored(copy.id)), { what: "the copy deleted" });
    assert(!(await page.$(`[data-picture-id="${copy.id}"]`)) && !(await menu().count()), "gone from the page, its menu too");
    assertNoProblems(page);
  });

  await step("picture: its caption is typed in its row, where Delete and Backspace edit the text and leave the picture", async () => {
    await picEl(pastedId).click();
    await page.waitForSelector(`[data-picture-id="${pastedId}"].selected`, { timeout: 5000 });
    const row = page.locator(`.blockRowWrap[data-block-id="${pastedId}"]`);
    await row.locator(".blockRendered").click(); // its "(empty)"
    await row.locator(".blockEditorCm .cm-content").waitFor({ timeout: 5000 });
    await page.keyboard.type("XA pasted caption!");
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Home");
    await page.keyboard.press("Delete");
    await until(async () => (await stored(pastedId))?.content === "A pasted caption", { what: "the caption on the server" });
    await closeEditor(page);
    assert(await stored(pastedId), "the picture stays");
    assertEq(await picEl(pastedId).locator("img").getAttribute("alt"), "A pasted caption", "the caption is its alt text on the page");
    assertNoProblems(page);
  });

  await step("picture: a stylus moves a selected picture and turns it by its handle, writing no ink", async () => {
    await page.click("button[aria-label='Handwriting tools']"); // the strip, closed since the reload
    await page.click(".pdfInkBar button[aria-label='Hand']");
    const cdp = await ctx.newCDPSession(page);
    const pen = stylus(cdp);
    const paths = '[data-page="1"] .inkLayer path';
    const groups = async () => (await subtree(pageId)).children.filter((b) => b.properties?.ink_url !== undefined).length;
    const [paths0, groups0] = [await page.locator(paths).count(), await groups()];
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const at = await spotOn(page, firstId);
    await page.mouse.click(at.x, at.y); // the hand is armed: a stylus on a picture not selected writes over it
    await page.waitForSelector(`[data-picture-id="${firstId}"].selected`, { timeout: 5000 });
    await pause();
    const before = (await stored(firstId)).properties.picture;
    const from = await spotOn(page, firstId);
    await pen(from.x, from.y, from.x + 40, from.y + 30);
    const moved = await storedWhen(firstId, (p) => p.x !== before.x, "the stylus's move on the server");
    near(moved.x, before.x + 40 / k, "moved as far as the stylus", 1.5);
    near(moved.y, before.y + 30 / k, "down too", 1.5);
    await pause();
    const mid = centre(await picEl(firstId).boundingBox());
    const handle = centre(await page.locator(".pictureHandle.inkTransform-rotate").boundingBox());
    const a = (30 * Math.PI) / 180, dx = handle.x - mid.x, dy = handle.y - mid.y;
    await pen(handle.x, handle.y, mid.x + dx * Math.cos(a) - dy * Math.sin(a), mid.y + dx * Math.sin(a) + dy * Math.cos(a));
    const turned = await storedWhen(firstId, (p) => p.angle !== 0, "the stylus's turn on the server");
    near(turned.angle, 30, "turned as far as the stylus went round", 1);
    assertEq(await page.locator(paths).count(), paths0, "no stroke drawn");
    assertEq(await groups(), groups0, "no ink group made");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.angle === 0, "the turn undone");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.x === before.x && p.y === before.y, "the move undone");
    await cdp.detach();
    assertNoProblems(page);
  });

  await step("picture: Escape during a handle drag or a move takes back the drag alone; a press elsewhere mid-drag lets go of the picture and of the drag's preview", async () => {
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const pic = (await stored(firstId)).properties.picture;
    const width = async () => (await picEl(firstId).boundingBox()).width;
    const midDrag = async () => {
      const grip = centre(await page.locator(".pictureHandle.inkTransform-resize").boundingBox());
      await page.mouse.move(grip.x, grip.y);
      await page.mouse.down();
      await page.mouse.move(grip.x + 50, grip.y + 25, { steps: 6 });
      await until(async () => (await width()) > pic.w * k + 20, { what: "the drag's preview" });
    };
    await midDrag();
    assert(!(await menu().count()), "no menu while a handle is dragged");
    await page.keyboard.press("Escape");
    await until(async () => Math.abs((await width()) - pic.w * k) < 1, { what: "Escape takes the preview back" });
    await page.mouse.up();
    assert(await page.$(`[data-picture-id="${firstId}"].selected`), "the picture still selected");
    await menu().waitFor({ timeout: 5000 });
    // Escape during a move: the picture goes back, stays selected, stores nothing
    const left = async () => (await picEl(firstId).boundingBox()).x;
    const x0 = await left();
    const on = await spotOn(page, firstId);
    await page.mouse.move(on.x, on.y);
    await page.mouse.down();
    await page.mouse.move(on.x + 60, on.y + 40, { steps: 6 });
    await until(async () => (await left()) > x0 + 30, { what: "the move's preview" });
    await page.keyboard.press("Escape");
    await until(async () => Math.abs((await left()) - x0) < 1, { what: "Escape takes the move back" });
    await page.mouse.up();
    assert(await page.$(`[data-picture-id="${firstId}"].selected`), "the picture still selected after the move taken back");
    await page.waitForTimeout(400); // a negative check: nothing to wait for
    assertEq(JSON.stringify((await stored(firstId)).properties.picture), JSON.stringify(pic), "the move taken back stored nothing");
    // another press (a palm, a second finger) while the handle is held
    await midDrag();
    await page.evaluate(() => {
      for (const type of ["pointerdown", "pointerup"]) {
        document.body.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 77, pointerType: "mouse", isPrimary: false }));
      }
    });
    await until(async () => !(await page.$(`[data-picture-id="${firstId}"].selected`)), { what: "the press elsewhere lets go of it" });
    await until(async () => Math.abs((await width()) - pic.w * k) < 1, { what: "and of the drag's preview" });
    await page.mouse.up();
    await page.waitForTimeout(600); // a negative check: nothing to wait for
    assertEq(JSON.stringify((await stored(firstId)).properties.picture), JSON.stringify(pic), "neither drag stored anything");
    const at = await spotOn(page, firstId);
    await page.mouse.click(at.x, at.y);
    await page.waitForSelector(`[data-picture-id="${firstId}"].selected`, { timeout: 5000 });
    await menu().waitFor({ timeout: 5000 });
    near(await width(), pic.w * k, "selected again, drawn as stored");
    assertNoProblems(page);
  });

  await step("picture: a text box over its handles takes none of their presses", async () => {
    await pause();
    const pic = (await stored(firstId)).properties.picture;
    // a caption box just under the picture, over its bottom-right handle
    const boxId = "pictureCaptionBox";
    await account.api(`/api/pages/${pageId}/ops`, { method: "POST", body: { client: "e2e", ops: [{ op: "insert", id: boxId, parent: pageId,
      content: "A caption under the picture", props: { text_box: { x: pic.x + pic.w - 40, y: pic.y + pic.h, w: 160, h: 40, auto: false }, pdf_page: 1 } }] } });
    await page.waitForSelector(`[data-box-id="${boxId}"]`, { timeout: 10000 });
    const handle = centre(await page.locator(".pictureHandle.inkTransform-resize").boundingBox());
    const hit = await page.evaluate(({ x, y, boxId }) => {
      const stack = document.elementsFromPoint(x, y);
      return { top: !!stack[0]?.closest(".pictureHandle"), box: stack.some((el) => el.closest(`[data-box-id="${boxId}"]`)) };
    }, { ...handle, boxId });
    assert(hit.box, "the box lies under the handle");
    assert(hit.top, "the handle over it");
    await drag(handle, 30, 15);
    const sized = await storedWhen(firstId, (p) => p.w !== pic.w, "the resize from the handle over the box");
    assert(sized.w > pic.w, "larger");
    assert(await page.$(`[data-picture-id="${firstId}"].selected`) && !(await page.$(`[data-box-id="${boxId}"]:is(.selected, .editing)`)),
      "the picture still selected, the box not");
    await page.keyboard.press("Control+z");
    await storedWhen(firstId, (p) => p.w === pic.w, "the resize undone");
    await account.api(`/api/blocks/${boxId}`, { method: "DELETE" });
    await until(async () => !(await page.$(`[data-box-id="${boxId}"]`)), { what: "the box gone" });
    assertNoProblems(page);
  });

  await step("picture: an upload that lands late leaves the text box opened meanwhile open, and lands unselected", async () => {
    const blockIn = (list, id) => (list || []).reduce((f, b) => f || (b.id === id ? b : blockIn(b.children, id)), null);
    let release;
    const held = new Promise((done) => { release = done; });
    const route = async (r) => { await held; await r.continue(); };
    await page.route("**/api/upload-image*", route);
    await pickPicture(png(80, 60), "late.png");
    // while it is held: the Text tool, a box opened on the page, typed in
    const r = await picEl(firstId).boundingBox(); // a free spot under the pictures, in view
    await page.keyboard.press("t");
    await page.waitForSelector(".pdfInkBar button[title^='Text (T)'].modeActive", { timeout: 5000 });
    await until(async () => !(await page.$(".pagePicture.selected")) && !(await menu().count()), { what: "the tool lets go of the picture" });
    await page.mouse.click(r.x + 10, r.y + r.height + 120);
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    const boxId = await page.locator(".textBox.editing").getAttribute("data-box-id");
    await page.keyboard.type("Typed while it uploads");
    release();
    const late = await until(async () => picturesIn((await subtree(pageId)).children).find((p) => ![firstId, pastedId].includes(p.id)),
      { what: "the late picture on the server" });
    await picEl(late.id).waitFor({ timeout: 5000 });
    await page.waitForTimeout(300); // a negative check: nothing to wait for
    assert(await page.$(`.textBox.editing[data-box-id="${boxId}"]`), "the box still open");
    assert(!(await page.$(".pagePicture.selected")), "the late picture not selected");
    await page.keyboard.type(" and after");
    await page.keyboard.press("Backspace");
    await until(async () => blockIn((await subtree(pageId)).children, boxId)?.content === "Typed while it uploads and afte",
      { what: "the keys in the box" });
    assert(await stored(late.id), "Backspace left the picture alone");
    await page.unroute("**/api/upload-image*", route);
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".textBox.editing")), { what: "the box's editor closed" });
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".textBox.selected")), { what: "the box let go" });
    await page.keyboard.press("v"); // the hand again
    await page.waitForSelector(".pdfInkBar button[aria-label='Hand'].modeActive", { timeout: 5000 });
    for (const id of [boxId, late.id]) await account.api(`/api/blocks/${id}`, { method: "DELETE" });
    await until(async () => !(await page.$(`[data-picture-id="${late.id}"], [data-box-id="${boxId}"]`)), { what: "both gone" });
    assertNoProblems(page);
  });

  await step("picture: the notes show it as a card under its marker; the card shows it on its page", async () => {
    const row = page.locator(`.blockRowWrap[data-block-id="${firstId}"]`);
    await row.locator(".pictureMarker").waitFor({ timeout: 5000 });
    assertEq(await row.locator(".blockMeta").first().innerText(), "p.1", "the page named");
    await row.locator(".blockPictureCard img").waitFor({ timeout: 5000 });
    assert((await row.locator(".blockPictureCard .pagePictureFlip").evaluate((el) => el.style.transform)) === "scale(1, 1)",
      "drawn as placed");
    await page.keyboard.press("Escape");
    await row.locator(".blockPictureCard").click();
    await page.waitForSelector(`[data-picture-id="${firstId}"] .pictureFlash`, { timeout: 5000 });
    await page.locator(`.blockRowWrap[data-block-id="${pastedId}"] .pictureMarker`).click();
    await page.waitForSelector(`[data-picture-id="${pastedId}"] .pictureFlash`, { timeout: 5000 });
    await shot("picture-notes");
    assertNoProblems(page);
    await ctx.close();
  });

  // (Chromium's emulation delivers no click for a tap after a touch
  // scroll, so the tap comes first.)
  await step("picture: a finger tap selects a picture and a drag moves it; over a picture not selected the finger scrolls; two fingers on a selected one pinch and move nothing", async () => {
    ctx = await account.context(browser, { hasTouch: true });
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await picEl(pastedId).waitFor({ timeout: 15000 });
    const cdp = await ctx.newCDPSession(page);
    const fingerDrag = async (from, dx, dy) => {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
      for (let i = 1; i <= 12; i++) await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove", touchPoints: [{ x: from.x + dx * i / 12, y: from.y + dy * i / 12 }] });
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    };
    const scrollTop = () => page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const p = await spotOn(page, pastedId);
    await page.touchscreen.tap(p.x, p.y);
    await page.waitForSelector(`[data-picture-id="${pastedId}"].selected`, { timeout: 5000 });
    const before = (await stored(pastedId)).properties.picture;
    const scrollAt = await scrollTop();
    await fingerDrag(await spotOn(page, pastedId), 50, 30);
    const moved = await storedWhen(pastedId, (q) => q.x !== before.x, "the finger's move on the server");
    near(moved.x, before.x + 50 / k, "moved as far as the finger", 1.5);
    near(moved.y, before.y + 30 / k, "down too", 1.5);
    assertEq(await scrollTop(), scrollAt, "a finger on the selected picture does not scroll");
    // A finger over the other picture, not selected, scrolls the page.
    const firstBefore = (await stored(firstId)).properties.picture;
    const scrollBefore = await scrollTop();
    const room = await page.locator(".pdfViewer").evaluate((el) => el.scrollHeight - el.clientHeight);
    await fingerDrag(await spotOn(page, firstId), 0, scrollBefore > room / 2 ? 120 : -120); // towards the side it can scroll to
    await until(async () => Math.abs(await scrollTop() - scrollBefore) > 20, { what: "a finger over a picture not selected scrolls the page" });
    assertEq(JSON.stringify((await stored(firstId)).properties.picture), JSON.stringify(firstBefore), "and leaves the picture");
    assert(!(await page.$(`[data-picture-id="${firstId}"].selected`)), "nor selects it");
    // Two fingers on a selected picture pinch (the page zooms): the picture
    // stays where it is. (Selected by the mouse: no tap after that scroll.)
    await picEl(pastedId).scrollIntoViewIfNeeded();
    const m = await spotOn(page, pastedId);
    await page.mouse.click(m.x, m.y);
    await page.waitForSelector(`[data-picture-id="${pastedId}"].selected`, { timeout: 5000 });
    const c = await spotOn(page, pastedId);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: c.x - 5, y: c.y, id: 1 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: c.x - 5, y: c.y, id: 1 }, { x: c.x + 5, y: c.y, id: 2 }] });
    for (let i = 1; i <= 10; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove",
      touchPoints: [{ x: c.x - 5 - 6 * i, y: c.y - 2 * i, id: 1 }, { x: c.x + 5 + 6 * i, y: c.y + 2 * i, id: 2 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(1200); // a negative check: nothing to wait for
    assertEq(JSON.stringify((await stored(pastedId)).properties.picture), JSON.stringify(moved), "a pinch moves no picture");
    await cdp.detach();
    assertNoProblems(page);
    await ctx.close();
  });

  await step("picture: a read-only visitor sees it, and a click on it shows its note; an edit share's visitor drops one on the page and sees it at once", async () => {
    const { token } = await account.api(`/api/share/${pageId}`, { method: "POST" });
    const viewer = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const v = await openPage(viewer, `${server.base}/?share=${token}`);
    await waitForPdf(v, 1);
    const el = v.locator(`[data-picture-id="${pastedId}"]`); // the one on top
    await el.waitFor({ timeout: 15000 });
    await until(() => el.locator("img").evaluate((img, token) => img.complete && img.naturalWidth > 0
      && new URL(img.src).searchParams.get("share") === token, token), { what: "the image served through the share token" });
    await el.click();
    await v.waitForSelector(`.blockRowWrap[data-block-id="${pastedId}"] .blockRow.focused`, { timeout: 5000 });
    assert(!(await v.$(".pagePicture.selected")) && !(await v.$(".pictureHandle")), "nothing to edit");
    assertNoProblems(v);
    await viewer.close();
    // An edit share: the page takes a dragged image though the app around
    // it takes no files (a drag nobody accepts never drops: the browser
    // would open the file instead).
    await account.api(`/api/share-settings/${pageId}`, { method: "PUT", body: { audience: "anyone", role: "edit" } });
    const editor = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const e = await openPage(editor, `${server.base}/?share=${token}`);
    await waitForPdf(e, 1);
    await e.locator(`[data-picture-id="${pastedId}"]`).waitFor({ timeout: 15000 });
    await until(async () => (await e.textContent("body")).includes("Can edit"), { what: "the edit badge" });
    const taken = (type) => e.evaluate((type) => {
      const dt = new DataTransfer();
      dt.items.add(new File(["x"], "f", { type }));
      const ev = new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true });
      document.querySelector('[data-page="1"]').dispatchEvent(ev);
      return ev.defaultPrevented;
    }, type);
    assert(await taken("image/png"), "a dragged image is taken");
    assert(!(await taken("application/pdf")), "another file is not");
    const pg = await e.locator('[data-page="1"]').boundingBox(), k = pg.width / 612;
    await dropFile(e, '[data-page="1"]', png(40, 40), "shared.png", pg.x + 100 * k, pg.y + 100 * k);
    const dropped = await until(async () => picturesIn((await subtree(pageId)).children).find((b) => ![firstId, pastedId].includes(b.id)),
      { what: "the visitor's picture on the server" });
    near(dropped.properties.picture.x + dropped.properties.picture.w / 2, 100, "at the drop point");
    // The visitor's own upload reads through the link at once, before the
    // block naming it is saved (gamma/routers/uploads.py _share_reads_own_upload).
    const shownPic = e.locator(`[data-picture-id="${dropped.id}"] img`);
    await shownPic.waitFor({ timeout: 10000 });
    await until(() => shownPic.evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the visitor's picture shown" });
    assertNoProblems(e);
    await editor.close();
    await account.api(`/api/blocks/${dropped.id}`, { method: "DELETE" });
    await account.api(`/api/share-settings/${pageId}`, { method: "DELETE" });
  });

  await step("picture: a highlighter across a picture multiplies into it on a light page, a flipped one and in Gamma Dark; where the picture is transparent it does as on the page around it", async () => {
    // A picture is never inverted with a dark page, so where a highlighter
    // plane screens (a flipped page, Gamma Dark) it would all but vanish
    // over one: over a picture it multiplies, the picture's colour times
    // the highlighter's at its opacity, as on a light page (InkLayer, "a
    // highlighter over a picture"). The picture is a pale grey, mirrored, so
    // its opaque half shows on the right of its frame, and its other half
    // transparent, where the highlighter takes the page's blend as the page
    // past the picture does. Over the text under it the highlighter keeps
    // the page's rule (#147, ink.mjs): black text on a light page, light on
    // a dark one.
    const up = await account.upload("/api/uploads", makePdf([["Highlight across", "and some text"]]), "highlight.pdf", "application/pdf");
    const paper = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Highlighted picture", source_url: up.source_url } });
    const GREY = 236;
    const light = encodePng(200, 40, (x) => (x < 100 ? [GREY, GREY, GREY, 255] : [0, 0, 0, 0]));
    const { url } = await account.upload("/api/upload-image", light, "light.png", "image/png");
    // on the first line of text (its baseline 72 pt down), right of its words
    const pic = normalizePicture({ url, x: 320, y: 46, w: 200, h: 40, flip_h: true });
    await account.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [{ op: "insert", id: "picLight", parent: paper.id,
      content: "", props: { picture: pic, pdf_page: 1 } }] } });
    const hctx = await account.context(browser);
    const p = await openPage(hctx, `${server.base}/?page=${paper.id}&ws=${account.ws}`);
    await waitForPdf(p, 1);
    const img = p.locator('[data-picture-id="picLight"] img');
    await img.waitFor({ timeout: 15000 });
    await until(() => img.evaluate((el) => el.complete && el.naturalWidth > 0), { what: "the picture shown" });
    const line = await until(() => p.locator('[data-page="1"] .textLayer span').evaluateAll((spans) => {
      const r = spans.find((s) => s.textContent.startsWith("Highlight"))?.getBoundingClientRect();
      return r && r.width > 60 && { x: r.left, y: r.top + r.height / 2, width: r.width };
    }), { what: "the first line of text" });
    const pg = await p.locator('[data-page="1"]').boundingBox(), k = pg.width / 612;
    const at = (pt) => pg.x + pt * k;
    const frame = await p.locator('[data-picture-id="picLight"]').boundingBox();
    assert(line.y > frame.y + 4 && line.y < frame.y + frame.height - 4, `the line runs through the picture: ${line.y} in ${JSON.stringify(frame)}`);
    await p.click("button[aria-label='Handwriting tools']");
    await p.waitForSelector(".pdfInkBar");
    assert(await armHighlighter(p), "a highlighter preset is armed");
    await p.mouse.move(line.x + 4, line.y);
    await p.mouse.down();
    await p.mouse.move(at(600), line.y, { steps: 16 });
    await p.mouse.up();
    const block = await until(async () => (await subtree(paper.id)).children.find((b) => b.properties?.ink_url), { what: "the highlighter saved" });
    const stroke = (await account.api(block.properties.ink_url)).strokes[0];
    assertEq(stroke.tool, "highlighter", "a highlighter stroke");
    await until(async () => (await p.locator('[data-page="1"] .inkOverPictures use').count()) === 1
      && (await p.locator('[data-page="1"] .inkCanvas').evaluate((c) => c.style.display)) === "none", { what: "the highlighter drawn over the picture" });
    const laid = await p.locator('[data-page="1"]').evaluate((wrap, id) => {
      const plane = wrap.querySelector(`.inkLayer g[data-ink-id="${id}"]`).closest(".inkLayer");
      return { blend: plane.dataset.blend, next: plane.nextElementSibling?.getAttribute("class") };
    }, block.id);
    assertEq(JSON.stringify(laid), JSON.stringify({ blend: "multiply", next: "inkOverPictures" }), "the copy over the pictures right after the highlighter's plane");

    const multiplied = (grey) => highlighted(stroke, [grey, grey, grey]);
    const band = (x0, x1) => ({ x: x0, y: line.y - 2, width: x1 - x0, height: 4 });
    const text = band(line.x + 16, line.x + line.width - 16), clear = band(at(330), at(410)), opaque = band(at(430), at(510));
    const past = band(at(535), at(585));
    // The page as `look` draws it, `grey` the picture's grey there; a dark
    // page dims the picture, which may take a frame to draw its mask.
    const check = async (look, grey, textOk) => {
      let shown = null;
      for (let i = 0; i < 20; i++) {
        shown = { text: await screenPixels(p, text), clear: await screenPixels(p, clear), opaque: await screenPixels(p, opaque),
          past: await screenPixels(p, past) };
        if (sameColour(shown.opaque.median, multiplied(grey))) break;
        await p.waitForTimeout(150);
      }
      const what = `${look}: ${JSON.stringify({ ...shown, want: multiplied(grey).map(Math.round) })}`;
      assert(sameColour(shown.opaque.median, multiplied(grey)) && shown.opaque.tinted > 0, `over the picture the highlighter multiplies, ${what}`);
      assert(sameColour(shown.clear.median, shown.past.median), `where the picture is transparent it does as on the page past it, ${what}`);
      assert(textOk(shown.text), `over the text it keeps the page's rule, ${what}`);
    };
    await check("a light page", GREY, (t) => Math.max(...t.darkest) < 90 && t.tinted > 0);
    await p.locator(".pdfViewer").evaluate((el) => el.classList.add("pdfDark"));
    await check("a flipped page", GREY * 0.88, (t) => Math.min(...t.lightest) > 190);
    await p.locator(".pdfViewer").evaluate((el) => el.classList.remove("pdfDark"));
    const theme = await p.evaluate(() => document.documentElement.dataset.theme);
    await p.evaluate(() => { document.documentElement.dataset.theme = "gamma-dark"; });
    await check("Gamma Dark", GREY * 0.88, (t) => Math.min(...t.lightest) > 190);
    await p.evaluate((was) => {
      if (was == null) delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = was;
    }, theme ?? null);
    if (flags.keep) await p.screenshot({ path: `${server.dir}/picture-highlighted.png` });
    assertNoProblems(p);
    await hctx.close();
  });

  await step("picture: on a sheet in the notebook view, under the sheet; moved, undone, kept after a reload; on dark paper a highlighter over it multiplies", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    await page.click("button[aria-label='Add']");
    await page.getByRole("menuitem", { name: "New notebook" }).or(page.locator("button", { hasText: "New notebook" })).first().click();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
    await page.keyboard.type("Picture notebook");
    await page.keyboard.press("Enter");
    const nbId = await until(async () => ((await account.api("/api/blocks/root/children")).children || [])
      .find((b) => b.content === "Picture notebook")?.id, { what: "the notebook in the library" });
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    // The strip's key presses the Picture button; its keys listen from the
    // effect after the strip shows, so a press that comes first is pressed again.
    let chooser = null;
    for (let i = 0; i < 5 && !chooser; i++) {
      const opened = page.waitForEvent("filechooser", { timeout: 2000 }).catch(() => null);
      await page.keyboard.press("i");
      chooser = await opened;
    }
    assert(chooser, "I opens the file picker");
    await chooser.setFiles({ name: "sheet.png", mimeType: "image/png", buffer: png(100, 80) });
    await page.waitForSelector(".nbSheet .pagePicture.selected", { timeout: 10000 });
    const id = await until(async () => picturesIn((await subtree(nbId)).children)[0]?.id, { what: "the picture on the server" });
    const block = await stored(id, nbId);
    const [sheet] = (await subtree(nbId)).children.filter((c) => c.properties?.sheet);
    assert(sheet.children.some((c) => c.id === id), "the sheet's child");
    assert(!("pdf_page" in block.properties), "no PDF page");
    const paper = normalizePaper(sheet.properties.sheet);
    const p0 = block.properties.picture;
    near(p0.x + p0.w / 2, paper.width / 2, "centred on the sheet");
    const k = (await page.locator(".nbSheet").first().boundingBox()).width / paper.width;
    await dragBy(picEl(id), 50, 30);
    const moved = await storedWhen(id, (p) => p.x !== p0.x, "the move on the sheet", nbId);
    near(moved.x, p0.x + 50 / k, "moved with the drag");
    await page.keyboard.press("Control+z");
    await storedWhen(id, (p) => p.x === p0.x, "the move undone", nbId);
    await page.keyboard.press("Control+y");
    await storedWhen(id, (p) => p.x === moved.x, "the move redone", nbId);
    await page.reload();
    await page.waitForSelector(`.nbViewer .nbSheet [data-picture-id="${id}"]`, { timeout: 15000 });
    await shot("picture-sheet");
    // On dark paper a highlighter is drawn as it is, since multiplied it
    // would all but vanish (#147), but over a picture it multiplies still:
    // across the picture's red half, and on past it.
    await page.click("button[aria-label='Paper of this page']");
    await page.waitForSelector(".nbPaperMenu");
    await page.locator(".nbPaperMenu").getByRole("button", { name: "Dark", exact: true }).click();
    await until(async () => (await page.locator(".nbSheet").first().getAttribute("data-paper")) === "dark", { what: "the sheet's paper dark" });
    await page.keyboard.press("Escape");
    if (!(await page.locator(".pdfInkBar").isVisible().catch(() => false))) await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    assert(await armHighlighter(page), "a highlighter preset is armed");
    const fr = await picEl(id).boundingBox(), y = Math.round(fr.y + fr.height / 2);
    await drag({ x: fr.x - 30, y }, fr.width + 100, 0, 16);
    const inkOf = (list) => (list || []).flatMap((b) => [...(b.properties?.ink_url ? [b] : []), ...inkOf(b.children)]);
    const hl = await until(async () => inkOf((await subtree(nbId)).children)[0], { what: "the highlighter on the sheet saved" });
    const stroke = (await account.api(hl.properties.ink_url)).strokes[0];
    await until(async () => (await page.locator(".nbSheet >> nth=0 >> .inkOverPictures use").count()) === 1
      && (await page.locator(".nbSheet >> nth=0 >> .inkCanvas").evaluate((c) => c.style.display)) === "none", { what: "the highlighter drawn" });
    const band = (x0, x1, at = y) => ({ x: Math.round(x0), y: at - 2, width: Math.round(x1 - x0), height: 4 });
    let dark = null;
    for (let i = 0; i < 20; i++) {
      dark = { red: await screenPixels(page, band(fr.x + fr.width * 0.1, fr.x + fr.width * 0.4)),
        past: await screenPixels(page, band(fr.x + fr.width + 15, fr.x + fr.width + 50)),
        paper: await screenPixels(page, band(fr.x + fr.width + 15, fr.x + fr.width + 50, y - 40)) };
      if (sameColour(dark.red.median, highlighted(stroke, [220, 40, 0]))) break;
      await page.waitForTimeout(150);
    }
    const seen = JSON.stringify({ ...dark, stroke: { color: stroke.color, opacity: stroke.opacity } });
    assert(sameColour(dark.red.median, highlighted(stroke, [220, 40, 0])), `over the picture on dark paper the highlighter multiplies: ${seen}`);
    assert(sameColour(dark.past.median, highlighted(stroke, dark.paper.median, true)), `past it, it is drawn as it is: ${seen}`);
    // In the notes view the sheet is drawn in its row: a picture dropped on
    // it is the sheet's, and the row's file-drop outline stays off.
    await page.click(".pdfZoomOverlay button[aria-label='Notes view']");
    const inRow = page.locator(".noteSheet .nbSheet").first();
    await inRow.waitFor({ timeout: 10000 });
    const r = await inRow.boundingBox();
    await dropFile(page, ".noteSheet .nbSheet", png(50, 50), "row.png", r.x + r.width / 2, r.y + r.height / 3);
    const onRow = await until(async () => picturesIn((await subtree(nbId)).children).find((b) => b.id !== id),
      { what: "the picture dropped on the sheet among the notes" });
    assert((await subtree(nbId)).children.find((c) => c.properties?.sheet).children.some((c) => c.id === onRow.id), "under the sheet");
    await page.locator(`.noteSheet [data-picture-id="${onRow.id}"]`).waitFor({ timeout: 5000 });
    assertEq(await page.locator(".blockRowWrap.fileDragOver").count(), 0, "no row outlined");
    await page.locator(".noteSheetBar button[aria-label='Notebook view']").first().click();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 5000 });
    assertNoProblems(page);
    await ctx.close();
  });

  await step("picture: two people on one picture: a move made while the other crops it and types its caption keeps all three, and its undo keeps the crop; a crop that moves the frame keeps the part kept where it is on the picture moved meanwhile", async () => {
    // A shared workspace: alice owns it, bob edits (manage.py: admin-only setup).
    const out = await server.manage("create-workspace", "Picture team", "alice", "shared");
    const teamId = (out.match(/workspace (\S+)/) || [])[1];
    assert(teamId, `shared workspace id from: ${out}`);
    await server.manage("set-member", teamId, "bob", "editor");
    const aliceT = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: teamId });
    const bobT = Object.assign(Object.create(Object.getPrototypeOf(bob)), bob, { ws: teamId });
    const up = await aliceT.upload("/api/uploads", makePdf([["A page for two", "with a picture"]]), "two.pdf", "application/pdf");
    const paper = await aliceT.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Picture for two", source_url: up.source_url } });
    const { url } = await aliceT.upload("/api/upload-image", png(200, 100), "two.png", "image/png");
    const pic0 = normalizePicture({ url, x: 100, y: 200, w: 200, h: 100 });
    await aliceT.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [{ op: "insert", id: "picTwo", parent: paper.id,
      content: "", props: { picture: pic0, pdf_page: 1 } }] } });
    const shared = async () => picturesIn((await aliceT.api(`/api/blocks/${paper.id}/subtree`)).block.children).find((b) => b.id === "picTwo");
    const ctxA = await aliceT.context(browser), ctxB = await bobT.context(browser);
    const pageUrl = `${server.base}/?page=${paper.id}&ws=${teamId}`;
    const A = await openPage(ctxA, pageUrl), B = await openPage(ctxB, pageUrl);
    const el = (p) => p.locator('[data-picture-id="picTwo"]');
    await el(A).waitFor({ timeout: 30000 });
    await el(B).waitFor({ timeout: 30000 });
    // Where a screen shows the picture (upright), in points of the page.
    const shownAt = async (p) => {
      const pg = await p.locator('[data-page="1"]').boundingBox(), r = await el(p).boundingBox(), f = pg.width / 612;
      return { x: (r.x - pg.x) / f, y: (r.y - pg.y) / f, w: r.width / f };
    };
    const mouseDrag = async (p, from, dx, dy) => {
      await p.mouse.move(from.x, from.y);
      await p.mouse.down();
      await p.mouse.move(from.x + dx, from.y + dy, { steps: 8 });
      await p.mouse.up();
    };
    // bob, offline, crops by the corner `corner` dragged by dx, dy, so his
    // edit waits while alice moves the picture.
    const bobCrops = async (corner, dx, dy) => {
      await B.getByRole("toolbar", { name: "Edit picture", exact: true }).getByRole("button", { name: "Crop", exact: true }).click();
      await B.waitForSelector(`.pictureCrop .pictureCrop-${corner}`, { timeout: 5000 });
      await mouseDrag(B, centre(await B.locator(`.pictureCrop-${corner}`).boundingBox()), dx, dy);
      await B.keyboard.press("Enter");
      await until(async () => !(await B.$(".pictureCrop")), { what: "bob's crop applied" });
    };
    const k = (await A.locator('[data-page="1"]').boundingBox()).width / 612;

    await el(B).click();
    await B.waitForSelector('[data-picture-id="picTwo"].selected', { timeout: 5000 });
    await ctxB.setOffline(true);
    await bobCrops("se", -40, -20); // the right and bottom edges: its left and top stay
    await B.waitForEvent("requestfailed", { predicate: (r) => r.url().includes("/ops"), timeout: 8000 });
    const row = B.locator('.blockRowWrap[data-block-id="picTwo"]');
    await row.locator(".blockRendered").click();
    await row.locator(".blockEditorCm .cm-content").waitFor({ timeout: 5000 });
    await B.keyboard.type("Typed meanwhile");
    await closeEditor(B);
    await mouseDrag(A, centre(await el(A).boundingBox()), 120, 60);
    const moved = await until(async () => {
      const p = (await shared()).properties.picture;
      return p.x !== pic0.x ? p : null;
    }, { what: "alice's move on the server" });
    near(moved.x, pic0.x + 120 / k, "moved as far as the drag", 1.5);
    await ctxB.setOffline(false);
    const both = await until(async () => {
      const b = await shared();
      return b.content === "Typed meanwhile" && b.properties.picture.crop.w < 1 ? b : null;
    }, { what: "bob's crop and caption on the server", timeout: 15000 });
    const p1 = both.properties.picture;
    assertEq(JSON.stringify([p1.x, p1.y]), JSON.stringify([moved.x, moved.y]), "the move survives bob's crop");
    assertEq(JSON.stringify([p1.crop.x, p1.crop.y]), JSON.stringify([0, 0]), "bob's crop, from the top left");
    near(p1.w, pic0.w * p1.crop.w, "its frame shrunk to the part kept", 0.05);
    near(p1.h, pic0.h * p1.crop.h, "down too", 0.05);
    for (const [p, who] of [[A, "alice"], [B, "bob"]]) {
      await until(async () => {
        const s = await shownAt(p);
        return Math.abs(s.x - p1.x) < 1 && Math.abs(s.y - p1.y) < 1 && Math.abs(s.w - p1.w) < 1;
      }, { what: `the picture where alice put it, cropped as bob did, on ${who}'s screen` });
    }
    // alice's Ctrl+Z (her picture is selected) takes her move back, not bob's crop or caption
    await A.keyboard.press("Control+z");
    const p2 = await until(async () => {
      const p = (await shared()).properties.picture;
      return p.x === pic0.x ? p : null;
    }, { what: "alice's move undone" });
    assertEq(p2.y, pic0.y, "up too");
    assertEq(JSON.stringify([p2.w, p2.h, p2.crop]), JSON.stringify([p1.w, p1.h, p1.crop]), "bob's crop stays");
    assertEq((await shared()).content, "Typed meanwhile", "and his caption");
    assert((await A.locator(".statusPill").allInnerTexts()).join(" | ").includes("Undone: picture move"), "the undo names the move");

    // Both move its frame: bob's crop by the top left corner moves it too,
    // and his edit lands last: its shift goes onto the place alice moved it
    // to, so the part he kept stays where it is on her moved picture
    // (tests/shared/picturemerge.json).
    await until(async () => Math.abs((await shownAt(B)).x - p2.x) < 1, { what: "the undo on bob's screen" });
    await el(B).click();
    await B.waitForSelector('[data-picture-id="picTwo"].selected', { timeout: 5000 });
    await ctxB.setOffline(true);
    await bobCrops("nw", 30, 20);
    await B.waitForEvent("requestfailed", { predicate: (r) => r.url().includes("/ops"), timeout: 8000 });
    await mouseDrag(A, centre(await el(A).boundingBox()), -60, 40);
    const moved2 = await until(async () => {
      const p = (await shared()).properties.picture;
      return p.x !== p2.x ? p : null;
    }, { what: "alice's second move on the server" });
    await ctxB.setOffline(false);
    const last = await until(async () => {
      const p = (await shared()).properties.picture;
      return p.crop.x > 0 ? p : null;
    }, { what: "bob's second crop on the server", timeout: 15000 });
    const bobs = cropTo(p2, last.crop);
    assertEq(JSON.stringify(normalizePicture(last)), JSON.stringify(mergePicture(moved2, bobs, p2)), "bob's crop merged onto alice's move");
    assertEq(JSON.stringify([last.w, last.h]), JSON.stringify([bobs.w, bobs.h]), "the frame his crop made");
    near(last.x - moved2.x, bobs.x - p2.x, "moved by his crop from where alice put it", 0.02);
    near(last.y - moved2.y, bobs.y - p2.y, "down too", 0.02);
    for (const [p, who] of [[A, "alice"], [B, "bob"]]) {
      await until(async () => {
        const s = await shownAt(p);
        return Math.abs(s.x - last.x) < 1 && Math.abs(s.y - last.y) < 1 && Math.abs(s.w - last.w) < 1;
      }, { what: `the merged frame on ${who}'s screen` });
    }
    await B.keyboard.press("Escape");
    assertNoProblems(A);
    // The failed POSTs and the dropped socket are expected while offline.
    assertNoProblems(B, [/ERR_INTERNET_DISCONNECTED|Failed to fetch|WebSocket|net::ERR/]);
    await ctxA.close();
    await ctxB.close();
  });
}

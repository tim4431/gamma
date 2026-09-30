// The iPad's replica (src/replica/*, docs/dev/ipad.md) against a real
// server: the sync core the app runs in JavaScriptCore, driven here by an
// in-memory host (tests/replica/memoryHost.mjs) instead of the app's Swift
// one. No browser: the same rounds the desktop's mirror runs
// (backend/tests/test_mirror*.py pins the Python engine), here from the
// device's side — the first fill, pushes, merges of text and drawings, an
// edit beating a delete, deletions, a lost answer, a block moved between
// pages, receive-only.
import { Account } from "../harness.mjs";
import { MemoryHost } from "../../replica/memoryHost.mjs";
import { syncRound } from "../../../src/replica/round.js";
import { addNote, addSheet, createNotebook, deleteBlock, deletePage, saveInk, setText } from "../../../src/replica/edits.js";
import { pageView } from "../../../src/replica/views.js";
import { encodeStroke, newInk, serializeInk } from "../../../src/ink/ink.js";

const stroke = (id, x) => encodeStroke({ id, ch: "xypt", t0: 1757760000000, samples: [
  { x, y: 100, p: 0.4, t: 0 }, { x: x + 20, y: 110, p: 0.6, t: 12 }, { x: x + 40, y: 105, p: 0.5, t: 24 }] });

export async function replicaScenarios({ server, makePdf, step, until, assert, assertEq }) {
  server.manage("create-user", "replica", "replica-pw");
  const me = await new Account(server, "replica", "replica-pw").login();
  const token = (await me.api("/api/integrations/tokens", { method: "POST",
    body: { name: "iPad (test)", scope: "write", expires_in_days: 30 } })).token;
  const host = new MemoryHost({ base: server.base, token, remoteWs: me.ws, user: "replica" });
  const round = async () => {
    const status = await syncRound(host);
    if (status.last_error) throw new Error(`round: ${status.last_error}`);
    return status;
  };
  const there = async (id) => (await me.api(`/api/blocks/${id}/subtree`)).block;
  const kid = (tree, bid) => {
    const walk = (list) => { for (const b of list || []) { if (b.id === bid) return b; const x = walk(b.children); if (x) return x; } return null; };
    return walk(tree.children);
  };
  const ops = (page, list) => me.api(`/api/pages/${page}/ops`, { method: "POST", body: { client: "web", ops: list } });
  const uploadInk = async (ink) => {
    const r = await fetch(`${server.base}/api/upload-ink`, { method: "POST", headers: me.headers({ "Content-Type": "application/json" }), body: serializeInk(ink) });
    return (await r.json()).url;
  };
  const strokesThere = async (url) => (await me.api(url)).strokes.map((s) => s.id).sort();
  const local = async (id) => (await host.page(id)).snapshot;
  let paperId, noteId, inkId, inkUrl, bookId;

  await step("replica: the first round brings the workspace here, its files by hash", async () => {
    const up = await me.upload("/api/uploads", makePdf([["A paper to read on the train", "p. 1"]]), "train.pdf", "application/pdf");
    paperId = (await me.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Train paper", source_url: up.source_url } })).id;
    noteId = "rpNote1";
    inkId = "rpInk1";
    inkUrl = await uploadInk({ ...newInk(1, 612, 792), strokes: [stroke("a", 100)] });
    await ops(paperId, [{ op: "insert", id: noteId, parent: paperId, position: "a0", content: "the main claim", props: {} },
      { op: "insert", id: inkId, parent: paperId, position: "a1", content: "", props: { ink_url: inkUrl, pdf_page: 1, ink_strokes: 1 } }]);
    const status = await round();
    assert(status.pages_pulled >= 1, "pages came over");
    const view = pageView(await local(paperId), paperId);
    assertEq(view.kind, "pdf", "the paper is a PDF page here");
    assertEq(view.pdfInk[1][0].url, inkUrl, "its ink group, by PDF page");
    assert(host.files.has(`${up.doc_id}.pdf`) && host.files.has(inkUrl.split("/").pop()), "its PDF and drawing are here");
    const again = await round();
    assertEq(again.pages_pulled + again.pages_pushed, 0, "a second round moves nothing");
  });

  await step("replica: a notebook, a note and a drawing made here go there", async () => {
    await setText(host, paperId, noteId, "the main claim, checked", "the main claim");
    await addNote(host, paperId, { content: "written on the iPad", id: "rpNote2" });
    bookId = await createNotebook(host, { title: "Train notebook" });
    const view = pageView(await local(bookId), bookId);
    const sheet = view.sheets[0].id;
    await saveInk(host, bookId, { blockId: "rpBookInk", ink: { format: "gamma-ink", version: 1,
      space: { kind: "canvas", width: 595.28, height: 841.89 }, strokes: [stroke("n1", 60)] }, parent: sheet });
    const second = await addSheet(host, bookId);
    await round();
    const paper = await there(paperId);
    assertEq(kid(paper, noteId).content, "the main claim, checked", "the text edit arrived");
    assertEq(kid(paper, "rpNote2").content, "written on the iPad", "the new note arrived");
    const book = await there(bookId);
    assertEq(book.content, "Train notebook", "the notebook arrived under its id");
    assert(book.properties.notebook?.sheet, "with its paper");
    assertEq(book.children.map((c) => c.id).join(","), `${sheet},${second}`, "its two sheets, in order");
    const group = kid(book, "rpBookInk");
    assert(group && group.properties.ink_strokes === 1 && !("pdf_page" in group.properties), "the drawing, under its sheet");
    assertEq((await me.api(group.properties.ink_url)).space.kind, "canvas", "on the sheet's canvas");
    const again = await round();
    assertEq(again.pages_pulled + again.pages_pushed, 0, "settled: nothing moves");
  });

  await step("replica: one text and one drawing changed on both sides merge on both", async () => {
    await ops(paperId, [{ op: "set", id: noteId, content: "The main claim, checked", base: "the main claim, checked" }]);
    const theirs = await uploadInk({ ...newInk(1, 612, 792), strokes: [stroke("a", 100), stroke("x", 300)] });
    await ops(paperId, [{ op: "set", id: inkId, props: { ink_url: theirs, ink_strokes: 2 }, base_props: { ink_url: inkUrl } }]);
    await setText(host, paperId, noteId, "the main claim, checked twice", "the main claim, checked");
    await saveInk(host, paperId, { blockId: inkId, baseUrl: inkUrl, ink: { ...newInk(1, 612, 792), strokes: [stroke("a", 100), stroke("b", 400)] } });
    await round();
    const paper = await there(paperId);
    assertEq(kid(paper, noteId).content, "The main claim, checked twice", "both spans kept there");
    assertEq((await local(paperId))[noteId].content, "The main claim, checked twice", "and here");
    const url = kid(paper, inkId).properties.ink_url;
    assertEq((await strokesThere(url)).join(","), "a,b,x", "both sides' strokes there");
    assertEq((await local(paperId))[inkId].props.ink_url, url, "the same drawing here");
    assertEq(kid(paper, inkId).properties.ink_strokes, 3, "its count follows");
    inkUrl = url;
  });

  await step("replica: an edit beats a delete, both ways", async () => {
    await ops(paperId, [{ op: "insert", id: "rpKeep1", parent: paperId, position: "a5", content: "keep me", props: {} },
      { op: "insert", id: "rpKeep2", parent: paperId, position: "a6", content: "me too", props: {} }]);
    await round();
    await ops(paperId, [{ op: "delete", id: "rpKeep1" }]);              // deleted there …
    await setText(host, paperId, "rpKeep1", "keep me, edited here", "keep me"); // … edited here
    await deleteBlock(host, paperId, "rpKeep2");                          // deleted here …
    await ops(paperId, [{ op: "set", id: "rpKeep2", content: "me too, edited there", base: "me too" }]); // … edited there
    await round();
    const paper = await there(paperId);
    assertEq(kid(paper, "rpKeep1")?.content, "keep me, edited here", "the edit made here came back there");
    assertEq((await local(paperId)).rpKeep2?.content, "me too, edited there", "the edit made there came back here");
    assert(host.conflicts.some((c) => c.kind === "kept_local_edit" && c.block_id === "rpKeep1"), "the decision is recorded");
  });

  await step("replica: a push whose answer was lost is confirmed, not applied twice", async () => {
    let drop = true;
    host.hooks.request = async (method, path, send) => {
      const out = await send();
      if (drop && method === "POST" && path.endsWith("/ops")) { drop = false; throw new Error("the link dropped on the way back"); }
      return out;
    };
    await setText(host, paperId, "rpNote2", "written on the iPad, on the train", "written on the iPad");
    const cut = await syncRound(host);
    assert(cut.last_error.includes("link dropped"), "the round saw the dropped link");
    assertEq(kid(await there(paperId), "rpNote2").content, "written on the iPad, on the train", "the push had landed");
    await setText(host, paperId, "rpNote2", "written on the iPad, on the train home", "written on the iPad, on the train");
    host.hooks.request = null;
    await round();
    assertEq(kid(await there(paperId), "rpNote2").content, "written on the iPad, on the train home", "nothing doubled there");
    assertEq((await local(paperId)).rpNote2.content, "written on the iPad, on the train home", "nor here");
  });

  await step("replica: a block moved to another page there moves here, with what was typed here", async () => {
    const other = (await me.api("/api/pages", { method: "POST", body: { title: "Another page" } })).id;
    await round();
    await me.api(`/api/blocks/${noteId}/reorder`, { method: "POST", body: { parent_id: other, before: null, after: null } });
    await setText(host, paperId, noteId, "The main claim, checked twice. Moved?", "The main claim, checked twice");
    await round();
    await round(); // the page it left is looked at again once the move is known
    assert(!(noteId in (await local(paperId))), "gone from the page it left, here");
    assertEq((await local(other))[noteId]?.content, "The main claim, checked twice. Moved?", "here where it went, with the typing");
    assertEq(kid(await there(other), noteId)?.content, "The main claim, checked twice. Moved?", "and the typing went there too");
    assert(!kid(await there(paperId), noteId), "not back in the page it left, there");
  });

  await step("replica: pages deleted on one side go on the other", async () => {
    const theirs = (await me.api("/api/pages", { method: "POST", body: { title: "Deleted there" } })).id;
    await round();
    assert(await local(theirs), "the page is here");
    await me.api(`/api/blocks/${theirs}`, { method: "DELETE" });
    await deletePage(host, bookId);
    await round();
    assertEq(await local(theirs), null, "deleted there: gone here");
    const r = await me.api(`/api/blocks/${bookId}/subtree`, { raw: true });
    assertEq(r.status, 404, "deleted here: gone there (to its Recently deleted)");
    const again = await round();
    assertEq(again.pages_deleted, 0, "the tombstones are done with");
  });

  await step("replica: receive only keeps edits made here until it may push", async () => {
    host.config.mode = "pull";
    await setText(host, paperId, "rpKeep1", "keep me, edited here, offline", "keep me, edited here");
    const pull = await round();
    assertEq(pull.mode, "pull", "the round only received");
    assertEq(kid(await there(paperId), "rpKeep1").content, "keep me, edited here", "nothing went there");
    host.config.mode = "two-way";
    await round();
    assertEq(kid(await there(paperId), "rpKeep1").content, "keep me, edited here, offline", "sent once it may push");
  });
}

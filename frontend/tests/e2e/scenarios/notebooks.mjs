export async function notebookScenarios({ server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage }) {
  let ctx, page, id;
  await step("notebook: create, draw, append and reload stable sheets", async () => {
    ctx = await alice.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await page.getByText("New notebook", { exact: true }).click();
    await page.locator(".notebookPaper").waitFor();
    await page.getByRole("button", { name: "Handwriting tools", exact: true }).click();
    id = new URL(page.url()).searchParams.get("block");
    assert(id, "new notebook has a page id");
    const sheet = await page.locator(".notebookSheet").getAttribute("data-sheet-id");
    const paper = await page.locator(".notebookPaper").boundingBox();
    await page.mouse.move(paper.x + 60, paper.y + 100);
    await page.mouse.down();
    await page.mouse.move(paper.x + 180, paper.y + 150, { steps: 15 });
    await page.mouse.up();
    await until(async () => {
      const tree = (await alice.api(`/api/blocks/${id}/subtree`)).block;
      const group = tree.children.find((s) => s.id === sheet)?.children.find((g) => g.properties?.ink_strokes === 1);
      if (!group) return false;
      const ink = await alice.api(group.properties.ink_url);
      assertEq(ink.space.sheet_id, sheet);
      assertEq(ink.version, 2);
      return true;
    }, { what: "saved notebook ink" });
    await page.getByRole("button", { name: "Add page", exact: true }).click();
    await until(async () => (await alice.api(`/api/blocks/${id}/subtree`)).block.children.filter((s) => s.properties?.type === "notebook-sheet").length === 2);
    await page.reload();
    await page.locator(".notebookPaper").first().waitFor();
    assertEq(await page.locator(".notebookPaper").count(), 2);
    await until(async () => await page.locator(".notebookPaper .inkLayer path").count() === 1);
    assertEq(await page.locator(".notebookSheet").first().getAttribute("data-sheet-id"), sheet);
    assertNoProblems(page);
  });
  await step("notebook: paper settings persist and generated PDF downloads", async () => {
    await page.getByRole("button", { name: "Paper pattern", exact: true }).first().click();
    await page.getByRole("button", { name: "Grid", exact: true }).click();
    await until(async () => (await alice.api(`/api/blocks/${id}/subtree`)).block.children[0].properties.paper.pattern === "grid");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export PDF", exact: true }).click();
    const file = await download;
    assertEq(await file.failure(), null);
    assert(file.suggestedFilename().endsWith(".pdf"), "generated paper exported as PDF");
    assertNoProblems(page);
  });
  await step("notebook: concurrent ink is guarded, survives reload, and keeps both versions", async () => {
    await page.getByRole("button", { name: "Handwriting tools", exact: true }).click();
    const tree = (await alice.api(`/api/blocks/${id}/subtree`)).block;
    const original = tree.children[0].children.find((b) => b.properties?.ink_url);
    let raced = false, target;
    const routePattern = `**/api/pages/${id}/ops`;
    await page.route(routePattern, async (route) => {
      const payload = route.request().postDataJSON();
      const op = payload?.ops?.find((op) => op.op === "set" && op.props?.ink_url);
      if (op && !raced) {
        raced = true; target = op.id;
        await alice.api(`/api/pages/${id}/ops`, { method: "POST", body: { batch: "other-ink", ops: [
          { op: "set", id: op.id, props: original.properties, base_props: op.base_props },
        ] } });
      }
      await route.continue();
    });
    const box = await page.locator(".notebookPaper").first().boundingBox();
    await page.mouse.move(box.x + 70, box.y + 210); await page.mouse.down();
    await page.mouse.move(box.x + 210, box.y + 250, { steps: 12 }); await page.mouse.up();
    await page.getByRole("button", { name: "Keep both versions", exact: true }).waitFor();
    assert(raced, "other device changed the group before local save");
    assertNoProblems(page, [/POST .*\/ops -> 409/]);
    await page.unroute(routePattern);
    await page.reload();
    let lostRecoveryAck = false;
    await page.route(routePattern, async (route) => {
      const recovery = route.request().postDataJSON()?.ops?.find((op) => op.props?.ink_conflict);
      if (recovery && !lostRecoveryAck) {
        await route.fetch(); lostRecoveryAck = true; await route.abort("connectionfailed");
      } else await route.continue();
    });
    await page.getByRole("button", { name: "Keep both versions", exact: true }).click();
    await until(async () => lostRecoveryAck && await page.getByRole("button", { name: "Keep both versions", exact: true }).isEnabled());
    await page.reload();
    await page.getByRole("button", { name: "Keep both versions", exact: true }).click();
    await until(async () => {
      const tree = (await alice.api(`/api/blocks/${id}/subtree`)).block;
      const groups = tree.children[0].children;
      const theirs = groups.find((b) => b.id === target);
      const ours = groups.find((b) => b.properties?.ink_conflict?.source_block_id === target);
      assertEq(groups.filter((b) => b.properties?.ink_conflict?.source_block_id === target).length, 1, "retry after lost response never duplicates the recovery");
      return theirs?.properties.ink_url === original.properties.ink_url && ours?.properties.ink_url && ours.properties.ink_url !== theirs.properties.ink_url;
    }, { what: "both concurrent files referenced" });
    await until(async () => await page.locator(".inkConflicts").count() === 0);
    await page.unroute(routePattern);
    assertNoProblems(page);
  });
  await step("notebook: first stroke recovers after its block insert is interrupted by reload", async () => {
    const tree = (await alice.api(`/api/blocks/${id}/subtree`)).block;
    const count = tree.children[0].children.filter((b) => b.properties?.ink_url).length;
    let interrupted = 0, online = false;
    const routePattern = `**/api/pages/${id}/ops`;
    await page.route(routePattern, async (route) => {
      if (!online) { interrupted++; await route.abort("connectionfailed"); }
      else await route.continue();
    });
    await page.getByRole("button", { name: "Handwriting tools", exact: true }).click();
    const box = await page.locator(".notebookPaper").first().boundingBox();
    await page.mouse.move(box.x + 50, box.y + 310); await page.mouse.down();
    await page.mouse.move(box.x + 180, box.y + 320, { steps: 10 }); await page.mouse.up();
    await until(() => interrupted > 0);
    online = true;
    await page.reload();
    await until(async () => (await alice.api(`/api/blocks/${id}/subtree`)).block.children[0].children.filter((b) => b.properties?.ink_url).length === count + 1,
      { what: "first ink group restored from durable draft" });
    await page.unroute(routePattern);
    assertNoProblems(page);
  });
  await step("notebook: native recording metadata replays vectors and seeking restores the page", async () => {
    // A real, silent PCM file exercises media loading and seeking without a microphone.
    const wav = Buffer.alloc(44 + 8000 * 3 * 2);
    wav.write("RIFF", 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
    wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
    wav.write("data", 36); wav.writeUInt32LE(wav.length - 44, 40);
    const uploaded = await alice.upload("/api/upload-file", wav, "recording.wav", "audio/wav");
    const tree = (await alice.api(`/api/blocks/${id}/subtree`)).block;
    const group = tree.children[0].children.find((b) => b.properties?.ink_url);
    const ink = await alice.api(group.properties.ink_url);
    await alice.api(`/api/pages/${id}/ops`, { method: "POST", body: { batch: "recording-metadata", ops: [{
      op: "insert", id: "recording", parent: id, content: "Recording", props: { type: "audio",
        audio_segments: [{ id: "segment", url: uploaded.url, duration_ms: 3000 }],
        audio_events: [{ kind: "stroke", segment_id: "segment", block_id: group.id, stroke_id: ink.strokes[0].id,
          sheet_id: tree.children[0].id, start_ms: 1000, end_ms: 2000 }],
      },
    }] } });
    await page.reload();
    const audio = page.locator(".audioCard audio");
    await audio.waitFor();
    await until(() => audio.evaluate((el) => el.readyState >= 1));
    await until(async () => await page.locator(".notebookPaper .inkLayer path").count() >= 3);
    const count = await page.locator(".notebookPaper .inkLayer path").count();
    await audio.evaluate((el) => { el.currentTime = 0.2; });
    await until(async () => await page.locator(".notebookPaper .inkLayer path").count() === count - 1, { what: "future recorded stroke hidden" });
    await audio.evaluate((el) => { el.currentTime = 2.5; });
    await until(async () => await page.locator(".notebookPaper .inkLayer path").count() === count, { what: "recorded stroke restored by seeking" });
    await page.getByRole("checkbox", { name: "Replay handwriting" }).uncheck();
    assertNoProblems(page);
  });
  if (ctx) await ctx.close();
}

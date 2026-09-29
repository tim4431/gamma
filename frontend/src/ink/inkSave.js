// One commit contract for PDF and notebook handwriting. The expected URL is
// captured when editing begins; using the latest tree URL would hide a race.
export function inkProperties(ink, uploaded) {
  return {
    ink_url: uploaded.url, ink_strokes: ink.strokes.length,
    pdf_position: uploaded.pdf_position || null,
    pdf_page: ink.space.kind === "pdf-page" ? ink.space.page : null,
    sheet_id: ink.space.kind === "notebook-page" ? ink.space.sheet_id : null,
  };
}

export async function saveInk({ api, id, pageId, ink, baseUrl, batch, paper }) {
  const headers = { "Content-Type": "application/json" };
  const uploaded = await api("/api/upload-ink", { method: "POST", headers, body: JSON.stringify(ink) });
  const properties = inkProperties(ink, uploaded);
  const op = { op: "set", id, base_props: { ink_url: baseUrl ?? null }, props: properties };
  const ops = [];
  if (baseUrl === "") {
    // Inserts are create-if-absent on the server. This also recovers the
    // first stroke when a reload interrupted its queued placeholder insert.
    const sheet = ink.space.sheet_id;
    if (sheet && paper) ops.push({ op: "insert", id: sheet, parent: pageId, content: "", props: { type: "notebook-sheet", paper } });
    ops.push({ op: "insert", id, parent: sheet || pageId, content: "", props: { ink_url: "", ink_strokes: 0,
      ...(sheet ? { sheet_id: sheet } : { pdf_page: ink.space.page }) } });
  }
  ops.push(op);
  await api(`/api/pages/${encodeURIComponent(pageId)}/ops`, {
    method: "POST", headers, body: JSON.stringify({ batch, ops }),
  });
  return properties;
}

export function inkConflict(error) {
  return error?.status === 409 && (error.data?.conflict === "property_changed"
    || error.data?.detail?.conflict === "property_changed");
}

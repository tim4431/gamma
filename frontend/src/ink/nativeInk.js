// Only the native input surface is new. All requests use the web app's
// session/identity guard, workspace routing and ordinary open-format ink.
export function hasNativeInk(host = globalThis.window) {
  return !!host?.webkit?.messageHandlers?.gammaInk?.postMessage;
}

export function assertSameContext(before, now) {
  if (!now || now.readOnly || ["user", "workspace", "pageId", "pdfUrl"].some((k) => !before[k] || before[k] !== now[k])) {
    throw new Error("The account, workspace or document changed. Reopen handwriting in its original workspace.");
  }
}

export function checkNativeResult(request, result) {
  const ink = result?.ink;
  if (result?.requestId !== request.requestId || !/^[A-Za-z0-9_-]{1,64}$/.test(result.blockId || "") ||
      (result.expectedURL !== null && typeof result.expectedURL !== "string") ||
      (request.existing && result.blockId !== request.blockId && !(result.asCopy === true && result.expectedURL === null)) ||
      ink?.format !== "gamma-ink" || ink.version !== 1 || ink.space?.kind !== "pdf-page" ||
      ["page", "width", "height"].some((k) => ink.space[k] !== request.ink.space[k]) || !Array.isArray(ink.strokes)) {
    throw new Error("The iPad returned an invalid drawing.");
  }
  return ink;
}

// Native retains its sheet and durable draft until a save is acknowledged.
// Close keeps the draft; a failure lets the user retry the same save.
export async function presentNativeInk(request, save, host = window) {
  const handler = host.webkit.messageHandlers.gammaInk;
  let saving = false;
  const onSave = async (event) => {
    if (event.detail?.requestId !== request.requestId || saving) return;
    saving = true;
    try {
      const ink = checkNativeResult(request, event.detail);
      await save(event.detail, ink);
      await handler.postMessage({ action: "saved", requestId: request.requestId });
    } catch (error) {
      await handler.postMessage({ action: "failed", requestId: request.requestId, error: String(error.message || error) });
    } finally { saving = false; }
  };
  host.addEventListener("gamma-native-ink-save", onSave);
  try { await handler.postMessage({ action: "open", ...request }); }
  finally { host.removeEventListener("gamma-native-ink-save", onSave); }
}

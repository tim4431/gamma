// The account's AI models (docs/dev/ai.md "Models for each task"): what
// GET /api/ai/models says (`aiInfo`: the models every connection offers,
// with their facts, `roles`, each task's pick and the model it resolves
// to, and `missing`, the names of the picks no longer offered), the
// favorites the chat's model chip lists first, and the writes that change
// them (PUT /api/ai/models/roles). The picks live on the server, so every
// device and the server's own work (a clip's metadata) use them; nothing
// of them is kept in this browser.
//
// - The list is read again when the window comes back (at most every 15 s,
//   as prefs.js's focus pull), and after a write elsewhere asks
//   (`refresh()`: a connection saved, a key tested).
// - Which answers land, against the writes and an account switch, is
//   modelsSync.js's; a pick shows at once as the server will resolve it
//   (aiModels.js withPick).
// - A share link's visitor reads but never writes.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { modelFor as roleModel, withPick } from "./aiModels.js";
import { createModelsSync } from "./modelsSync.js";

const PULL_MIN_MS = 15000;

// `user` the signed-in account ("" for none), `shareMode` a share link's
// view, `onError(message)` a write the server refused.
export function useAiModels({ user, shareMode, onError }) {
  const [aiInfo, setAiInfo] = useState(null);
  // The last read failed: the chat, waiting to learn its AI before it shows
  // its tools, stops waiting.
  const [aiInfoFailed, setAiInfoFailed] = useState(false);
  const latest = useRef({});
  latest.current = { aiInfo, shareMode, onError };
  const [sync] = useState(() => createModelsSync({
    request: (path, init) => apiJson(`${API}${path}`, init),
    setInfo: setAiInfo,
    setFailed: setAiInfoFailed,
    onError: (message) => latest.current.onError?.(message),
    canWrite: () => !latest.current.shareMode,
    storage: () => { try { return window.localStorage; } catch { return null; } },
  }));

  useEffect(() => {
    const next = user || "";
    if (sync.user() === next) return;
    sync.switchTo(next);
    if (next) sync.refresh();
  }, [user, sync]);

  useEffect(() => {
    let last = Date.now();
    const wake = () => {
      if (document.hidden || !sync.user() || Date.now() - last < PULL_MIN_MS) return;
      last = Date.now();
      sync.refresh();
    };
    const onVisibility = () => { if (!document.hidden) wake(); };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [sync]);

  // A task's pick: a registry id, `engine:<id>` for translation, or "" to
  // follow the chat model (the chat itself: the first model offered).
  const setRole = useCallback((role, id) => sync.write("/ai/models/roles", "PUT", { [role]: id || "" },
    (info) => withPick(info, role, id || "")), [sync]);

  const toggleFavorite = useCallback((id) => {
    const on = (latest.current.aiInfo?.favorites || []).includes(id);
    return sync.write("/ai/models/roles", "PUT", on ? { favorites_remove: [id] } : { favorites_add: [id] },
      (info) => ({ ...info, favorites: on ? (info.favorites || []).filter((x) => x !== id) : [...(info.favorites || []), id] }));
  }, [sync]);

  return useMemo(() => ({
    aiInfo, aiInfoFailed, refresh: sync.refresh, setRole, toggleFavorite,
    models: aiInfo?.models || [],
    roles: aiInfo?.roles || {},
    favorites: aiInfo?.favorites || [],
    modelFor: (role) => roleModel(aiInfo, role),
    canWrite: !shareMode && !!user,
  }), [aiInfo, aiInfoFailed, sync, setRole, toggleFavorite, shareMode, user]);
}

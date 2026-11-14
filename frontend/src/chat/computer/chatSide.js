// Which side each page's chat shows, for the tab's life: "computer" for a
// page switched to it, else Gamma AI's; each shown chat hears a change. A
// module of its own, apart from useComputerChat.js, because the Background
// tasks rows (tasks/useAppJobs.js) are on the first screen and the computer
// side's state and its helpers are not: they load with ChatDock.
import { useCallback, useEffect, useState } from "react";

const sides = new Map();
const sideHeard = new Set();

// The page's chat on the computer side from outside it (a Background tasks
// row of a turn), before or while the chat shows the page.
export function showComputerSide(pageId) {
  if (!pageId) return;
  sides.set(pageId, "computer");
  for (const heard of sideHeard) heard();
}

// [side, setSide] for the page's chat: "ai" or "computer", the latter only
// while the page has a computer side (`available`).
export function useChatSide(pageId, available) {
  const [, redraw] = useState(0);
  useEffect(() => {
    const heard = () => redraw((n) => n + 1);
    sideHeard.add(heard);
    return () => { sideHeard.delete(heard); };
  }, []);
  const side = available && pageId && sides.get(pageId) === "computer" ? "computer" : "ai";
  const setSide = useCallback((next) => {
    if (!pageId) return;
    if (next === "computer") sides.set(pageId, next);
    else sides.delete(pageId);
    redraw((n) => n + 1);
  }, [pageId]);
  return [side, setSide];
}

// The agent each bucket's chat was picked with, for the tab's life, as the
// computer agent's model and effort are (chat/agents.js `id`: "gamma",
// "codex", "claude"); a bucket never picked shows chat/agents.js
// pickAgent's. Each shown chat hears a change. A module of its own, apart
// from useComputerChat.js, because the Background tasks rows
// (tasks/useAppJobs.js) are on the first screen and a computer agent's
// conversation state and its helpers are not: they load with ChatDock.
import { useCallback, useEffect, useState } from "react";

const picked = new Map();
const pickHeard = new Set();

// The bucket's chat shown with `agent` from outside it (a Background tasks
// row of a turn), before or while the chat shows the bucket.
export function pickChatAgent(bucket, agent) {
  if (!bucket || !agent) return;
  picked.set(bucket, agent);
  for (const heard of pickHeard) heard();
}

// [agent id or "", setAgent] for the bucket's chat.
export function useChatAgent(bucket) {
  const [, redraw] = useState(0);
  useEffect(() => {
    const heard = () => redraw((n) => n + 1);
    pickHeard.add(heard);
    return () => { pickHeard.delete(heard); };
  }, []);
  const setAgent = useCallback((agent) => pickChatAgent(bucket, agent), [bucket]);
  return [picked.get(bucket) || "", setAgent];
}

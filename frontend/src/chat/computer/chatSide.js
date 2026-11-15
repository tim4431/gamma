// The agent each bucket's chat was picked with, for the tab's life, as the
// computer agent's model and effort are (chat/agents.js `id`: "gamma",
// "codex", "claude"); a bucket never picked shows chat/agents.js
// pickAgent's. And the conversation a page's chat shows in place of its
// own, until it is left: its comment thread's (a thread's *Open in chat*,
// docs/dev/local_agents.md "A comment sent to an agent") or a co-author's
// chat with the reader's agent (History, Background tasks: "A co-author's
// turn"). Each shown chat hears a change. A module of its own, apart from
// useComputerChat.js, because the Background tasks rows
// (tasks/useAppJobs.js) and the comment threads are on the first screen and
// a computer agent's conversation state and its helpers are not: they load
// with ChatDock.
import { useCallback, useEffect, useState } from "react";

const picked = new Map();
const shownIn = new Map();   // bucket → {thread} | {conversation}: what it shows in place of its own
const pickHeard = new Set();
const tell = () => { for (const heard of pickHeard) heard(); };

// The bucket's chat shown with `agent`, before or while the chat shows the
// bucket.
function pickChatAgent(bucket, agent) {
  if (!bucket || !agent) return;
  picked.set(bucket, agent);
  tell();
}

// The agent picked for the bucket's chat in this tab, "" for none: the
// one a comment's *Send to* names when its thread has no agent's comment
// (comments/sendTo.js tickAgent).
export const chatPick = (bucket) => picked.get(bucket) || "";

// The bucket's chat shown on another conversation than its own, with
// `agent` (the conversation's): `shown` {thread} that comment thread's,
// {conversation} that co-author's chat; null shows its own again.
export function showInChat(bucket, shown, agent = "") {
  if (!bucket) return;
  if (shown?.thread) shownIn.set(bucket, { thread: shown.thread });
  else if (shown?.conversation) shownIn.set(bucket, { conversation: shown.conversation });
  else shownIn.delete(bucket);
  if (agent) picked.set(bucket, agent);
  tell();
}

// [agent id or "", setAgent] for the bucket's chat.
export function useChatAgent(bucket) {
  useHeard();
  const setAgent = useCallback((agent) => pickChatAgent(bucket, agent), [bucket]);
  return [picked.get(bucket) || "", setAgent];
}

// [what the bucket's chat shows in place of its own ({thread} or
// {conversation}), or null, leave]: leave() shows its own again.
export function useChatShown(bucket) {
  useHeard();
  const leave = useCallback(() => {
    if (shownIn.delete(bucket)) tell();
  }, [bucket]);
  return [shownIn.get(bucket) || null, leave];
}

function useHeard() {
  const [, redraw] = useState(0);
  useEffect(() => {
    const heard = () => redraw((n) => n + 1);
    pickHeard.add(heard);
    return () => { pickHeard.delete(heard); };
  }, []);
}

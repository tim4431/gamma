// The agent each bucket's chat was picked with, for the tab's life, as the
// computer agent's model and effort are (chat/agents.js `id`: "gamma",
// "codex", "claude"); a bucket never picked shows chat/agents.js
// pickAgent's. And the comment thread whose conversation a page's chat
// shows in place of its own (a thread's *Open in chat*, docs/dev/
// local_agents.md "A comment sent to an agent"), until it is left. Each
// shown chat hears a change. A module of its own, apart from
// useComputerChat.js, because the Background tasks rows
// (tasks/useAppJobs.js) and the comment threads are on the first screen and
// a computer agent's conversation state and its helpers are not: they load
// with ChatDock.
import { useCallback, useEffect, useState } from "react";

const picked = new Map();
const threads = new Map();   // bucket → the thread whose conversation it shows
const pickHeard = new Set();
const tell = () => { for (const heard of pickHeard) heard(); };

// The bucket's chat shown with `agent` from outside it (a Background tasks
// row of a turn), before or while the chat shows the bucket.
export function pickChatAgent(bucket, agent) {
  if (!bucket || !agent) return;
  picked.set(bucket, agent);
  tell();
}

// The agent picked for the bucket's chat in this tab, "" for none: the
// one a comment's *Send to* names when its thread has no agent's comment
// (comments/sendTo.js tickAgent).
export const chatPick = (bucket) => picked.get(bucket) || "";

// The page's chat shown on the conversation of its comment thread
// `threadId` with `agent` (the conversation's), from the thread.
export function showThreadChat(bucket, threadId, agent) {
  if (!bucket || !threadId) return;
  threads.set(bucket, threadId);
  if (agent) picked.set(bucket, agent);
  tell();
}

// [agent id or "", setAgent] for the bucket's chat.
export function useChatAgent(bucket) {
  useHeard();
  const setAgent = useCallback((agent) => pickChatAgent(bucket, agent), [bucket]);
  return [picked.get(bucket) || "", setAgent];
}

// [the thread whose conversation the bucket's chat shows, or "", leave]:
// leave() shows the bucket's own conversation again.
export function useChatThread(bucket) {
  useHeard();
  const leave = useCallback(() => {
    if (threads.delete(bucket)) tell();
  }, [bucket]);
  return [threads.get(bucket) || "", leave];
}

function useHeard() {
  const [, redraw] = useState(0);
  useEffect(() => {
    const heard = () => redraw((n) => n + 1);
    pickHeard.add(heard);
    return () => { pickHeard.delete(heard); };
  }, []);
}

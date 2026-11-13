// Where App mounts the feedback view (docs/dev/feedback.md "In the app"):
// it reads the dialog's store (feedback/useFeedback.js), which each opener
// writes where it is, and while a scope is open renders the dialog, fetched
// on first use (shared/ui/lazy.jsx). App hands it what the dialog needs of
// App's and keeps no state for it.
import React from "react";
import { LazyBoundary, lazySurface } from "../shared/ui/lazy";
import { closeFeedback, useFeedbackDialog } from "./useFeedback.js";

const FeedbackDialog = lazySurface(() => import("./FeedbackDialog"));

// `me`: the signed-in username; `canSend`: the person may edit; `links`:
// the desktop app's work-folder vaults by folder id (vault/useVault.js);
// onOpenThread(threadId, blockId, pageId): App's openThreadAt.
export default function FeedbackHost({ me, canSend, links, onOpenThread }) {
  const scope = useFeedbackDialog();
  if (!scope) return null;
  return (
    <LazyBoundary>
      <FeedbackDialog scope={scope} me={me} canSend={canSend} links={links} onOpenThread={onOpenThread} onClose={closeFeedback} />
    </LazyBoundary>
  );
}

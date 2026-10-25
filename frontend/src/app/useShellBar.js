// The iPad app's own bar above the page, the desktop app's server and
// workspace switcher (docs/dev/ipad.md "The chrome"). The person can hide it
// with its button or ⌘⇧B, and the View menu's Server bar row brings it back,
// since nothing of the shell is on screen then. Under a host that declares
// `shellBar`, the page asks how it stands when it loads, sets it with the
// row, and hears when it changes elsewhere: `{shown}` each way, the person's
// choice (the shell still shows the bar while it needs it). Null anywhere
// else, and until the shell has answered, so no row is offered.
import { useCallback, useEffect, useState } from "react";
import { nativeHost, onHostMessage, postToHost } from "../shared/lib/host.js";

export function useShellBar() {
  const offered = Boolean(nativeHost()?.shellBar);
  const [shown, setShown] = useState(null);
  useEffect(() => {
    if (!offered) return undefined;
    let live = true;
    const take = (msg) => { if (live && typeof msg?.shown === "boolean") setShown(msg.shown); };
    const off = onHostMessage("shellBar", take);
    postToHost({ type: "shellBar" }).then(take, () => {});
    return () => { live = false; off(); };
  }, [offered]);
  const set = useCallback((on) => {
    setShown(on);
    postToHost({ type: "shellBar", shown: on }).catch(() => {});
  }, []);
  return offered && shown !== null ? { shown, set } : null;
}

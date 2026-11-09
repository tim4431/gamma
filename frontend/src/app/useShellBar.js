// A native app's own bar above the page, the desktop app's server and
// workspace switcher (docs/dev/ipad.md "The chrome"). The person can hide it
// with its button (or ⌘⇧B on the iPad), and the View menu's Server bar row
// brings it back, since nothing of the shell is on screen then. Under a host that declares
// `shellBar`, the page asks how it stands when it loads, sets it with the
// row, and hears when it changes elsewhere: `{shown}` each way, the person's
// choice (the shell still shows the bar while it needs it). Null anywhere
// else, and until the shell has answered, so no row is offered.
// A hide the shell did itself tells the guide, which says once where that
// row is (`shellBar.hidden`, guide/tours/hints.js); the shell's first answer
// only reports how the bar already stands, and a hide from the row needs no
// hint. Either way the bar coming back retires it (`shellBar.shown`).
import { useCallback, useEffect, useRef, useState } from "react";
import { guideEvents } from "../guide/events.js";
import { nativeHost, onHostMessage, postToHost } from "../shared/lib/host.js";

export function useShellBar() {
  const offered = Boolean(nativeHost()?.shellBar);
  const [shown, setShown] = useState(null);
  const was = useRef(null);
  useEffect(() => {
    if (!offered) return undefined;
    let live = true;
    const take = (msg, fromShell = false) => {
      if (!live || typeof msg?.shown !== "boolean") return;
      if (fromShell && was.current !== null && was.current !== msg.shown) guideEvents.emit(msg.shown ? "shellBar.shown" : "shellBar.hidden");
      was.current = msg.shown;
      setShown(msg.shown);
    };
    const off = onHostMessage("shellBar", (msg) => take(msg, true));
    postToHost({ type: "shellBar" }).then(take, () => {});
    return () => { live = false; off(); };
  }, [offered]);
  const set = useCallback((on) => {
    was.current = on;
    setShown(on);
    if (on) guideEvents.emit("shellBar.shown");
    postToHost({ type: "shellBar", shown: on }).catch(() => {});
  }, []);
  return offered && shown !== null ? { shown, set } : null;
}

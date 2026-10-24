import React, { useCallback, useLayoutEffect, useRef } from "react";
import { useLeave } from "./leave.js";
import { arriving } from "./motion.js";

// The shells of the surfaces that come and go (docs/dev/ui-design.md,
// "Motion"): each arrives with its recipe in motion.css and leaves through
// useLeave. Their props go to the element they render.

// The backdrop of the one dialog surface ("Dialogs"): `.reportOverlay`,
// with its `.reportModal` (or the settings modal) inside. It fades in as
// the dialog grows from its middle, and they leave together. A class goes
// beside reportOverlay; the backdrop press (backdropPress) and
// data-popover go on it.
// A dialog opened inside one still arriving (Settings opened straight onto
// an editor, the chat's AI tiles) waits for that one to land, then arrives:
// growing together, its backdrop would be laid out inside the growing
// dialog and snap to the window as it landed, under a second dim fading
// in at once (motion.css [data-wait]).
export const DialogOverlay = React.forwardRef(function DialogOverlay({ className = "", children, ...rest }, ref) {
  const own = useRef(null);
  const setRef = useCallback((node) => {
    own.current = node;
    if (typeof ref === "function") ref(node);
    else if (ref) ref.current = node;
  }, [ref]);
  const leave = useLeave("dialog", setRef);
  useLayoutEffect(() => {
    const el = own.current;
    const around = el?.parentElement ? arriving(el.parentElement) : [];
    if (!around.length) return undefined;
    el.dataset.wait = "";
    let mounted = true;
    Promise.all(around.map((a) => a.finished.catch(() => {}))).then(() => { if (mounted) delete el.dataset.wait; });
    return () => { mounted = false; };
  }, []);
  return (
    <div ref={leave} className={`reportOverlay ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
});

// A popover under the button that opens it: `.popover`, which grows from
// that button's corner (`.popUp`: one that opens upward) and shrinks back
// into it. `as` renders it as another element, MenuScope for a popover
// whose rows are a menu.
export const Popover = React.forwardRef(function Popover({ as: As = "div", className = "", children, ...rest }, ref) {
  const leave = useLeave("surface", ref);
  return (
    <As ref={leave} className={`popover ${className}`.trim()} {...rest}>
      {children}
    </As>
  );
});

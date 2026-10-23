import React from "react";
import { useLeave } from "./leave.js";

// The shells of the surfaces that come and go (docs/dev/ui-design.md,
// "Motion"): each arrives with its recipe in motion.css and leaves through
// useLeave. Their props go to the element they render.

// The backdrop of the one dialog surface ("Dialogs"): `.reportOverlay`,
// with its `.reportModal` (or the settings modal) inside. It fades in as
// the dialog grows from its middle, and they leave together. A class goes
// beside reportOverlay; the backdrop press (backdropPress) and
// data-popover go on it.
export const DialogOverlay = React.forwardRef(function DialogOverlay({ className = "", children, ...rest }, ref) {
  const leave = useLeave("dialog", ref);
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

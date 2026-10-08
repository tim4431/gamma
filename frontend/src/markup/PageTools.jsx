// The page tools every surface's layers read (markup/MarkupLayers.jsx): a
// PDF page, a sheet in the notebook view and a sheet among the notes all
// take the same tools from here. App owns the state and the handlers and
// provides PageToolsContext around the viewer and the notes
// (docs/dev/handwriting.md).
import { createContext, useContext } from "react";

// {readOnly, ink: {tool, penTool, penOnly, pressure, eraserMode, eraserSize,
// lassoMode}, text: {armed, style}, actions}. ink.tool is the armed tool and
// ink.penTool what a stylus writes with when nothing is armed; both are null
// on a read-only page and while the Text tool is armed. text.armed: the Text
// tool is armed; text.style: the style of new boxes ({size, color, bg}).
// actions carries the handlers: the ink ones (onStroke, onErase,
// onErasePartial, onSelect, onAction, onMoveSelection, onFingerScroll),
// onJump (show a mark's block in the notes) and the text boxes'
// (markup/useTextBoxes.js) — one object for App's life whose functions call
// the latest handlers (shared/lib/stableActions.js), so the context value
// changes only with the tools and a memoized page never re-renders for a
// new closure.
export const PageToolsContext = createContext(null);
export const usePageTools = () => useContext(PageToolsContext);

// The classes a surface's container takes for the armed tool (app.css,
// markup.css): inkArmed, inkTouchDraw when a finger draws too, textArmed.
export const armedClasses = ({ ink, text }) => (ink.tool ? " inkArmed" : "") + (ink.tool && !ink.penOnly ? " inkTouchDraw" : "")
  + (text.armed ? " textArmed" : "");

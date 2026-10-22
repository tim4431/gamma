// The handwriting commands' context (app/appCommands.js `ink.copy`,
// `ink.cut`, `ink.paste`, `ink.duplicate`, `ink.selectAll`;
// docs/dev/handwriting.md "Client"), pure: the fields App's command
// context (`appCmdRef`) carries for them, from the strip's state and the
// lasso's (useInkStrip.js, useInkSelection.js). frontend/tests/
// inkCommands.test.mjs runs them through APP_COMMANDS.
//
// The guards: inkOpen, the strip is open; inkTool, a tool other than the
// hand is armed; inkSelected, the lasso holds strokes on a page that can
// be edited; inkClip, the handwriting clipboard holds strokes. The
// actions run the selection's with the render's state. Copy, cut and
// select all answer whether they did anything, so a key that did nothing
// stays the browser's; paste and duplicate answer nothing, so their key
// stays claimed.
export function inkCommandContext({ inkUi, inkTool, inkSelection, readOnly, inkClip },
  { copyInkSelection, pasteAtCentre, duplicateInkSelection, selectAllInk }) {
  return {
    inkOpen: inkUi.open, inkTool: !!inkTool, inkSelected: !!inkSelection && !readOnly, inkClip,
    inkCopy: () => copyInkSelection(false),
    inkCut: () => copyInkSelection(true),
    inkPaste: () => { pasteAtCentre(); },
    inkDuplicate: () => { duplicateInkSelection(); },
    inkSelectAll: () => selectAllInk(),
  };
}

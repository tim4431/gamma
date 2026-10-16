# Menus seen on the wrong side for a frame

The question: menus across the app were first seen on the wrong side of
what they opened from, and then jumped to the right one. The recording
player's menus were fixed on their own the day before. Which surfaces do
this, why, and what is the one fix? Measured in October 2026. The rule
now in force is in
[dev/ui-design.md](../dev/ui-design.md#menus-and-popups-open-in-place).

## How it was measured

An isolated server (the e2e harness) and headless Chromium. A probe opened
each surface and recorded, frame by frame, where every floating element
stood and whether it was visible.

The first version sampled in `requestAnimationFrame`. That reads the page
too early: a frame runs its animation callbacks, then layout, then
ResizeObserver callbacks, then paints. So it missed corrections made in the
same frame and reported jumps that were never painted.

The probe that counts samples from a ResizeObserver made during that frame.
Observers are called in the order they were made, so this one runs after
the app's own, just before the paint. A jump is a visible element moving
between two frames without changing its size or its text. A move together
with a resize is one placement, made in one frame, and is not a jump. The
browser suite keeps this as `assertOpensInPlace` (`tests/e2e/harness.mjs`).

## Finding: a position set after the paint shows a frame of the old one

React 18 renders a state update straight away, before the paint, only
when it comes from:
- a layout effect, or
- a discrete event (a click, a key).

An update from any of these renders in a later task, after the browser has
painted the frame:
- a ResizeObserver,
- a scroll or resize listener,
- `requestAnimationFrame`,
- a timer,
- a passive effect.

A surface positioned from one of those therefore shows one frame where it
stood before. The pattern had spread through several features:

| Surface | What was set after the paint | Measured before the fix |
|---|---|---|
| Handwriting and text box menus (`useSelectionMenuAnchor`, from "ink improve", September 2026, shared with text boxes later) | The menu's height. The hook guessed 96 px and picked above or below from the guess; a ResizeObserver then reported the real 54 px. | Every open. With the stroke near the top of the view: painted below it at y 262, then above at y 125 the next frame. That is the reported flash. With room above: painted 42 px too high, then moved. |
| The same menus, opening Color or Width | The menu's new position after it grew. | One frame grown downward from the old top (54 to 137 px tall, reaching over the selection), then moved up. |
| `ContextMenu` after a size change (its ResizeObserver, from "ai chat model improve", September 2026) | The re-placement. | One stale frame whenever an open menu changes size: a filtering model list, a menu that opens upward and grows. |
| PDF selection popup (`TipFrame`) | The flip above when a streaming translation grows it. | One frame past the window's bottom, then flipped. |
| Caret popups (`useCaretAnchored`: "/", `[[`, `@`, LaTeX completion and preview) | Re-placement after a size change or a scroll, through a ResizeObserver and then a `requestAnimationFrame`. | One or two stale frames. The first open was clean: its dependencies re-run a layout effect. |
| Guide card (`GuideOverlay`) | The anchor's box, measured in a passive effect. | On Adding to your library's last Next: the new card painted at x 548 against the last step's anchor, then at x 669 beside its own. |

These surfaces were already clean and stay as they are:
- placed in their event handler or timer before they render: the
  handwriting strip's tooltips, the note badge tip, the citation preview,
  the metadata popover;
- placed by CSS under their button: every topbar popover;
- flyouts flipped in a layout effect (`SubMenuItem`);
- the touch editing bar, which stays transparent while it moves.

The recording player's earlier fix kept `ContextMenu` hidden until it was
first placed. That covers the first frame only: the size-change path above
still painted stale frames.

## What was built

`shared/ui/placement.js` is one way to place a floating surface:
- `placeFloating` holds the geometry: its own side, then the other side of
  what it opens from (`flipY`) when its own has no room, then clamped
  inside the window and a band (`bounds`).
- `useFloatingPosition` places in a layout effect and returns null until it
  has, while the surface stays hidden. Every later re-placement, from a
  ResizeObserver, a resize, or with `follow` a scroll, goes through
  `flushSync`, so it lands before that frame is painted.

`ContextMenu`, `TipFrame` and `useCaretAnchored` are built on it. The
selection menus no longer guess a height: they pass the selection's edges,
and the menu picks the side from its own measured size. Their scroll
following uses `flushSync` too. The guide measures its anchor in a layout
effect, and its frame-throttled re-measures commit with `flushSync`.

Measured after the fix, on the same probes: no jump anywhere. The ink menu
opens on its final side from its first visible frame. Opening Color grows
and moves the menu in one frame. Every step of the guide card opens beside
its own anchor.

## Still open

- `SubMenuItem` flips and clamps only when it opens. A flyout whose content
  grows later (typing a new label) is not placed again; this overflows
  rather than flickers.
- The note badge tip and the citation preview pick their side from a
  height guessed before render. A tall note or quote can run off the
  window; nothing corrects it.
- An options row that no longer fits above the selection moves the
  handwriting menu below it. That is one frame and the correct side for the
  new size, but the menu changes sides under the finger. Keeping the side
  it opened on would need its own rule.

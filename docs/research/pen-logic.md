# What a pen does with nothing armed

Should a stylus always write? Until November 2026 it did: with nothing armed
it wrote with the last pen, so on an iPad or a tablet a stylus could not
select text at all. This note records why the rule changed to "a pen is what
is armed", what the browsers and the two shells allow, and which further
improvements survived checking. The mechanics are in
[dev/handwriting.md](../dev/handwriting.md) "Claiming input".

Sources: the code as of November 2026, WebKit at commit `58fe6ea4` (main,
5 November 2026) and Chromium and AOSP main as read on 6 November 2026.
Neither is the build on a shipping device, and nothing here was run on a real
iPad or tablet. "Inferred" marks what could only be reasoned from the source.

## The rule

- An ink tool armed: the pen is that tool.
- Nothing armed on a PDF page: the pen does what a mouse does. A drag selects
  text, the text highlighter highlights it at the lift, and in rectangle mode
  the drag draws the rectangle.
- Nothing armed where there is no text (paper, the zoom window's panel): it
  writes. This is a choice, not a consequence: see [Open decisions](#open-decisions).

*Stylus draws on PDFs right away* keeps the old behaviour on PDFs, off by
default.

## What decided the design

- **A changed default would have reached no one.** `usePersistedState` stores
  a preference on mount, default or not, so every browser had stored the on
  of `gamma-ink-auto-pen` and it could not tell a choice from the default. The
  preference has a new key, and the old one is left behind unread.
- **A stylus selects nothing by itself.** On non-editable text a stylus pans
  in both engines (inferred on each: no source states it directly). Chromium's
  own stylus selection needs a button held at the down and swallows the
  stream, so it is no help, and the Android shell's rewrite of that stroke to
  an eraser is what keeps it from taking the barrel
  ([`stylus_text_selector.cc`](https://github.com/chromium/chromium/blob/main/content/browser/renderer_host/input/stylus_text_selector.cc)).
  So the selection is made in the page, from the caret under the pen, one path
  for every device.
- **The pan.** iPadOS decides from the first `touchmove` alone; preventing a
  later one does nothing
  ([WebKit bug 243250](https://bugs.webkit.org/show_bug.cgi?id=243250)). In
  Chromium a touch whose `touchstart` went through needs every `touchmove`
  cancelled: the first that is not starts a scroll, and the pointer is cancelled
  ([`input_router_impl.cc`](https://github.com/chromium/chromium/blob/main/components/input/input_router_impl.cc#L232)).
  `touch-action` cannot do this: it applies to fingers too, and it cannot change
  once a gesture has begun
  ([Pointer Events](https://w3c.github.io/pointerevents/)).
- **Cancelling from `touchstart`, not only `touchmove`.** The first version left
  `touchstart` free so a tap would click. On an iPad a Pencil tap with any
  position change fires a `touchmove`, whose cancel probably kills the click
  (inferred), a tap's small movement being the usual case. It also left the
  system's long-press selection armed in both engines, competing with the page's
  ([`touch_disposition_gesture_filter.cc`](https://github.com/chromium/chromium/blob/main/ui/events/gesture_detection/touch_disposition_gesture_filter.cc)).
  And WebKit's tests of a cancelled `touchmove` use finger streams, while
  cancelling from `touchstart` is what an armed pen has always done on a real
  Pencil. So a pen's touch is cancelled the same way everywhere, and the page
  sends a tap's click itself, where the pen sends touch events at all.
- **Palms have no help from the shells.** `PencilCapture` and `PenRouter` guard
  only a claimed stroke, and an unclaimed pen is passed through untouched, so the
  page has to hold fingers as palms for the whole drag.
- **A palm is a palm wherever it lands.** The first version kept the pen's state
  per page. A palm on the next page or in a gap then counted as the second finger
  of a `viewerZoom` pinch, and it is the usual posture when selecting low on a
  page. So there is one fact, whether a pen is down on any layer, which every
  layer, the finger chords and `viewerZoom` read.
- **The caret over a gap.** Over a gap between spans both engines may answer the
  top of the text layer, because pdf.js's spans are out of flow
  ([WebKit](https://github.com/WebKit/WebKit/blob/58fe6ea4b621a1b4c53ba42ce2c493971159630d/Source/WebCore/rendering/RenderBlockFlow.cpp#L4043-L4050),
  [Blink](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/layout/physical_box_fragment.cc#L1541)).
  Gamma's bare `TextLayer` has no `endOfContent` guard against it, so a caret
  counts only in a text node of the layer.
- **The hand is a tool to go back to.** Once a hand and a pen differ, a Pencil
  double tap from the hand to the eraser and back has to end on the hand, or the
  pen starts writing by itself.

## What only a device can settle

Run on desktop engines, Chromium 153, Firefox 155 and WebKit 26.6 (WebKit on both
caret calls, since iPadOS before 26.2 has only the older): `penSelect.js` and the
hit-test rule over a layer shaped as pdf.js builds it, scaled absolute spans with a
link box, a highlight and an ink path above them. Selections ran under the overlays
and across lines, a gap kept the focus, and a tap selected nothing. That covers the
engines' hit tests, not a Pencil, UIKit or a tablet's input stack:

- Whether a selection set from script shows on an iPad with no earlier tap. UIKit
  draws it, not WebKit (`ENABLE_TEXT_SELECTION` is 0 on iOS), it activates when the
  content view becomes first responder, and a Pencil's cancelled touch never makes
  it so. This is the largest unknown. If it does not show, the page would have to
  draw the selection from the range's rects and make the real one at the lift. It
  is also why the text highlighter's colour does not show during a drag there.
- Whether the Pencil's pointer events run through a cancelled touch for a drag as
  they do for a stroke. They should: it is the same path.
- What a palm down before the pen does on either platform, and how far system palm
  rejection goes. Apple publishes nothing on it.
- What *Only Draw with Apple Pencil* and Scribble change over a PDF's text.

These are device check 36 in [dev/ipad.md](../dev/ipad.md) and check 25 in
[dev/android.md](../dev/android.md).

## Open decisions

These change behaviour someone may rely on, so they are the developer's.

1. **Paper with nothing armed.** It writes today, as it did before, since a
   notebook is for writing and there is nothing to select. The strict reading of
   the rule says it should not. If it should not, a pen needs another use there
   (scrolling, which needs `onTouch` to let it through) and notebooks need a way
   to start armed.
2. **The double tap from the hand.** Apple's default, `switchEraser`, goes hand,
   eraser, hand and never reaches a pen, and Android's button does the same, so the
   default gesture cannot start writing. Limited to the `system` path it would keep
   an explicit *Eraser* choice intact, but it departs from Apple's meaning of the
   action.
3. **Keeping the pen armed across a page switch.** Leaving a page disarms the tool,
   which used to cost nothing and now turns the next drag into a selection. Keeping
   it means a pen landing on a paper opened only to read writes on it, and a tap on
   a link leaves a mark. The other way round the cost is a stray selection, which
   is harmless. No reason for the disarm is recorded.

## Improvements that survived checking

Each was checked against the code by a second reader trying to refute it, and most
came back narrower than proposed. Effort is the checker's.

**Small, web only**

- **Tell people moving from the old default.** Their pen changes meaning with no
  word. Show one pill, once ever, at the first drag with nothing armed, only in a
  browser that still holds `gamma-ink-auto-pen`, then remove that key: "Nothing is
  armed, so the pen does not write", with *Write with pen*. It fires from the pen's
  selection path, whether or not it selected, since writing over a text PDF selects
  and that is the common case.
- **Undo the highlight the pen just made.** With the text highlighter on, a wrong
  drag makes a highlight at once, while the Pencil's Undo and the finger chords step
  the stroke history and take an older stroke instead. Give *Highlight saved.* an
  Undo through `undoableStatus`, give the made highlight's bar a Delete, and have
  the gesture and the chords undo that one highlight until the next stroke or tool.
  A real merge of the two histories is not possible: the block history keeps no
  count, and one ref cannot order two stacks.
- **A dismissing tap and a quick erase leave nothing behind.** With a pen armed, a
  pen tap that only lets go of a finger's selection still commits a dot, which can
  start a new group. A held squeeze from the closed strip leaves it open. The
  checker found nothing to correct in this one.
- **Show what a gesture switched to.** A tool switch is silent on every Pencil but
  the Pro. Show the tool's name briefly under the strip, and give the `ui` path the
  tick it lacks when it arms a tool.
- **Show on hover that the pen will not write.** An I-beam over a text span, a faint
  dot otherwise, with no caret hit test and only with no button down. Depends on
  hover reaching the page, which is unverified on both platforms.
- **Keep the last pen over a reload.** `inkUi.pen` is session state, so the strip
  reopens on the first pen. A reload is not rare if WebKit drops the process.

**Small, after a device check**

- **Android's button click.** Swap the defaults so a click goes to the last tool,
  the hand included, which is the cheapest way to arm from the pen. The checker
  corrected the claim that it toggles hand and pen: after pen then highlighter it
  toggles those two. Whether Samsung's Air Command takes the click is unknown.
- **A palm just after the pen.** With a stroke tool armed, a touch within 300 to
  500 ms of the last contact gets no tap, no held selection and no zoom move. Leave
  the chords alone, or undo right after a stroke breaks, and do not count hover.
  Whether iPadOS delivers such a touch at all is unproven.
- **Android: a finger after a claim** stays swallowed until the whole gesture ends.
  First record whether a finger and a stylus ever share a stream, since on separate
  digitizers the system holds it anyway; if they do, take the iPad's rule.

**Medium**

- **Auto-scroll at the viewer's edge.** A selection stops at what shows, and the pan
  is off. A frame loop in `beginPenSelect`, stopped before the anchor's page can
  drop its text layer. Do not gate selection on the press landing on text: a press
  in a gap is meant to select, as a mouse's does.
- **A palette at the tip,** for `showContextualPalette` only, placed from the hover
  pose the shell already reads. The shell must tell that action apart: it folds it
  into `showInkAttributes` today.
- **A pen that pans on a page with no text at all,** the scanned PDF, keeping `mode:
  "page"` so palms stay held. Needs a device spike first: nothing shows a clean pan
  starts after a pen's pointer events.

**Android, unproven on hardware**

- **Back from a stroke begun at the edge.** A band from `setSystemGestureExclusionRects`
  by a hovering pen. Reach is small: Galaxy tablets default to the button bar, and
  Samsung's *Block gestures with S Pen* covers it. Reproduce on an emulator first.

**Rejected**

- Letting a pen pan whenever its press misses text: it breaks selecting from a margin
  or a gap, and dropping `mode: "page"` lets a palm and the pen start a pinch.
- Deciding each Android finger by hover and a size threshold: it would drop the
  two-finger undo right after a stroke, the case people want most.
- A general order over the block and stroke histories: see above.

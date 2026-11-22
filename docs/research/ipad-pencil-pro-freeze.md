# The Pencil Pro freeze on an iPad Air

Open as of 2026-11-22. On an iPad Air with an Apple Pencil Pro, the page in
the iPad app stops answering taps from the Pencil's first contact. Nothing
here has reproduced it: no Pencil Pro, and no simulator sends Pencil
touches. This note keeps the reports, what each test ruled out, what is
left, and how to go on. The mechanics it names are in
[dev/ipad.md](../dev/ipad.md) ("The pen", "The touch report", "Tests").

## The reports

**2026-11-13, iPad Air (M2, M3).** After the Pencil tapped the strip's pen
or eraser, or a double tap armed the eraser, neither a finger nor the
Pencil did anything on the PDF, and the double tap stopped too. On those
iPads only an Apple Pencil Pro has a double tap. The page-side fix,
0ad3a5f6 in 0.4.0, assumed WebKit sometimes drops the Pencil's
`pointerup`. It ends a pen's contact when the pen hovers or goes down again
(`penLifted`, [dev/ipad.md](../dev/ipad.md) "A lift that never comes").
No device confirmed that cause.

**2026-11-21, on 0.4.7.** An iPad Air 11-inch (M3, the 7th generation), an
Apple Pencil Pro, iPadOS 27.2.1, This iPad's server. From the Pencil's
first contact, on any page and whatever tool is armed (none included), the
page answers no tap, the Pencil's or a finger's.

**2026-11-22, the reporter's answers:**

- The Pencil's hover still reaches the page: Settings rows highlight under
  it.
- The shell's bar answers both a finger and the Pencil. Its Reload works,
  but the page stays deaf after the reload.
- Toggling Scribble did not help. It was toggled after the freeze, which
  the freeze outlives anyway.
- The settings: Pen only (fingers scroll and zoom), *Stylus draws on PDFs
  right away* off, *Draw and hold* on, *Hover preview* on, *Undo with
  fingers* on a double tap.

**The developer's iPad:** an iPad Pro 11-inch (M2) with an Apple Pencil
(2nd generation). It hovers too, and nothing freezes there.

## Ruled out

- **The web app's state.** A reload starts the web app afresh, and the
  freeze outlives it. That rules out `penLifted` and every pen hold on the
  page: the ink layer's drag and claims (`hostOwned`, `hostClaims`), the
  PDF's pen selection, the region and order drags, and the finger chords.
- **The shell's Pencil capture left open** (`PencilCapture`). While a
  capture is open the recognizer tracks every finger, the bar's included,
  and the bar answers a finger. With nothing armed the page claims no
  stroke, so no capture ever opens.
- **The shell's edit menu.** `ShellWebView.editMenuShown` goes off on a
  PDF selection, and a reload leaves it off, since the page says nothing
  as it unloads. In the iPad Air 11-inch simulator a finger's selection
  of PDF text turned it off, and taps still answered with the selection
  up, after it was gone, and after ⌘R (`TapsTests`). That rules out its
  `canPerformAction` and `buildMenu` overrides as the cause of dead taps
  on iPadOS 27.2. It is still a defect that the menu stays off across
  reloads.
- **The live line view.** It takes no touches (`isUserInteractionEnabled`
  is false).
- **The hover classes.** `inkHovering` only hides the cursor.
- **Haptics.** The page asks for one only on a held shape, a pen lasso's
  selection or a gesture's tool switch, not on a contact.
- **A non-finite sample.** The roll is checked and the force guarded.
  The bridge does drop a message it cannot encode, silently: `deliver`'s
  `try?` around `JSONEncoder`. That would lose a `stroke`, but the freeze
  needs no claim.

## What is left

- **WebKit's touch handling in the page's web view**, left in a state no
  reload resets. The content view and its recognizers outlive a reload.
  The bar is another web view, with recognizers of its own, and it keeps
  working. Hover goes through recognizers of its own and keeps working.
  WebKit has open bugs of this family, where pointer events stop for good
  after a dismissed UIKit menu or a system gesture and only a reload
  recovers: [257735](https://bugs.webkit.org/show_bug.cgi?id=257735) and
  [236390](https://bugs.webkit.org/show_bug.cgi?id=236390) (reported on
  an iPad Air). Neither involves the Pencil, and nothing public matches
  this freeze.
- **A native piece every Pencil touch on the page reaches.**
  `PencilCapture` records each Pencil touch on the page, ignores the
  bar's, and stays `possible` for it. `UIPencilInteraction`, on the web
  views' container, puts a `UIHoverGestureRecognizer` there; the first
  version of `TouchReportTests` found it.
- **What the reporter's hardware adds.** A Pencil Pro adds squeeze, barrel
  roll (an estimated touch property) and haptics over a 2nd-generation
  Pencil. An iPad Air's screen runs at 60 Hz, a Pro's at 120.

## The touch report

The shell writes `touch-report.log` beside the local server's log at every
Reload ([dev/ipad.md](../dev/ipad.md) "The touch report"). It covers the
device, the Pencil's system settings, `PencilCapture`'s state, every
recognizer and interaction from the window down with its state, and the
last 100 touch lines. Each line gives a touch's begin, or a Pencil touch's
end, cancel or reset, with every recognizer UIKit gave it.

To get one from the reporter, on a TestFlight build that has it:

1. Launch the app, touch the page once with the Pencil, and check that
   taps are dead.
2. Tap the bar's Reload once.
3. Open the launcher (the bar's mark, or ⌘⇧L), press *Show server log* on
   This iPad's row, and share `touch-report.log` from Files.

A report from a session that has not frozen, from the same iPad or any
other, is the baseline to compare with. In the frozen report, look for:

- a recognizer on the page's `WKContentView` that is not `possible` and
  takes no touch: a `WKDeferringGestureRecognizer`,
  `WKSyntheticTapGestureRecognizer` or `WKTouchEventsGestureRecognizer`
  left `began`, `changed` or `ended`;
- touch lines after the freeze whose recognizers differ from the
  baseline's;
- the Pencil touch's own lines: an `ended` without a `reset`, or an
  `expecting` (estimated properties still to come) on its end.

## Next questions for the reporter

1. **Safari on the same iPad.** Open any page, touch it with the Pencil,
   then tap links. If Safari freezes too, the bug is WebKit's or the
   system's, and the app needs a workaround. If not, something of the
   shell's is part of it.
2. **Scribble off before the first contact.** Quit the app, turn Scribble
   off, launch, then touch.
3. **The system's *Only Draw with Apple Pencil*,** on and off, from a
   fresh launch.
4. **Quitting the app** (swiped away): does it clear the freeze?

## Mimicking the reporter's iPad here

The iPad Air 11-inch (M4) simulator has the M3's screen: 1180 × 820
points, 2360 × 1640 pixels, the size of the reporter's screenshot. It runs
the app and This iPad's server, and a UI test can drive it with a finger.
It cannot send Apple Pencil touches. Neither the Simulator nor XCTest
makes a touch of type `.pencil`, so it has no Pencil contact, hover,
squeeze or roll, and this freeze cannot be reproduced there.

1. Boot it: `xcrun simctl list devices available | grep "iPad Air"`, then
   `xcrun simctl boot <udid>`.
2. In `ipad/`, run `xcodegen generate`, then `xcodebuild test -project
   GammaIPad.xcodeproj -scheme GammaIPadUITests -destination
   "id=<udid>" ONLY_ACTIVE_ARCH=YES ARCHS=arm64 CODE_SIGNING_ALLOWED=NO`
   ([dev/ipad.md](../dev/ipad.md) "Tests").
3. For screenshots as PNGs, add
   `TEST_RUNNER_GAMMA_UITEST_SHOTS=<folder>` before `xcodebuild`.
   They are in the result bundle either way.
4. The test's ⌘R writes a touch report inside the simulator, at
   `~/Library/Developer/CoreSimulator/Devices/<udid>/data/Containers/Data/Application/<app>/Documents/Gamma/local/logs/touch-report.log`.
   Find it with
   `find ~/Library/Developer/CoreSimulator/Devices/<udid> -name touch-report.log`.

A test of something else on that screen can start from `TapsTests`: its
launch argument opens This iPad without the launcher, and it signs in as
a guest.

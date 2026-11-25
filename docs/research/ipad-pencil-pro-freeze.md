# The Pencil Pro freeze on an iPad Air

Solved: users reported on 2026-11-24 that handwriting on the iPad works
since 0.4.11. On an iPad Air with an Apple Pencil Pro, the page in the iPad
app stopped answering taps from the Pencil's first contact. On the
evidence, the shell's Pencil recognizer never settled after a touch it did
not claim ("The cause"); the reporter's own iPad was never measured. The mechanics are in [dev/ipad.md](../dev/ipad.md) ("The
pen", "The touch report", "Tests").

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

**2026-11-23 (the reporter's clock), after 0.4.9 came out.** Still broken,
though the PDF no longer sticks and still moves. Before, once the pen was
picked, writing worked as long as no other function was touched; now
nothing can be written after picking it either. "As if the pen had not been
let go", with a video of the Pencil over the library. The build they
compare with is not known, nor whether 0.4.9 is the build they ran. 0.4.9
differs from 0.4.7 on the pen's path only by the touch report's logging in
`PencilCapture`.

**2026-11-23, Safari.** The reporter used <https://demo.gammapdf.com> and it
did not freeze. The demo ran `0.4.9-dev.4` (43bddc43), whose `frontend/src`
does not differ from v0.4.9's.

**2026-11-24, on 0.4.11: solved.** Users report that the handwriting issue
is gone. Between 0.4.9 and 0.4.11 one commit touches `ipad/` (7628a652, the
fix with the switches below, first shipped in 0.4.10), and none the web
app's pen and touch code. Whether the reporters had all three switches on,
whether 0.4.10 was enough for them, and whether iPadOS was updated
meanwhile was not said.

**The developer's iPad:** an iPad Pro 11-inch (M2) with an Apple Pencil
(2nd generation). It hovers too, and nothing freezes there. Its iPadOS
version is not recorded.

## The cause

`PencilCapture` never reached a final state for a touch it did not claim.
A finger was ignored and a Pencil's tap recorded and dropped, and the
recognizer stayed `possible` both times. UIKit does not reset such a
recognizer: in the simulator, 14 Pencil taps gave 14 ends and no reset,
where a claimed stroke (`began`, `changed`, `ended`) is reset at once.

What waits on a recognizer that never settles never settles either. In the
iPad Air 11-inch simulator (27.2), with a finger as the Pencil
(`PencilStandIn`) and one recognizer that gets only Pencil touches, that
WebKit's tap recognizer waits on and that waits on `PencilCapture`:

| | before the fix | with it |
|---|---|---|
| a finger's tap, before any Pencil touch | answered | answered |
| a finger's taps after one unclaimed Pencil tap | none answered | all answered |
| the same after a reload | none answered | all answered |
| `WKSyntheticTapGestureRecognizer` in the touch report | `failed`, `touches 1` | `possible` |

Pointer events went on arriving in the frozen page (11 of 11 down and up)
and only the clicks were missing. That is the reports' picture: taps dead
for both, hover and scrolling alive, the bar's own web view alive, a reload
no help, and writing alive until a tool is tapped, since a claimed stroke
settles and a tap does not.

The link is made up. Which of the system's recognizers waits on
`PencilCapture` for a Pencil touch, and why only on some iPads, was never
seen: no touch report came from there.

**The fix** (`PencilCapture.settle`): with no Pencil touch of the page's
left and no stroke captured, it fails, and UIKit resets it. Seven of seven
strokes were still claimed and delivered, straight after tool taps too. A
palm resting through taps and strokes changed no claim and no sample
count. Fingers are settled too: a direct link to WebKit's tap recognizer
killed every finger's tap from the start without that, and all 11 taps
were answered with it.

## Ruled out

- **The web app's code, and WebKit by itself.** The same code in Safari does
  not freeze. What the web app does only in the app (`nativeHost()`: the
  claim, the haptics, the edit menu, the Pencil's gestures, the status
  bar's colour) handles no tap on the library, which freezes too.
- **The web app's state**, if the page stayed deaf to a finger after the
  reload and before the Pencil touched it again, which the reporter was not
  asked. A reload starts the web app afresh: `penLifted` and every pen hold
  on the page (the ink layer's drag and claims `hostOwned` and
  `hostClaims`, the PDF's pen selection, the region and order drags, the
  finger chords).
- **A capture left open** in `PencilCapture`. While one is open the
  recognizer tracks every finger, the bar's included, and the bar answers a
  finger. With nothing armed the page claims no stroke, so none opens. That
  does not clear the recognizer itself ("The cause").
- **The shell's edit menu.** `ShellWebView.editMenuShown` goes off on a
  PDF selection, and a reload leaves it off, since the page says nothing
  as it unloads. In the iPad Air 11-inch simulator a finger's selection
  of PDF text turned it off, and taps still answered with the selection
  up, after it was gone, and after ⌘R (`TapsTests`; its first version could
  not fail, as it took the PDF's word "Settings" for the menu's item). That rules out its
  `canPerformAction` and `buildMenu` overrides as the cause of dead taps
  on iPadOS 27.2. That the menu stayed off across reloads was a defect of
  its own, since put right (`ShellController.pageGone`).
- **The live line view.** It takes no touches (`isUserInteractionEnabled`
  is false).
- **The hover classes.** `inkHovering` only hides the cursor.
- **Haptics.** The page asks for one only on a held shape, a pen lasso's
  selection or a gesture's tool switch, not on a contact.
- **A non-finite sample.** The roll is checked and the force guarded.
  The bridge dropped a message it could not encode, silently. That would
  have lost a `stroke`, but the freeze needs no claim. A stroke's end now
  goes again without its samples (`Bridge.sendStroke`).
- **Escape and the keyboard.** In the simulator no key reaches the page
  after a finger taps a button, and Escape never does. Safari there
  behaves the same on a plain page, so it is not the shell's.

## The other pieces

Safari left what the app puts around the page. Besides `PencilCapture`, two
pieces of its own reach the page's web view: the content view's class,
swapped at run time for one without the keyboard's accessory bar
(`WebShell.hideKeyboardAccessoryBar`), and `UIPencilInteraction` on the
container, which puts a `UIHoverGestureRecognizer` there. All three got a
switch in the iPad's Settings app, under Gamma ([dev/ipad.md](../dev/ipad.md)
"The native pieces' switches"), to be turned off one at a time had the fix
not been enough. In the simulator each takes away its own piece and no
other.

**Open:** the switches go once it is known that they were all on for the
people it was solved for. With one off, the piece behind it may be what
ended the freeze, and the build without the switch would bring it back.

## The touch report

The shell writes `touch-report.log` beside the local server's log at every
Reload ([dev/ipad.md](../dev/ipad.md) "The touch report"). It covers the
device, the Pencil's system settings, `PencilCapture`'s state, every
recognizer and interaction from the window down with its state, and the
last 100 touch lines. Each line gives a touch's begin, or a Pencil touch's
end, cancel or reset, with every recognizer UIKit gave it.

Should a freeze come back, to get one from an iPad that has it, on 0.4.9 or
later:

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
- the Pencil touch's own lines: which recognizers it names that a finger's
  lines do not, and an `expecting` (estimated properties still to come) on
  its end. Up to 0.4.9 an `ended` without a `reset` is every unclaimed
  touch; with the fix a `reset` follows each.

## Mimicking the reporter's iPad here

The iPad Air 11-inch (M4) simulator has the M3's screen: 1180 × 820
points, 2360 × 1640 pixels, the size of the reporter's screenshot. It runs
the app and This iPad's server, and a UI test can drive it with a finger.
It cannot send Apple Pencil touches. Neither the Simulator nor XCTest
makes a touch of type `.pencil`, so it has no hover, squeeze or roll and
none of the system's own Pencil recognizers. A finger can take the Pencil's
place in the gestures' arbitration, though: under `-gamma.pencilStandIn` a
debug simulator build takes a touch that begins in the window's left half
for the Pencil, natively and in the page, and `-gamma.pencilStandIn.waits`
adds the link above (`PencilStandIn`).

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

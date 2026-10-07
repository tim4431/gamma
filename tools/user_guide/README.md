# User guide animations

The animations in the [user guide](../../docs/user_guide/user_guide.md) are
looping SVGs in `docs/user_guide/assets/`, generated here: the idea of each
interaction drawn in the branding style (the warm paper, the amber accent, a
paper and a notes window), not a recording. They animate with SMIL only, so
they play inside a plain `<img>` on GitHub and on gammapdf.com, where scripts
and CSS animations in an SVG do not. Light only.

```powershell
backend\venv\Scripts\python.exe tools\user_guide\build.py            # every scene
backend\venv\Scripts\python.exe tools\user_guide\build.py annotate   # one scene
node tools\user_guide\snap.mjs annotate 1 3 5 8                      # frames at those seconds → artifacts/user-guide/
```

`snap.mjs` uses the frontend's Playwright (`npm ci --prefix frontend` once);
it pauses the SVG's clock and screenshots each moment, which is how a scene is
checked without a browser window.

## Writing a scene

- A scene is a function in a `scenes_*.py` module returning the SVG text, listed
  in that module's `SCENES = [(stem, build), ...]`; `build.py` finds the modules
  by itself. `scene.py` holds the shared pieces: the palette, `frame()`,
  `paper()`, `panel()`, `block()`, `chip()`, `button()`, `field()`, `key()`
  (a keycap), `strip()` (the pen tool column), `mark()` (the Gamma mark), the
  `pointer()` with its click rings, `stroke()` for pen lines with
  `path_length()` and `path_points()` to pace it, and the SMIL helpers
  `anim()`, `show()`, `hide()`, `move()`. `frame()` defines two shadows:
  `#shadow` for windows and `#lift` for small floating things.
- One idea per scene, 8–14 seconds, on the 1400 × 620 canvas. Few elements,
  large enough to read at half width: a paper, a window, a chip, the pointer.
  Real UI words where the guide uses them (**Share**, **Save to Gamma**), grey
  bars for everything that is just text.
- Typed text goes through `typed()` (the branding typewriter), never a
  `<text>` that appears whole. The pointer moves before anything it causes, and
  a click shows its ring.
- Everything an action adds fades out just before the loop restarts
  (`show()` does this), so the loop reads as a clean repeat.
- Every `keyTimes` list runs from 0 to 1 (`write()` checks), and the `<title>`
  and `<desc>` say what happens, for readers who see no animation.
- A multi-word handwriting line is one `stroke()` per word: the dash sweep
  restarts at every subpath, so a path with several `M`s draws them all at once.
- Keep a file under about 40 KB.

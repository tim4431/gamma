# pypdfium2 on the iPad

Findings of October 2026, with the path they suggest. The question: can
the server inside the iPad app ([dev/ipad.md](../dev/ipad.md)) have
pypdfium2, which has no iOS wheel, so that PDF text comes with its
positions and pages render to pictures there as on every other server?
Without it the server falls back to PyPDF2 for text and renders nothing
([dev/ipad.md, The wheels](../dev/ipad.md#the-wheels)).

## What exists

- **pdfium itself is built for iOS.** Every weekly release of
  [pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) carries
  `pdfium-ios-device-arm64.tgz`, `pdfium-ios-simulator-{arm64,x64}.tgz` and
  Catalyst builds: simulator since `chromium/4763` (2021-12), device since
  `chromium/6886` (2024-12). Each holds one dynamic library,
  `lib/libpdfium.dylib` (7 MB for the device), with install name
  `./libpdfium.dylib`, an rpath of `@executable_path/Frameworks`, a
  minimum iOS of 17.0 ([#236](https://github.com/bblanchon/pdfium-binaries/issues/236),
  [#237](https://github.com/bblanchon/pdfium-binaries/pull/237)) and no code
  signature. There is no static iOS asset in the latest release, though
  `-static` jobs were merged ([#239](https://github.com/bblanchon/pdfium-binaries/pull/239)).
  As it comes, the dylib cannot ship: App Store validation refuses loose
  libraries (ITMS-90171, [#183](https://github.com/bblanchon/pdfium-binaries/issues/183)).
  [#245](https://github.com/bblanchon/pdfium-binaries/pull/245) (2026-10)
  stages a `pdfium.framework` for this.
- **pypdfium2 knows iOS, as a build target only.** Its setup maps
  `PDFIUM_PLATFORM=ios_arm64_dev` to the device tgz and tags the wheel
  `ios_17_0_arm64_iphoneos`
  ([setupsrc/base.py](https://raw.githubusercontent.com/pypdfium2-team/pypdfium2/main/setupsrc/base.py),
  [tagging.py](https://raw.githubusercontent.com/pypdfium2-team/pypdfium2/main/setupsrc/tagging.py)).
  The changelog for 5.0.0 says iOS "will not actually work yet": the
  binary must move to the bundle's Frameworks and the library search
  needs a patch, and asks for help. PyPI carries Android wheels, none for
  iOS; [PLATFORMS.csv](https://raw.githubusercontent.com/pypdfium2-team/pypdfium2/main/PLATFORMS.csv)
  marks iOS "setup only, untested". The loader (`bindings.py`
  `_get_library`) tries `./libpdfium.dylib` beside the bindings, two
  LibreOffice paths and `ctypes.util.find_library`; nothing at run time
  overrides it.
- **Nobody has shipped it on iOS.** It is in none of
  [pypi.flet.dev](https://pypi.flet.dev/), BeeWare's and Flet's
  mobile-forge recipes, or kivy-ios. PyMuPDF is: Flet's index has 1.27 for
  cp312 to cp314, device and simulator, with `libmupdf.dylib` (57 MB
  unpacked) inside the wheel. Flet's host
  ([serious_python_darwin 4.2.1](https://pub.dev/packages/serious_python_darwin/versions/4.2.1/changelog))
  wraps such dylibs into frameworks at build time, the precedent to copy.
- **What iOS demands.** Frameworks inside the bundle, signed; no loose
  dylibs ([Placing content in a bundle](https://developer.apple.com/documentation/bundleresources/placing-content-in-a-bundle)).
  `dlopen` of a bundled framework works (Apple DTS,
  [forum thread](https://developer.apple.com/forums/thread/670761)), and
  CPython's iOS guide describes the `.fwork` scheme BeeWare's
  `install_python` applies, but only to `*.so` files: a wheel's `.dylib`
  stays where it is. Guideline 2.5.2 forbids downloaded or self-modifying
  code; a bundled, signed framework is what it asks for.

## The path

1. **Build the wheel on a Mac**: `PDFIUM_PLATFORM=ios_arm64_dev pip wheel
   pypdfium2==5.14.0 --no-build-isolation`, and `ios_arm64_simu` for the
   simulator; keep both in a local index the build script reads. The pip
   call in `ipad/scripts/build-python.sh` needs `--platform
   ios_17_0_arm64_iphoneos` besides `ios_13_0`, since the wheel is tagged
   17.0. Hours; the risk is that the untested build does not go through.
2. **Wrap the dylib** in `project.yml`'s install step after
   `install_python`: `pypdfium2_raw/libpdfium.dylib` becomes
   `Frameworks/pdfium.framework/pdfium` with an `Info.plist`
   (`MinimumOSVersion` 17.0), its install name fixed, signed like the other
   frameworks (serious_python_darwin and pdfium-binaries #245 show how).
   Half a day. The app's deployment target must be 17.0 or later
   (ITMS-90208 otherwise).
3. **Point the loader at it**: at build time, add
   `../../../Frameworks/pdfium.framework/pdfium` to `bindings.py`'s
   `libpaths` (a relative entry resolves against the bindings directory),
   and offer the change upstream, which asked for it. An hour, redone at
   each pypdfium2 bump until it is upstream.
4. **Verify** on the simulator, a device and TestFlight, where the
   validation runs: `pdf_text` extraction and `render_page` with iOS's
   fonts. A day.
5. **If that stalls**: PyMuPDF from Flet's index behind the same guards
   (AGPL or a commercial licence, 57 MB, a second PDF API for
   `gamma/pdf_text.py`), or PDFKit in the Swift host, which leaves the
   server's pdfium paths dead on the iPad and bridges text positions and
   pictures by hand.

## Not confirmed

- That pypdfium2's iOS wheel build succeeds (upstream says untested).
- Whether a static iOS pdfium is published anywhere.
- App Store acceptance of a framework Python opens with `dlopen`, beyond
  Apple's forum answer and the iOS guide.
- pdfium's font handling on iOS devices.

## Outcome (October 2026)

Step 5 came first: the server moved to PyMuPDF the same month, for
MuPDF's built-in fonts and text extraction and for this ready wheel
([pypdfium2-vs-pymupdf.md](pypdfium2-vs-pymupdf.md) has the comparison
and its own outcome), so no pypdfium2 wheel was built. The wrapping step
2 describes was built for MuPDF's two dylibs instead:
[pymupdf-ios.md](pymupdf-ios.md) records the wheel's contents and the
step, [dev/ipad.md](../dev/ipad.md#the-wheels) the iPad as it stands.
The iOS facts above (frameworks only, `.fwork` markers for `.so` files, a
`.dylib` left where it is) held and shaped that step.

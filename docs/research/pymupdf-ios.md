# PyMuPDF on the iPad

Findings of October 2026, with the build step they led to. The server's
PDF reader moved from pypdfium2 to PyMuPDF
([pypdfium2-vs-pymupdf.md](pypdfium2-vs-pymupdf.md) compared them; the
decision went to PyMuPDF for the fonts built into MuPDF and for its
ready iOS wheel, the fallback [pypdfium2-ios.md](pypdfium2-ios.md)
named). The question: what is in that wheel, what does the bundle's
install step ([dev/ipad.md](../dev/ipad.md#how-the-bundle-is-made)) do
with it, and what has to be added for MuPDF to load on an iPad?

## The wheel

- **Flet's index has it.** `pymupdf==1.27.2.3`, build 1, cp313, for
  `ios_13_0_arm64_iphoneos` (42.3 MB, sha256 `03c2a1d4…a979b`) and
  `ios_13_0_arm64_iphonesimulator` (42.9 MB, `da4aaa32…8c5f`). pip
  resolves the pin to these two files for both platforms with the flags
  `build-python.sh` uses. Both unpack to 76 MB: `pymupdf/` (the 2.5 MB
  SWIG layer `mupdf.py`, the package, four binaries), a `fitz/` shim and
  the dist-info. It is built by Flet's fork of BeeWare's mobile-forge
  ([recipes/pymupdf](https://github.com/flet-dev/mobile-forge/tree/main/recipes/pymupdf)),
  whose README says the 1.28 series is "not a routine version bump", so
  the iPad stays on 1.27 while the server runs 1.28.2.
- **MuPDF is dynamic, not static.** Four Mach-O files, every one
  `MH_DYLIB`, arm64, built for iOS 13.0 (the simulator slice 14.0) with
  the 26.5 SDK; the device binaries carry no code signature, the
  simulator ones an ad-hoc linker signature:

  | File | Size | Install name (`LC_ID_DYLIB`) | Depends on | `LC_RPATH` |
  |---|---|---|---|---|
  | `libmupdf.dylib` | 57.0 MB | `@rpath/pymupdf.libmupdf.framework/pymupdf.libmupdf` | libSystem | — |
  | `libmupdfcpp.dylib` | 1.9 MB | `@rpath/pymupdf.libmupdfcpp.framework/pymupdf.libmupdfcpp` | libmupdf (by that name), libc++, libSystem | `@loader_path/.` |
  | `_mupdf.so` | 13.6 MB | a build path | libmupdfcpp, libmupdf (by those names), libc++, libSystem | `@loader_path/.` |
  | `_extra.so` | 0.3 MB | an absolute runner path | the same | `@loader_path/` |

  None of the four names `Python.framework` in a load command (the
  standard library's modules do, as `@rpath/Python.framework/Python`).
- **The wheel is laid out for the framework scheme already.** The
  recipe gives the two libraries the install names serious-python's
  conversion would produce (`<package>.<lib>`, a dotted relative path),
  and patches `pymupdf/__init__.py` (`ios-dylib-preload.patch`) to load
  both with `ctypes.CDLL(..., RTLD_GLOBAL)` before importing `_extra`: a
  sibling `.dylib` if there is one, else the framework a `<lib>.fwork`
  marker beside it names, found by walking up to twelve parent
  directories. Failures are swallowed. Order matters, libmupdf first.
- **Fonts and features.** The base-14 faces and a Noto/CJK fallback set
  are compiled in, so pages render without system fonts; no OCR, no
  libcrypto (the recipe's README).

## What `install_python` does, and does not

Python-Apple-support 3.13-b16's `Python.xcframework/build/utils.sh` (the
testbed script the project's `Python.patch` adds to CPython; the copy
in the tarball is identical). `install_python` copies the standard
library, then runs `process_dylibs` over `lib-dynload` and each package
path given, which is `find -name "*.so"` and, per file, `install_dylib`:

- The framework is `Frameworks/<dotted.module.name>.framework`, the name
  from the file's path under the package root, so the wheel's modules
  become `pymupdf._mupdf.framework/pymupdf._mupdf` and
  `pymupdf._extra.framework/pymupdf._extra`.
- Its `Info.plist` is the `iOS-dylib-Info-template.plist` from the same
  folder (`CFBundlePackageType` APPL, `MinimumOSVersion` 13.0,
  `CFBundleSupportedPlatforms` iPhoneOS, also for the simulator) with
  `CFBundleExecutable` and `CFBundleIdentifier`
  (`$PRODUCT_BUNDLE_IDENTIFIER.<name>`, `_` as `-`) filled in by
  `plutil`.
- The `.so` is moved into it; a `.fwork` marker holding the path
  `Frameworks/<name>.framework/<name>` is written where it was, and a
  `.origin` back reference into the framework; a `PrivacyInfo.xcprivacy`
  moves along if the module has one.
- The framework is signed: `codesign --force --sign
  $EXPANDED_CODE_SIGN_IDENTITY -o runtime --timestamp=none
  --preserve-metadata=identifier,entitlements,flags
  --generate-entitlement-der`.
- It never runs `install_name_tool`: a module keeps its id and its
  references. And it never looks at a `.dylib`: the two MuPDF libraries
  stay loose in `python/app_packages/pymupdf`, unsigned, where iOS will
  not map them and App Store validation refuses them (ITMS-90171).

How a framework's binary finds the others: every standard-library module
references `@rpath/Python.framework/Python` and carries no `LC_RPATH` of
its own, and they load, so the run path in force for a framework Python
`dlopen`s is the app's: XcodeGen's iOS default
`LD_RUNPATH_SEARCH_PATHS = $(inherited) @executable_path/Frameworks`
(its `SettingPresets/Platforms/iOS.yml`), the setting CPython's iOS
guide asks for. That resolves `@rpath/pymupdf.libmupdf.framework/pymupdf.libmupdf`
to `Frameworks/pymupdf.libmupdf.framework/pymupdf.libmupdf` once that
exists.

The precedent: serious-python's darwin package
(`darwin/xcframework_utils.sh`, `sync_site_packages.sh`) converts a
wheel's `.dylib` files as it does its `.so` files since 4.2.1, the same
`<dotted.path>.framework` and `.fwork` pointer, and since its PR #223
`reconcile_framework_install_names` sets each framework binary's id to
`@rpath/<fw>.framework/<fw>`, rewrites every reference that pointed at
an old id, and signs ad hoc, because a bare `@rpath/libfoo.dylib` crashed
apps at launch.

## The post-build step

In `ipad/project.yml`, after `install_python`, for each
`python/app_packages/pymupdf/libmupdf*.dylib`:

1. `Frameworks/pymupdf.<lib>.framework/pymupdf.<lib>`, with an
   `Info.plist` of our own (`FMWK`, `MinimumOSVersion` 17.0, the
   deployment target; `CFBundleSupportedPlatforms` iPhoneOS or
   iPhoneSimulator from `PLATFORM_NAME`; `CFBundleIdentifier`
   `$PRODUCT_BUNDLE_IDENTIFIER.pymupdf.<lib>`), the `.fwork` marker and
   the `.origin` file as `install_python` writes them, and
   `install_name_tool -id @rpath/pymupdf.<lib>.framework/pymupdf.<lib>`.
2. For the four binaries (the `.fwork` markers under `pymupdf/` name
   them, the two modules' frameworks included), `install_name_tool
   -change <old id> <new id>` for each library, then `codesign --force`
   with `install_dylib`'s flags, since `install_name_tool` undoes a
   signature.
3. A build fails if any `.dylib` is still loose under `app_packages`.

With this wheel the ids and references are already the framework paths,
so the `-id` and `-change` calls change nothing; they are there for a
wheel built with bare names. The step runs on every build, because the
`rsync --delete` before `install_python` brings the loose dylibs back.
Two paths then load the libraries, either enough on its own: pymupdf's
own preload through the markers, and dyld's `@rpath` through the app's
run path. The bundle grows by about 75 MB per platform, 57 of them the
one library.

## Not confirmed

- The step was written on Windows and checked with `bash -n` and a dry
  run against a fake bundle with stub `otool`, `install_name_tool` and
  `codesign`: the device and simulator plists, a second run with nothing
  to do, and the loose-dylib guard. Its real runs are CI's (`ipad.yml`,
  on every pull request that touches `ipad/`). The build there exercises
  `otool -D`'s two-line output (the id is read as the last line),
  `install_name_tool` on the 57 MB library and on the modules
  `install_python` already signed, and `codesign` accepting the
  hand-written `Info.plist` with `--preserve-metadata=identifier` on a
  binary that was never signed; `LocalServerStartTests` then uploads a
  PDF to the embedded server and asks for a page picture, which only
  MuPDF can draw ([dev/ipad.md "Tests"](../dev/ipad.md#tests)). A green
  run is the simulator's confirmation. A device with a real team is not
  covered: it enforces the code signature of everything it loads, where
  the simulator does not, and nothing has run there.
- App Store validation of the hand-made frameworks (ITMS-90171,
  ITMS-90208, ITMS-91065 territory); the app has not been submitted.
- The server's code against Flet's build of 1.27.2.3 itself. The server
  runs on 1.28.2; its PDF test files (208 tests) pass against PyPI's
  1.27.2.3 for Windows, so the calls it makes (`get_text`, `rawdict`,
  `get_pixmap`, `get_toc`, `page.rect`, `transformation_matrix`,
  `set_rotation`, `xref_get_key`, and the bbox device `pdf_notes` builds
  on `pymupdf.mupdf.FzDevice2` and runs with `fz_run_page`, reading the
  clip with `fz_device_current_scissor`) exist and behave in the 1.27
  series; the iOS build is exercised only by CI's start test, a page
  walk and one render, not the rest of those calls.
- MuPDF's rendering and fonts on the device, and the server's memory
  with the library mapped, on an older iPad.
- The recipe's README says MuPDF releases the GIL while it works; the
  server measured 1.28.2 holding it inside every call
  ([pypdfium2-vs-pymupdf.md](pypdfium2-vs-pymupdf.md)). Not measured
  on 1.27 or on the device.

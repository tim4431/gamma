# Gamma on Android: phone or tablet, how to build it, how to publish it

Survey from 2026-10-07. Findings only; nothing is built. The question
was: how to also release an Android app, for phones or tablets or both,
how to compile it, and how to put it on a store. The iPad went through the
same question a year earlier ([ipad.md](ipad.md)); this note starts from
what that decision settled and lists what Android changes. Dates are the
source's; a fact marked *unverified* was not confirmed on a primary source.

## The short version

- **Both form factors from one build.** One Android App Bundle serves
  phones, tablets, foldables and Chromebooks; Play lists it on tablets once
  the listing has tablet screenshots. The web app already has the compact
  shell for phones and the docks for a tablet in landscape
  ([dev/ipad.md](../dev/ipad.md) "Layout by orientation"), so the question
  is not which to support but which to design for. Every comparable
  handwriting app went tablet-first; every comparable reading app shipped
  one responsive build and treats the phone as capture-and-read.
- **Android Chrome is already the best browser for Gamma's pen.** The ink
  layer's Chromium-only niceties (coalesced and predicted pointer events,
  the low-latency canvas) work there and not in Safari. The motive that
  pushed the iPad to native pen capture is weaker here; what remains native
  is front-buffer latency and offline storage.
- **A Trusted Web Activity does not fit** a product whose users type their
  own server address: it verifies one fixed https origin. The web-based
  shapes are a Kotlin WebView shell or Capacitor.
- **A shape the iPad never had:** the Python backend can run on the device
  (official CPython Android builds, Chaquopy 17), so an Android app could
  be a desktop clone — the whole web app offline against a local server,
  synced by the Python mirror engine as it is — at the price of tens of
  megabytes and a handful of native wheels to build.
- **Compiling needs no Mac.** Gradle builds on the developer's Windows
  machine and on the `ubuntu-24.04` runner, which preinstalls the SDK. A
  signed bundle, a sideloadable APK and a Play upload fit into the existing
  release workflow's shape; the version comes from the same tag rule.
- **Publishing costs $25 once**, a verified identity whose legal name shows
  on the listing, and, for a personal account, a closed test with twelve
  testers for fourteen days. An app that sells nothing and shows the
  Gamma Cloud plan read-only is the "consumption-only" case Play allows
  without its billing. AGPL is fine on Play (Signal ships under it).

## What Android gets today, without an app

The web app in Chrome on Android is already most of a client:

- **Install.** The manifest (`display: standalone`, maskable icon) makes
  Chrome and Edge offer *Install Gamma*, which produces a WebAPK with its
  own icon and window ([dev/ipad.md](../dev/ipad.md) "The installed web
  app"). There is no service worker, so nothing works offline.
- **Pen.** `InkLayer.jsx` reads `pointerType: "pen"`, pressure, the barrel
  button, `getCoalescedEvents`, `getPredictedEvents` and draws the live
  stroke on a `desynchronized` canvas. All of it is Chromium
  ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/PointerEvent/getCoalescedEvents),
  [Chrome's low-latency canvas](https://developer.chrome.com/blog/desynchronized));
  Safari lacks the three niceties ([handwriting.md](handwriting.md)).
  Android 14 also writes into text fields with the pen by default,
  including "WebView text widgets"
  ([Android docs](https://developer.android.com/develop/ui/views/touch-and-input/stylus-input/stylus-input-in-text-fields)).
- **Keyboard.** Since Chrome 108 the on-screen keyboard resizes only the
  visual viewport on Android, as iPadOS does
  ([Chrome blog](https://developer.chrome.com/blog/viewport-resize-behavior)),
  so the editing bar's `visualViewport` logic carries over unchanged.
- **Layout.** `App.jsx` treats "Android" without "Mobile" as a tablet: the
  compact shell upright, the docks in landscape; a phone keeps the compact
  shell both ways. The browser suite emulates 834×1194 and 390×844
  Chromium touch contexts; an Android device descriptor would be a one-line
  addition.
- **Share target, a cheap win.** An installed WebAPK can register as a
  system share target that receives files by a `multipart/form-data` POST
  ([MDN](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/share_target),
  [Chrome](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target)).
  The POST can land on a backend route that reuses the clip pipeline
  (`/api/uploads` then `/api/clip`, [dev/extension.md](../dev/extension.md))
  and redirects to the new page, so no service worker is needed. That is
  "share a PDF to Gamma" from any Android app, with no store app at all.

What it cannot do: read or write offline, open PDFs from the file manager,
appear in Play, or get front-buffer pen latency.

## What the iPad decision settled, and what differs here

[ipad.md](ipad.md) built the installed web app first and the native app
later, for two requirements the browser could not meet: reading and
writing with no connection by the mirror's rules, and native Pencil
capture. It rejected a `WKWebView` wrapper as "a later distribution
decision, not a feature one". The reasoning carries over; the ground does
not:

| | iPad | Android |
|---|---|---|
| Build machine | A Mac per developer, a macOS runner, Xcode | Windows or Linux; Gradle; the `ubuntu-24.04` runner preinstalls SDK 36/37, Build Tools 36, JDK 17/21, Gradle 9.8 ([runner image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)) |
| Store | App Store: $99/year, review, a Mac to upload; never done | Play: $25 once; also F-Droid, Galaxy Store, a GitHub APK |
| Pen in the browser | Safari: pressure and tilt, no coalesced or predicted events | Chromium: everything the ink layer asks for |
| Native pen | PencilKit (rejected for its format); raw `UITouch` samples | `MotionEvent` with pressure, tilt, orientation, hover, batched samples; Jetpack Ink 1.0 for rendering; front-buffer rendering for latency |
| Native PDF | PDFKit, in the OS | Nothing comparable in the OS before Android 15; Jetpack `androidx.pdf` 1.0.0-beta01 (minSdk 28), pdfium, MuPDF |
| JavaScript core host | JavaScriptCore, in the OS, synchronous | No engine in the OS: QuickJS (~1 MB per ABI), Javet V8 (~25 MB per ABI), or a hidden WebView |
| A server on the device | Impossible | Possible: CPython 3.13+ is an official Android target, Chaquopy packages it |
| Google sign-in in a web view | Blocked (`WKWebView` is named) | Blocked too; Chrome Custom Tabs are the way out |

The last row is a finding about the existing iPad app as much as about
Android: Google refuses OAuth from any embedded user agent, detected by
user agent at Google's endpoint whatever site started the flow
([policy](https://developers.google.com/identity/protocols/oauth2/policies),
[native-app guide](https://developers.google.com/identity/protocols/oauth2/native-app)).
A Gamma Cloud account made with Google cannot sign in on the iPad app's
web page today unless it also has a password. The fix on both platforms is
a system browser tab (Custom Tabs or Chrome's Auth Tab on Android,
`ASWebAuthenticationSession` on iPadOS) that returns to the app with a
one-time code.

## The shapes

### A. The installed web app, plus a share target

Nothing to build but the share-target route. Zero Play presence, zero
offline, the pen as good as Chrome makes it. This is the floor every other
shape is measured against, and it should exist whatever else is built.

### B. A thin Kotlin shell on Play

A `WebView` that loads the user's server, with the pieces a browser tab
cannot add: a server-address screen, cookies that persist, the file
chooser and downloads, `ACTION_SEND` and `ACTION_VIEW` intents (a PDF
shared or opened from the file manager uploads into the library),
cleartext for LAN servers through `network_security_config`
([docs](https://developer.android.com/privacy-and-security/security-config)),
and sign-in handed to a Custom Tab. About two to five megabytes; one to
three weeks for a solid shell. The WebView is Chromium, updated from Play
since Android 5 (version 155 in October 2026), so the pen is Chrome's.

Three things bite in a WebView that do not in Chrome:

- **The keyboard.** Android 15 draws edge to edge and Android 16 removes
  the opt-out, so the window is no longer resized for the keyboard; the
  host must apply the IME inset itself or fields hide under the keyboard
  ([Android docs](https://developer.android.com/develop/ui/views/layout/webapps/understand-window-insets);
  Capacitor's open issues [#8055](https://github.com/ionic-team/capacitor/issues/8055),
  [#8601](https://github.com/ionic-team/capacitor/issues/8601)). The
  editing bar's measurement will need a device check in a shell.
- **Policy.** Play's Spam policy forbids "a webview of a website without
  permission from the website owner or administrator" and its Minimum
  Functionality policy forbids apps "static without app-specific
  functionalities, for example, text only or PDF file apps"
  ([Spam](https://support.google.com/googleplay/android-developer/answer/9899034),
  [Minimum functionality](https://support.google.com/googleplay/android-developer/answer/9898783)).
  The owner publishing their own app is permitted, but the intents, the
  server management and the pen should be visible in the listing so a
  reviewer does not read it as a bare wrapper.
- **Reputation.** Obsidian, a Capacitor shell, carries unresolved S Pen
  threads ("the cursor randomly scrolls to the top",
  [forum](https://forum.obsidian.md/t/using-the-s-pen-on-samsung-android-handwriting-mode-glitches-and-scrolls-the-note-to-the-top/90643)),
  and Logseq is leaving browser storage for "fully native ux" in its
  Android rewrite ([forum](https://discuss.logseq.com/t/logseq-db-android-app-access/34924)).
  Those are handwriting-into-text and storage complaints, not canvas ink;
  Goodnotes ships its whole ink engine as WebAssembly on a canvas inside a
  Trusted Web Activity and is the reference Android handwriting app
  ([web.dev case study](https://web.dev/case-studies/goodnotes)).

### C. A desktop clone on the device: the Python server in the app

The desktop app's model, with a Kotlin shell in Electron's place: the
backend runs on `127.0.0.1`, the WebView shows it, and offline is the
mirror ([dev/mirror.md](../dev/mirror.md)) with no second implementation
of anything. The shell honours the same contract the Electron shell does
(the `GAMMA_*` environment, `/api/health`, `/api/session` and `/api/login`,
the `data-theme` attribute, [dev/desktop.md](../dev/desktop.md)) and the
desktop's one-click `keepOffline` flow is the model for the first sync.

What makes it possible now:

- CPython has been an official Android target since 3.13 (PEP 738, tier
  3, API 21+) and python.org publishes Android embeddable packages for
  every 3.14.x ([downloads](https://www.python.org/downloads/android/)).
  PyPI accepts `android_<api>_<abi>` wheels and cibuildwheel builds them
  (on Linux or macOS hosts, not Windows;
  [docs](https://cibuildwheel.pypa.io/en/stable/platforms/)).
- Chaquopy 17.0.0 (2025-12-01, MIT since 2022) packages Python 3.10 to
  3.14 into a Gradle build, minSdk 24, 16 KB pages ready, and from 3.13
  consumes standard PyPI Android wheels
  ([changelog](https://chaquo.com/chaquopy/doc/current/changelog.html)).
  It declares AGP 7.3 to 9.2, so the project pins AGP below the current
  9.4.

What it costs:

- **Wheels.** Chaquopy's own index has cryptography, bcrypt, numpy and
  Pillow but not pydantic-core, orjson, uvloop, httptools or watchfiles
  ([index](https://chaquo.com/pypi-13.1/)). pypdfium2 publishes its own
  Android wheels since 5.0 (API 23+,
  [platforms](https://pypdfium2-team.github.io/pypdfium2/platforms.html)).
  So `uvicorn` runs without `[standard]` (its pure-Python h11 path),
  orjson becomes optional, and pydantic-core (Rust) is the one wheel to
  take from PyPI or build with cibuildwheel. Every `.so` must be 16 KB
  aligned ([Play rule](https://android-developers.googleblog.com/2025/05/prepare-play-apps-for-devices-with-16kb-page-size.html)).
- **Size and start.** "Several MB per ABI" for the runtime plus the
  packages; the desktop's frozen backend is about 50 MB, which is the
  order to expect per ABI, so the release ships `arm64-v8a` only. Cold
  start is the interpreter plus FastAPI's imports: seconds, unmeasured.
- **Background.** A foreground service needs a declared type on Android
  14+; `dataSync` is capped at six hours a day on Android 15 and
  `specialUse` needs a justification Play reviews
  ([types](https://developer.android.com/develop/background-work/services/fgs/service-types)).
  The simple rule is the desktop's: the server runs while the app is in
  front and syncs then; no service at all.
- **No precedent found** of a Play app running FastAPI on the device.

### D. A native host for the shared core: the iPad design, ported

The iPad app is a Swift host of about 3,400 lines around a 176 KB
JavaScript core that decides everything ([dev/ipad.md](../dev/ipad.md)
"Keeping the host in step"). The Android twin is a Kotlin host of the same
twenty host methods, with native storage (Room or `androidx.sqlite`), HTTP,
a PDF renderer, a pen canvas and a notes outline:

- **The engine.** The core expects one synchronous native function,
  `invoke(method, argsJSON)`. A WebView's `@JavascriptInterface` methods
  are exactly that (synchronous from JavaScript, returning a String), at
  zero size, but the runtime lives in the WebView process and calls into
  it are asynchronous. QuickJS bindings (`quickjs-kt`,
  [repo](https://github.com/dokar3/quickjs-kt)) add about a megabyte per
  ABI, with no JIT; Javet brings V8 at roughly 25 MB per ABI
  ([Maven](https://repo1.maven.org/maven2/com/caoccao/javet/javet-v8-android/6.0.2/)).
  For a 176 KB bundle doing merges and geometry, QuickJS is the fit.
- **The PDF.** Jetpack `androidx.pdf` reached 1.0.0-beta01 on 2026-08-26
  with minSdk 28, text selection, find, forms and its own pen and
  highlighter ([releases](https://developer.android.com/jetpack/androidx/releases/pdf));
  Gamma's ink would be an overlay over it, as over PDFKit. Below that:
  pdfium (BSD, weekly Android builds from the same project pypdfium2
  repacks, [pdfium-binaries](https://github.com/bblanchon/pdfium-binaries))
  with a JNI wrapper of one's own, or MuPDF (AGPL, compatible with
  Gamma's licence). Zotero's Android app licenses Nutrient instead
  ([build file](https://github.com/zotero/zotero-android)), which costs
  "a few thousand dollars per year for a single platform" upward
  ([Nutrient](https://www.nutrient.io/blog/pdf-sdk-pricing-and-licensing-explained/)).
- **The pen.** `MotionEvent` gives pressure, tilt, orientation, hover
  distance, batched samples and palm cancellation
  ([advanced stylus features](https://developer.android.com/develop/ui/views/touch-and-input/stylus-input/advanced-stylus-features));
  the stroke is encoded by the core as on the iPad. Jetpack Ink 1.0.0
  (2025-12-17) takes its own `StrokeInput` points and renders through
  `GLFrontBufferedRenderer`, the "closest apps can get to drawing directly
  to the screen" ([Ink releases](https://developer.android.com/jetpack/androidx/releases/ink),
  [low latency](https://medium.com/androiddevelopers/stylus-low-latency-d4a140a9c982));
  Notability's 2026 Android app is built on it
  ([Notability](https://blog.notability.com/post/notability-for-android-is-finally-here)).
  Ink's brush geometry is not perfect-freehand's, so either the live
  stroke uses Ink and the committed stroke the core's outline, or the
  front-buffer renderer draws the core's outline directly.

This is the only shape with native latency and an offline replica at
once, and the most expensive: a third host beside Swift and the tests'
memory host, two to four months, and a second native app to keep in step.
Zotero's native Android app took two and a half years from repository to
general release.

### E. The shapes not worth taking

- **Trusted Web Activity** (Bubblewrap, PWABuilder): Digital Asset Links
  verify one fixed https origin per package; any other origin, and any
  `http` LAN address, opens as a Custom Tab with browser chrome on top
  ([Chromium](https://chromium.googlesource.com/devtools/devtools-frontend/+/fb0401276434f564f4a91d091a6db53ad461ecbe/front_end/issues/descriptions/TwaDigitalAssetLinksFailed.md)).
  It would work only as a wrapper of Gamma Cloud's one address.
- **Capacitor**: a WebView shell with a plugin catalogue, proven by
  Obsidian and Logseq. The bundled assets run from `https://localhost`, so
  the user's server must allow that CORS origin and cookies need
  `SameSite=None` ([Ionic](https://ionicframework.com/docs/troubleshooting/cors)),
  or every call goes through its native HTTP bridge. Pointed at a remote
  URL it is shape B with a larger toolchain (JDK 21, pinned AGP 8.13,
  Node). Against a local backend it needs shape C's Python anyway.
- **Tauri 2**: mobile since 2024-10, but a sidecar binary does not run on
  Android ([issue](https://github.com/tauri-apps/tauri/issues/9774)), so
  it cannot carry the backend, and its WebView is the same one.
- **React Native, Flutter**: rewrites of the UI that reuse neither pdf.js
  nor CodeMirror. Flutter got Android's pen-to-text only in 2024 after a
  two-year issue; Saber, an open-source Flutter notes app, shows pressure
  ink is doable there ([repo](https://github.com/saber-notes/saber)).

### Side by side

| | A. web app + share target | B. WebView shell | C. server on device | D. native host |
|---|---|---|---|---|
| Effort | days | 1–3 weeks | 3–6 weeks plus wheels | 2–4 months |
| Download size | 0 | 2–5 MB | ~50 MB (one ABI) | 5–15 MB with QuickJS |
| Offline | no | no | whole app, by the mirror engine | replica, as the iPad |
| Pen | Chrome's | Chrome's | Chrome's | front buffer |
| New code to keep in step | one route | a shell | a shell, a wheel build | a third host |
| Play policy | n/a | wrapper scrutiny | foreground-service type if any | low |
| Any server address | yes | yes | yes, plus local | yes |
| Phones | compact shell | compact shell | compact shell | needs its own phone layout |

The shapes stack rather than compete. A is free and should be done. B is
the store presence and the intents, and it is the shell C and D both
need, so nothing in it is thrown away. C and D are the two offline
designs, and the choice between them is the one decision this note cannot
make: C reuses the most and weighs the most; D is the iPad's design, with
the latency, at the cost of a third host.

## Phone or tablet

What the field did ([comparables survey](#sources-by-section)):

- **Handwriting apps went tablet-first, Samsung-first.** Goodnotes' beta
  was Samsung tablets only (2023-03), its page still says "Android tablets
  and Chromebooks" and recommends 4 GB of RAM, and phone stylus support is
  an open request ([goodnotes.com/android](https://www.goodnotes.com/android)).
  Notability came to Android in 2026-08 with Samsung co-marketing. Both
  admit feature gaps against the iPad years in.
- **Reading and reference apps shipped one build for both.** Zotero for
  Android (GA 2025-06-05) "functions on both phones and tablets; no
  distinction made" ([forum](https://forums.zotero.org/discussion/124672/now-available-zotero-for-android)),
  with ink annotation and an S Pen that "works well on Samsung Galaxy
  phones". Paperpile, Papers and Readwise did the same. Nobody ships
  phone-only.
- **The phone is capture and read.** Zotero's phone use is share-a-URL,
  scan a barcode, add by DOI, read on the go; Paperpile's user survey put
  "quickly finding PDFs and reading them" as important for 92% of users
  ([Paperpile](https://paperpile.com/blog/android-ios-release/)). The
  phone pen exists on one line only: the Galaxy S Ultra, whose S Pen
  kept pressure and lost Bluetooth in 2025
  ([SamMobile](https://www.sammobile.com/news/it-is-true-galaxy-s25-ultra-s-pen-does-not-have-bluetooth-features/)).
- **Android tablets are a Samsung-led minority of a shrinking market.**
  IDC Q2 2025: Apple 33.0%, Samsung 18.7%, the largest Android maker
  ([SamMobile](https://www.sammobile.com/news/samsung-biggest-android-tablet-brand-q2-2025/));
  tablet web usage iPadOS 51% to Android 49% ([StatCounter](https://gs.statcounter.com/os-market-share/tablet/worldwide/)).
  Google pulled the Pixel Tablet in 2026-09 and cancelled its successors
  ([9to5Google](https://9to5google.com/2026/09/09/google-pixel-tablet-discontinued/)).
  The pen tablets of 2026 are the Galaxy Tab S11, OnePlus Pad 3, Lenovo,
  Xiaomi Pad 8, Honor; Huawei ships without Google services. Boox is the
  one e-ink maker with full Play, on Wacom EMR; third-party pen apps lag
  there without Onyx's SDK ([Squid's note](https://papyrus.uservoice.com/forums/177239-general/suggestions/36748015-e-ink-support)),
  and Zotero users read on Boox regardless
  ([forum](https://forums.zotero.org/discussion/129048/e-reader-or-e-paper-tablet-zotero)).
- **Chromebooks count as tablets.** Every ChromeOS device since 2019 runs
  Play apps and pen Chromebooks must support USI
  ([chromium.org](https://www.chromium.org/chromium-os/chrome-os-systems-supporting-android-apps/));
  Google's Android-based laptop OS ships on "Googlebooks" from late 2026
  with ChromeOS supported to 2028. A Play listing is how Gamma reaches
  school and lab laptops with pens.
- **Shipping Android is a resourcing bet.** Mendeley retired its mobile
  apps in 2021 "due to a lack of resources"; Paperpile delisted its
  Android app in 2026 pending a rewrite
  ([Paperpile](https://paperpile.com/h/manual-install-android-app/)).

What this implies for Gamma: design for the tablet (the pen, the docks,
landscape reading) and let the phone have the compact shell it already
has; declare both form factors, ship one bundle. Build shape D's phone
layout, if D is chosen, last.

## Compiling

### Toolchain, October 2026

| | Value |
|---|---|
| Android Studio | Rabbit 1 (2026.2.1), stable 2026-10-01 ([blog](https://androidstudio.googleblog.com/)); not required, the command-line tools suffice |
| Android Gradle Plugin | 9.4.0, Gradle ≥ 9.6, JDK ≥ 17, Build Tools 36.0.0, Kotlin built in since 9.0 ([releases](https://developer.android.com/build/releases/gradle-plugin)) |
| JDK | Temurin 21 satisfies AGP (17+) and Capacitor (21) |
| SDK | `compileSdk`/`targetSdk` 36 (Play requires 36 from 2026-08-31, [rule](https://developer.android.com/google/play/requirements/target-sdk)); `minSdk` 28 with `androidx.pdf`, 24 otherwise (Chaquopy and Capacitor floor) |
| Emulator on Windows | Windows Hypervisor Platform; HAXM is gone and the AEHD driver ends 2026-12-31 ([acceleration](https://developer.android.com/studio/run/emulator-acceleration)). The emulator forwards a host pen's pressure and tilt only when the Windows machine has a digitizer ([Microsoft](https://devblogs.microsoft.com/surface-duo/android-emulator-pen-support/)); otherwise a mouse arrives as a mouse |
| A device | Android 11+ pairs over Wi-Fi (`adb pair`). Real pressure, tilt, hover and palm rejection need a pen tablet; a Galaxy Tab with S Pen is the cheapest reference device and, on Android 15, also a 16 KB-page device |

Chrome DevTools emulates touch, not pen or pressure; the web layer's pen
path is tested with synthetic `PointerEvent`s, as the browser suite's
touch scenarios already do with fingers.

### Commands

```powershell
# once: JDK 21, command-line tools in %LOCALAPPDATA%\Android\Sdk
sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"
keytool -genkeypair -v -keystore upload.jks -alias gamma -keyalg RSA -keysize 2048 -validity 10000

# each build: the version pinned from outside, like the desktop's npm version
.\gradlew bundleRelease -PversionCode=1002003 -PversionName=1.2.3
#   -> app\build\outputs\bundle\release\app-release.aab

# a sideloadable APK from the same bundle, and the 16 KB check
java -jar bundletool.jar build-apks --mode=universal --bundle app-release.aab --output gamma.apks --ks upload.jks --ks-key-alias gamma
Expand-Archive gamma.apks apks
zipalign -c -P 16 -v 4 apks\universal.apk
adb install -r apks\universal.apk
```

The signing config reads the keystore path and passwords from the
environment, so a local build and the runner share one `build.gradle.kts`;
the bundle is signed with the *upload* key and Play re-signs it with the
app signing key it holds ([app signing](https://developer.android.com/studio/publish/app-signing)).
R8 keeps `@JavascriptInterface` methods only when told:
`-keepclassmembers class * { @android.webkit.JavascriptInterface <methods>; }`.
AGP 8.5.1+ aligns native libraries to 16 KB pages by itself; `zipalign -c
-P 16` proves it ([page sizes](https://developer.android.com/guide/practices/page-sizes)).
`bundletool` is the one tool the runner image lacks
([releases](https://github.com/google/bundletool/releases)).

### The version

`versionCode` is an integer that must grow and never repeat, at most
2,100,000,000 ([versioning](https://developer.android.com/studio/publish/versioning)).
From the release workflow's computed `<major>.<minor>.<patch>`
([dev/github_actions.md](../dev/github_actions.md) "Versions and tags"),
`major × 1,000,000 + minor × 1,000 + patch` keeps the order and stays
derivable offline; the run number would not survive a recreated workflow,
and days since the epoch collide on two releases a day. `versionName` is
the tag. Both are passed as Gradle properties, so the repository file
stays a floor as `desktop/package.json` does.

### The workflow job

An `android` job in the release workflow, after the `meta` step that
computes the version, on `ubuntu-24.04`: `actions/setup-java` (Temurin
21), `gradle/actions/setup-gradle` (its cache writes only from the default
branch), the keystore decoded from a base64 secret into `$RUNNER_TEMP`,
`gradlew bundleRelease` with the version properties, `bundletool` for the
universal APK and the alignment check, the APK uploaded onto the same
GitHub Release beside the installers, and the bundle sent to Play's
internal track. A cold Kotlin shell builds in three to five minutes; a
Chaquopy build adds pip resolution per ABI and a Python on the runner
matching the app's version.

Sending to Play from CI:

- The first bundle of a new app is uploaded by hand in the Console; the
  API refuses an unknown package ([upload-google-play README](https://github.com/r0adkll/upload-google-play)).
- A service account is created in Google Cloud and invited in the Play
  Console under *Users and permissions* with release rights; no Cloud
  project link is needed any more ([getting started](https://developers.google.com/android-publisher/getting_started)).
- `r0adkll/upload-google-play` v1.1.5 takes the service account's JSON,
  the package, the bundle, the track (`internal`, `closed`, `production`),
  a status (`draft`, `completed`, `inProgress` with a `userFraction`), a
  release-notes folder and the R8 mapping file. It takes no access token.
  For keyless auth like the Chrome Web Store job's Workload Identity
  Federation, `google-github-actions/auth` mints the token and either
  `fastlane supply --json_key_data` (it accepts federation credentials,
  [docs](https://docs.fastlane.tools/actions/supply/)) or four `curl`
  calls to the Edits API (insert, upload bundle, update track, commit;
  [Edits](https://developers.google.com/android-publisher/edits)) do the
  upload. Gradle Play Publisher is in maintenance mode.

For shape C the build also needs: AGP pinned to ≤ 9.2, `buildPython`
pointing at an interpreter of the app's Python version (Chaquopy 17
requires the match), `abiFilters` of `arm64-v8a` (and `x86_64` for the
emulator), `pip { install("-r", "requirements.txt") }`, and a wheel
directory for whatever PyPI and Chaquopy's index lack, built once with
cibuildwheel on a Linux runner.

## Publishing

### Google Play, step by step

Once:

1. **The account.** $25, a government ID and a payments profile in the
   developer's legal name ([register](https://support.google.com/googleplay/android-developer/answer/6112435)).
   A *personal* account shows the legal name, country and developer email
   on every listing; an *organisation* account needs a legal entity and a
   D-U-N-S number (free, up to 28 days) and shows the address and phone
   too ([verification](https://support.google.com/googleplay/android-developer/answer/13628312)).
   Verification "usually seven days or less".
2. **The closed test.** A personal account created after 2023-11-13 must
   run a closed test with at least twelve testers opted in continuously
   for fourteen days (twenty until December 2024), then answer a
   production-access questionnaire about recruitment, engagement, the
   app's value and a first-year install estimate; Google checks real
   usage, and the answer takes about a week ([rule](https://support.google.com/googleplay/android-developer/answer/14151465)).
   A Google Group is the tester list; lab colleagues and the GitHub
   community are the recruits, with a written test plan against the demo
   server. An organisation account skips this.
3. **Developer verification.** Google's Android Developer Verification
   covers Play developers automatically through Play App Signing; the
   same console can register other package names and signing keys, which
   is how a GitHub-released APK keeps installing once certified devices
   enforce verification (Brazil, Indonesia, Singapore and Thailand from
   2026-09-30 for participating stores, the world from 2027, sideloading
   "not yet"; [FAQ](https://developer.android.com/developer-verification/guides/faq),
   [rollout](https://android-developers.googleblog.com/2026/03/android-developer-verification-rolling-out-to-all-developers.html)).
   A lost signing key cannot be registered, so the keystore is backed up
   like the Azure and Apple signing secrets.
4. **The app's content declarations.** A privacy policy URL; the Data
   safety form; account deletion; app access for reviewers; the IARC
   content rating; target audience 18+; no ads. See the risk register.
5. **The listing.** A 512×512 icon, a 1024×500 feature graphic, at least
   four phone screenshots of 1080 px or more (one 1920×1080 landscape for
   high-visibility placement), and for the tablet listing at least four
   7-inch and four 10-inch screenshots between 1080 and 7680 px, 16:9 or
   9:16; Chromebook screenshots optional ([assets](https://support.google.com/googleplay/android-developer/answer/9866151)).
   `tools/readme-media` records the demo workspace already; a tablet
   viewport and the screenshot sizes are a configuration of it.
6. **The first upload** by hand, then the service account for CI.

Per release: a higher `versionCode`; `targetSdk` 36 now and 37 by August
2027; every native library 16 KB aligned (the Console's bundle explorer
shows it); no orientation lock; the bundle signed with the upload key; CI
to `internal`, promote to `closed` and `production` with a staged rollout;
the Data safety form updated if a data flow changed. Tracks: internal up
to 100 testers and lightly reviewed, closed lists up to 2,000, open
unlimited ([tracks](https://support.google.com/googleplay/android-developer/answer/9845334)).
Review of a new app from a new account runs one to two weeks; updates a
day to a week (secondary sources, 2026). End to end for a new personal
account: three to five weeks.

### The policy register for this app

| Risk | The rule | What satisfies it |
|---|---|---|
| Web wrapper | Spam: no "webview of a website without permission from the website owner"; Minimum functionality: no "static" PDF apps ([spam](https://support.google.com/googleplay/android-developer/answer/9899034), [minimum](https://support.google.com/googleplay/android-developer/answer/9898783)) | The owner publishes it. List the native pieces: server management, share and open intents, the pen, offline where built. State in the app-access notes that the developer owns the web application |
| Payments | Play Billing is required for digital goods bought in the app; a *consumption-only* app may say "go to our website to upgrade" with no purchase link, outside the US and EEA programs ([payments](https://support.google.com/googleplay/android-developer/answer/10281818)) | Show the Gamma Cloud plan read-only; a sentence naming gammapdf.com; no checkout link. The US program (after Epic v. Google, external links allowed since 2025-12) and the EEA external-offers program permit links but charge 5–20% on resulting web purchases and require reporting ([US](https://support.google.com/googleplay/android-developer/answer/15582165), [EEA](https://support.google.com/googleplay/android-developer/answer/16505463)); not worth it for a sentence |
| Account deletion | An app that lets users *create* an account in the app must offer in-app deletion and a web deletion link ([rule](https://support.google.com/googleplay/android-developer/answer/13327111)) | Sign in only, no sign-up in the app; or an in-app delete that calls the account server, plus a web page. Accounts on a user's own server are not the developer's, but the same in-app path can call that server's deletion endpoint |
| Data safety | "Collection" is data leaving the device, even to a server the user chose; user-initiated transfers with prominent disclosure may be exempt ([form](https://support.google.com/googleplay/android-developer/answer/10787469)) | Declare personal info (email, name, account id), files and documents, and app activity as collected, not shared, encrypted in transit, deletable; the privacy policy names self-hosted servers |
| Login | "You must provide all required details to enable access" ([app access](https://support.google.com/googleplay/android-developer/answer/9859455)) | A reviewer account on the demo server with its address, and a Gamma Cloud test account with a password; up to five instruction sets |
| Target API | 36 by 2026-08-31, extension to 2026-11-01; 37 by August 2027 ([rule](https://support.google.com/googleplay/android-developer/answer/11926878)) | `targetSdk` 36 from the first build |
| 16 KB pages | Since 2025-11-01 for apps targeting Android 15+; Play blocks non-compliant updates from 2027-02-01 ([page sizes](https://developer.android.com/guide/practices/page-sizes)) | Only shapes C and D ship native libraries; check each `.so` |
| Orientation | Targeting 36, orientation and resizability constraints are ignored on screens of 600 dp and up; 37 removes the opt-out ([Android 16](https://android-developers.googleblog.com/2025/01/orientation-and-resizability-changes-in-android-16.html)) | Lock nothing; the web app already follows the viewport. Tier 3 of the large-screen guidelines asks for no letterboxing, state kept through rotation, keyboard and mouse basics, and the pen able to select, scroll and write into fields ([tier 3](https://developer.android.com/develop/adaptive-apps/quality-guidelines/adaptive-app-quality/tier-3)) |
| Foreground service | Shape C only: a declared type and, for `specialUse`, a justification Play reviews | No service: the server runs while the app is in front |

Costs: $25 once. A consumption-only app that links nowhere pays Play no
fee. Play ranks apps meeting the large-screen guidelines higher on
tablets and warns on listings with per-device crash rates above 8%
([Play blog](https://android-developers.googleblog.com/2023/07/introducing-new-play-store-for-large-screens.html)).

### Beside Play

| Channel | Fit | Rules and status |
|---|---|---|
| **GitHub Releases APK** | The universal APK from the same run, beside the installers; Obtainium updates it on the device | Needs the package and key registered for developer verification before 2027 ([FAQ](https://developer.android.com/developer-verification/guides/faq)) |
| **F-Droid** | Fits Gamma's licence and audience; the Logseq route | Everything FLOSS, no Play Services; prebuilt binaries only from Maven Central, Google Maven, JitPack and the like, PyPI wheels "may be acceptable" ([inclusion](https://f-droid.org/en/docs/Inclusion_Policy/)); the system WebView is an OS component, not a dependency. Chaquopy is MIT, but shape C's wheels would be argued case by case. Submission is a merge request to `fdroiddata`; inclusion takes weeks (community experience). F-Droid opposes Google's verification and advises against registering ([open letter](https://f-droid.org/2026/02/24/open-letter-opposing-developer-verification.html)), which is a tension for a developer who wants both channels |
| **Samsung Galaxy Store** | The S Pen audience | Commercial seller status: ID, bank details, a D-U-N-S or business document; days of review; no fee stated ([prepare](https://developer.samsung.com/galaxy-store/prepare.html)) |
| **Amazon Appstore** | Fire tablets only | Non-Amazon Android distribution ended 2025-08-20 ([Amazon](https://developer.amazon.com/apps-and-games/blogs/2025/02/upcoming-changes-to-amazon-appstore-for-android-devices-and-coins-program)); Fire OS has a Chromium WebView and no Google services |
| **Huawei AppGallery** | No Google services | Password and GitHub sign-in work; Google sign-in does not ([registration](https://developer.huawei.com/consumer/en/doc/start/mracoei-0000001062678404)) |
| **Windows Subsystem for Android** | None | Gone from the Microsoft Store since 2025-03-05 ([Microsoft](https://learn.microsoft.com/en-us/previous-versions/windows/android/wsa/release-notes)) |

## What this leaves open

Decisions, in the order they come up:

1. **A or more.** The share-target route is worth doing regardless. If the
   answer stops there, Android stays a browser platform with a documented
   install, as the iPad was for a year.
2. **The store shell (B)** decides the Play account type (personal, with
   the twelve-tester test, or an organisation with a D-U-N-S number), and
   with it the Custom Tab sign-in that the iPad app needs too.
3. **C or D for offline.** C is the mirror engine as it is, in about 50 MB
   and a wheel build; D is the iPad's host design in Kotlin with native
   latency, in two to four months. Measure first: the interpreter's cold
   start and memory on a mid-range Galaxy Tab for C; Chrome's pen latency
   on the same tablet against a front-buffer sample for D. If Chrome's
   latency is acceptable, C's one open cost is weight.
4. **Phone layout for D**, if D: the native host has none; the web view
   would carry the phone until it does.

Unverified or unmeasured: Javet's Android minSdk; Chaquopy's cold start in
seconds; CI minutes per shape; pen latency in milliseconds for Chrome
versus the front buffer; Play ratings of the comparables; the share of
Android tablets sold with a pen; whether a hidden WebView holds up as a
long-lived JavaScript runtime.

## Sources by section

The sources are linked inline. The surveys behind the sections were run
on 2026-10-07 against: Android and Play developer documentation and help
pages, Chromium and Chrome developer posts, the Chaquopy, cibuildwheel,
pypdfium2 and Javet documentation, the GitHub runner image readme, the
Zotero, Obsidian, Logseq, Goodnotes, Notability, Paperpile and Mendeley
announcements and forums, and IDC and StatCounter figures as reported by
secondary outlets where the primary was paywalled.

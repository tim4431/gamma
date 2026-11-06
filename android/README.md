# Gamma for Android

The Android app is a shell over Gamma's web app, the iPad app's design in
Kotlin. It shows a server's own interface in a WebView, under the desktop
app's own bar and launcher, and adds what Chrome cannot give that web
app: stylus strokes captured natively for any Android stylus, the
stylus's buttons as pen gestures, a Gamma Cloud sign-in that works with
Google, files opened from and saved for other apps, and a Gamma server of
its own inside the app ("This device"), so it works with no connection.
Every page, tool and setting is the web app's.

How it works, file by file, how it is released and published, and what
remains to check on a device: [docs/dev/android.md](../docs/dev/android.md).
The survey that chose this shape: [docs/research/android.md](../docs/research/android.md).
The Google Play listing: [STORE.md](STORE.md). This file covers building
and running it.

The code is under `app/src/main/kotlin/com/gammapdf/gamma/`, in five
packages: `app/` (the process, the window, the model), `shell/` (the
WebViews, the bridge, the chrome, the system's services), `pen/` (the
stylus), `server/` (the server on the device) and `json/` (JSON in and
out, over `org.json`). The server itself is
`../backend/`, entered through `../backend/gamma_mobile.py`, which the
iPad shares.

## Build and run

You need:

- JDK 21;
- the Android SDK with platform 36, the build tools and platform-tools
  (and the emulator for one), named by `ANDROID_HOME` or by
  `sdk.dir=<path>` in `android/local.properties`;
- Python 3.13, which Chaquopy runs pip under (`py -3.13`, `python3.13`,
  `python3` or `python` on the `PATH`, or `-Pgamma.buildPython=` with the
  interpreter's path, spaces and all, or a command such as `py -3.13`);
- Node.js, for the web app the app carries.

On Linux, macOS, or Git Bash on Windows, from `android/`:

```bash
bash ./gradlew :app:assembleDebug       # app/build/outputs/apk/debug/app-debug.apk
bash ./gradlew :app:installDebug        # onto the device or emulator adb sees
adb shell am start -n com.gammapdf.gamma/.app.MainActivity
bash ./gradlew :app:testDebugUnitTest   # the JVM tests
```

From the repository's root, the same with `bash android/gradlew -p android
<task>`. Call it through `bash`: the wrapper's executable bit does not
survive a commit from a Windows checkout.

In PowerShell on Windows:

```powershell
cd android
.\gradlew.bat :app:assembleDebug
.\gradlew.bat :app:installDebug
.\gradlew.bat :app:testDebugUnitTest
```

or `android\gradlew.bat -p android <task>` from the root.

- **The first build** downloads Gradle, the dependencies and the
  server's wheels (from PyPI and Flet's index), which takes a few minutes
  and needs the network; later builds reuse them.
- **The web app.** The build runs `tools/build_frontend_if_needed.mjs`,
  which rebuilds `frontend/dist` when `frontend/` changed. That empties
  the folder while it runs, so a development server serving it meanwhile
  shows errors. `-Pgamma.webBuild=prebuilt` takes `frontend/dist` as it
  is.
- **The ABIs.** A build packages Python for `arm64-v8a` and `x86_64`
  unless `-Pgamma.abis` names fewer. `-Pgamma.abis=x86_64` builds faster
  for an x86_64 emulator; an Apple Silicon Mac's emulator runs
  `arm64-v8a`.
- **A signed release build**: the release key named by
  `GAMMA_ANDROID_KEYSTORE`, `GAMMA_ANDROID_KEYSTORE_PASSWORD`,
  `GAMMA_ANDROID_KEY_ALIAS` and `GAMMA_ANDROID_KEY_PASSWORD`, then
  `bash ./gradlew :app:assembleRelease :app:bundleRelease
  -PversionName=<version> -PversionCode=<code> -Pgamma.abis=arm64-v8a`.
  Without them the release build is unsigned. Releases are built by CI
  ([docs/dev/android.md "Release"](../docs/dev/android.md#release)).

In the app, the launcher shows at the first launch: open **This device**,
or add another server's address, open it and sign in on its own page. The
first start after an install unpacks Python and its packages, which takes
longer than the starts after it. The bar's mark, *All servers…* or
Ctrl+Shift+L brings the launcher back; the chevron at the bar's right end,
or Ctrl+Shift+B, hides the bar, and the web app's View → *Server bar*
brings it back. In a debug build, `chrome://inspect` in a desktop Chrome
opens the page, the bar and the launcher.

## The emulator

With the SDK's command-line tools:

```bash
sdkmanager "platform-tools" "emulator" "platforms;android-36" "system-images;android-36;google_apis;x86_64"
avdmanager create avd -n gamma-tablet -k "system-images;android-36;google_apis;x86_64" -d pixel_tablet
emulator -avd gamma-tablet
```

On an Apple Silicon Mac, take the `arm64-v8a` system image instead. Then
`:app:installDebug` and start the app as above. A stylus can be injected:
`adb shell input stylus swipe <x1> <y1> <x2> <y2> 500` writes a stroke with
a pen armed, and on a PDF page selects text without one
(screen pixels, the page's CSS px times the density), and `adb shell input
stylus tap <x> <y>` taps. The emulator's hardware keyboard sends the
shell's shortcuts.

## The device test

```bash
bash ./gradlew :app:connectedDebugAndroidTest -Pgamma.abis=x86_64
```

`LocalServerStartTest` starts the server on the device for real, on a
data directory of its own (`cache/start-test/Gamma/local`), and reads a
PDF through it, its page picture included, which only PyMuPDF draws
([docs/dev/android.md "Tests"](../docs/dev/android.md#tests)). It skips
when the app starts its own server in the same process, which it does
when the last server opened in the app was This device: open another
server in the app first. The run uninstalls the app afterwards, and This
device's library with it, unless
`-Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true` is
passed, as CI passes it.

## Logs

- **In the app**, the launcher's card for This device has *Show server
  log*, which hands `server.log` to the share sheet; a failed start shows
  the log's last 4 KB in the launcher.
- **From a computer**, for a debug build (`run-as` needs a debuggable
  app):

  ```bash
  adb exec-out run-as com.gammapdf.gamma cat files/Gamma/local/logs/server.log    # This device's server
  adb exec-out run-as com.gammapdf.gamma cat files/Gamma/local/logs/server.state  # its supervisor's last word
  adb exec-out run-as com.gammapdf.gamma cat cache/start-test/Gamma/local/logs/server.log  # the device test's server
  adb logcat -s GammaPen GammaBridge python.stdout python.stderr AndroidRuntime
  ```

  The server's output goes to `server.log` once its entry has started;
  what Python printed before that, and the app's own warnings, are in
  logcat.
- **The tests' reports**: `app/build/reports/tests/testDebugUnitTest/`
  and `app/build/reports/androidTests/`.

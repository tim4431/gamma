// The Android app: a Kotlin shell over the Gamma web app with a native pen
// layer, and the Gamma server inside it on Chaquopy's CPython 3.13
// (docs/dev/android.md). The repository around android/ supplies the rest:
// the server (backend/), its mobile pins (backend/requirements-mobile.txt,
// shared with the iPad), the built web app (frontend/dist) and the desktop's
// chrome pages (desktop/ui).
//
// Properties (-P on the command line, or gradle.properties):
//   versionName, versionCode   the release's, from desktop.yml; a local build
//                              is "<desktop/package.json version>-dev", code 1
//   gamma.abis                 comma-separated ABIs, default arm64-v8a,x86_64
//                              (a release passes arm64-v8a: Chaquopy's Python
//                              packages are assets, which no split divides)
//   gamma.webBuild             "auto" (default) runs tools/build_frontend_if_needed.mjs;
//                              "prebuilt" only requires frontend/dist (CI)
//   gamma.buildPython          the Python 3.13 that runs pip, when Chaquopy's
//                              own search (py -3.13, python3.13, python3, python) misses it:
//                              an interpreter's path, or a command line split on spaces
// Signing, from the environment (unsigned without them, so forks build):
//   GAMMA_ANDROID_KEYSTORE, GAMMA_ANDROID_KEYSTORE_PASSWORD,
//   GAMMA_ANDROID_KEY_ALIAS, GAMMA_ANDROID_KEY_PASSWORD
import java.security.MessageDigest

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.chaquopy)
}

val repo: File = rootProject.projectDir.parentFile
fun prop(name: String): String? = providers.gradleProperty(name).orNull?.takeIf { it.isNotBlank() }

val floorVersion: String = Regex("\"version\"\\s*:\\s*\"([^\"]+)\"")
    .find(repo.resolve("desktop/package.json").readText())?.groupValues?.get(1) ?: "0.0.0"
val abis: List<String> = (prop("gamma.abis") ?: "arm64-v8a,x86_64").split(",").map { it.trim() }.filter { it.isNotEmpty() }
val keystore: String? = System.getenv("GAMMA_ANDROID_KEYSTORE")?.takeIf { it.isNotBlank() && File(it).isFile }

android {
    namespace = "com.gammapdf.gamma"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.gammapdf.gamma"
        minSdk = 29
        targetSdk = 36
        versionCode = prop("versionCode")?.toInt() ?: 1
        versionName = prop("versionName") ?: "$floorVersion-dev"
        ndk { abiFilters += abis }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = File(keystore)
                storePassword = System.getenv("GAMMA_ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("GAMMA_ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("GAMMA_ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures { buildConfig = true }

    testOptions {
        unitTests.all { it.systemProperty("gamma.repo", repo.path) }
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // A file's path as it is, spaces and all; else a command line (py -3.13).
        prop("gamma.buildPython")?.trim()?.let { python ->
            if (File(python).isFile) buildPython(python) else buildPython(*python.split(Regex("\\s+")).toTypedArray())
        }
        pip {
            // Flet's index publishes the Android builds of the native wheels
            // PyPI lacks; the same pins serve the iPad (docs/research/android.md).
            options("--extra-index-url", "https://pypi.flet.dev")
            install("-r", repo.resolve("backend/requirements-mobile.txt").path)
        }
        // The server reads files beside its modules (gamma's onboarding and
        // icons, certifi's bundle, the math fonts), so every package is a
        // real directory, extracted once per install.
        extractPackages("*")
    }
    sourceSets {
        getByName("main") {
            srcDir(repo.resolve("backend"))
            // tessdata/: the text recognition models (docs/dev/android.md
            // "What runs"). Chaquopy extracts every file of the app's code,
            // a folder that is no package too, so they land beside gamma/,
            // where config.tessdata_dir() looks when nothing says otherwise.
            include("gamma/**", "tessdata/**", "app.py", "gamma_mobile.py")
            exclude("**/__pycache__/**")
        }
    }
}

// The web app and the chrome pages, as assets: frontend/dist without its
// Brotli and gzip copies (the server sends the plain file when there is no
// copy, and nothing is gained by compressing over loopback) under
// frontend_dist/, with a list of its files and a stamp of their contents for
// the extraction at the first start (server/WebAssets.kt); desktop/ui under
// chrome/, served to the bar and the launcher by WebViewAssetLoader.
abstract class BundleWebAssets : DefaultTask() {
    @get:InputDirectory @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val chrome: DirectoryProperty

    @get:InputDirectory @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val webApp: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun bundle() {
        val out = outputDir.get().asFile
        out.deleteRecursively()
        val chromeDir = chrome.get().asFile
        chromeDir.walkTopDown().filter { it.isFile }.forEach {
            it.copyTo(out.resolve("chrome/" + it.relativeTo(chromeDir).invariantSeparatorsPath))
        }
        val webDir = webApp.get().asFile
        require(webDir.resolve("index.html").isFile) { "frontend/dist has no index.html: build the web app first" }
        val digest = MessageDigest.getInstance("SHA-256")
        val files = webDir.walkTopDown()
            .filter { it.isFile && !it.name.endsWith(".br") && !it.name.endsWith(".gz") && it.name != ".gamma-build.json" }
            .map { it.relativeTo(webDir).invariantSeparatorsPath to it }
            .sortedBy { it.first }
            .toList()
        for ((path, file) in files) {
            file.copyTo(out.resolve("frontend_dist/$path"))
            digest.update(path.toByteArray())
            digest.update(0)
            digest.update(file.readBytes())
        }
        out.resolve("frontend_dist.files").writeText(files.joinToString("\n") { it.first } + "\n")
        out.resolve("frontend_dist.stamp").writeText(digest.digest().joinToString("") { "%02x".format(it) } + "\n")
    }
}

// The web build, as the iPad's build-python.sh runs it: a no-op when
// frontend/dist is current. Under -Pgamma.webBuild=prebuilt (CI, which builds
// the web app once for every platform) it only has to exist.
val webBuild = tasks.register<Exec>("buildWebApp") {
    workingDir = repo
    onlyIf { prop("gamma.webBuild") != "prebuilt" }
    val npmCi = !repo.resolve("frontend/node_modules/.package-lock.json").isFile
    val windows = System.getProperty("os.name").lowercase().contains("windows")
    val shell = if (windows) listOf("cmd", "/c") else listOf("sh", "-c")
    commandLine(shell + ((if (npmCi) "npm ci --prefix frontend && " else "") + "node tools/build_frontend_if_needed.mjs"))
}

val bundleWebAssets = tasks.register<BundleWebAssets>("bundleWebAssets") {
    dependsOn(webBuild)
    chrome.set(repo.resolve("desktop/ui"))
    webApp.set(repo.resolve("frontend/dist"))
    outputDir.set(layout.buildDirectory.dir("generated/gammaAssets"))
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(bundleWebAssets, BundleWebAssets::outputDir)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.webkit)
    implementation(libs.androidx.browser)
    implementation(libs.androidx.graphics.core)
    implementation(libs.androidx.input.motionprediction)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(libs.junit)
    testImplementation(libs.json)
    testImplementation(libs.kotlinx.coroutines.test)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.ext.junit)
}

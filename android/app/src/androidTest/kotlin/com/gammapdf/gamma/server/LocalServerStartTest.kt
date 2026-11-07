package com.gammapdf.gamma.server

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import com.gammapdf.gamma.app.Credentials
import com.gammapdf.gamma.app.GammaApplication
import com.gammapdf.gamma.app.LocalState
import com.gammapdf.gamma.app.Prefs
import com.gammapdf.gamma.json.Json
import com.gammapdf.gamma.json.jsonDouble
import com.gammapdf.gamma.json.jsonInt
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The embedded server for real, in the app's process on a device or an
 * emulator (ports LocalServerStartTests, ipad/GammaIPadTests/ServerTests.swift):
 * Chaquopy's Python, the wheels from Flet's index and the backend start and
 * answer /api/health, and a PDF uploaded to the server is read through
 * PyMuPDF. CI has no other check of the packaged server, so a missing wheel,
 * a library that does not load or an import error fails here instead of on
 * a tablet. /api/health proves the start but not the reader, which the
 * server imports at its first PDF read: the manifest's page sizes and a page
 * picture both come from PyMuPDF, and page-image answers 404 when it draws
 * nothing.
 *
 * The server runs on a data directory of the test's own, made fresh each
 * run in the app's cache, with a vault of its own and a seed for a vault
 * that refuses, so the account is one the test knows and the person's
 * library is never touched. The interpreter starts once per process, so this
 * is one test, skipped when the app starts its own server.
 */
@RunWith(AndroidJUnit4::class)
class LocalServerStartTest {
    /** Prefs in memory: the port is picked afresh, and nothing is kept. */
    private class MemoryPrefs : Prefs {
        private val values = mutableMapOf<String, Any?>()
        override fun string(key: String) = values[key] as? String
        override fun putString(key: String, value: String?) = put(key, value)
        override fun bool(key: String, default: Boolean) = values[key] as? Boolean ?: default
        override fun putBool(key: String, value: Boolean) = put(key, value)
        override fun int(key: String) = values[key] as? Int
        override fun putInt(key: String, value: Int?) = put(key, value)
        private fun put(key: String, value: Any?) {
            if (value == null) values.remove(key) else values[key] = value
        }
    }

    /** What a call answered: the status, the Content-Type and the body. */
    private class Answer(val status: Int, val type: String, val body: ByteArray, val cookies: List<String>) {
        /** The body as a JSON object, or empty. */
        val json: Map<String, Any?> get() = Json.objectOrNull(String(body, Charsets.UTF_8)) ?: emptyMap()

        /** The body for a failure message: its head, or a picture's size. */
        val text: String
            get() = if (type.startsWith("image/")) "${body.size} bytes of $type" else String(body, Charsets.UTF_8).take(300)
    }

    @Test
    fun theEmbeddedServerStartsAndAnswers(): Unit = runBlocking {
        // A device whose last pick was This device starts the server with
        // the app, and a second interpreter cannot start. The app's own
        // server tells: it leaves Stopped in Application.onCreate, before any
        // test runs, while the interpreter starts only once the web app is
        // unpacked, which can still be under way.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val appServer = (context.applicationContext as GammaApplication).local
        val appStarted = withContext(Dispatchers.Main) { appServer.state.let { it == LocalState.Starting || it is LocalState.Running } }
        assumeFalse("the app started its own server already (its last pick was This device)", appStarted || PythonRuntime.started)

        val data = File(context.cacheDir, "start-test/Gamma/local").apply { deleteRecursively() }
        // The account a fresh data directory is seeded with where the vault
        // refuses the one LocalServer makes for it. A fixed password rather
        // than a random one: it lives for one run, on a server that listens
        // on loopback only, in a directory the next run deletes.
        val seed = Credentials(Credentials.ACCOUNT, "start-test-pw")
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
        val server = withContext(Dispatchers.Main) {
            LocalServer.onDevice(
                context,
                MemoryPrefs(),
                scope,
                dataDirectory = data,
                vault = KeystoreVault(context, "gamma.start-test"),
                seed = seed,
            ).also { it.start() }
        }
        try {
            // Longer than LocalServer's slow mark: the first start after an
            // install unpacks Python and every package, and a cold emulator on
            // a CI runner imports the whole server.
            val deadline = System.nanoTime() + 180_000_000_000L
            var url: String? = null
            while (url == null) {
                when (val state = withContext(Dispatchers.Main) { server.state }) {
                    is LocalState.Running -> url = state.url
                    is LocalState.Failed -> throw AssertionError("${state.message}\n${server.logTail()}")
                    LocalState.Starting, LocalState.Stopped -> {
                        if (System.nanoTime() > deadline) throw AssertionError("the server did not answer in 3 minutes\n${server.logTail()}")
                        delay(500)
                    }
                }
            }
            val api = "$url/api"
            val health = call("GET", "$api/health")
            val up = Health.isUp(health.status, health.body)
            assertTrue("/api/health answered ${health.status}", up)
            // Up by the desktop's rule, but standing aside: a data directory
            // this build cannot upgrade, which answers every /api call with the
            // same 503 (gamma/app.py _blocked_app). Not expected on a directory
            // made fresh, but nothing below could run.
            assumeFalse("the data directory cannot be upgraded by this build", health.status == 503)

            // The PDF part signs in as the seeded account: a fresh directory's
            // is one this test knows.
            val login = withContext(Dispatchers.Main) { server.login }
                ?: throw AssertionError("a fresh data directory is seeded with an account this test knows, but the server has none\n${server.logTail()}")
            val signedIn = call("POST", "$api/login", Json.write(mapOf("username" to login.username, "password" to login.password)).toByteArray(), "application/json")
            if (signedIn.status != 200) failed("POST /api/login", signedIn, server)
            // The session cookie: Android's HttpURLConnection keeps none itself.
            val cookie = signedIn.cookies.joinToString("; ") { it.substringBefore(';') }

            // Every call below works in the account's default workspace (no
            // X-Gamma-Workspace header, docs/dev/api.md "Auth model").
            val boundary = "gamma-${UUID.randomUUID()}"
            val form = ByteArrayOutputStream().apply {
                write("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"start-test.pdf\"\r\nContent-Type: application/pdf\r\n\r\n".toByteArray())
                write(onePagePdf())
                write("\r\n--$boundary--\r\n".toByteArray())
            }.toByteArray()
            val uploaded = call("POST", "$api/uploads", form, "multipart/form-data; boundary=$boundary", cookie)
            val docId = uploaded.json["doc_id"] as? String
            if (uploaded.status != 200 || docId.isNullOrEmpty()) failed("POST /api/uploads", uploaded, server)

            // The page that carries the PDF, which page-image is asked by.
            val page = call("POST", "$api/blocks/by-doc/$docId", Json.write(mapOf("default_title" to "The start test's PDF")).toByteArray(), "application/json", cookie)
            val pageId = page.json["id"] as? String
            if (page.status != 200 || pageId.isNullOrEmpty()) failed("POST /api/blocks/by-doc", page, server)

            // The manifest: the walk over the pages (pdf_meta), in PDF points.
            val manifest = call("GET", "$api/pdf-info/$docId", cookie = cookie)
            val dims = (manifest.json["dims"] as? List<*>)?.map { (it as? List<*>)?.map { n -> n.jsonDouble() } }
            if (manifest.status != 200 || manifest.json["pages"].jsonInt() != 1 || dims != listOf(listOf(612.0, 792.0))) {
                failed("GET /api/pdf-info (the manifest walk)", manifest, server)
            }

            // The picture: MuPDF rendered the page, or the server answers 404
            // here.
            val picture = call("GET", "$api/ai/page-image/$pageId?page=1", cookie = cookie)
            if (picture.status != 200 || !picture.type.startsWith("image/") || picture.body.size <= 2048) {
                failed("GET /api/ai/page-image (MuPDF rendering the page)", picture, server)
            }
        } finally {
            scope.cancel()
        }
    }

    /** A call that did not answer as expected: what it answered and the end of the server's log. */
    private fun failed(call: String, answer: Answer, server: LocalServer): Nothing =
        throw AssertionError("$call answered ${answer.status}: ${answer.text}\n${server.logTail()}")

    private suspend fun call(
        method: String,
        url: String,
        body: ByteArray? = null,
        type: String? = null,
        cookie: String? = null,
    ): Answer = withContext(Dispatchers.IO) {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.useCaches = false
            connection.connectTimeout = 30_000
            connection.readTimeout = 60_000
            if (cookie != null && cookie.isNotEmpty()) connection.setRequestProperty("Cookie", cookie)
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", type)
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            Answer(
                status = status,
                type = connection.contentType.orEmpty(),
                body = stream?.use { it.readBytes() } ?: ByteArray(0),
                cookies = connection.headerFields["Set-Cookie"].orEmpty(),
            )
        } finally {
            connection.disconnect()
        }
    }

    /**
     * A one-page PDF with a line of Helvetica text, the shape of the browser
     * suite's makePdf (frontend/tests/e2e/harness.mjs): US Letter, one content
     * stream, a correct xref. ASCII throughout, so the offsets are character
     * counts.
     */
    private fun onePagePdf(): ByteArray {
        val content = "BT\n/F1 20 Tf\n72 720 Td\n26 TL\n(Gamma on this device reads PDFs through MuPDF.) Tj T*\nET"
        val objects = listOf(
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Length ${content.length} >>\nstream\n$content\nendstream",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>",
        )
        val out = StringBuilder("%PDF-1.4\n")
        val offsets = mutableListOf<Int>()
        objects.forEachIndexed { index, body ->
            offsets += out.length
            out.append("${index + 1} 0 obj\n$body\nendobj\n")
        }
        val xref = out.length
        out.append("xref\n0 ${objects.size + 1}\n0000000000 65535 f \n")
        for (offset in offsets) out.append(offset.toString().padStart(10, '0')).append(" 00000 n \n")
        out.append("trailer\n<< /Size ${objects.size + 1} /Root 1 0 R >>\nstartxref\n$xref\n%%EOF\n")
        return out.toString().toByteArray(Charsets.US_ASCII)
    }
}

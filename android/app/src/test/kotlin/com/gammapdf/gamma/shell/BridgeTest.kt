package com.gammapdf.gamma.shell

import java.io.File
import com.gammapdf.gamma.json.Json
import com.gammapdf.gamma.pen.InkKind
import com.gammapdf.gamma.pen.InkTool
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The host contract's Kotlin half (shell/Bridge.kt), ported from
 * ipad/GammaIPadTests/BridgeTests.swift: what the web posts decodes as
 * docs/dev/ipad.md "The host contract" says, and what the shell sends
 * encodes the same way. The claim's look (InkTool) and the hold's reply
 * (HoldReply) are the pen layer's, tested in pen/; the web half is tested in
 * frontend/tests/host.test.mjs and the `android` browser group.
 */
class BridgeTest {
    /** A message as the transport hands it over: JSON text, decoded. */
    private fun decode(json: String): WebMessage = WebMessage.decode(Json.parse(json))

    private fun refused(json: String) {
        assertThrows(json, IllegalArgumentException::class.java) { decode(json) }
    }

    /** A message as the page receives it: the JSON text, decoded again. */
    @Suppress("UNCHECKED_CAST")
    private fun sent(message: HostMessage): Map<String, Any?> = Json.parse(message.json) as Map<String, Any?>

    @Test
    fun aClaimDecodesWithItsTool() {
        assertEquals(
            WebMessage.Claim(7, 120.5, 300.0, InkTool(InkKind.HIGHLIGHTER, "#fde047", 18.0, opacity = 0.35)),
            decode("""{"type":"claim","pointerId":7,"at":{"x":120.5,"y":300},"tool":{"kind":"highlighter","color":"#fde047","size":18,"opacity":0.35}}"""),
        )
    }

    @Test
    fun aToolWithoutOpacityIsOpaque() {
        val claim = decode("""{"type":"claim","pointerId":1,"at":{"x":0,"y":0},"tool":{"kind":"pen","color":"#111111","size":2}}""")
        assertEquals(1.0, (claim as WebMessage.Claim).tool.opacity, 0.0)
    }

    @Test
    fun themeDecodesWithItsName() {
        assertEquals(WebMessage.Theme("#1e1e1e", Scheme.DARK, "gamma-dark"),
                     decode("""{"type":"theme","bg":"#1e1e1e","scheme":"dark","name":"gamma-dark"}"""))
        assertEquals("a page from before the name", WebMessage.Theme("#1e1e1e", Scheme.DARK, null),
                     decode("""{"type":"theme","bg":"#1e1e1e","scheme":"dark"}"""))
        assertEquals("a name that is no string is dropped, not the theme", WebMessage.Theme("#fff", Scheme.LIGHT, null),
                     decode("""{"type":"theme","bg":"#fff","scheme":"light","name":3}"""))
    }

    @Test
    fun theOpenedFilesMessagesDecode() {
        assertEquals(WebMessage.Files, decode("""{"type":"files"}"""))
        assertEquals(WebMessage.FileData("F1", 2_097_152), decode("""{"type":"fileData","id":"F1","offset":2097152}"""))
        assertEquals(WebMessage.FileDone("F1"), decode("""{"type":"fileDone","id":"F1"}"""))
        refused("""{"type":"fileData","id":"F1"}""") // a read without its offset
        refused("""{"type":"fileData","id":"F1","offset":2.5}""") // an offset is a whole number
        refused("""{"type":"fileDone"}""") // done without the file
    }

    @Test
    fun anOpenedFileIsAnnouncedByTypeAlone() {
        assertEquals("""{"type":"filesOpened"}""", HostMessage.FilesOpened.json)
    }

    @Test
    fun theBarsChoiceGoesBothWays() {
        assertEquals(WebMessage.ShellBar(false), decode("""{"type":"shellBar","shown":false}"""))
        assertEquals("only asked how it stands", WebMessage.ShellBar(null), decode("""{"type":"shellBar"}"""))
        assertEquals("a shown that is no Bool only asks", WebMessage.ShellBar(null), decode("""{"type":"shellBar","shown":"no"}"""))
        assertEquals(mapOf("type" to "shellBar", "shown" to true), sent(HostMessage.ShellBar(true)))
    }

    @Test
    fun aHapticDecodesWithItsPlace() {
        assertEquals(WebMessage.Haptic(HapticKind.SNAP, 300.0, 412.5), decode("""{"type":"haptic","kind":"snap","at":{"x":300,"y":412.5}}"""))
        assertEquals(WebMessage.Haptic(HapticKind.SELECT, 1.0, 2.0), decode("""{"type":"haptic","kind":"select","at":{"x":1,"y":2}}"""))
        assertEquals("a tool switch has no place", WebMessage.Haptic(HapticKind.TOOL), decode("""{"type":"haptic","kind":"tool"}"""))
        assertEquals("an unknown kind is a tick all the same", WebMessage.Haptic(HapticKind.TOOL),
                     decode("""{"type":"haptic","kind":"buzz","at":"here"}"""))
        assertEquals("a place without its y is none", WebMessage.Haptic(HapticKind.SNAP), decode("""{"type":"haptic","kind":"snap","at":{"x":1}}"""))
    }

    @Test
    fun aFileToSaveComesInChunks() {
        assertEquals(WebMessage.Save("Paper (draft).pdf", "application/pdf", 5_242_880),
                     decode("""{"type":"save","name":"Paper (draft).pdf","mime":"application/pdf","size":5242880}"""))
        assertEquals("a name and type the page could not give", WebMessage.Save("", "", 12), decode("""{"type":"save","size":12}"""))
        refused("""{"type":"save","name":"a.svg","mime":"image/svg+xml"}""") // no size: it could never be complete
        refused("""{"type":"save","name":"a.svg","mime":"image/svg+xml","size":"12"}""")

        val chunk = decode("""{"type":"saveData","id":"S1","offset":2097150,"data":"R2FtbWEg8J+Ruw=="}""") as WebMessage.SaveData
        assertEquals("S1", chunk.id)
        assertEquals(2_097_150L, chunk.offset)
        assertArrayEquals("Gamma \uD83D\uDC7B".toByteArray(Charsets.UTF_8), chunk.data)
        assertEquals(chunk, decode("""{"type":"saveData","id":"S1","offset":2097150,"data":"R2FtbWEg8J+Ruw=="}"""))
        assertEquals(0, (decode("""{"type":"saveData","id":"S1","offset":0,"data":""}""") as WebMessage.SaveData).data.size)
        refused("""{"type":"saveData","id":"S1","offset":0,"data":"not base64!"}""")
        refused("""{"type":"saveData","id":"S1","data":"QQ=="}""") // no offset
        refused("""{"type":"saveData","offset":0,"data":"QQ=="}""") // no file

        assertEquals(WebMessage.SaveDone("S1", false), decode("""{"type":"saveDone","id":"S1"}"""))
        assertEquals(WebMessage.SaveDone("S1", true), decode("""{"type":"saveDone","id":"S1","cancel":true}"""))
        assertEquals("a cancel that is no true is none", WebMessage.SaveDone("S1", false),
                     decode("""{"type":"saveDone","id":"S1","cancel":"yes"}"""))
        refused("""{"type":"saveDone"}""")

        assertEquals(WebMessage.SaveUrl("http://127.0.0.1:50123/api/jobs/j1/download", "export.zip"),
                     decode("""{"type":"saveUrl","url":"http://127.0.0.1:50123/api/jobs/j1/download","name":"export.zip"}"""))
        assertEquals(WebMessage.SaveUrl("https://a.example/f", ""), decode("""{"type":"saveUrl","url":"https://a.example/f"}"""))
        refused("""{"type":"saveUrl","name":"export.zip"}""")
    }

    @Test
    fun theScreenStaysOnOnlyWhenThePageSaysSo() {
        assertEquals(WebMessage.KeepAwake(true), decode("""{"type":"keepAwake","on":true}"""))
        assertEquals(WebMessage.KeepAwake(false), decode("""{"type":"keepAwake","on":false}"""))
        assertEquals("no on: off", WebMessage.KeepAwake(false), decode("""{"type":"keepAwake"}"""))
        assertEquals("an on that is no true: off", WebMessage.KeepAwake(false), decode("""{"type":"keepAwake","on":"yes"}"""))
        assertEquals("a number is no true either", WebMessage.KeepAwake(false), decode("""{"type":"keepAwake","on":1}"""))
    }

    @Test
    fun anythingElseIsRefused() {
        refused("""{"type":"open-page"}""") // an unknown type
        refused("""{"type":"servers"}""") // no message of the host contract
        refused("""{"pointerId":1}""") // no type
        refused("""{"type":"claim","pointerId":1,"at":{"x":0,"y":0}}""") // a claim without its tool
        refused("""{"type":"claim","pointerId":1,"tool":{"kind":"pen","color":"#000","size":1}}""") // without its place
        refused("""{"type":"claim","pointerId":1,"at":{"x":0},"tool":{"kind":"pen","color":"#000","size":1}}""")
        refused("""{"type":"claim","at":{"x":0,"y":0},"tool":{"kind":"pen","color":"#000","size":1}}""") // without its pointer
        refused("""{"type":"claim","pointerId":1,"at":{"x":0,"y":0},"tool":{"color":"#000","size":1}}""") // a tool without its kind
        refused("""{"type":"claim","pointerId":1,"at":{"x":0,"y":0},"tool":{"kind":"pen","color":"#000"}}""") // without its size
        refused("""{"type":"theme","bg":"#fff","scheme":"sepia"}""") // an unknown scheme
        refused("""{"type":"theme","scheme":"light"}""") // no colour
        refused("\"claim\"") // not an object
        assertThrows(IllegalArgumentException::class.java) { WebMessage.decode(null) }
    }

    @Test
    fun aStrokeEncodesItsSamples() {
        val out = sent(HostMessage.Stroke(3, listOf(doubleArrayOf(10.0, 20.5, 0.4, 0.0), doubleArrayOf(11.0, 21.0, 0.5, 4.2))))
        assertEquals("stroke", out["type"])
        assertEquals(3, out["pointerId"])
        assertEquals(listOf(listOf(10, 20.5, 0.4, 0), listOf(11, 21, 0.5, 4.2)), numbers(out["samples"]))
        assertEquals("no shape drawn: no hold named", setOf("type", "pointerId", "samples"), out.keys)
        assertEquals("""{"type":"stroke","pointerId":9,"samples":[]}""", HostMessage.Stroke(9, emptyList()).json)
    }

    @Test
    fun aStrokeNamesTheHoldWhoseShapeItWasDrawnAs() {
        val out = sent(HostMessage.Stroke(61, listOf(doubleArrayOf(10.0, 20.0, 0.5, 0.0), doubleArrayOf(40.0, 20.0, 0.5, 300.0)), held = 22))
        assertEquals(22, out["held"])
        assertEquals(setOf("type", "pointerId", "samples", "held"), out.keys)
    }

    @Test
    fun aHoldEncodesTheSamplesSoFar() {
        val samples = listOf(doubleArrayOf(10.0, 20.0, 0.4, 0.0, 60.0, 135.0), doubleArrayOf(40.0, 20.0, 0.5, 300.0, 58.5, 134.0))
        val out = sent(HostMessage.Hold(61, samples))
        assertEquals("hold", out["type"])
        assertEquals(61, out["pointerId"])
        assertEquals(samples.map { s -> s.toList() }, numbers(out["samples"]).map { s -> s.map { (it as Number).toDouble() } })
        assertEquals(setOf("type", "pointerId", "samples"), out.keys)
    }

    @Test
    fun aPenButtonCarriesItsAction() {
        assertEquals(mapOf("type" to "pencil", "gesture" to "tap", "action" to "switchEraser"),
                     sent(HostMessage.Pencil(PencilGesture.TAP, "switchEraser")))
        val out = sent(HostMessage.Pencil(PencilGesture.SQUEEZE, "switchPrevious"))
        assertEquals("squeeze", out["gesture"])
        assertEquals("switchPrevious", out["action"])
        assertEquals("no phase: a click, or an older shell's squeeze", setOf("type", "gesture", "action"), out.keys)
    }

    @Test
    fun aSqueezesEndSaysItEnded() {
        val out = sent(HostMessage.Pencil(PencilGesture.SQUEEZE, "switchEraser", SqueezePhase.ENDED))
        assertEquals("pencil", out["type"])
        assertEquals("ended", out["phase"])
        assertEquals(setOf("type", "gesture", "action", "phase"), out.keys)
    }

    @Test
    fun aSqueezesStartAndCancelHaveATypeOfTheirOwn() {
        val began = sent(HostMessage.Squeeze(SqueezePhase.BEGAN, "switchEraser"))
        assertEquals(mapOf("type" to "squeeze", "phase" to "began", "action" to "switchEraser"), began)
        assertEquals("cancelled", sent(HostMessage.Squeeze(SqueezePhase.CANCELLED, "ignore"))["phase"])
    }

    @Test
    fun theHostInfoDeclaresTheAndroidHost() {
        assertEquals(
            """{"haptics":true,"hold":true,"ink":true,"inkLooks":["pressure","dash","blend","stabilize","pencil","calligraphy","snap"],"keepAwake":true,""" +
                """"openFiles":true,"pencil":true,"pencilGestures":["tap","squeeze"],"platform":"android","save":true,"shellBar":true,"version":"0.2.0"}""",
            HostInfo("0.2.0", haptics = true).json,
        )
        val quiet = Json.parse(HostInfo("0.2.0-dev", haptics = false).json) as Map<*, *>
        assertEquals("no vibrator, no haptics", false, quiet["haptics"])
        assertFalse("no squeeze phases on a stylus", "pencilPhases" in quiet)
        assertFalse("no barrel roll on a stylus", "pencilRoll" in quiet)
    }

    @Test
    fun theDocumentStartScriptCarriesTheFlags() {
        val template = File(System.getProperty("gamma.repo"), "android/app/src/main/assets/shell/host.js").readText()
        assertTrue("the script has the placeholder it is filled at", HostInfo.PLACEHOLDER in template)
        val info = HostInfo("0.2.0", haptics = true)
        val script = info.script(template)
        assertFalse(HostInfo.PLACEHOLDER in script)
        assertTrue(script, "const info = ${info.json};" in script)
        assertTrue("the page's handlers carry the platform", "setAttribute(\"data-host\", info.platform)" in script)
        assertTrue("the stub queues until host.js drains it", "queue.push({ msg, resolve, reject })" in script)
        // A version is JSON in the script, so nothing in it can end the literal.
        val odd = HostInfo("1.0\"; alert(1); \"\u2028", haptics = false).script(template)
        assertTrue("\"version\":\"1.0\\\"; alert(1); \\\"\\u2028\"" in odd)
    }

    /** The samples as numbers, whatever type the JSON reader gave each. */
    private fun numbers(samples: Any?): List<List<Any?>> = (samples as List<*>).map { (it as List<*>).map { v -> normal(v) } }

    private fun normal(v: Any?): Any? = (v as? Number)?.toDouble()?.let { if (it == Math.rint(it)) it.toInt() else it } ?: v
}

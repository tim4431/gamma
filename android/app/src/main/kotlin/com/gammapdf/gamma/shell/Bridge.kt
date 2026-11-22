package com.gammapdf.gamma.shell

import java.util.Base64
import com.gammapdf.gamma.json.Json
import com.gammapdf.gamma.json.jsonDouble
import com.gammapdf.gamma.json.jsonInt
import com.gammapdf.gamma.json.jsonObject
import com.gammapdf.gamma.pen.InkTool

// The one place that knows the protocol between the web app and the shell,
// ported from ipad/GammaIPad/Shell/Bridge.swift. The web half is
// frontend/src/shared/lib/host.js, and assets/shell/host.js carries both
// directions over the WebView's message port (shell/HostBridge.kt); both are
// described in docs/dev/ipad.md "The host contract". The vocabulary is kept
// small on purpose: a web feature reaches the app without touching it, and a
// new message is added here and in host.js together, with a flag in HostInfo
// saying what the web may rely on (`version` only names the build).

/**
 * What the shell declares as `window.gammaNative` before the bundle runs. A
 * flag left out is a capability the web leaves out; the Android app declares
 * no `pencilPhases` and no `pencilRoll` (a stylus has no squeeze phases and
 * no barrel-roll axis).
 */
data class HostInfo(
    /** The app's versionName. */
    val version: String,
    /** The device has a vibrator for `haptic` (PenHaptics). */
    val haptics: Boolean,
) {
    /**
     * The flags. `ink`: the host captures claimed stylus strokes. `inkLooks`:
     * what of the claim's look the live line draws (pen/LiveInk.kt). `hold`:
     * the host sends `hold` when a captured stylus stays still. `pencil` and
     * `pencilGestures`: the stylus's buttons, clicked out of contact, come as
     * `pencil` (`tap` the first button, `squeeze` the second). `openFiles`:
     * the host holds the files opened in the app (OpenedFiles). `save`: the
     * host saves files the page hands it (Saves). `shellBar`: the bar above
     * the page can be hidden, and the page offers it back. `keepAwake`: the
     * screen stays on while the page says (`keepAwake`).
     */
    val fields: Map<String, Any?>
        get() = mapOf(
            "platform" to PLATFORM,
            "version" to version,
            "ink" to true,
            "inkLooks" to INK_LOOKS,
            "hold" to true,
            "pencil" to true,
            "pencilGestures" to PENCIL_GESTURES,
            "haptics" to haptics,
            "openFiles" to true,
            "save" to true,
            "shellBar" to true,
            "keepAwake" to true,
        )

    /** The flags as JSON, keys sorted. */
    val json: String get() = Json.write(fields, sortKeys = true)

    /** assets/shell/host.js with this host's flags in place of its placeholder. */
    fun script(template: String): String = template.replace(PLACEHOLDER, json)

    companion object {
        const val PLATFORM = "android"
        const val PLACEHOLDER = "__GAMMA_HOST_INFO__"
        val INK_LOOKS = listOf("pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap")
        val PENCIL_GESTURES = listOf("tap", "squeeze")
    }
}

/** What a `haptic` is for: each plays as its own feedback (PenHaptics). */
enum class HapticKind(val wire: String) {
    /** A held stroke snapped to a shape. */
    SNAP("snap"),

    /** A pen lasso selected strokes. */
    SELECT("select"),

    /** A pen button switched the tool; also a kind the shell does not know. */
    TOOL("tool"),
}

enum class Scheme(val wire: String) { LIGHT("light"), DARK("dark") }

/**
 * Web → native: what `window.gammaNative.post(msg)` carries. A message that
 * does not decode gets an error reply ([NOT_UNDERSTOOD]), so the page's
 * promise rejects; a newer page's message reaches an older app that way.
 */
sealed interface WebMessage {
    /**
     * This stylus touch is a stroke: take it. Replied `{taken}`. (x, y) is
     * where the pointer went down, in viewport CSS px, which tells the shell
     * which touch the page means (ClaimQueue).
     */
    data class Claim(val pointerId: Int, val x: Double, val y: Double, val tool: InkTool) : WebMessage

    /**
     * Paint the window behind the page and pick the system bars' icons.
     * `name` is the theme's (`<html data-theme>`), which the chrome paints
     * in; a page from before it sends none, and the scheme is a theme's name too.
     */
    data class Theme(val bg: String, val scheme: Scheme, val name: String?) : WebMessage

    /** Play the kind's feedback, once per event; (x, y) is where, when the page says. */
    data class Haptic(val kind: HapticKind, val x: Double? = null, val y: Double? = null) : WebMessage

    /** The opened files the shell still holds: replied `{files: [{id, name, size, type}]}`. */
    data object Files : WebMessage

    /** Up to OpenedFiles.CHUNK_BYTES of a held file from `offset`: replied `{data}`, or `{}`. */
    data class FileData(val id: String, val offset: Long) : WebMessage

    /** The page is done with a held file: the shell drops its copy. Replied `{}`. */
    data class FileDone(val id: String) : WebMessage

    /**
     * The page's View menu: the bar shown or hidden by the person, or with
     * no Bool `shown` only asked how that stands. Replied `{shown}`, the
     * person's choice (the bar still shows while it is needed).
     */
    data class ShellBar(val shown: Boolean?) : WebMessage

    /** Begin a file to save, of exactly `size` bytes: replied `{id}`, or `{}` to refuse it (Saves). */
    data class Save(val name: String, val mime: String, val size: Long) : WebMessage

    /**
     * The next bytes of a file being saved, at `offset`, the bytes so far
     * (the page sends 2,097,150 at a time, each chunk base64 on its own).
     * Replied `{}`; an unknown id or an offset out of turn is an error reply.
     */
    class SaveData(val id: String, val offset: Long, val data: ByteArray) : WebMessage {
        override fun equals(other: Any?): Boolean =
            other is SaveData && other.id == id && other.offset == offset && other.data.contentEquals(data)

        override fun hashCode(): Int = (id.hashCode() * 31 + offset.hashCode()) * 31 + data.contentHashCode()

        override fun toString(): String = "SaveData(id=$id, offset=$offset, ${data.size} bytes)"
    }

    /** The file is complete, or with `cancel` dropped: replied `{saved}` at once, then presented. */
    data class SaveDone(val id: String, val cancel: Boolean) : WebMessage

    /**
     * Save what is served at `url`, fetched by the shell with the WebView's
     * cookies for that address (SavePresenter). Replied `{}`. The name is
     * `name` when the page gives one (the person's name for the file), else
     * the response's Content-Disposition, else the URL's last segment.
     */
    data class SaveUrl(val url: String, val name: String) : WebMessage

    /**
     * The screen kept on (`on`), or let sleep again: while the page reads a
     * scan's text in the window, which pauses while the app is hidden. Only
     * an `on` of true keeps it on, so a message the shell cannot read never
     * holds the screen. Replied `{}`.
     */
    data class KeepAwake(val on: Boolean) : WebMessage

    companion object {
        /** The error reply to a message that does not decode. */
        const val NOT_UNDERSTOOD = "Gamma for Android does not understand this message."

        /** The error reply to a message from anything but the server's own main frame. */
        const val ONLY_THE_SERVER = "Only the Gamma server's page talks to the app."

        /**
         * A message as the page posted it (decoded JSON: Map, List, Number,
         * String, Boolean, null). Throws IllegalArgumentException when it is
         * not a message of the host contract or a field it needs is missing
         * or of the wrong type; optional fields that cannot be read are dropped.
         */
        fun decode(body: Any?): WebMessage {
            val m = body.jsonObject() ?: refuse("not an object")
            return when (val type = m["type"]) {
                "claim" -> {
                    val at = m["at"].jsonObject() ?: refuse("a claim without its place")
                    Claim(int(m, "pointerId"), double(at, "x"), double(at, "y"),
                          InkTool.decode(m["tool"].jsonObject() ?: refuse("a claim without its tool")))
                }
                "theme" -> {
                    val scheme = Scheme.entries.firstOrNull { it.wire == m["scheme"] } ?: refuse("an unknown scheme")
                    Theme(string(m, "bg"), scheme, m["name"] as? String)
                }
                "haptic" -> {
                    val kind = HapticKind.entries.firstOrNull { it.wire == m["kind"] } ?: HapticKind.TOOL
                    val at = m["at"].jsonObject()
                    val x = at?.get("x").jsonDouble()
                    val y = at?.get("y").jsonDouble()
                    if (x != null && y != null) Haptic(kind, x, y) else Haptic(kind)
                }
                "files" -> Files
                "fileData" -> FileData(string(m, "id"), long(m, "offset"))
                "fileDone" -> FileDone(string(m, "id"))
                "shellBar" -> ShellBar(m["shown"] as? Boolean)
                "save" -> Save(m["name"] as? String ?: "", m["mime"] as? String ?: "", long(m, "size"))
                "saveData" -> SaveData(string(m, "id"), long(m, "offset"), Base64.getDecoder().decode(string(m, "data")))
                "saveDone" -> SaveDone(string(m, "id"), m["cancel"] == true)
                "saveUrl" -> SaveUrl(string(m, "url"), m["name"] as? String ?: "")
                "keepAwake" -> KeepAwake(m["on"] == true)
                else -> refuse("unknown message $type")
            }
        }

        private fun refuse(why: String): Nothing = throw IllegalArgumentException(why)

        private fun string(m: Map<String, Any?>, key: String): String = m[key] as? String ?: refuse("no string $key")

        private fun double(m: Map<String, Any?>, key: String): Double = m[key].jsonDouble() ?: refuse("no number $key")

        private fun int(m: Map<String, Any?>, key: String): Int = m[key].jsonInt() ?: refuse("no whole number $key")

        private fun long(m: Map<String, Any?>, key: String): Long {
            val d = m[key].jsonDouble()?.takeIf { it.isFinite() && it == Math.rint(it) && kotlin.math.abs(it) < 9.007199254740992E15 }
            return d?.toLong() ?: refuse("no whole number $key")
        }
    }
}

enum class PencilGesture(val wire: String) { TAP("tap"), SQUEEZE("squeeze") }

/** A squeeze's phase on the iPad (an Apple Pencil Pro); the Android app sends its button clicks without one. */
enum class SqueezePhase(val wire: String) { BEGAN("began"), ENDED("ended"), CANCELLED("cancelled") }

/** Native → web: `window.gammaNative.receive(msg)`, awaited. Optional keys are left out when null. */
sealed interface HostMessage {
    /** The message as plain JSON values, keys in the iPad's order. */
    fun encode(): Map<String, Any?>

    val json: String get() = Json.write(encode())

    /**
     * The finished stroke: `[x, y, p, t]` or `[x, y, p, t, a, z]` per sample
     * (pen/StrokeRecorder.kt). No samples: the system cancelled the touch, or
     * the page took the hold. `held` is the `n` of the hold whose shape the
     * line was drawn as, null when none was: the page commits a shape only
     * for that hold. The web resolves once the stroke is painted.
     */
    data class Stroke(val pointerId: Int, val samples: List<DoubleArray>, val held: Int? = null) : HostMessage {
        override fun encode(): Map<String, Any?> =
            linkedMapOf<String, Any?>("type" to "stroke", "pointerId" to pointerId, "samples" to samples)
                .also { if (held != null) it["held"] = held }
    }

    /** The captured stylus has held still: the samples so far. Asked; the page answers at once (HoldReply). */
    data class Hold(val pointerId: Int, val samples: List<DoubleArray>) : HostMessage {
        override fun encode(): Map<String, Any?> = linkedMapOf("type" to "hold", "pointerId" to pointerId, "samples" to samples)
    }

    /** A pen gesture with the action it asks for (the iPad's preferred-action vocabulary, pencilActions.js). */
    data class Pencil(val gesture: PencilGesture, val action: String, val phase: SqueezePhase? = null) : HostMessage {
        override fun encode(): Map<String, Any?> =
            linkedMapOf<String, Any?>("type" to "pencil", "gesture" to gesture.wire, "action" to action)
                .also { if (phase != null) it["phase"] = phase.wire }
    }

    /** A squeeze's start or its cancel, a type of its own so a page that predates it ignores both. */
    data class Squeeze(val phase: SqueezePhase, val action: String) : HostMessage {
        override fun encode(): Map<String, Any?> = linkedMapOf("type" to "squeeze", "phase" to phase.wire, "action" to action)
    }

    /** A file was opened in the app: the page asks for what is held (`files`) when it can take it. */
    data object FilesOpened : HostMessage {
        override fun encode(): Map<String, Any?> = mapOf("type" to "filesOpened")
    }

    /** The person's choice of the bar changed: the page's View menu checks its Server bar row by it. */
    data class ShellBar(val shown: Boolean) : HostMessage {
        override fun encode(): Map<String, Any?> = linkedMapOf("type" to "shellBar", "shown" to shown)
    }
}

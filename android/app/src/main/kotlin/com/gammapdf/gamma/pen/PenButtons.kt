package com.gammapdf.gamma.pen

import kotlin.math.abs

/**
 * A stylus's buttons as the host's pen gestures (docs/dev/android.md "The
 * pen"): a button pressed and released while the pen hovers, with no contact
 * in between, is a click, sent as `pencil` with the click's `gesture` and
 * `action`: the primary button (BUTTON_STYLUS_PRIMARY) the Pencil's double tap,
 * which toggles the eraser by default, the secondary a phase-less squeeze,
 * which goes back to the last tool. A press the pen touches the screen during,
 * or leaves range during (ACTION_HOVER_EXIT, ACTION_CANCEL), is no click, and
 * no new press counts until the button is seen up while the pen hovers: a
 * primary button held as the pen touches down reaches the page as an eraser
 * (PenRouter's rewrite), the secondary held then goes on as an ordinary
 * stroke, and neither sends anything. Some styluses report their buttons
 * as key events too (API 34 KEYCODE_STYLUS_BUTTON_PRIMARY and SECONDARY), by
 * the same rules; a key click within `window` seconds of the motion click of
 * the same button is that click, counted once.
 *
 * Pure: it is fed each stylus MotionEvent (its masked action, button state,
 * whether it is of the touch stream, its time in seconds) and each stylus
 * button key event, and returns the clicks they complete. The Android values
 * it reads are written out below (android.jar, API 36).
 */
class PenButtons(val window: Double = 0.3) {
    enum class Click(val mask: Int, val keyCode: Int, val gesture: String, val action: String) {
        PRIMARY(BUTTON_STYLUS_PRIMARY, KEYCODE_STYLUS_BUTTON_PRIMARY, "tap", "switchEraser"),
        SECONDARY(BUTTON_STYLUS_SECONDARY, KEYCODE_STYLUS_BUTTON_SECONDARY, "squeeze", "switchPrevious"),
    }

    private class Press {
        /** The button as the last event showed it. */
        var down = false
        /** Pressed while the pen hovered, and nothing since that spoils it. */
        var clean = false
        /** Seen held in contact or as the pen left: no press counts until it is seen up while hovering. */
        var blocked = false
    }

    /** One click heard, from the motion events or the keys, and whether the other path's has been matched to it. */
    private class Heard(val key: Boolean, val time: Double) {
        var paired = false
    }

    private val motionPresses = Array(Click.entries.size) { Press() }
    private val keyPresses = Array(Click.entries.size) { Press() }
    private val heard = arrayOfNulls<Heard>(Click.entries.size)

    /** The pen is on the screen, as the touch stream last said. */
    private var touching = false

    /**
     * A stylus MotionEvent: `action` its actionMasked, `buttonState` its
     * buttonState, `inContact` true for an event of the touch stream
     * (dispatchTouchEvent) and false for hover (dispatchGenericMotionEvent),
     * `time` its eventTime in seconds. ACTION_BUTTON_PRESS and RELEASE come as
     * generic events, so for them the contact is the one the touch stream left.
     */
    fun motion(action: Int, buttonState: Int, inContact: Boolean, time: Double): List<Click> {
        val button = action == ACTION_BUTTON_PRESS || action == ACTION_BUTTON_RELEASE
        val spoils = inContact || (button && touching) || action == ACTION_HOVER_EXIT || action == ACTION_CANCEL
        if (!button) touching = inContact && action != ACTION_UP && action != ACTION_CANCEL
        var clicks = emptyList<Click>()
        for (click in Click.entries) {
            val held = buttonState and click.mask != 0
            val press = motionPresses[click.ordinal]
            if (spoils) {
                if (held || press.down) press.blocked = true
                press.clean = false
                keyPresses[click.ordinal].clean = false
            }
            if (held && !press.down) {
                press.down = true
                press.clean = !spoils && !press.blocked
            } else if (!held && press.down) {
                press.down = false
                if (press.clean && hear(click, key = false, time)) clicks = clicks + click
                press.clean = false
            }
            if (!held && !spoils) press.blocked = false
        }
        return clicks
    }

    /**
     * A stylus button key event (dispatchKeyEvent): its keyCode, whether it is
     * ACTION_DOWN (a repeat changes nothing) or ACTION_UP, its eventTime in
     * seconds. The click it completes, or null; null for any other key.
     */
    fun key(keyCode: Int, down: Boolean, time: Double): Click? {
        val click = Click.entries.firstOrNull { it.keyCode == keyCode } ?: return null
        val press = keyPresses[click.ordinal]
        if (down) {
            if (!press.down) {
                press.down = true
                press.clean = !touching
            }
            return null
        }
        if (!press.down) return null
        press.down = false
        val clean = press.clean
        press.clean = false
        return if (clean && hear(click, key = true, time)) click else null
    }

    /** Forget every press and click, as when the window loses focus. */
    fun reset() {
        for (press in motionPresses + keyPresses) {
            press.down = false
            press.clean = false
            press.blocked = false
        }
        heard.fill(null)
        touching = false
    }

    /** Whether a click is new, and not the other path's report of one just heard. */
    private fun hear(click: Click, key: Boolean, time: Double): Boolean {
        val last = heard[click.ordinal]
        if (last != null && last.key != key && !last.paired && abs(time - last.time) <= window) {
            last.paired = true
            return false
        }
        heard[click.ordinal] = Heard(key, time)
        return true
    }

    companion object {
        const val ACTION_UP = 1
        const val ACTION_CANCEL = 3
        const val ACTION_HOVER_EXIT = 10
        const val ACTION_BUTTON_PRESS = 11
        const val ACTION_BUTTON_RELEASE = 12
        const val BUTTON_STYLUS_PRIMARY = 0x20
        const val BUTTON_STYLUS_SECONDARY = 0x40
        const val KEYCODE_STYLUS_BUTTON_PRIMARY = 308
        const val KEYCODE_STYLUS_BUTTON_SECONDARY = 309

        /** The keys PenButtons reads: the shell takes them; the tail button (311) stays the system's, which opens notes. */
        fun isButtonKey(keyCode: Int): Boolean =
            keyCode == KEYCODE_STYLUS_BUTTON_PRIMARY || keyCode == KEYCODE_STYLUS_BUTTON_SECONDARY
    }
}

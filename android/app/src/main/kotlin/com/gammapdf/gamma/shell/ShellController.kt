package com.gammapdf.gamma.shell

import android.animation.ValueAnimator
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Vibrator
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.WebView
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.core.animation.doOnEnd
import androidx.core.content.FileProvider
import androidx.core.graphics.ColorUtils
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updateLayoutParams
import java.io.File
import kotlin.math.max
import kotlin.math.roundToInt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.gammapdf.gamma.BuildConfig
import com.gammapdf.gamma.R
import com.gammapdf.gamma.app.AppModel
import com.gammapdf.gamma.app.GammaApplication
import com.gammapdf.gamma.pen.HoldReply
import com.gammapdf.gamma.pen.LiveInk
import com.gammapdf.gamma.pen.PenHaptics
import com.gammapdf.gamma.pen.PenLayer

/**
 * The window, ported from the ShellController in ipad/GammaIPad/Shell/
 * ShellView.swift. Bottom to top: the page's WebView in the pen layer (the
 * live ink over it), the launcher while it is up, and the bar unless the
 * person hid it (AppModel.barShown), all inside the system bars, the cutout
 * and the keyboard, whose strips are the window's own colour, the page's
 * theme's. The WebViews see no insets, so the pages pad for none. It
 * follows the model (AppModel.onChange), answers the page's messages
 * (HostBridge) and the chrome's calls (ChromeCalls), and carries the pen's
 * strokes to the page; none of it knows what the page shows
 * (docs/dev/android.md "The shell"). Main-thread confined; MainActivity
 * makes it in onCreate, and it goes with the activity.
 */
class ShellController(
    private val activity: ComponentActivity,
    private val app: GammaApplication,
    cloudSignIn: CloudSignIn,
    fileChooser: FileChooser,
    microphone: Microphone,
) : ChromeHost, PenLayer.Listener {
    private val model = app.model
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    /** The bar's strip: bar.html's `.bar` row on a touch shell, 44 CSS px, which are dp here. */
    private val barHeight = (BAR_HEIGHT_DP * activity.resources.displayMetrics.density).roundToInt()

    /** The window, in the page's theme colour, which the strips under the system bars and the cutout show. */
    val root = FrameLayout(activity)

    /** Inside the system bars, the cutout and the keyboard: the page, the launcher and the bar. */
    private val frame = FrameLayout(activity)

    /** The page's WebView with the pen's live ink over it, which sees every touch the page gets. */
    val penLayer = PenLayer(activity)

    private val calls = ChromeCalls(model, this)
    private val saver = SavePresenter(activity, frame, app.scope)
    private val navigation = Navigation(activity, cloudSignIn, fileChooser, microphone, saver)
    private val chrome = ChromeViews(activity, scope) { method, args -> calls.call(method, args) }
    private val page: PageView
    private var bar: WebView
    private var launcherView: WebView? = null
    private var launcher: AppModel.Launcher? = null

    /** The bar as it is placed, and the person's choice as the page was last told it (`shellBar`). */
    private var barShown = model.barShown
    private var barHidden = model.barHidden

    /** A dropdown of the bar's is open, and its view covers the content (`expandBar`). */
    private var barExpanded = false
    private var barMotion: ValueAnimator? = null

    /** The page's and the launcher's top within the frame: under the bar's strip, or the top while it is away. */
    private var contentTop = if (barShown) barHeight else 0

    /** The window's colour: the page's theme's background, or the last one, or the system's. */
    private var background: Int

    /** The page's scheme, which the system bars' icons follow; null until it reports one. */
    private var scheme: Scheme? = null
    private var attempt = 0

    private val follow: () -> Unit = { update() }

    // A file opened while the page is up: the page asks for it when it can take it. One opened
    // before the page loaded is asked for at its start, so this may reach no one.
    private val arrival: () -> Unit = { scope.launch { page.bridge.send(HostMessage.FilesOpened) } }

    init {
        val template = activity.assets.open("shell/host.js").bufferedReader().use { it.readText() }
        val hostScript = HostInfo(BuildConfig.VERSION_NAME, hasVibrator(activity)).script(template)
        background = PageView.savedBackground(app.prefs) ?: PageView.windowBackground(activity)
        root.setBackgroundColor(background)
        root.addView(frame, FrameLayout.LayoutParams(MATCH, MATCH))
        frame.addView(penLayer, contentParams())
        page = PageView(activity, app.prefs, navigation, hostScript, scope) { penLayer.setPage(it) }
        bar = chrome.make(bar = true, background = Color.TRANSPARENT)
        bar.alpha = if (barShown) 1f else 0f
        bar.visibility = if (barShown) View.VISIBLE else View.GONE
        frame.addView(bar, FrameLayout.LayoutParams(MATCH, barHeight))
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            place(insets)
            WindowInsetsCompat.CONSUMED
        }

        navigation.onLoad = { failure ->
            if (failure != null) {
                page.bridge.pageLeft()
                model.loadFailed(failure)
            } else {
                model.pageLoaded()
            }
        }
        page.onChange = { pushState() }
        page.bridge.onMessage = { answer(it) }
        // A save is its document's, and none can finish once that is gone.
        // The process's scope: the page's own teardown, after this
        // controller's scope has ended, drops them too. So is the screen
        // kept on (`keepAwake`): a reload, another server or an ended
        // renderer lets it sleep.
        page.bridge.onDocumentGone = {
            val delete = app.saves.cancelAll()
            app.scope.launch(Dispatchers.IO) { delete() }
            keepAwake(false)
        }
        chrome.onGone = { replaceChrome(it) }
        penLayer.listener = this
        app.opened.onArrival = arrival
        chrome.load(bar, ChromePages.BAR)
        model.onChange = follow
        update()
        applyScheme()
    }

    /** Whether the person hid the bar (the shortcut's title says the other way). */
    val barIsHidden: Boolean get() = model.barHidden

    // --- following the model -------------------------------------------------------------

    /** Every change of the model: the page's server, the launcher, the bar and the chrome's state. */
    private fun update() {
        val url = model.shellUrl
        page.login = model.login(url)
        show(url, model.connects)
        showLauncher(model.launcher)
        placeBar(model.barShown, model.barHidden)
        pushState()
    }

    private fun show(url: String?, attempt: Int) {
        if (url != null && url != page.server) {
            page.open(url)
        } else if (attempt != this.attempt) {
            page.retry()
        }
        this.attempt = attempt
    }

    /** The launcher over the page, each showing a fresh load of it, or none. */
    private fun showLauncher(launcher: AppModel.Launcher?) {
        if (launcher === this.launcher) return
        this.launcher = launcher
        if (launcher == null) {
            launcherView?.let { chrome.destroy(it) }
            launcherView = null
            return
        }
        val view = launcherView ?: chrome.make(bar = false, background = background).also {
            frame.addView(it, frame.indexOfChild(bar), contentParams())
            launcherView = it
        }
        chrome.load(view, ChromePages.launcher(launcher.error))
    }

    /** The state to every chrome page: when the model changes, and when the page's session or theme does. */
    private fun pushState() = chrome.push(calls.state())

    /**
     * The bar up or away (AppModel.barShown): the content's top moves with it
     * as the bar fades, after an open dropdown closes, and the faded bar is
     * gone so the page takes every touch. The person's choice goes to the
     * page when it changes (`shellBar`), for its View menu's check; a page
     * that loads asks for it itself.
     */
    private fun placeBar(shown: Boolean, hidden: Boolean) {
        if (hidden != barHidden) {
            barHidden = hidden
            page.bridge.post(HostMessage.ShellBar(!hidden))
        }
        if (shown == barShown) return
        barShown = shown
        if (!shown) closeBarMenu()
        barMotion?.cancel()
        val fromTop = contentTop
        val toTop = if (shown) barHeight else 0
        val fromAlpha = bar.alpha
        val toAlpha = if (shown) 1f else 0f
        // Animations off in the system's settings are the iPad's Reduce Motion.
        if (!root.isAttachedToWindow || !ValueAnimator.areAnimatorsEnabled()) {
            setContentTop(toTop)
            bar.alpha = toAlpha
            bar.visibility = if (shown) View.VISIBLE else View.GONE
            return
        }
        bar.visibility = View.VISIBLE
        barMotion = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = BAR_FADE_MS
            addUpdateListener {
                val f = it.animatedFraction
                setContentTop((fromTop + (toTop - fromTop) * f).roundToInt())
                bar.alpha = fromAlpha + (toAlpha - fromAlpha) * f
            }
            // The latest placing decides when two cross.
            doOnEnd { bar.visibility = if (barShown) View.VISIBLE else View.GONE }
            start()
        }
    }

    private fun setContentTop(top: Int) {
        contentTop = top
        for (view in listOfNotNull<View>(penLayer, launcherView)) {
            view.updateLayoutParams<FrameLayout.LayoutParams> { topMargin = top }
        }
    }

    private fun contentParams() = FrameLayout.LayoutParams(MATCH, MATCH).apply { topMargin = contentTop }

    /**
     * The frame inside the status bar, the cutout and the navigation bar,
     * and above the keyboard, so the page resizes for it rather than pans
     * (the pen's claims assume a visual viewport at the page's origin).
     */
    private fun place(insets: WindowInsetsCompat) {
        val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
        val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
        frame.updateLayoutParams<FrameLayout.LayoutParams> {
            setMargins(bars.left, bars.top, bars.right, max(bars.bottom, keyboard.bottom))
        }
    }

    // --- the theme ------------------------------------------------------------------------

    /**
     * The system bars' icons follow the page's theme, which can differ from
     * the system's: only the icons do, never the activity's night mode, so a
     * page on the "system" theme still reads the system's preference. Before
     * the page reports, the window's colour decides.
     */
    private fun applyScheme() {
        val light = when (scheme) {
            Scheme.LIGHT -> true
            Scheme.DARK -> false
            null -> ColorUtils.calculateLuminance(background) > 0.5
        }
        WindowCompat.getInsetsController(activity.window, root).run {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
    }

    /** The system went light or dark: a window not yet painted by any page follows it. */
    fun configurationChanged() {
        if (scheme == null && PageView.savedBackground(app.prefs) == null) {
            background = PageView.windowBackground(activity)
            root.setBackgroundColor(background)
        }
        applyScheme()
    }

    // --- the system's back and the keyboard ---------------------------------------------------

    /** The system's back (BackStep): false when it is the app's to leave. */
    fun back(): Boolean {
        val launcher = model.launcher
        val closes = model.current != null && launcher?.error == null
        return when (BackStep.of(barExpanded, launcher != null, closes, pageCanGoBack())) {
            BackStep.CLOSE_MENU -> {
                closeBarMenu()
                true
            }
            BackStep.CLOSE_LAUNCHER -> {
                model.toggleLauncher()
                true
            }
            BackStep.PAGE_BACK -> {
                page.webView.goBack()
                true
            }
            BackStep.LEAVE -> false
        }
    }

    /** The page's history has an entry before this one on the server shown (BackStep.staysOnServer). */
    private fun pageCanGoBack(): Boolean {
        val history = page.webView.copyBackForwardList()
        val index = history.currentIndex - 1
        val previous = if (index >= 0) history.getItemAtIndex(index)?.url else null
        return BackStep.staysOnServer(previous, page.server)
    }

    fun shortcut(shortcut: Shortcut) = when (shortcut) {
        Shortcut.ALL_SERVERS -> calls.toggleLauncher()
        Shortcut.RELOAD -> page.reload()
        Shortcut.TOGGLE_BAR -> model.setBarHidden(!model.barHidden)
    }

    // --- the activity's life --------------------------------------------------------------------

    /** The activity is in front: the pages run and show as visible. */
    fun resume() {
        page.resume()
        chrome.resume()
    }

    /** The activity is behind another, or away: the pages save and show as hidden (visibilitychange). */
    fun pause() {
        page.pause()
        chrome.pause()
    }

    fun destroy() {
        if (model.onChange === follow) model.onChange = null
        if (app.opened.onArrival === arrival) app.opened.onArrival = null
        barMotion?.cancel()
        scope.cancel()
        penLayer.listener = null
        penLayer.reset()
        page.destroy()
        chrome.destroyAll()
    }

    // --- the chrome's calls (ChromeCalls) --------------------------------------------------------

    override val version: String get() = BuildConfig.VERSION_NAME
    override val themeName: String get() = page.themeName
    override val session: Map<String, Any?>? get() = page.session
    override val address: String? get() = page.url

    override fun readSession() = page.readSession()

    override fun openWorkspace(id: String) = page.openWorkspace(id)

    override fun reload() = page.reload()

    /** The bar's view over the whole content while a dropdown is open, so its menu has room and a tap beside it closes it (bar.html), else its strip. */
    override fun expandBar(on: Boolean) {
        barExpanded = on
        bar.updateLayoutParams<ViewGroup.LayoutParams> { height = if (on) MATCH else barHeight }
    }

    /** An open dropdown closes in the bar's page too (its Escape), so its next tap opens it again; the view goes back to its strip. */
    override fun closeBarMenu() {
        chrome.escape(bar)
        expandBar(false)
    }

    /**
     * This device's data folder is the app's own storage, which no files app
     * shows: the app's settings page, with its storage, is what there is.
     */
    override fun revealData() {
        val settings = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", activity.packageName, null))
        try {
            activity.startActivity(settings)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(activity, R.string.no_app_for_link, Toast.LENGTH_SHORT).show()
        }
    }

    /** The server's log, shared through the app's FileProvider (res/xml/file_paths.xml) to whatever takes text. */
    override fun revealLog() {
        val log = app.local.logFile
        val uri = try {
            if (log.isFile) FileProvider.getUriForFile(activity, activity.packageName + FILES_AUTHORITY, log) else null
        } catch (e: IllegalArgumentException) {
            null
        }
        if (uri == null) {
            Toast.makeText(activity, R.string.no_server_log, Toast.LENGTH_SHORT).show()
            return
        }
        val send = Intent(Intent.ACTION_SEND)
            .setType("text/plain")
            .putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        send.clipData = ClipData.newRawUri(log.name, uri)
        try {
            activity.startActivity(Intent.createChooser(send, activity.getString(R.string.share_server_log)))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(activity, R.string.no_app_for_link, Toast.LENGTH_SHORT).show()
        }
    }

    override fun copy(text: String) {
        activity.getSystemService(ClipboardManager::class.java)
            ?.setPrimaryClip(ClipData.newPlainText(activity.getString(R.string.app_name), text))
    }

    /**
     * A chrome page's renderer ended (with the page's: an app's WebViews
     * share one): a new view takes the old one's place and loads its page
     * again, the bar back to its strip.
     */
    private fun replaceChrome(dead: WebView) {
        val fresh = when {
            dead === bar -> chrome.make(bar = true, background = Color.TRANSPARENT).also { bar = it }
            dead === launcherView -> chrome.make(bar = false, background = background).also { launcherView = it }
            else -> return chrome.destroy(dead)
        }
        val url = chrome.urlOf(dead)
        val index = frame.indexOfChild(dead)
        val params = dead.layoutParams
        fresh.alpha = dead.alpha
        fresh.visibility = dead.visibility
        chrome.destroy(dead)
        frame.addView(fresh, index, params)
        if (fresh === bar) expandBar(false)
        url?.let { chrome.load(fresh, it) }
    }

    // --- the page's messages (HostBridge) ------------------------------------------------------

    private suspend fun answer(message: WebMessage): Map<String, Any?> = when (message) {
        is WebMessage.Claim -> mapOf("taken" to penLayer.claim(message.pointerId, message.x, message.y, message.tool))
        is WebMessage.Theme -> {
            page.paint(message.bg, message.name ?: message.scheme.wire)?.let {
                background = it
                root.setBackgroundColor(it)
            }
            scheme = message.scheme
            applyScheme()
            emptyMap()
        }
        is WebMessage.Haptic -> {
            page.webView.performHapticFeedback(PenHaptics.constant(message.kind.wire, Build.VERSION.SDK_INT))
            emptyMap()
        }
        WebMessage.Files -> app.opened.listing
        is WebMessage.FileData -> withContext(Dispatchers.IO) { app.opened.chunk(message.id, message.offset) } ?: emptyMap()
        is WebMessage.FileDone -> {
            withContext(Dispatchers.IO) { app.opened.done(message.id) }
            emptyMap()
        }
        is WebMessage.ShellBar -> {
            message.shown?.let { model.setBarHidden(!it) }
            mapOf("shown" to !model.barHidden)
        }
        is WebMessage.Save ->
            withContext(Dispatchers.IO) { app.saves.begin(message.name, message.mime, message.size) }
                ?.let { mapOf("id" to it) } ?: emptyMap()
        is WebMessage.SaveData -> {
            if (!withContext(Dispatchers.IO) { app.saves.append(message.id, message.offset, message.data) }) {
                throw Refusal(NOT_SAVING)
            }
            emptyMap()
        }
        is WebMessage.SaveDone -> {
            val mime = app.saves.mime(message.id).orEmpty()
            val file = withContext(Dispatchers.IO) { app.saves.finish(message.id, message.cancel) }
            // Answered at once; the file is presented after the reply has gone.
            if (file != null) scope.launch(Dispatchers.Main) { present(file, mime) }
            mapOf("saved" to (file != null))
        }
        // The page reads blob: and data: URLs itself (saveFile.js); the
        // shell fetches the rest, the WebView's cookies going only to the
        // server whose page asked (SaveHops).
        is WebMessage.SaveUrl -> {
            val scheme = UrlParts.schemeOf(message.url)
            if (scheme != "http" && scheme != "https") throw Refusal(NOT_FETCHABLE)
            saver.saveUrl(message.url, message.name, page.webView.settings.userAgentString, page.bridge.origin)
            emptyMap()
        }
        is WebMessage.KeepAwake -> {
            keepAwake(message.on)
            emptyMap()
        }
    }

    /**
     * The screen kept on while the page asks (`keepAwake`): the window's
     * FLAG_KEEP_SCREEN_ON, which holds only while the window shows, so the
     * app going behind another lets the screen sleep and its return keeps it
     * on again. What the page asked is its document's (onDocumentGone).
     */
    private fun keepAwake(on: Boolean) {
        val flag = WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        if (on) activity.window.addFlags(flag) else activity.window.clearFlags(flag)
    }

    /** A saved file into Downloads, then its temporary copy goes (Saves.release). */
    private suspend fun present(file: File, mime: String) {
        try {
            withContext(Dispatchers.IO) { saver.saveFile(file, file.name, mime) }
        } finally {
            withContext(NonCancellable + Dispatchers.IO) { app.saves.release(file) }
        }
    }

    // --- the pen (PenLayer) --------------------------------------------------------------------

    /**
     * The lift: the samples go to the page, with the hold whose shape the
     * line was drawn as, and the line stays until the page has painted the
     * stroke itself, or a while, so a page that never answers leaves nothing.
     */
    override fun onStroke(pointerId: Int, samples: List<DoubleArray>, held: Int?, line: LiveInk.Handle?) {
        val stroke = HostMessage.Stroke(pointerId, samples, held)
        scope.launch {
            page.bridge.send(stroke)
            penLayer.release(line)
        }
        scope.launch {
            delay(PAINT_TIMEOUT_MS)
            penLayer.release(line)
        }
    }

    /** The system cancelled a claimed stroke, or the page took the hold: a stroke with no samples ends the claim and saves nothing. */
    override fun onAbandon(pointerId: Int) {
        page.bridge.post(HostMessage.Stroke(pointerId, emptyList()))
    }

    /**
     * The captured pen held still: the page answers at once, from the
     * samples (a shape to draw, the hold taken for *Paste here*, or
     * nothing), and the pen layer follows its answer.
     */
    override fun onHold(pointerId: Int, samples: List<DoubleArray>) {
        scope.launch {
            val reply = page.bridge.ask(HostMessage.Hold(pointerId, samples))
            penLayer.held(pointerId, HoldReply.from(reply))
        }
    }

    /** A pen button clicked out of contact (PenButtons): the page acts on it as the iPad's double tap or squeeze. */
    override fun onPenButton(gesture: String, action: String) {
        val kind = PencilGesture.entries.firstOrNull { it.wire == gesture } ?: return
        page.bridge.post(HostMessage.Pencil(kind, action))
    }

    companion object {
        /** bar.html's touch row (body[data-touch] .bar). */
        const val BAR_HEIGHT_DP = 44

        /** How long the bar takes to fade away or back. */
        const val BAR_FADE_MS = 200L

        /** How long a finished stroke's line waits for the page's paint before it goes anyway. */
        const val PAINT_TIMEOUT_MS = 3_000L

        /** The FileProvider's authority after the application id (AndroidManifest.xml). */
        const val FILES_AUTHORITY = ".files"

        const val NOT_SAVING = "Gamma for Android is saving no such file, or these bytes are out of turn."
        const val NOT_FETCHABLE = "Gamma for Android saves only http and https addresses."

        private const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT

        private fun hasVibrator(context: Context): Boolean =
            context.getSystemService(Vibrator::class.java)?.hasVibrator() == true
    }
}

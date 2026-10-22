package app.remotesupport

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.text.InputType
import android.view.HapticFeedbackConstants
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Shows the remote screen and turns touch / keyboard input into the protocol's input events.
 *
 * Touch:  tap = left click | long-press = right click | one finger moves the pointer
 *         "Drag" mode: finger holds the left button (move windows, select text)
 *         two fingers: drag = scroll wheel, pinch = zoom (then drag to pan)
 */
class RemoteView(context: Context) : View(context) {

    interface Input {
        fun move(x: Float, y: Float)
        fun button(b: String, down: Boolean, x: Float, y: Float)
        fun scroll(dx: Int, dy: Int)
        fun key(name: String, down: Boolean)
    }

    var input: Input? = null
    var dragMode = false
    var onStickyChanged: (() -> Unit)? = null
    /** Modifiers armed from the on-screen key bar; applied to the next key, then cleared. */
    val sticky = LinkedHashSet<String>()
    var frames = 0
        private set
    val frameSize: Pair<Int, Int>? get() = fb?.let { it.width to it.height }

    private var fb: Bitmap? = null
    private var fbCanvas: Canvas? = null
    private val paint = Paint(Paint.FILTER_BITMAP_FLAG)
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#64748b"); textAlign = Paint.Align.CENTER; textSize = 15f * resources.displayMetrics.density * resources.configuration.fontScale
    }
    private var zoom = 1f
    private var panX = 0f
    private var panY = 0f
    private val slop = ViewConfiguration.get(context).scaledTouchSlop.toFloat()
    private val scrollStep = 28f * resources.displayMetrics.density

    init {
        isFocusable = true
        isFocusableInTouchMode = true
    }

    // ---- framebuffer ------------------------------------------------------------------------
    /** Must be called on the UI thread. [region] is the already-decoded changed rectangle. */
    fun applyFrame(sw: Int, sh: Int, x: Int, y: Int, region: Bitmap) {
        var buf = fb
        if (buf == null || buf.width != sw || buf.height != sh) {
            buf = Bitmap.createBitmap(sw, sh, Bitmap.Config.ARGB_8888)
            fb = buf
            fbCanvas = Canvas(buf).also { it.drawColor(Color.BLACK) }
            resetZoom()
        }
        fbCanvas!!.drawBitmap(region, x.toFloat(), y.toFloat(), null)
        region.recycle()
        frames++
        invalidate()
    }

    fun resetZoom() {
        zoom = 1f; panX = 0f; panY = 0f
        invalidate()
    }

    private fun imageRect(): RectF? {
        val b = fb ?: return null
        if (width == 0 || height == 0) return null
        val s = min(width.toFloat() / b.width, height.toFloat() / b.height) * zoom
        val w = b.width * s
        val h = b.height * s
        val maxX = max(0f, (w - width) / 2f)
        val maxY = max(0f, (h - height) / 2f)
        panX = panX.coerceIn(-maxX, maxX)
        panY = panY.coerceIn(-maxY, maxY)
        val cx = width / 2f + panX
        val cy = height / 2f + panY
        return RectF(cx - w / 2f, cy - h / 2f, cx + w / 2f, cy + h / 2f)
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Color.parseColor("#0b1220"))
        val r = imageRect()
        val b = fb
        if (r == null || b == null) {
            canvas.drawText("Waiting for the remote screen…", width / 2f, height / 2f, textPaint)
        } else {
            canvas.drawBitmap(b, null, r, paint)
        }
    }

    /** Screen px -> normalised 0..1 position on the remote monitor, or null if outside the image. */
    private fun norm(px: Float, py: Float): Pair<Float, Float>? {
        val r = imageRect() ?: return null
        val nx = (px - r.left) / r.width()
        val ny = (py - r.top) / r.height()
        if (nx < 0f || nx > 1f || ny < 0f || ny > 1f) return null
        return nx to ny
    }

    // ---- touch --------------------------------------------------------------------------------
    private enum class Mode { IDLE, ONE, MULTI_WAIT, MULTI_ZOOM, MULTI_SCROLL }

    private var mode = Mode.IDLE
    private var downX = 0f
    private var downY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var moved = false
    private var longPressed = false
    private var dragging = false
    private var lastMoveSent = 0L
    private var startSpan = 0f
    private var lastSpan = 0f
    private var startMidX = 0f
    private var startMidY = 0f
    private var lastMidX = 0f
    private var lastMidY = 0f
    private var accX = 0f
    private var accY = 0f

    private val longPress = Runnable {
        if (mode == Mode.ONE && !moved && !dragging) {
            longPressed = true
            performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            norm(downX, downY)?.let { (x, y) ->
                input?.button("right", true, x, y)
                input?.button("right", false, x, y)
            }
        }
    }

    private fun midX(e: MotionEvent) = (e.getX(0) + e.getX(1)) / 2f
    private fun midY(e: MotionEvent) = (e.getY(0) + e.getY(1)) / 2f
    private fun span(e: MotionEvent) = hypot(e.getX(0) - e.getX(1), e.getY(0) - e.getY(1))

    private fun endOneFinger() {
        removeCallbacks(longPress)
        if (dragging) {
            val n = norm(lastX, lastY) ?: (0f to 0f)
            input?.button("left", false, n.first, n.second)
            dragging = false
        }
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                requestFocus()
                mode = Mode.ONE
                downX = e.x; downY = e.y; lastX = e.x; lastY = e.y
                moved = false; longPressed = false; dragging = false
                norm(e.x, e.y)?.let { (x, y) ->
                    input?.move(x, y)
                    if (dragMode) {
                        input?.button("left", true, x, y)
                        dragging = true
                    }
                }
                if (!dragMode) postDelayed(longPress, 550)
            }

            MotionEvent.ACTION_POINTER_DOWN -> if (e.pointerCount == 2) {
                endOneFinger()
                mode = Mode.MULTI_WAIT
                startSpan = span(e); lastSpan = startSpan
                startMidX = midX(e); startMidY = midY(e)
                lastMidX = startMidX; lastMidY = startMidY
                accX = 0f; accY = 0f
            }

            MotionEvent.ACTION_MOVE -> when (mode) {
                Mode.ONE -> {
                    if (!moved && hypot(e.x - downX, e.y - downY) > slop) {
                        moved = true
                        removeCallbacks(longPress)
                    }
                    if (moved) {
                        lastX = e.x; lastY = e.y
                        val now = SystemClock.uptimeMillis()
                        if (now - lastMoveSent >= 16) {
                            lastMoveSent = now
                            norm(e.x, e.y)?.let { (x, y) -> input?.move(x, y) }
                        }
                    }
                }
                Mode.MULTI_WAIT, Mode.MULTI_ZOOM, Mode.MULTI_SCROLL -> if (e.pointerCount >= 2) multiMove(e)
                Mode.IDLE -> {}
            }

            MotionEvent.ACTION_POINTER_UP -> if (mode != Mode.ONE && mode != Mode.IDLE && e.pointerCount - 1 < 2) {
                mode = Mode.IDLE          // ignore the remaining finger until everything is lifted
            }

            MotionEvent.ACTION_UP -> {
                removeCallbacks(longPress)
                if (mode == Mode.ONE) {
                    if (dragging) {
                        endOneFinger()
                    } else if (!moved && !longPressed) {
                        norm(downX, downY)?.let { (x, y) ->
                            input?.button("left", true, x, y)
                            input?.button("left", false, x, y)
                        }
                    }
                }
                mode = Mode.IDLE
            }

            MotionEvent.ACTION_CANCEL -> {
                endOneFinger()
                mode = Mode.IDLE
            }
        }
        return true
    }

    private fun multiMove(e: MotionEvent) {
        val sp = span(e)
        val mx = midX(e)
        val my = midY(e)
        when (mode) {
            Mode.MULTI_WAIT -> {
                val dSpan = abs(sp - startSpan)
                val dMid = hypot(mx - startMidX, my - startMidY)
                if (dSpan > slop * 1.5f && dSpan > dMid) {
                    mode = Mode.MULTI_ZOOM; lastSpan = sp; lastMidX = mx; lastMidY = my
                } else if (dMid > slop) {
                    mode = Mode.MULTI_SCROLL; lastMidX = mx; lastMidY = my
                }
            }
            Mode.MULTI_ZOOM -> {
                if (lastSpan > 0f) {
                    val newZoom = (zoom * sp / lastSpan).coerceIn(1f, 6f)
                    val f = newZoom / zoom
                    val cx = width / 2f + panX
                    val cy = height / 2f + panY
                    panX = (mx - (mx - cx) * f) - width / 2f + (mx - lastMidX)
                    panY = (my - (my - cy) * f) - height / 2f + (my - lastMidY)
                    zoom = newZoom
                    invalidate()
                }
                lastSpan = sp; lastMidX = mx; lastMidY = my
            }
            Mode.MULTI_SCROLL -> {
                accX += mx - lastMidX
                accY += my - lastMidY
                lastMidX = mx; lastMidY = my
                val ny = (accY / scrollStep).toInt()
                val nx = (accX / scrollStep).toInt()
                if (ny != 0 || nx != 0) {
                    accY -= ny * scrollStep
                    accX -= nx * scrollStep
                    // finger moves down => page moves down => wheel "up" (positive); same idea sideways
                    input?.scroll((-nx).coerceIn(-20, 20), ny.coerceIn(-20, 20))
                }
            }
            else -> {}
        }
    }

    // ---- keyboard -----------------------------------------------------------------------------
    private val held = LinkedHashSet<String>()

    private fun rawKey(name: String, down: Boolean) {
        if (down) held.add(name) else held.remove(name)
        input?.key(name, down)
    }

    /** Press + release one key, wrapped in any armed modifiers (Ctrl/Alt/Shift/Win). */
    fun tapKey(name: String) {
        val mods = sticky.toList()
        mods.forEach { rawKey(it, true) }
        rawKey(name, true)
        rawKey(name, false)
        mods.asReversed().forEach { rawKey(it, false) }
        if (mods.isNotEmpty()) {
            sticky.clear()
            onStickyChanged?.invoke()
        }
    }

    fun toggleSticky(name: String): Boolean {
        val on = if (sticky.contains(name)) { sticky.remove(name); false } else { sticky.add(name); true }
        onStickyChanged?.invoke()
        return on
    }

    fun typeText(text: CharSequence) {
        for (ch in text) {
            val name = when {
                ch == '\n' -> "Return"
                ch == '\t' -> "Tab"
                ch == ' ' -> "space"
                ch.code >= 32 && !ch.isSurrogate() -> ch.toString()
                else -> null
            }
            if (name != null) tapKey(name)
        }
    }

    fun releaseAll() {
        endOneFinger()
        held.toList().forEach { input?.key(it, false) }
        held.clear()
    }

    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (!hasWindowFocus) releaseAll()   // never leave Ctrl/Alt stuck on the remote PC
    }

    override fun onCheckIsTextEditor() = true

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection {
        outAttrs.inputType = InputType.TYPE_CLASS_TEXT or
            InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or
            InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
        outAttrs.imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI or
            EditorInfo.IME_FLAG_NO_FULLSCREEN or EditorInfo.IME_ACTION_NONE
        return object : BaseInputConnection(this, false) {
            override fun commitText(text: CharSequence, newCursorPosition: Int): Boolean {
                typeText(text); return true
            }
            override fun setComposingText(text: CharSequence, newCursorPosition: Int) = true
            override fun finishComposingText() = true
            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                repeat(max(beforeLength, 1)) { tapKey("BackSpace") }
                return true
            }
        }
    }

    private fun keyName(code: Int, e: KeyEvent): String? = when (code) {
        KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER -> "Return"
        KeyEvent.KEYCODE_DEL -> "BackSpace"
        KeyEvent.KEYCODE_FORWARD_DEL -> "Delete"
        KeyEvent.KEYCODE_TAB -> "Tab"
        KeyEvent.KEYCODE_ESCAPE -> "Escape"
        KeyEvent.KEYCODE_DPAD_UP -> "Up"
        KeyEvent.KEYCODE_DPAD_DOWN -> "Down"
        KeyEvent.KEYCODE_DPAD_LEFT -> "Left"
        KeyEvent.KEYCODE_DPAD_RIGHT -> "Right"
        KeyEvent.KEYCODE_MOVE_HOME -> "Home"
        KeyEvent.KEYCODE_MOVE_END -> "End"
        KeyEvent.KEYCODE_PAGE_UP -> "Prior"
        KeyEvent.KEYCODE_PAGE_DOWN -> "Next"
        KeyEvent.KEYCODE_INSERT -> "Insert"
        KeyEvent.KEYCODE_SPACE -> "space"
        KeyEvent.KEYCODE_CAPS_LOCK -> "Caps_Lock"
        KeyEvent.KEYCODE_SHIFT_LEFT -> "Shift_L"
        KeyEvent.KEYCODE_SHIFT_RIGHT -> "Shift_R"
        KeyEvent.KEYCODE_CTRL_LEFT -> "Control_L"
        KeyEvent.KEYCODE_CTRL_RIGHT -> "Control_R"
        KeyEvent.KEYCODE_ALT_LEFT -> "Alt_L"
        KeyEvent.KEYCODE_ALT_RIGHT -> "Alt_R"
        KeyEvent.KEYCODE_META_LEFT -> "Super_L"
        KeyEvent.KEYCODE_META_RIGHT -> "Super_R"
        in KeyEvent.KEYCODE_F1..KeyEvent.KEYCODE_F12 -> "F${code - KeyEvent.KEYCODE_F1 + 1}"
        else -> {
            val u = e.getUnicodeChar(0)          // base character, no modifiers applied
            if (u in 32..0xFFFF && u != 127 && !Character.isSurrogate(u.toChar())) u.toChar().toString() else null
        }
    }

    private val modifierNames = setOf("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R")
    private var stickyHeldForKey: List<String> = emptyList()

    private fun handleKey(code: Int, e: KeyEvent, down: Boolean): Boolean {
        if (code == KeyEvent.KEYCODE_BACK || code == KeyEvent.KEYCODE_VOLUME_UP ||
            code == KeyEvent.KEYCODE_VOLUME_DOWN || code == KeyEvent.KEYCODE_VOLUME_MUTE ||
            code == KeyEvent.KEYCODE_POWER || code == KeyEvent.KEYCODE_HOME
        ) return false
        val name = keyName(code, e) ?: return false
        if (name in modifierNames) { rawKey(name, down); return true }
        if (down) {
            if (e.repeatCount == 0 && sticky.isNotEmpty()) {
                stickyHeldForKey = sticky.toList()
                stickyHeldForKey.forEach { rawKey(it, true) }
                sticky.clear()
                onStickyChanged?.invoke()
            }
            rawKey(name, true)
        } else {
            rawKey(name, false)
            stickyHeldForKey.asReversed().forEach { rawKey(it, false) }
            stickyHeldForKey = emptyList()
        }
        return true
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent) = handleKey(keyCode, event, true) || super.onKeyDown(keyCode, event)
    override fun onKeyUp(keyCode: Int, event: KeyEvent) = handleKey(keyCode, event, false) || super.onKeyUp(keyCode, event)
}

package com.hatvpip.receiver

import android.app.Service
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

class OverlayPlayerService : Service() {
    private lateinit var windowManager: WindowManager
    private var overlayView: ViewGroup? = null
    private var textOverlayView: ViewGroup? = null
    private var player: ExoPlayer? = null
    private var errorTextView: TextView? = null
    private var title: String = ""
    private var url: String = PlayerActivity.TEST_STREAM_URL
    private var previewUrl: String? = null
    private var fallbackUrl: String? = null
    private var fallbackStreamType: StreamType? = null
    private var showNotification: Boolean = false
    private var muted: Boolean = true
    private var message: String? = null
    private var style: NotificationStyle = NotificationStyle()
    private var streamType: StreamType = StreamType.Hls
    private var durationSeconds: Int? = null
    private var actions: List<OverlayAction> = emptyList()
    private var actionCallbackUrl: String? = null
    private var actionInFlight: Boolean = false
    private val actionButtons = mutableListOf<ActionButton>()
    private var buttonStyle: ButtonStyle = ButtonStyle()
    private var mediaFocus: String = MEDIA_FOCUS_NONE
    private var audioFocusRequest: AudioFocusRequest? = null
    @Volatile private var mjpegRunning: Boolean = false
    private var mjpegThread: Thread? = null
    private val autoCloseHandler = Handler(Looper.getMainLooper())
    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopSelf()
            else -> {
                title = intent?.getStringExtra(PlayerActivity.EXTRA_TITLE) ?: title
                url = intent?.getStringExtra(PlayerActivity.EXTRA_URL) ?: url
                previewUrl = intent?.getStringExtra(PlayerActivity.EXTRA_PREVIEW_URL)
                fallbackUrl = intent?.getStringExtra(PlayerActivity.EXTRA_FALLBACK_URL)
                fallbackStreamType = when (
                    intent?.getStringExtra(PlayerActivity.EXTRA_FALLBACK_STREAM_TYPE)
                ) {
                    StreamType.Hls.wireName -> StreamType.Hls
                    StreamType.Mjpeg.wireName -> StreamType.Mjpeg
                    StreamType.Snapshot.wireName -> StreamType.Snapshot
                    else -> null
                }
                showNotification = intent?.getBooleanExtra(
                    PlayerActivity.EXTRA_SHOW_NOTIFICATION,
                    false
                ) ?: false
                muted = intent?.getBooleanExtra(PlayerActivity.EXTRA_MUTED, true) ?: true
                message = intent?.getStringExtra(PlayerActivity.EXTRA_MESSAGE)
                style = NotificationStyle(
                    position = NotificationPosition.fromWire(
                        intent?.getStringExtra(PlayerActivity.EXTRA_POSITION)
                            ?: NotificationPosition.TopRight.wireName
                    ),
                    titleColor = intent?.getStringExtra(PlayerActivity.EXTRA_TITLE_COLOR) ?: "#50BFF2",
                    titleSize = intent?.getIntExtra(PlayerActivity.EXTRA_TITLE_SIZE, 24)?.coerceIn(10, 48) ?: 24,
                    messageColor = intent?.getStringExtra(PlayerActivity.EXTRA_MESSAGE_COLOR) ?: "#fbf5f5",
                    messageSize = intent?.getIntExtra(PlayerActivity.EXTRA_MESSAGE_SIZE, 18)?.coerceIn(10, 40) ?: 18,
                    backgroundColor = intent?.getStringExtra(PlayerActivity.EXTRA_BACKGROUND_COLOR) ?: DEFAULT_BACKGROUND_COLOR,
                    textOverlay = intent?.getBooleanExtra(PlayerActivity.EXTRA_TEXT_OVERLAY, false) ?: false,
                    width = intent?.takeIf {
                        it.hasExtra(PlayerActivity.EXTRA_WIDTH)
                    }?.getIntExtra(PlayerActivity.EXTRA_WIDTH, 0)?.takeIf { it > 0 },
                    height = intent?.takeIf {
                        it.hasExtra(PlayerActivity.EXTRA_HEIGHT)
                    }?.getIntExtra(PlayerActivity.EXTRA_HEIGHT, 0)?.takeIf { it > 0 }
                )
                streamType = when (intent?.getStringExtra(PlayerActivity.EXTRA_STREAM_TYPE)) {
                    StreamType.Mjpeg.wireName -> StreamType.Mjpeg
                    StreamType.Snapshot.wireName -> StreamType.Snapshot
                    StreamType.Notification.wireName -> StreamType.Notification
                    else -> StreamType.Hls
                }
                durationSeconds = intent?.takeIf {
                    it.hasExtra(PlayerActivity.EXTRA_DURATION_SECONDS)
                }?.getIntExtra(PlayerActivity.EXTRA_DURATION_SECONDS, 0)?.takeIf { it > 0 }
                actionCallbackUrl = intent?.getStringExtra(PlayerActivity.EXTRA_ACTION_CALLBACK_URL)
                actions = if (actionCallbackUrl != null) {
                    OverlayAction.listFromJsonString(
                        intent?.getStringExtra(PlayerActivity.EXTRA_ACTIONS_JSON)
                    )
                } else {
                    emptyList()
                }
                buttonStyle = ButtonStyle.fromJsonString(
                    intent?.getStringExtra(PlayerActivity.EXTRA_BUTTON_STYLE_JSON)
                )
                mediaFocus = mediaFocusFrom(intent?.getStringExtra(PlayerActivity.EXTRA_MEDIA_FOCUS))
                actionInFlight = false
                showOverlay()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        autoCloseHandler.removeCallbacksAndMessages(null)
        removeOverlay()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun showOverlay() {
        if (overlayView != null) {
            removeOverlay()
        }

        if (
            streamType != StreamType.Notification &&
            showNotification &&
            !style.textOverlay &&
            actions.isEmpty()
        ) {
            showMediaWithFooterOverlay()
            return
        }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = roundedBackground()
            // Overlay video uses a texture-backed PlayerView so the outer outline can clip
            // the whole notification into a single rounded glass container.
            clipToOutline = true
        }
        if (streamType == StreamType.Notification) {
            addNotificationContent(root, fillHeight = style.height != null)
        } else {
            val mediaContainer = FrameLayout(this).apply {
                setBackgroundColor(Color.BLACK)
                layoutParams = mediaLayoutParams()
            }
            if (streamType == StreamType.Snapshot) {
                addSnapshotView(mediaContainer, url, updateStateOnLoad = true)
            } else if (streamType == StreamType.Mjpeg) {
                addMjpegView(mediaContainer)
            } else {
                addPlayerView(mediaContainer)
            }

            if (showNotification && style.textOverlay) {
                addNotificationOverlay(mediaContainer)
            }
            errorTextView = buildErrorTextView()
            mediaContainer.addView(errorTextView)

            root.addView(mediaContainer)

            if (showNotification && !style.textOverlay) {
                addNotificationContent(root, fillHeight = false)
            }
        }
        if (actions.isNotEmpty()) {
            root.addView(buildActionBar())
        }

        val params = overlayLayoutParams(overlayHeight(), OVERLAY_MARGIN_PX)
        if (actions.isNotEmpty()) {
            // Take D-pad focus so the remote drives the buttons. The window gives focus back
            // to the app underneath as soon as it is removed (Back, timeout, or after an action).
            params.flags = params.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
        }
        if (streamType == StreamType.Notification) {
            applyBackgroundBlur(params)
        }

        runCatching {
            windowManager.addView(root, params)
            overlayView = root
            requestMediaFocus()
            actionButtons.firstOrNull()?.root?.let { button -> button.post { button.requestFocus() } }
            updatePlaybackState(
                status = if (streamType == StreamType.Notification) PlaybackStatus.Ready else PlaybackStatus.Buffering,
                isPlaying = false,
                errorMessage = null
            )
            AppLog.playbackStart(url)
            scheduleAutoClose()
        }.onFailure { error ->
            AppLog.error("Unable to show overlay fallback", error)
            player?.release()
            player = null
            stopSelf()
        }
    }

    private fun showMediaWithFooterOverlay() {
        val mediaRoot = FrameLayout(this).apply {
            background = roundedBackground()
            clipToOutline = true
        }
        if (streamType == StreamType.Snapshot) {
            addSnapshotView(mediaRoot, url, updateStateOnLoad = true)
        } else if (streamType == StreamType.Mjpeg) {
            addMjpegView(mediaRoot)
        } else {
            addPlayerView(mediaRoot)
        }
        errorTextView = buildErrorTextView()
        mediaRoot.addView(errorTextView)

        val footer = buildNotificationContent(fillHeight = false).apply {
            background = glassTextBackground()
        }
        val footerHeight = measureFooterHeight(footer)
        val mediaHeight = style.height
            ?.let { (it - footerHeight).coerceAtLeast(MIN_MEDIA_HEIGHT_PX) }
            ?: OVERLAY_HEIGHT_PX
        val mediaYOffset = if (isBottomPosition()) {
            OVERLAY_MARGIN_PX + footerHeight
        } else {
            OVERLAY_MARGIN_PX
        }
        val footerYOffset = if (isBottomPosition()) {
            OVERLAY_MARGIN_PX
        } else {
            OVERLAY_MARGIN_PX + mediaHeight
        }
        val mediaParams = overlayLayoutParams(mediaHeight, mediaYOffset)
        val footerParams = overlayLayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            footerYOffset
        )
        applyBackgroundBlur(footerParams)

        runCatching {
            windowManager.addView(mediaRoot, mediaParams)
            overlayView = mediaRoot
            requestMediaFocus()
            windowManager.addView(footer, footerParams)
            textOverlayView = footer
            updatePlaybackState(
                status = if (streamType == StreamType.Notification) PlaybackStatus.Ready else PlaybackStatus.Buffering,
                isPlaying = false,
                errorMessage = null
            )
            AppLog.playbackStart(url)
            scheduleAutoClose()
        }.onFailure { error ->
            AppLog.error("Unable to show overlay fallback", error)
            runCatching { windowManager.removeView(mediaRoot) }
            runCatching { windowManager.removeView(footer) }
            player?.release()
            player = null
            stopSelf()
        }
    }

    private fun overlayLayoutParams(height: Int, yOffset: Int): WindowManager.LayoutParams =
        WindowManager.LayoutParams(
            overlayWidth(),
            height,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = overlayGravity()
            x = OVERLAY_MARGIN_PX
            y = yOffset
        }

    private fun measureFooterHeight(footer: View): Int {
        footer.measure(
            View.MeasureSpec.makeMeasureSpec(overlayWidth(), View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        )
        return footer.measuredHeight.coerceAtLeast(MIN_FOOTER_HEIGHT_PX)
    }

    private fun roundedBackground(): GradientDrawable =
        GradientDrawable().apply {
            setColor(parseColorOrDefault(style.backgroundColor, DEFAULT_BACKGROUND_COLOR_INT))
            cornerRadius = NOTIFICATION_CORNER_RADIUS_PX
        }

    private fun applyBackgroundBlur(params: WindowManager.LayoutParams) {
        if (!BuildConfig.EXPERIMENTAL_OVERLAY_BACKGROUND_BLUR) {
            return
        }

        val backgroundColor = parseColorOrDefault(
            style.backgroundColor,
            DEFAULT_BACKGROUND_COLOR_INT
        )
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || Color.alpha(backgroundColor) == 255) {
            return
        }

        // Android applies this at the window level, so opaque video/image pixels are not
        // needlessly blurred. The effect is only visible through the translucent glass areas.
        params.flags = params.flags or WindowManager.LayoutParams.FLAG_BLUR_BEHIND
        params.blurBehindRadius = OVERLAY_BACKGROUND_BLUR_RADIUS_PX
    }

    private fun buildErrorTextView(): TextView =
        TextView(this).apply {
            setTextColor(Color.WHITE)
            setBackgroundColor(0xCC000000.toInt())
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(16, 12, 16, 12)
            visibility = TextView.GONE
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        }

    /** One door button: an optional round icon badge and a label, drawn per [buttonStyle]. */
    private inner class ActionButton(
        val root: LinearLayout,
        private val badge: TextView,
        private val label: TextView
    ) {
        var action: OverlayAction? = null
            private set

        fun show(next: OverlayAction) {
            action = next
            label.text = next.label
            val glyph = iconGlyph(next.icon)
            badge.text = glyph.orEmpty()
            badge.visibility = if (buttonStyle.showIcon && glyph != null) View.VISIBLE else View.GONE
        }

        fun setLabel(text: String) {
            label.text = text
        }
    }

    private fun buildActionBar(): LinearLayout {
        actionButtons.clear()
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(ACTION_BAR_PADDING_PX, ACTION_BAR_PADDING_PX, ACTION_BAR_PADDING_PX, ACTION_BAR_PADDING_PX)
            background = glassTextBackground()
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        actions.forEachIndexed { index, action ->
            val button = buildActionButton(index)
            button.show(action)
            button.root.setOnClickListener { onActionPressed(button) }
            actionButtons += button
            bar.addView(button.root)
        }
        return bar
    }

    private fun buildActionButton(index: Int): ActionButton {
        val style = buttonStyle
        val accent = parseColorOrDefault(style.accentColor, DEFAULT_ACCENT_COLOR)
        val textColor = parseColorOrDefault(style.textColor, Color.WHITE)
        val badge = TextView(this).apply {
            gravity = Gravity.CENTER
            textSize = style.textSize.toFloat()
            includeFontPadding = false
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(withAlpha(accent, BADGE_ALPHA))
            }
            layoutParams = LinearLayout.LayoutParams(BADGE_SIZE_PX, BADGE_SIZE_PX).apply {
                marginEnd = BADGE_GAP_PX
            }
        }
        val label = TextView(this).apply {
            setTextColor(textColor)
            textSize = style.textSize.toFloat()
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            includeFontPadding = false
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = if (style.shape == "tile") Gravity.CENTER_VERTICAL or Gravity.START else Gravity.CENTER
            setPadding(ACTION_BUTTON_H_PADDING_PX, ACTION_BUTTON_V_PADDING_PX, ACTION_BUTTON_H_PADDING_PX, ACTION_BUTTON_V_PADDING_PX)
            background = actionButtonBackground(style, accent)
            isFocusable = true
            isFocusableInTouchMode = true
            isClickable = true
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                if (index > 0) marginStart = ACTION_BUTTON_GAP_PX
            }
            addView(badge)
            addView(label)
            setOnKeyListener { _, keyCode, event ->
                if (keyCode == KeyEvent.KEYCODE_BACK) {
                    if (event.action == KeyEvent.ACTION_UP) {
                        AppLog.playbackStop(reason = "overlay_back_pressed")
                        stopSelf()
                    }
                    true
                } else {
                    false
                }
            }
            setOnFocusChangeListener { view, hasFocus ->
                view.animate().scaleX(if (hasFocus) 1.04f else 1f)
                    .scaleY(if (hasFocus) 1.04f else 1f)
                    .setDuration(120)
                    .start()
            }
        }
        return ActionButton(root, badge, label)
    }

    private var armedActionId: String? = null

    private fun onActionPressed(button: ActionButton) {
        val action = button.action ?: return
        if (actionInFlight) return
        if (action.confirm && armedActionId != action.id) {
            armedActionId = action.id
            button.setLabel(action.confirmLabel ?: getString(R.string.overlay_action_confirm))
            mainHandler.postDelayed({
                if (armedActionId == action.id && !actionInFlight) {
                    armedActionId = null
                    button.setLabel(action.label)
                }
            }, ACTION_CONFIRM_WINDOW_MS)
            return
        }
        armedActionId = null
        runAction(button, action)
    }

    private fun runAction(button: ActionButton, action: OverlayAction) {
        val callbackUrl = actionCallbackUrl ?: return
        actionInFlight = true
        // Don't let the popup time out underneath a request that is still running.
        autoCloseHandler.removeCallbacksAndMessages(null)
        button.setLabel(getString(R.string.overlay_action_working))
        Thread {
            val result = runCatching { postAction(callbackUrl, action.id) }
            mainHandler.post {
                actionInFlight = false
                if (overlayView == null) return@post
                result.onSuccess { response ->
                    AppLog.lifecycleEvent("overlay_action_succeeded", action.label)
                    val next = response.optJSONObject("next")?.let { json ->
                        OverlayAction.listFromJsonString(org.json.JSONArray().put(json).toString())
                            .firstOrNull()
                    }
                    if (next != null) {
                        // Door unlocked / garage opened: keep the video up and offer
                        // Lock / Close. Home Assistant closes the popup once secured.
                        button.show(next)
                        val staySeconds = response.optLong("stayOpenSeconds", 0L)
                        val stayMillis = if (staySeconds > 0) staySeconds * 1_000L else FOLLOW_UP_CLOSE_DELAY_MS
                        autoCloseHandler.postDelayed({ stopSelf() }, stayMillis)
                    } else {
                        button.setLabel(getString(R.string.overlay_action_done))
                        autoCloseHandler.postDelayed({ stopSelf() }, ACTION_SUCCESS_CLOSE_DELAY_MS)
                    }
                }.onFailure { error ->
                    AppLog.error("Overlay action failed: ${action.label}", error)
                    button.setLabel(getString(R.string.overlay_action_failed))
                    mainHandler.postDelayed({
                        if (overlayView != null && !actionInFlight) button.setLabel(action.label)
                    }, ACTION_FAILED_RESET_MS)
                    autoCloseHandler.postDelayed({ stopSelf() }, ACTION_FAILED_CLOSE_DELAY_MS)
                }
            }
        }.start()
    }

    private fun postAction(callbackUrl: String, actionId: String): JSONObject {
        val token = PairingState.pairedToken(applicationContext)
            ?: throw IOException("Receiver is not paired")
        val body = JSONObject()
            .put("deviceId", ReceiverDeviceInfo.stableDeviceId(applicationContext))
            .put("actionId", actionId)
            .toString()
            .toByteArray(Charsets.UTF_8)
        val connection = (URL(callbackUrl).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = ACTION_TIMEOUT_MS
            readTimeout = ACTION_TIMEOUT_MS
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Authorization", "Bearer $token")
        }
        try {
            connection.outputStream.use { it.write(body) }
            val status = connection.responseCode
            if (status !in 200..299) {
                val detail = runCatching {
                    connection.errorStream?.bufferedReader()?.use { it.readText() }
                        ?.let { JSONObject(it).optString("error") }
                }.getOrNull()
                throw IOException("Home Assistant returned HTTP $status${detail?.let { ": $it" } ?: ""}")
            }
            val text = connection.inputStream.bufferedReader().use { it.readText() }
            return runCatching { JSONObject(text) }.getOrDefault(JSONObject())
        } finally {
            connection.disconnect()
        }
    }

    private fun actionButtonBackground(style: ButtonStyle, accent: Int): StateListDrawable {
        val fill = parseColorOrDefault(style.backgroundColor, DEFAULT_BUTTON_COLOR)
        val radius = when (style.shape) {
            "pill" -> PILL_RADIUS_PX
            "square" -> 0f
            else -> style.cornerRadius.toFloat()
        }
        return StateListDrawable().apply {
            addState(
                intArrayOf(android.R.attr.state_focused),
                GradientDrawable().apply {
                    setColor(fill)
                    cornerRadius = radius
                    setStroke(ACTION_FOCUSED_STROKE_PX, accent)
                }
            )
            addState(
                intArrayOf(),
                GradientDrawable().apply {
                    setColor(fill)
                    cornerRadius = radius
                    setStroke(1, IDLE_OUTLINE_COLOR)
                }
            )
        }
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))

    private fun addNotificationContent(root: LinearLayout, fillHeight: Boolean) {
        root.addView(
            buildNotificationContent(fillHeight).apply {
                background = glassTextBackground()
            }
        )
    }

    private fun addNotificationOverlay(root: FrameLayout) {
        val content = buildNotificationContent(fillHeight = false).apply {
            setBackgroundColor(parseColorOrDefault(style.backgroundColor, DEFAULT_BACKGROUND_COLOR_INT))
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        }
        root.addView(content)
    }

    private fun glassTextBackground(): GradientDrawable {
        val baseColor = parseColorOrDefault(
            style.backgroundColor,
            DEFAULT_BACKGROUND_COLOR_INT
        )
        return GradientDrawable().apply {
            setColor(baseColor)
            cornerRadii = floatArrayOf(
                0f,
                0f,
                0f,
                0f,
                NOTIFICATION_CORNER_RADIUS_PX,
                NOTIFICATION_CORNER_RADIUS_PX,
                NOTIFICATION_CORNER_RADIUS_PX,
                NOTIFICATION_CORNER_RADIUS_PX
            )
            setStroke(TEXT_GLASS_STROKE_PX, TEXT_GLASS_STROKE_COLOR)
        }
    }

    private fun buildNotificationContent(fillHeight: Boolean): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 20, 24, 20)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                if (fillHeight) {
                    ViewGroup.LayoutParams.MATCH_PARENT
                } else {
                    ViewGroup.LayoutParams.WRAP_CONTENT
                }
            )
        }.also { content ->
            TextView(this).apply {
                text = title.ifBlank { getString(R.string.notification_default_title) }
                setTextColor(parseColorOrDefault(style.titleColor, Color.rgb(80, 191, 242)))
                textSize = style.titleSize.toFloat()
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                includeFontPadding = false
                content.addView(this)
            }
            message?.takeIf { it.isNotBlank() }?.let { body ->
                TextView(this).apply {
                    text = body
                    setTextColor(parseColorOrDefault(style.messageColor, Color.rgb(251, 245, 245)))
                    textSize = style.messageSize.toFloat()
                    setPadding(0, 10, 0, 0)
                    includeFontPadding = false
                    content.addView(this)
                }
            }
        }

    private fun addPlayerView(root: FrameLayout) {
        val previewImageView = previewUrl?.let { imageUrl ->
            addSnapshotView(root, imageUrl, updateStateOnLoad = false)
        }
        lateinit var playerView: PlayerView
        var fallbackStarted = false
        val overlayPlayer = buildReceiverPlayer(this).also { exoPlayer ->
            exoPlayer.volume = if (muted) 0f else 1f
            exoPlayer.addListener(
                object : Player.Listener {
                    override fun onPlaybackStateChanged(playbackState: Int) {
                        val playerError = exoPlayer.playerError
                        if (playerError == null && playbackState == Player.STATE_READY) {
                            playerView.alpha = 1f
                            previewImageView?.visibility = ImageView.GONE
                        }
                        updatePlaybackState(
                            status = playerError?.let { PlaybackStatus.Error }
                                ?: playbackState.toPlaybackStatus(),
                            isPlaying = exoPlayer.isPlaying,
                            errorMessage = playerError?.toDisplayMessage()
                        )
                    }

                    override fun onIsPlayingChanged(isPlaying: Boolean) {
                        val playerError = exoPlayer.playerError
                        if (playerError == null && isPlaying) {
                            playerView.alpha = 1f
                            previewImageView?.visibility = ImageView.GONE
                        }
                        updatePlaybackState(
                            status = playerError?.let { PlaybackStatus.Error }
                                ?: exoPlayer.playbackState.toPlaybackStatus(),
                            isPlaying = isPlaying,
                            errorMessage = playerError?.toDisplayMessage()
                        )
                    }

                    override fun onPlayerError(error: PlaybackException) {
                        val message = error.toDisplayMessage()
                        val playableFallbackUrl = fallbackUrl
                        if (
                            !fallbackStarted &&
                            playableFallbackUrl != null &&
                            fallbackStreamType == StreamType.Mjpeg
                        ) {
                            fallbackStarted = true
                            AppLog.error(
                                "Overlay playback failed, starting MJPEG fallback: $message",
                                error
                            )
                            exoPlayer.release()
                            playerView.visibility = PlayerView.GONE
                            url = playableFallbackUrl
                            streamType = StreamType.Mjpeg
                            updatePlaybackState(
                                status = PlaybackStatus.Buffering,
                                isPlaying = false,
                                errorMessage = message
                            )
                            addMjpegView(root, previewImageView)
                            return
                        }
                        val hasPreviewFallback = previewImageView != null
                        if (hasPreviewFallback) {
                            playerView.alpha = 0f
                            previewImageView.visibility = ImageView.VISIBLE
                        }
                        updatePlaybackState(
                            status = PlaybackStatus.Error,
                            isPlaying = false,
                            errorMessage = if (hasPreviewFallback) {
                                getString(R.string.snapshot_fallback_after_video_error, message)
                            } else {
                                message
                            }
                        )
                        errorTextView?.text = error.toOverlayMessage(this@OverlayPlayerService)
                        errorTextView?.visibility = if (hasPreviewFallback) {
                            TextView.GONE
                        } else {
                            TextView.VISIBLE
                        }
                        AppLog.error("Overlay playback failed: $message", error)
                    }
                }
            )
            exoPlayer.setMediaItem(MediaItem.fromUri(url))
            exoPlayer.repeatMode = Player.REPEAT_MODE_ONE
            exoPlayer.playWhenReady = true
            exoPlayer.prepare()
        }
        player = overlayPlayer

        playerView = LayoutInflater.from(this)
            .inflate(R.layout.overlay_player_view, root, false) as PlayerView
        playerView.apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            useController = false
            this.player = overlayPlayer
            alpha = if (previewImageView == null) 1f else 0.01f
        }
        root.addView(playerView)
    }

    private fun addMjpegView(root: FrameLayout, existingPreviewImageView: ImageView? = null) {
        val previewImageView = existingPreviewImageView ?: previewUrl?.let { imageUrl ->
            addSnapshotView(root, imageUrl, updateStateOnLoad = false)
        }
        val streamImageView = ImageView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.CENTER_CROP
            setBackgroundColor(Color.BLACK)
            alpha = if (previewImageView == null) 1f else 0.01f
        }
        root.addView(streamImageView)

        mjpegRunning = true
        mjpegThread = Thread {
            var firstFrameRendered = false
            runCatching {
                val connection = URL(url).openConnection().apply {
                    connectTimeout = MJPEG_CONNECT_TIMEOUT_MS
                    readTimeout = MJPEG_READ_TIMEOUT_MS
                }
                BufferedInputStream(connection.getInputStream()).use { stream ->
                    while (mjpegRunning) {
                        val frame = readJpegFrame(stream) ?: break
                        val bitmap = BitmapFactory.decodeByteArray(frame, 0, frame.size)
                            ?: error(getString(R.string.error_snapshot_unsupported_image))
                        mainHandler.post {
                            streamImageView.setImageBitmap(bitmap)
                            streamImageView.alpha = 1f
                            previewImageView?.visibility = ImageView.GONE
                            if (!firstFrameRendered) {
                                firstFrameRendered = true
                                updatePlaybackState(
                                    status = PlaybackStatus.Ready,
                                    isPlaying = true,
                                    errorMessage = null
                                )
                            }
                        }
                    }
                }
            }.onFailure { error ->
                mainHandler.post {
                    val message = error.message ?: getString(R.string.error_unknown_snapshot)
                    val hasPreviewFallback = previewImageView != null
                    if (hasPreviewFallback) {
                        streamImageView.alpha = 0f
                        previewImageView.visibility = ImageView.VISIBLE
                    }
                    updatePlaybackState(
                        status = PlaybackStatus.Error,
                        isPlaying = false,
                        errorMessage = if (hasPreviewFallback) {
                            getString(R.string.snapshot_fallback_after_video_error, message)
                        } else {
                            message
                        }
                    )
                    errorTextView?.text = getString(R.string.snapshot_unavailable)
                    errorTextView?.visibility = if (hasPreviewFallback) {
                        TextView.GONE
                    } else {
                        TextView.VISIBLE
                    }
                    AppLog.error("MJPEG stream failed: $message", error)
                }
            }
        }.apply { start() }
    }

    private fun readJpegFrame(stream: BufferedInputStream): ByteArray? {
        val frame = ByteArrayOutputStream()
        var previous = -1
        var inFrame = false

        while (mjpegRunning) {
            val current = stream.read()
            if (current == -1) {
                return null
            }

            if (!inFrame) {
                if (previous == JPEG_MARKER && current == JPEG_START_MARKER) {
                    frame.reset()
                    frame.write(JPEG_MARKER)
                    frame.write(JPEG_START_MARKER)
                    inFrame = true
                }
            } else {
                frame.write(current)
                if (frame.size() > MAX_MJPEG_FRAME_BYTES) {
                    throw IOException(
                        getString(
                            R.string.error_mjpeg_frame_too_large,
                            MAX_MJPEG_FRAME_BYTES
                        )
                    )
                }
                if (previous == JPEG_MARKER && current == JPEG_END_MARKER) {
                    return frame.toByteArray()
                }
            }
            previous = current
        }
        return null
    }

    private fun addSnapshotView(
        root: FrameLayout,
        imageUrl: String,
        updateStateOnLoad: Boolean
    ): ImageView {
        val imageView = ImageView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.CENTER_CROP
            setBackgroundColor(android.graphics.Color.BLACK)
        }
        root.addView(imageView)

        Thread {
            runCatching {
                val connection = URL(imageUrl).openConnection().apply {
                    connectTimeout = SNAPSHOT_CONNECT_TIMEOUT_MS
                    readTimeout = SNAPSHOT_READ_TIMEOUT_MS
                }
                connection.getInputStream().use { stream ->
                    BitmapFactory.decodeStream(stream)
                        ?: error(getString(R.string.error_snapshot_unsupported_image))
                }
            }.onSuccess { bitmap ->
                mainHandler.post {
                    imageView.setImageBitmap(bitmap)
                    if (updateStateOnLoad) {
                        updatePlaybackState(
                            status = PlaybackStatus.Ready,
                            isPlaying = false,
                            errorMessage = null
                        )
                    }
                }
            }.onFailure { error ->
                mainHandler.post {
                    val message = error.message ?: getString(R.string.error_unknown_snapshot)
                    if (updateStateOnLoad) {
                        updatePlaybackState(
                            status = PlaybackStatus.Error,
                            isPlaying = false,
                            errorMessage = message
                        )
                        errorTextView?.text = getString(R.string.snapshot_unavailable)
                        errorTextView?.visibility = TextView.VISIBLE
                    }
                    AppLog.error("Snapshot load failed: $message", error)
                }
            }
        }.start()
        return imageView
    }

    /**
     * Pause or quiet whatever is playing while the popup is up. Taking transient
     * audio focus makes well-behaved apps (Netflix, YouTube, Plex, ...) pause or
     * duck, and resume on their own when the focus is given back in [removeOverlay].
     */
    private fun requestMediaFocus() {
        if (mediaFocus == MEDIA_FOCUS_NONE || Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val audioManager = getSystemService(AUDIO_SERVICE) as? AudioManager ?: return
        val gain = if (mediaFocus == MEDIA_FOCUS_DUCK) {
            AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK
        } else {
            AudioManager.AUDIOFOCUS_GAIN_TRANSIENT
        }
        val request = AudioFocusRequest.Builder(gain)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
            .setOnAudioFocusChangeListener { }
            .build()
        runCatching { audioManager.requestAudioFocus(request) }
            .onSuccess { audioFocusRequest = request }
            .onFailure { AppLog.error("Could not pause playback for the popup", it) }
    }

    private fun abandonMediaFocus() {
        val request = audioFocusRequest ?: return
        audioFocusRequest = null
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val audioManager = getSystemService(AUDIO_SERVICE) as? AudioManager ?: return
        runCatching { audioManager.abandonAudioFocusRequest(request) }
    }

    private fun removeOverlay() {
        abandonMediaFocus()
        mainHandler.removeCallbacksAndMessages(null)
        actionButtons.clear()
        armedActionId = null
        mjpegRunning = false
        mjpegThread?.interrupt()
        mjpegThread = null
        overlayView?.let { view ->
            runCatching { windowManager.removeView(view) }
        }
        textOverlayView?.let { view ->
            runCatching { windowManager.removeView(view) }
        }
        overlayView = null
        textOverlayView = null
        errorTextView = null
        player?.release()
        player = null
        ReceiverRuntimeState.markIdle()
        AppLog.playbackStop(reason = "overlay_service_stopped")
    }

    private fun updatePlaybackState(
        status: PlaybackStatus,
        isPlaying: Boolean,
        errorMessage: String?
    ) {
        ReceiverRuntimeState.update(
            ReceiverPlaybackSnapshot(
                status = status,
                isPlaying = isPlaying,
                mode = ReceiverPlaybackMode.Overlay,
                title = title,
                url = url,
                previewUrl = previewUrl,
                fallbackUrl = fallbackUrl,
                fallbackStreamType = fallbackStreamType?.wireName,
                streamType = streamType.wireName,
                errorMessage = errorMessage
            )
        )
    }

    private fun scheduleAutoClose() {
        autoCloseHandler.removeCallbacksAndMessages(null)
        val duration = durationSeconds ?: return
        autoCloseHandler.postDelayed({ stopSelf() }, duration * 1_000L)
    }

    private fun overlayGravity(): Int = overlayGravityFor(style.position)

    private fun isBottomPosition(): Boolean =
        style.position == NotificationPosition.BottomRight ||
            style.position == NotificationPosition.BottomLeft

    private fun overlayWidth(): Int =
        style.width ?: if (streamType == StreamType.Notification) {
            NOTIFICATION_WIDTH_PX
        } else {
            OVERLAY_WIDTH_PX
        }

    private fun overlayHeight(): Int =
        style.height ?: if (streamType == StreamType.Notification) {
            WindowManager.LayoutParams.WRAP_CONTENT
        } else if (showNotification || actions.isNotEmpty()) {
            WindowManager.LayoutParams.WRAP_CONTENT
        } else {
            OVERLAY_HEIGHT_PX
        }

    private fun mediaLayoutParams(): LinearLayout.LayoutParams =
        if ((showNotification || actions.isNotEmpty()) && style.height == null) {
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                OVERLAY_HEIGHT_PX
            )
        } else {
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f
            )
        }

    companion object {
        const val ACTION_SHOW = "com.hatvpip.receiver.action.SHOW_OVERLAY"
        const val ACTION_STOP = "com.hatvpip.receiver.action.STOP_OVERLAY"
        private const val OVERLAY_WIDTH_PX = 640
        private const val OVERLAY_HEIGHT_PX = 360
        private const val MIN_MEDIA_HEIGHT_PX = 120
        private const val MIN_FOOTER_HEIGHT_PX = 1
        private const val NOTIFICATION_WIDTH_PX = 512
        private const val NOTIFICATION_CORNER_RADIUS_PX = 18f
        private const val OVERLAY_BACKGROUND_BLUR_RADIUS_PX = 28
        private const val TEXT_GLASS_STROKE_PX = 1
        private const val TEXT_GLASS_STROKE_COLOR = 0x44FFFFFF
        private const val OVERLAY_MARGIN_PX = 48
        private const val DEFAULT_BACKGROUND_COLOR = "#B30F0E0E"
        private const val MJPEG_CONNECT_TIMEOUT_MS = 5_000
        private const val MJPEG_READ_TIMEOUT_MS = 5_000
        private const val SNAPSHOT_CONNECT_TIMEOUT_MS = 5_000
        private const val SNAPSHOT_READ_TIMEOUT_MS = 5_000
        private const val MAX_MJPEG_FRAME_BYTES = 8 * 1024 * 1024
        private const val JPEG_MARKER = 0xFF
        private const val JPEG_START_MARKER = 0xD8
        private const val JPEG_END_MARKER = 0xD9
        private val DEFAULT_BACKGROUND_COLOR_INT = 0xB30F0E0E.toInt()
        private const val ACTION_BAR_PADDING_PX = 16
        private const val ACTION_BUTTON_GAP_PX = 16
        private const val ACTION_BUTTON_H_PADDING_PX = 20
        private const val ACTION_BUTTON_V_PADDING_PX = 14
        private const val ACTION_FOCUSED_STROKE_PX = 3
        private const val ACTION_CONFIRM_WINDOW_MS = 3_000L
        private const val ACTION_SUCCESS_CLOSE_DELAY_MS = 1_500L
        private const val ACTION_FAILED_RESET_MS = 3_000L
        private const val ACTION_FAILED_CLOSE_DELAY_MS = 10_000L
        private const val ACTION_TIMEOUT_MS = 30_000
        private const val FOLLOW_UP_CLOSE_DELAY_MS = 15 * 60 * 1_000L
        private const val BADGE_SIZE_PX = 40
        private const val BADGE_GAP_PX = 14
        private const val BADGE_ALPHA = 0x33
        private const val PILL_RADIUS_PX = 999f
        private const val IDLE_OUTLINE_COLOR = 0x22FFFFFF
        private val DEFAULT_ACCENT_COLOR = 0xFF03A9F4.toInt()
        private val DEFAULT_BUTTON_COLOR = 0xEB1C1C1C.toInt()
    }
}

internal fun iconGlyph(icon: String?): String? =
    when (icon?.trim()?.lowercase()?.removePrefix("mdi:")) {
        null, "" -> null
        "unlock", "lock-open", "lock-open-variant" -> "\uD83D\uDD13"
        "lock", "lock-outline" -> "\uD83D\uDD12"
        "garage", "garage-open", "garage-variant", "car" -> "\uD83D\uDE97"
        "garage-close", "garage-lock" -> "\u2B07\uFE0F"
        "door", "door-open", "door-close", "door-closed" -> "\uD83D\uDEAA"
        "light", "lightbulb" -> "\uD83D\uDCA1"
        "bell", "doorbell" -> "\uD83D\uDD14"
        else -> icon?.takeIf { it.codePointCount(0, it.length) <= 2 }
    }

internal fun overlayGravityFor(position: NotificationPosition): Int =
    when (position) {
        NotificationPosition.TopRight -> Gravity.TOP or Gravity.END
        NotificationPosition.TopLeft -> Gravity.TOP or Gravity.START
        NotificationPosition.BottomRight -> Gravity.BOTTOM or Gravity.END
        NotificationPosition.BottomLeft -> Gravity.BOTTOM or Gravity.START
    }

private fun parseColorOrDefault(value: String, defaultColor: Int): Int =
    runCatching { Color.parseColor(value) }.getOrDefault(defaultColor)

private fun Int.toPlaybackStatus(): PlaybackStatus =
    when (this) {
        Player.STATE_BUFFERING -> PlaybackStatus.Buffering
        Player.STATE_READY -> PlaybackStatus.Ready
        Player.STATE_ENDED -> PlaybackStatus.Ended
        Player.STATE_IDLE -> PlaybackStatus.Idle
        else -> PlaybackStatus.Idle
    }

private fun PlaybackException.toDisplayMessage(): String =
    "$errorCodeName: ${message ?: "unknown playback error"}"

private fun PlaybackException.toOverlayMessage(context: android.content.Context): String =
    when (errorCode) {
        PlaybackException.ERROR_CODE_DECODER_INIT_FAILED ->
            context.getString(R.string.error_unsupported_video_stream)
        else -> context.getString(R.string.error_playback_error_code, errorCodeName)
    }

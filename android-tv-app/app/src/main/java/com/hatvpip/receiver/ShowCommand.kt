package com.hatvpip.receiver

import org.json.JSONException
import org.json.JSONObject

enum class StreamType(val wireName: String) {
    Hls("hls"),
    Mjpeg("mjpeg"),
    Snapshot("snapshot"),
    Notification("notification")
}

data class ShowCommand(
    val title: String,
    val url: String,
    val streamType: StreamType,
    val durationSeconds: Int?,
    val enterPip: Boolean,
    val previewUrl: String?,
    val fallbackUrl: String?,
    val fallbackStreamType: StreamType?,
    val showNotification: Boolean,
    val message: String?,
    val style: NotificationStyle,
    val muted: Boolean = true,
    val actions: List<OverlayAction> = emptyList(),
    val actionCallbackUrl: String? = null,
    val buttonStyle: ButtonStyle = ButtonStyle(),
    /** "none", "pause" (pause other apps, resume after) or "duck" (lower their volume). */
    val mediaFocus: String = MEDIA_FOCUS_NONE
) {
    val hasActions: Boolean
        get() = actions.isNotEmpty() && actionCallbackUrl != null

    companion object {
        fun testVideo(title: String = "Test Video"): ShowCommand =
            ShowCommand(
                title = title,
                url = PlayerActivity.TEST_STREAM_URL,
                streamType = StreamType.Hls,
                durationSeconds = null,
                enterPip = false,
                previewUrl = null,
                fallbackUrl = null,
                fallbackStreamType = null,
                showNotification = false,
                message = null,
                style = NotificationStyle(),
                muted = true
            )

        fun fromJson(body: String): Result<ShowCommand> =
            runCatching {
                val json = JSONObject(body)
                val streamType = when (val value = json.optString("streamType", "hls")) {
                    StreamType.Hls.wireName -> StreamType.Hls
                    StreamType.Mjpeg.wireName -> StreamType.Mjpeg
                    StreamType.Snapshot.wireName -> StreamType.Snapshot
                    StreamType.Notification.wireName -> StreamType.Notification
                    else -> error("Unsupported `streamType`: $value")
                }
                val url = json.optString("url").trim()
                if (streamType == StreamType.Notification) {
                    require(url.isEmpty() || url.startsWith("http://") || url.startsWith("https://")) {
                        "`url` must be an HTTP or HTTPS URL"
                    }
                } else {
                    require(url.isNotEmpty()) { "`url` is required" }
                    require(url.startsWith("http://") || url.startsWith("https://")) {
                        "`url` must be an HTTP or HTTPS URL"
                    }
                }

                val durationSeconds = if (json.has("durationSeconds") && !json.isNull("durationSeconds")) {
                    json.getInt("durationSeconds").also {
                        require(it > 0) { "`durationSeconds` must be greater than 0" }
                    }
                } else {
                    null
                }
                val previewUrl = json.optString("previewUrl").trim().ifBlank { null }
                if (previewUrl != null) {
                    require(previewUrl.startsWith("http://") || previewUrl.startsWith("https://")) {
                        "`previewUrl` must be an HTTP or HTTPS URL"
                    }
                }
                val fallbackUrl = json.optString("fallbackUrl").trim().ifBlank { null }
                if (fallbackUrl != null) {
                    require(fallbackUrl.startsWith("http://") || fallbackUrl.startsWith("https://")) {
                        "`fallbackUrl` must be an HTTP or HTTPS URL"
                    }
                }
                val fallbackStreamType = fallbackUrl?.let {
                    when (val value = json.optString("fallbackStreamType", StreamType.Mjpeg.wireName)) {
                        StreamType.Hls.wireName -> StreamType.Hls
                        StreamType.Mjpeg.wireName -> StreamType.Mjpeg
                        StreamType.Snapshot.wireName -> StreamType.Snapshot
                        else -> error("Unsupported `fallbackStreamType`: $value")
                    }
                }

                ShowCommand(
                    title = json.optString("title", "HA TV PiP").ifBlank { "HA TV PiP" },
                    url = url,
                    streamType = streamType,
                    durationSeconds = durationSeconds,
                    enterPip = json.optBoolean("enterPip", true),
                    previewUrl = previewUrl,
                    fallbackUrl = fallbackUrl,
                    fallbackStreamType = fallbackStreamType,
                    showNotification = json.optBoolean(
                        "showNotification",
                        streamType == StreamType.Notification || json.has("message")
                    ),
                    message = json.optString("message").trim().ifBlank { null },
                    style = NotificationStyle.fromJson(json),
                    muted = json.optBoolean("muted", true),
                    actions = OverlayAction.listFromJson(json),
                    buttonStyle = ButtonStyle.fromJson(json.optJSONObject("buttonStyle")),
                    mediaFocus = mediaFocusFrom(json.optString("mediaFocus", MEDIA_FOCUS_NONE)),
                    actionCallbackUrl = json.optString("actionCallbackUrl").trim().ifBlank { null }
                        ?.also {
                            require(it.startsWith("http://") || it.startsWith("https://")) {
                                "`actionCallbackUrl` must be an HTTP or HTTPS URL"
                            }
                        }
                )
            }.recoverCatching { error ->
                if (error is JSONException) {
                    throw IllegalArgumentException("Request body must be valid JSON", error)
                }
                throw error
            }
    }
}

const val MEDIA_FOCUS_NONE = "none"
const val MEDIA_FOCUS_PAUSE = "pause"
const val MEDIA_FOCUS_DUCK = "duck"

fun mediaFocusFrom(value: String?): String =
    when (value) {
        MEDIA_FOCUS_PAUSE, MEDIA_FOCUS_DUCK -> value
        else -> MEDIA_FOCUS_NONE
    }

/**
 * A button shown under the overlay media. The receiver never decides what an action does:
 * it only sends [id] (a one-time token minted by Home Assistant) back to
 * `actionCallbackUrl`, and Home Assistant runs the service call it stored for that id.
 */
data class OverlayAction(
    val id: String,
    val label: String,
    val icon: String? = null,
    val confirm: Boolean = true,
    val confirmLabel: String? = null
) {
    fun toJson(): JSONObject =
        JSONObject()
            .put("id", id)
            .put("label", label)
            .put("icon", icon)
            .put("confirm", confirm)
            .put("confirmLabel", confirmLabel)

    companion object {
        const val MAX_ACTIONS = 3

        fun listFromJson(json: JSONObject): List<OverlayAction> {
            val array = json.optJSONArray("actions") ?: return emptyList()
            require(array.length() <= MAX_ACTIONS) { "At most $MAX_ACTIONS `actions` are supported" }
            return (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                val id = item.optString("id").trim()
                val label = item.optString("label").trim()
                require(id.isNotEmpty()) { "`actions[$index].id` is required" }
                require(label.isNotEmpty()) { "`actions[$index].label` is required" }
                OverlayAction(
                    id = id,
                    label = label.take(40),
                    icon = item.optString("icon").trim().ifBlank { null },
                    confirm = item.optBoolean("confirm", true),
                    confirmLabel = item.optString("confirmLabel").trim().ifBlank { null }?.take(40)
                )
            }
        }

        fun listToJsonString(actions: List<OverlayAction>): String =
            org.json.JSONArray().also { array -> actions.forEach { array.put(it.toJson()) } }
                .toString()

        fun listFromJsonString(value: String?): List<OverlayAction> =
            if (value.isNullOrBlank()) {
                emptyList()
            } else {
                runCatching { listFromJson(JSONObject().put("actions", org.json.JSONArray(value))) }
                    .getOrDefault(emptyList())
            }
    }
}

/**
 * How door buttons are drawn. Home Assistant sends this with every popup (set in
 * the integration's appearance settings), so the look changes without an app update.
 * Defaults match Home Assistant's dark dashboard tile cards.
 */
data class ButtonStyle(
    val shape: String = "tile",
    val backgroundColor: String = "#EB1C1C1C",
    val textColor: String = "#E1E1E1",
    val accentColor: String = "#03A9F4",
    val textSize: Int = 16,
    val cornerRadius: Int = 12,
    val showIcon: Boolean = true
) {
    fun toJson(): JSONObject =
        JSONObject()
            .put("shape", shape)
            .put("backgroundColor", backgroundColor)
            .put("textColor", textColor)
            .put("accentColor", accentColor)
            .put("textSize", textSize)
            .put("cornerRadius", cornerRadius)
            .put("showIcon", showIcon)

    companion object {
        private val SHAPES = setOf("tile", "pill", "square")

        fun fromJson(json: JSONObject?): ButtonStyle {
            if (json == null) return ButtonStyle()
            val defaults = ButtonStyle()
            return ButtonStyle(
                shape = json.optString("shape", defaults.shape).takeIf { it in SHAPES }
                    ?: defaults.shape,
                backgroundColor = json.optString("backgroundColor", defaults.backgroundColor),
                textColor = json.optString("textColor", defaults.textColor),
                accentColor = json.optString("accentColor", defaults.accentColor),
                textSize = json.optInt("textSize", defaults.textSize).coerceIn(10, 32),
                cornerRadius = json.optInt("cornerRadius", defaults.cornerRadius).coerceIn(0, 40),
                showIcon = json.optBoolean("showIcon", defaults.showIcon)
            )
        }

        fun fromJsonString(value: String?): ButtonStyle =
            if (value.isNullOrBlank()) {
                ButtonStyle()
            } else {
                runCatching { fromJson(JSONObject(value)) }.getOrDefault(ButtonStyle())
            }
    }
}

enum class NotificationPosition(val wireName: String, val fallbackIndex: Int) {
    TopRight("top_right", 0),
    TopLeft("top_left", 1),
    BottomRight("bottom_right", 2),
    BottomLeft("bottom_left", 3);

    companion object {
        fun fromWire(value: String): NotificationPosition =
            entries.firstOrNull { it.wireName == value }
                ?: value.toIntOrNull()?.let { index ->
                    entries.firstOrNull { it.fallbackIndex == index }
                }
                ?: TopRight
    }
}

data class NotificationStyle(
    val position: NotificationPosition = NotificationPosition.TopRight,
    val titleColor: String = "#50BFF2",
    val titleSize: Int = 24,
    val messageColor: String = "#fbf5f5",
    val messageSize: Int = 18,
    val backgroundColor: String = "#B30F0E0E",
    val textOverlay: Boolean = false,
    val width: Int? = null,
    val height: Int? = null
) {
    companion object {
        fun fromJson(json: JSONObject): NotificationStyle =
            NotificationStyle(
                position = NotificationPosition.fromWire(json.optString("position", "top_right")),
                titleColor = json.optString("titleColor", "#50BFF2"),
                titleSize = json.optInt("titleSize", 24).coerceIn(10, 48),
                messageColor = json.optString("messageColor", "#fbf5f5"),
                messageSize = json.optInt("messageSize", 18).coerceIn(10, 40),
                backgroundColor = json.optString("backgroundColor", "#B30F0E0E"),
                textOverlay = json.optBoolean("textOverlay", false),
                width = json.optionalDimension("width", 240, 1600),
                height = json.optionalDimension("height", 120, 900)
            )
    }
}

private fun JSONObject.optionalDimension(name: String, minimum: Int, maximum: Int): Int? =
    if (has(name) && !isNull(name)) {
        getInt(name).coerceIn(minimum, maximum)
    } else {
        null
    }

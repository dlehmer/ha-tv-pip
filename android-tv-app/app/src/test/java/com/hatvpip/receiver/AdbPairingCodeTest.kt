package com.hatvpip.receiver

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdbPairingCodeTest {
    @Test
    fun acceptsOnlySixDigitCodes() {
        assertTrue(PairingState.isValidCode("123456"))
        assertFalse(PairingState.isValidCode("12345"))
        assertFalse(PairingState.isValidCode("1234567"))
        assertFalse(PairingState.isValidCode("12a456"))
        assertFalse(PairingState.isValidCode(""))
    }
}

class ButtonStyleTest {
    @Test
    fun defaultsMatchHomeAssistantTiles() {
        val style = ButtonStyle.fromJson(null)
        org.junit.Assert.assertEquals("tile", style.shape)
        org.junit.Assert.assertEquals("#03A9F4", style.accentColor)
        org.junit.Assert.assertEquals(12, style.cornerRadius)
    }

    @Test
    fun parsesAndClampsHomeAssistantSettings() {
        val style = ButtonStyle.fromJson(
            org.json.JSONObject()
                .put("shape", "pill")
                .put("accentColor", "#FF9800")
                .put("textSize", 99)
                .put("showIcon", false)
        )
        org.junit.Assert.assertEquals("pill", style.shape)
        org.junit.Assert.assertEquals("#FF9800", style.accentColor)
        org.junit.Assert.assertEquals(32, style.textSize)
        assertFalse(style.showIcon)
        org.junit.Assert.assertEquals(style, ButtonStyle.fromJsonString(style.toJson().toString()))
    }

    @Test
    fun unknownShapeFallsBackToTile() {
        val style = ButtonStyle.fromJson(org.json.JSONObject().put("shape", "blob"))
        org.junit.Assert.assertEquals("tile", style.shape)
    }
}

class MediaFocusTest {
    @Test
    fun parsesMediaFocusFromShowCommand() {
        val pause = ShowCommand.fromJson(
            """{"url": "https://example.com/a.jpg", "streamType": "snapshot", "mediaFocus": "pause"}"""
        ).getOrThrow()
        org.junit.Assert.assertEquals(MEDIA_FOCUS_PAUSE, pause.mediaFocus)

        val mute = ShowCommand.fromJson(
            """{"url": "https://example.com/a.jpg", "streamType": "snapshot", "mediaFocus": "mute"}"""
        ).getOrThrow()
        org.junit.Assert.assertEquals(MEDIA_FOCUS_MUTE, mute.mediaFocus)

        val unknown = ShowCommand.fromJson(
            """{"url": "https://example.com/a.jpg", "streamType": "snapshot", "mediaFocus": "loud"}"""
        ).getOrThrow()
        org.junit.Assert.assertEquals(MEDIA_FOCUS_NONE, unknown.mediaFocus)

        val absent = ShowCommand.fromJson(
            """{"url": "https://example.com/a.jpg", "streamType": "snapshot"}"""
        ).getOrThrow()
        org.junit.Assert.assertEquals(MEDIA_FOCUS_NONE, absent.mediaFocus)
    }
}

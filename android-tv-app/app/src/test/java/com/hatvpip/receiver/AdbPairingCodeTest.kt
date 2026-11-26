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

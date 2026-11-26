package com.hatvpip.receiver

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Lets Home Assistant pair over ADB without anyone reading a code off the screen.
 *
 * Home Assistant, already connected to the TV over ADB (which the user approved on
 * the TV), picks a pairing code and sends it here with `am broadcast`. The manifest
 * protects this receiver with android.permission.DUMP, which the ADB shell holds and
 * ordinary apps cannot, so no other app on the TV can start a pairing this way.
 * Home Assistant then confirms the pairing with the same code over the usual
 * /pair/confirm endpoint.
 */
class AdbPairingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_ADB_PAIR) {
            resultCode = RESULT_REJECTED
            return
        }
        val code = intent.getStringExtra(EXTRA_CODE)?.trim().orEmpty()
        val clientId = intent.getStringExtra(EXTRA_CLIENT_ID)?.trim().orEmpty()
        val clientName = intent.getStringExtra(EXTRA_CLIENT_NAME)?.trim().orEmpty()
        if (!PairingState.isValidCode(code) || clientId.isEmpty()) {
            AppLog.error("ADB pairing request rejected: invalid code or client id")
            resultCode = RESULT_REJECTED
            return
        }

        val result = PairingState.startPairing(
            context = context.applicationContext,
            request = PairingStartRequest(
                clientId = clientId,
                clientName = clientName.ifEmpty { "Home Assistant" },
                replaceExisting = true
            ),
            presetCode = code
        )
        resultCode = if (result.isSuccess) {
            AppLog.pairingEvent("pairing_started_over_adb", PairingStatus.Pending.wireName)
            Activity.RESULT_OK
        } else {
            AppLog.error("ADB pairing request failed", result.exceptionOrNull())
            RESULT_REJECTED
        }
    }

    companion object {
        const val ACTION_ADB_PAIR = "com.dlehmer.hatvpipplus.action.ADB_PAIR"
        const val EXTRA_CODE = "code"
        const val EXTRA_CLIENT_ID = "clientId"
        const val EXTRA_CLIENT_NAME = "clientName"
        private const val RESULT_REJECTED = 1
    }
}

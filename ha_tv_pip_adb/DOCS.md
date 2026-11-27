# HA TV PiP Plus ADB server

Runs Google's `adb` server for the HA TV PiP Plus integration. The integration
installs and starts this app for you when a TV only offers **Wireless
debugging** (newer Google TV devices), then uses it to pair with the TV and
install the HA TV PiP Plus app.

There is nothing to configure. The server is reachable only from inside Home
Assistant; it publishes no ports on your network. Pairings are kept in the
app's data, so TVs stay paired after restarts.

You can stop or uninstall the app once your TVs are set up. The integration
starts it again when you use **Install or update TV app** or **Grant overlay
permission** on a TV that needs Wireless debugging.

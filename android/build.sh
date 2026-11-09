#!/bin/bash
# Rebuilds the KhorochAi Android app (a Trusted Web Activity) into android/app-release-signed.apk.
#
# One-time setup on the build machine (nothing here touches the website):
#   - a JDK 17 and the Android SDK, pointed to by ~/.bubblewrap/config.json
#   - the signing key at ~/.khorochai-android/khorochai.keystore, with its password in keystore-password.txt next to it
#   - a `tools` link inside the SDK folder:  ln -s cmdline-tools/latest tools   (Bubblewrap looks for it)
#
# Before a release that changes the app, raise appVersionCode in twa-manifest.json. After building, copy the APK to
# static/downloads/KhorochAi.apk and deploy the site so the download buttons serve the new file.
set -euo pipefail
cd "$(dirname "$0")"

KEY_DIR="$HOME/.khorochai-android"
export BUBBLEWRAP_KEYSTORE_PASSWORD="$(cat "$KEY_DIR/keystore-password.txt")"
export BUBBLEWRAP_KEY_PASSWORD="$BUBBLEWRAP_KEYSTORE_PASSWORD"

npx -y @bubblewrap/cli@latest update --skipVersionUpgrade --manifest=./twa-manifest.json
npx -y @bubblewrap/cli@latest build --skipPwaValidation
ls -l app-release-signed.apk app-release-bundle.aab

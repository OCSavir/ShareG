# ShareG on Android

ShareG ships to Android via `flet build apk` (Python app wrapped by the Flet
SDK through Flutter's toolchain - no Java code required).

## One-time setup
1. Install JDK 17 and the Android SDK (or Android Studio).
2. `pip install flet` (build CLI included).

## Build
```bash
flet build apk
```
Output: `build/apk/build/outputs/apk/release/app-release.apk`
(sign release builds with `--android-sign`).

## Install on a device
```bash
adb install build/apk/build/outputs/apk/release/app-release.apk
```

## Permissions (granted via the [tool.flet.android] section of pyproject.toml)
- INTERNET, ACCESS_NETWORK_STATE, ACCESS_WIFI_STATE - TCP/UDP sockets
- CHANGE_WIFI_MULTICAST_STATE - UDP multicast discovery
- READ_MEDIA_IMAGES/VIDEO/AUDIO + READ/WRITE_EXTERNAL_STORAGE - pickers & saved files

## Notes
- Multicast reception on Wi-Fi can require a MulticastLock; ShareG's
  discovery module retries the multicast join automatically if it fails.
- Received files land in Downloads/ShareG where writable, else app storage.

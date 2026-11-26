# Changelog

## Unreleased

- Renamed the app to Lysnav for Windows. The README and About view now identify Lysnav as an independent third-party app.
- Renamed the installed executable to `Lysnav.exe` and future installer and portable downloads to use the `Lysnav-Windows-` prefix. Existing installs retain their installer identity, pairing, and device approvals.

## 1.0.0-rc.2 (2026-11-26)

- Fixed memory growth during long-running tray sessions. Hub refreshes now dispose of replaced room and climate controls.
- Reused the notification-area menu across refreshes and updated its room choices when the approved rooms change.
- Released the Tray Settings dialog after Save or Cancel.
- Added Qt regression tests for repeated refreshes, room selection, and dialog disposal.

## 1.0.0-rc.1

First release candidate with a Windows x64 installer and portable executable, per-room lighting controls, climate readings, and notification-area shortcuts.

# Clients

This folder contains future native or semi-native clients built on top of the existing `frontend/` app.

## Current Direction

- `desktop/`: Tauri-based desktop client
- `android/`: Capacitor-based Android client

The current scaffolds intentionally reuse the existing web frontend so the repository can evolve incrementally.
The product goal is to move more file/process storage to the user device in these clients.

## Local-First Status

Both native clients now prioritize device-local persistence for:

- guest document copies
- logged-in user process backups

The shared frontend installs a native storage bridge when it detects:

- `desktop-tauri`
- `android-capacitor`

If the bridge is unavailable, the frontend still falls back to browser IndexedDB.

## Validation Status

- `desktop/`
  - `npm install` completed
  - `npx tauri info` completed
  - Windows build environment completed on `D:\dev\skrt-tools`
  - `npm run tauri:build` completed
  - bundled outputs verified:
    - NSIS installer
    - MSI installer
- `android/`
  - `npm install` completed
  - `npm run build:web` completed
  - `npx cap add android` completed
  - `npm run cap:sync` completed
  - Android SDK installed on `D:\dev\skrt-tools\android\sdk`
  - JDK 21 installed on `D:\dev\skrt-tools\jdk-21`
  - `gradlew assembleDebug` completed

## Release Artifacts

Use:

```powershell
.\clients\prepare-release.ps1
```

This copies the latest build outputs into:

- `clients/artifacts/sKrt-setup.exe`
- `clients/artifacts/sKrt.apk`

## Frontend Bridge Contract

The shared frontend now reads local-persistence capability from:

- `frontend/src/lib/clientPersistence.ts`

The native bridge is installed from:

- `frontend/src/lib/nativeStorageBridge.ts`

`desktop` / `android` clients should expose `window.__SKRT_NATIVE_STORAGE__` and implement:

- `listGuestDocuments`
- `putGuestDocument`
- `deleteGuestDocument`
- `listUserBackups`
- `putUserBackup`
- `deleteUserBackup`

If the bridge is missing, the frontend falls back to browser IndexedDB automatically.

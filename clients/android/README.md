# Android Client

This directory is the starting point for the future Android app.

## Technology Choice

- Framework: `Capacitor`
- Why:
  - better fit than TWA for local-device storage goals
  - can evolve toward stronger local file/process handling
  - still reuses the existing `frontend/` codebase

## Current State

This scaffold currently packages the existing `frontend/` build output.

Validation already completed in this repo:

- `npm install`: passed
- `npm run build:web`: passed
- `npx cap add android`: passed
- `npm run cap:sync`: passed
- `gradlew assembleDebug`: currently blocked because Android SDK is not configured

## Planned Local-First Work

- local original-file persistence on device
- local process-file persistence on device
- update reminder dialog with download jump
- later native plugins only when truly needed

## Useful Commands

From this directory:

```bash
npm install
npm run build:web
npm run cap:sync
npm run cap:open:android
```

From `android/`:

```bash
gradlew.bat assembleDebug
```

## Prerequisites

Before building an APK on Windows, configure one of these:

- `ANDROID_HOME`
- `android/local.properties` with `sdk.dir=...`

Java is already available on the current machine. The missing part is the Android SDK path.

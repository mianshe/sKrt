# Clients Plan

## Current Decision

- PWA is now treated as a **cloud-first** client.
- Uploading in PWA should mean:
  - cloud parsing
  - cloud vectorization
  - cloud indexing
  - cloud chat over processed knowledge
- Do not continue designing the PWA around a split "local vs cloud processing" mental model.

## Why Desktop / APK Still Matter

The future `exe` and `apk` versions are not just wrappers for branding. Their main product purpose is:

- keeping original files on the user device when possible
- keeping process files on the user device when possible
- keeping heavier local caches on the user device when possible
- providing a better path for local-first or hybrid knowledge handling than a pure PWA can offer

## Important Constraint

If a future task asks for "store process files locally but still use the processed knowledge", prefer solving it in:

- desktop client first
- then native/mobile client

Do **not** assume the current PWA/browser architecture can cleanly provide the same local-storage guarantees.

## Build Direction

- `frontend/`: keep as PWA and cloud-first
- future `clients/desktop/`: local-first capable desktop app, prefer `Tauri`
- future `clients/android/`: Android client path, prefer `Capacitor`

## Update Direction

When `exe` / `apk` arrive, they may be maintained separately from the PWA and should support:

- version check on startup
- update reminder dialog
- download jump for new releases
- optional forced update when versions are too old

## Technology Choice

### Desktop

- Prefer `Tauri` over `Electron`
- Reason:
  - lighter package size
  - lower memory usage
  - better fit for "local files + local process artifacts + existing web frontend"

### Android

- Prefer `Capacitor` for the Android app
- Reason:
  - the Android app exists mainly to keep data and process files on the user device
  - this requires stronger local-device capability than a plain web wrapper
  - the current native path is maintained directly under `clients/android/`

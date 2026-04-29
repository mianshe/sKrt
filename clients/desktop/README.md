# Desktop Client

This directory is the starting point for the future `exe` client.

## Technology Choice

- Framework: `Tauri`
- Why:
  - smaller package size than Electron
  - lower idle memory use
  - fits the project goal of local-first file and process artifact storage

## Current State

This scaffold currently wraps the existing `frontend/` Vite app:

- development: runs `frontend/` through Vite and loads it in Tauri
- build: runs `frontend/` build and packages the generated web assets

Validation already completed in this repo:

- `npm install`: passed
- `npx tauri info`: passed
- current machine blocker:
  - `rustc` / `cargo` not installed
  - missing Visual Studio Build Tools with MSVC and Windows SDK

## Current Local-First Work

- guest document copies are persisted to local desktop app data
- logged-in user process backups are persisted to local desktop app data
- the shared frontend uses a Tauri native storage bridge when available

If the native bridge is unavailable for any reason, the app falls back to browser IndexedDB inside the embedded web runtime.

## Useful Commands

From this directory:

```bash
npm install
npx tauri info
npm run tauri:dev
npm run tauri:build
```

## Prerequisites

Before `tauri:dev` or `tauri:build` can work on Windows, install:

- `Rust` via `rustup`
- Visual Studio Build Tools
- MSVC toolchain
- Windows SDK

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

## Planned Local-First Work

- local original-file storage
- local process-file storage
- local cache and sync policy
- client-side update reminder dialog

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

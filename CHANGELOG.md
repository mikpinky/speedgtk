# Changelog

## 2.0 — 2026-08-09

- Completed the modular `src/` architecture with separate domain, storage,
  speed-test integration, UI, dialog, and widget layers.
- Isolated the Ookla adapter and prepared a dormant provider boundary for a
  future LibreSpeed implementation.
- Hardened subprocess shutdown so every active CLI process is terminated when
  the window or application closes.
- Added characterization and regression coverage for core behavior, provider
  parsing, rendering, and process lifecycle.

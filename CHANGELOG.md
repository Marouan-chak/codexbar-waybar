# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-05-31

### Added
- **Reset time format picker.** A new `resetTimeFormat` setting (`provider`,
  `local`, or `utc`) controls how the "Resets ..." line in both the tooltip and
  the popover is rendered. Provider keeps the upstream string as-is (default,
  no behavior change on upgrade); Local reformats `resetsAt` in the system
  timezone with an explicit TZ suffix; UTC does the same with a literal "UTC".
  All three modes tier the format by proximity (today drops the date,
  this-year drops the year). Configurable from the popover's Settings view,
  via `~/.config/codexbar-waybar/state.json`, or per-instance with the
  `CODEXBAR_RESET_TIME_FORMAT` env var. Useful when the provider's
  description is timezone-ambiguous -- for example Codex emits `"7:10 AM"`
  for a `resetsAt` that's actually in UTC.

### Fixed
- **Cached provider visibility.** The wrapper now keeps the last successful
  provider snapshot visible as stale when a requested provider returns empty,
  invalid, or errored output, instead of dropping it from the tooltip during
  transient refresh failures.

## [0.2.1] — 2026-05-30

### Fixed
- **Antigravity auth and local TLS on Linux.** The CLI looks for Google OAuth creds at
  `~/.codexbar/antigravity/oauth_creds.json` (written by the macOS app), which
  never exists on Linux — so it failed with "Antigravity Google auth not found.
  Use Antigravity login to authenticate." The wrapper now bridges the creds
  that `agy login` writes to `~/.gemini/oauth_creds.json` into the
  `ANTIGRAVITY_OAUTH_CREDENTIALS_JSON` env var. Additionally, a dynamic TLS redirect shim
  (`cert_redirect.so`) is compiled on install and preloaded during loopback Antigravity queries
  to securely trust the local server's self-signed SSL certificates without altering the
  system CA store. Override the source credentials path with `CODEXBAR_ANTIGRAVITY_CREDS`.
- **CLI install URL.** Release assets are now versioned
  (`CodexBarCLI-<tag>-linux-<arch>.tar.gz`); the old
  `releases/latest/download/CodexBarCLI-linux-x86_64.tar.gz` link 404s.
  `install.sh` and the README now resolve the latest tag from the GitHub API.

## [0.2.0] — 2026-05-17

### Added
- **Provider logos in the popover.** 39 SVG brand marks mirrored from
  upstream CodexBar (MIT, see `assets/providers/NOTICE`) are recoloured at
  load time and rendered next to provider names in both the tab strip and
  the Settings provider list. `install.sh` drops them at
  `~/.local/share/codexbar-waybar/icons/`.
- **Pin a provider to the bar.** Settings now has a **Show in bar** picker
  above the provider list: chips for each enabled provider plus *Highest*.
  Picking a provider writes `~/.config/codexbar-waybar/state.json` and
  signals waybar (`SIGRTMIN+8`) so the bar text updates within a second.
  When pinned, the bar shows `🤖 P% • W%` (session • weekly) instead of the
  cross-provider maximum.
- `CODEXBAR_BAR_PROVIDER` env var to override the pinned provider per Waybar
  instance (e.g., one bar per monitor pinned to a different provider).

### Changed
- **Claude OAuth 429s now fall back to `--source cli`** transparently.
  Anthropic's rate limits no longer leave the popover stuck on
  "Cached — last refresh failed" — the wrapper retries with the local Claude
  CLI source and produces fresh data.
- **Reset descriptions are normalised** end-to-end. Both the OAuth output
  (`May 17 at 6:20AM`) and the CLI output (`Resets6:20am(Europe/Paris)`) get
  the missing spaces inserted, so the popover and tooltip read consistently
  in either source.
- README screenshots refreshed to reflect logos, pin mode, and the inline
  Settings view.

## [0.1.1] — 2026-05-16

### Changed
- **Wrapper script reads enabled providers from `~/.codexbar/config.json`**
  instead of hardcoding `(codex claude gemini)`. A Codex-only user no longer
  sees Claude/Gemini error tabs. Override per-instance with
  `CODEXBAR_PROVIDERS="codex claude"` if you need to bypass the config.
- **Default refresh interval dropped from 60 s to 30 s** and the module now
  declares `signal: 8`, so a `pkill -RTMIN+8 waybar` forces an immediate
  refresh. The popover sends that signal after a Save so the bar reflects
  toggled providers without waiting for the next tick.
- **`install.sh` now hard-fails if the `codexbar` CLI is missing**, with the
  exact tarball + libxml2-legacy commands inline. Previous behaviour was a
  soft warning that left users with a silent half-install.
- **Inline Settings view** (already in 0.1.1 — moved out of Unreleased):
  clicking *Settings…* swaps the popover body to a scrollable provider
  list with per-provider toggle switches and a *Save* button. macOS-only
  providers appear in a separate grayed section with a hint, so users
  don't waste time enabling things that can't work on Linux.

## [0.1.0] — 2026-05-16

### Added
- `codexbar.sh` — Waybar custom-module backend that polls the CodexBar Linux
  CLI per provider with a configurable stagger, caches the last successful
  snapshot, and emits Waybar JSON (`text` / `tooltip` / `class` / `percentage`)
  keyed on the highest used-percent.
- `codexbar-popup.py` — GTK4 + `gtk4-layer-shell` popover that mirrors the
  macOS menu: provider tab strip, flat sections, thin progress bars, reset
  countdowns, credit balances. Auto-detects `libgtk4-layer-shell.so` across
  Arch / Debian / Fedora.
- `codexbar.jsonc` — Waybar module definition with click-to-open and
  right-click `notify-send` fallback.
- `codexbar.css` — `ok` / `warning` / `critical` / `stale` state styling for
  the Waybar entry.
- `install.sh` — idempotent installer.

[Unreleased]: https://github.com/Marouan-chak/codexbar-waybar/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Marouan-chak/codexbar-waybar/releases/tag/v0.3.0
[0.2.1]: https://github.com/Marouan-chak/codexbar-waybar/releases/tag/v0.2.1
[0.2.0]: https://github.com/Marouan-chak/codexbar-waybar/releases/tag/v0.2.0
[0.1.1]: https://github.com/Marouan-chak/codexbar-waybar/releases/tag/v0.1.1
[0.1.0]: https://github.com/Marouan-chak/codexbar-waybar/releases/tag/v0.1.0

# codexbar-waybar

> AI provider usage in your [Waybar](https://github.com/Alexays/Waybar) — and a macOS-style popover when you click it.

[CodexBar](https://github.com/steipete/CodexBar) is a macOS menu-bar app that
surfaces Codex / Claude / Gemini / Copilot / … usage limits and reset windows.
It ships a Linux CLI but no desktop UI for Linux compositors.

This repo bridges that gap:

- A tiny **Waybar custom module** that polls the `codexbar` CLI and shows the
  most-constrained provider as `🤖 27%` with state classes for styling.
- A **GTK4 popover** modeled on the macOS menu — provider tab strip, flat
  sections, thin progress bars, reset countdowns, credits balance.

<p align="center">
  <img src="assets/popup.png" alt="codexbar-waybar popover" width="380" />
</p>

Validated on Arch Linux + Hyprland (HyDE), but should work on any Wayland
compositor with Waybar + gtk4-layer-shell.

## Requirements

- The `codexbar` Linux CLI from
  [steipete/CodexBar releases](https://github.com/steipete/CodexBar/releases/latest).
- [Waybar](https://github.com/Alexays/Waybar).
- `jq`, `python3`, `python-gobject` (PyGObject), `gtk4`, `gtk4-layer-shell`,
  `libadwaita` (optional but harmless).

### Arch Linux

```bash
sudo pacman -S waybar jq python-gobject gtk4 gtk4-layer-shell libadwaita
```

> **libxml2 gotcha**: Arch currently ships `libxml2.so.16` (2.15+); the
> CodexBar Linux tarball links against `libxml2.so.2`. Install the compat
> package:
>
> ```bash
> sudo pacman -S libxml2-legacy
> ```

### Debian / Ubuntu

```bash
sudo apt install waybar jq python3-gi gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0
```

### Install the codexbar CLI

```bash
curl -LO https://github.com/steipete/CodexBar/releases/latest/download/CodexBarCLI-vX-linux-x86_64.tar.gz
tar -xzf CodexBarCLI-vX-linux-x86_64.tar.gz
install -m 0755 CodexBarCLI ~/.local/bin/codexbar
codexbar --help
```

Then configure providers in `~/.codexbar/config.json`. Minimal Linux-friendly
config:

```json
{
  "providers": [
    { "id": "codex",  "enabled": true },
    { "id": "claude", "enabled": true },
    { "id": "gemini", "enabled": true }
  ],
  "version": 1
}
```

Make sure you've already signed in via the providers' own CLIs (`codex login`,
`claude /login`, `gcloud auth application-default login` for Gemini, etc.).

## Install codexbar-waybar

Clone and run the installer:

```bash
git clone https://github.com/Marouan-chak/codexbar-waybar.git
cd codexbar-waybar
./install.sh
```

The installer:

- Copies `codexbar.sh` and `codexbar-popup.py` to `~/.config/waybar/scripts/`.
- Drops `codexbar.jsonc` as `~/.config/waybar/modules/custom-codexbar.json`.
- Appends `codexbar.css` to `~/.config/waybar/user-style.css` (idempotent).

The one manual step is wiring `custom/codexbar` into your `config.jsonc`. For a
hand-curated config, add it to a `modules-right` group:

```jsonc
"group/pill#right1": {
  "modules": ["backlight", "pulseaudio", "custom/codexbar", "clock"]
}
```

Reload Waybar (`Ctrl+Alt+W` on HyDE, or `pkill waybar; waybar &`).

## Usage

- **Left-click** the `🤖 nn%` icon → opens the popover (clicking again closes
  it).
- **Right-click** → `notify-send` summary, no GUI.
- **Tab strip** in the popover lets you switch between providers.
- **ESC** or the `✕` button closes the popover.

## Tuning

Each knob is an environment variable; set it inside the Waybar module
definition or your shell profile.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEXBAR_BIN` | `~/.local/bin/codexbar` | Path to the CLI binary. |
| `CODEXBAR_STAGGER` | `0.5` | Seconds between provider fetches (raises this if Claude OAuth keeps 429-ing). |
| `XDG_CACHE_HOME` | `~/.cache` | Where `last.json` snapshots live. |
| `CODEXBAR_LAYER_SHELL_LIB` | auto-detected | Override path to `libgtk4-layer-shell.so` if your distro stashes it somewhere unusual. |

To change which providers appear, edit `PROVIDERS=( … )` and `SOURCE_OVERRIDES`
at the top of `codexbar.sh`. Codex and Claude need `--source oauth` on Linux
because their `auto` source tries the (macOS-only) web scrape first.

## How it works

1. Waybar runs `codexbar.sh` every 60 s.
2. The script invokes `codexbar usage --provider <p> --format json` once per
   enabled provider, sequentially, with a small stagger to dodge per-provider
   rate limits.
3. Each response is the same JSON payload the macOS menu-bar app consumes:
   primary / secondary / tertiary usage windows, reset timestamps, credit
   balances, error info.
4. The shell wrapper collapses that into Waybar's JSON contract
   `{"text", "tooltip", "class", "percentage"}` keyed on the highest
   `usedPercent`.
5. The latest successful per-provider response is cached at
   `~/.cache/codexbar-waybar/last.json`. The popover paints from the cache for
   an instant first frame, then refetches in the background.

The popup itself is a GTK4 + `gtk4-layer-shell` window anchored to the top-right
of the focused output, with all rendering done in pure CSS — no extra widget
frameworks.

## States and styling

The Waybar module emits one of these classes; style them in
`~/.config/waybar/user-style.css`:

| Class | Meaning |
| --- | --- |
| `ok` | `0 ≤ percentage < 70` |
| `warning` | `70 ≤ percentage < 90` |
| `critical` | `percentage ≥ 90` |
| `stale` | All providers errored, or any value came from cache |

## Troubleshooting

| Symptom | Likely fix |
| --- | --- |
| `libxml2.so.2: cannot open shared object file` | Install your distro's libxml2 v2.13 compat (`libxml2-legacy` on Arch). |
| Module text is blank | Run `~/.config/waybar/scripts/codexbar.sh` directly — it should print one JSON line. |
| Popover never shows | Run `~/.config/waybar/scripts/codexbar-popup.py` from a terminal; check the warnings. The most common one is `gtk4-layer-shell` not preloading — set `CODEXBAR_LAYER_SHELL_LIB`. |
| `HTTP 429 rate_limit_error` from Claude | Raise `CODEXBAR_STAGGER=1.0` and the module `interval` to 120. |
| Tabs render dark / unreadable | You're probably running an old version that fought Adwaita. Pull latest and reinstall. |

## Roadmap

- Auto-dismiss on outside click (today: ESC, `✕`, or click the bar icon).
- Dark-mode palette (auto-detect from `gsettings color-scheme` or HyDE
  wallbash).
- AUR `PKGBUILD` for one-shot install on Arch.
- Optional Quickshell variant for compositors without Waybar.

## Related

- [CodexBar](https://github.com/steipete/CodexBar) — the upstream macOS app and
  Linux CLI this project wraps.
- [Win-CodexBar](https://github.com/Finesssee/Win-CodexBar) — Windows port of
  the macOS app.

## License

MIT. See [LICENSE](LICENSE).

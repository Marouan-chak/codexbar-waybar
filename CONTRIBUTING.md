# Contributing

Thanks for your interest! This is a small, focused project — easy to hack on.

## Quick start

```bash
git clone https://github.com/Marouan-chak/codexbar-waybar.git
cd codexbar-waybar
./install.sh   # installs to ~/.config/waybar/
```

Reload Waybar and click the `🤖` icon to test changes.

## Running the popup standalone

```bash
~/.config/waybar/scripts/codexbar-popup.py
```

Useful for iterating on CSS/layout without going through Waybar.

## Running the wrapper standalone

```bash
~/.config/waybar/scripts/codexbar.sh | jq
```

Should print one Waybar JSON object. If it errors, your `codexbar` CLI or
provider config is the most likely culprit.

## Coding style

- `codexbar.sh` — POSIX-ish bash with a `set -u`, jq for JSON. Avoid GNU-only
  flags so it works on bash 5+ across distros.
- `codexbar-popup.py` — Python 3.10+, no external deps beyond stdlib + `gi`
  bindings already present on any Wayland desktop with GTK4.
- `codexbar.css` — keep selectors explicit (`button.codexbar-tab`, etc.). GTK4
  CSS doesn't support `!important`, so specificity matters.
- Indent: 4 spaces everywhere.

## Tests

CI runs:

- `bash -n` on every shell script.
- `python3 -m py_compile` on the popup.
- `python3 -m unittest discover -s tests` for focused popup behavior tests.
- `shellcheck -x` on shell scripts.
- A `jq`-based JSONC parse on `codexbar.jsonc`.

Run them locally before opening a PR:

```bash
bash -n codexbar.sh install.sh
python3 -m py_compile codexbar-popup.py
python3 -m unittest discover -s tests
shellcheck -x codexbar.sh install.sh
```

## Provider support

Linux only handles providers that work without browser cookies (Codex/Claude
OAuth, Gemini, Copilot device flow, API-token providers like OpenRouter /
DeepSeek / etc.). If you want to wire one of those into the default list, edit
`PROVIDERS` and `SOURCE_OVERRIDES` in `codexbar.sh` and the README's "Configure
providers" section.

If you need a provider that only has a macOS web/cookie source, open an issue
against [steipete/CodexBar](https://github.com/steipete/CodexBar/issues) for
Linux support there first.

## Pull requests

- One topic per PR. Keep the diff focused.
- Include a screenshot for any visible change (popup layout, Waybar styling).
- Update `CHANGELOG.md` under the `Unreleased` section.
- Don't introduce new runtime dependencies without flagging it in the PR
  description.

## Reporting bugs

Open an issue with:

- Your distro + compositor (e.g. Arch + Hyprland 0.40).
- Output of `codexbar --version` and `~/.config/waybar/scripts/codexbar.sh`.
- For popup issues: output from running the script directly in a terminal so
  GTK warnings are visible.

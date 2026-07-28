import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path


def load_popup_module():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")

    class Application:
        pass

    repository.GLib = types.SimpleNamespace()
    repository.Gtk = types.SimpleNamespace(Application=Application)
    repository.Gtk4LayerShell = types.SimpleNamespace()
    gi.repository = repository
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    os.environ["CODEXBAR_POPUP_PRELOADED"] = "1"

    popup_path = Path(__file__).resolve().parents[1] / "codexbar-popup.py"
    spec = importlib.util.spec_from_file_location("codexbar_popup", popup_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


popup = load_popup_module()


def usage_entry(provider, used_percent, *, account=None, error=None):
    entry = {
        "provider": provider,
        "usage": {"primary": {"usedPercent": used_percent}},
    }
    if account is not None:
        entry["account"] = account
    if error is not None:
        entry["error"] = error
    return entry


class DefaultProviderTests(unittest.TestCase):
    def test_configured_provider_overrides_highest_usage(self):
        data = [
            usage_entry("commandcode", 90),
            usage_entry("codex", 20),
        ]

        self.assertEqual(
            popup.default_provider(data, {"popupProvider": "codex"}),
            "codex",
        )

    def test_configured_provider_prefers_a_healthy_account(self):
        data = [
            usage_entry("codex", 80, account="broken", error="refresh failed"),
            usage_entry("codex", 20, account="healthy"),
            usage_entry("commandcode", 90),
        ]

        self.assertEqual(
            popup.default_provider(data, {"popupProvider": "codex"}),
            "codex\0healthy",
        )

    def test_unavailable_configured_provider_falls_back_to_highest_usage(self):
        data = [
            usage_entry("codex", 20),
            usage_entry("commandcode", 90),
        ]

        self.assertEqual(
            popup.default_provider(data, {"popupProvider": "claude"}),
            "commandcode",
        )

    def test_missing_configuration_preserves_highest_usage_behavior(self):
        data = [
            usage_entry("codex", 20),
            usage_entry("commandcode", 90),
            usage_entry("claude", 100, error="refresh failed"),
        ]

        self.assertEqual(popup.default_provider(data, {}), "commandcode")

    def test_configured_provider_with_only_errors_is_still_selected(self):
        data = [
            usage_entry("codex", 80, account="broken", error="refresh failed"),
            usage_entry("commandcode", 90),
        ]

        self.assertEqual(
            popup.default_provider(data, {"popupProvider": "codex"}),
            "codex\0broken",
        )

    def test_empty_data_has_no_default_provider(self):
        self.assertIsNone(popup.default_provider([], {"popupProvider": "codex"}))


if __name__ == "__main__":
    unittest.main()

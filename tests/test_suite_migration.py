import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "suite_migration", Path(__file__).parents[1] / "scripts/migrate_qing_suites.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def setup_config(tmp_path, name, value, bom=False):
    path = tmp_path / "config" / (name + "_config.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8-sig" if bom else "utf-8")
    return path


def test_preview_is_read_only_and_supports_bom(tmp_path):
    setup_config(
        tmp_path,
        "tavily_result_limiter",
        {"max_results": 10, "chat_budget_seconds": 180},
        bom=True,
    )
    plans = migration.migrate(tmp_path, {"tavily_result_limiter"})
    assert len(plans) == 2
    assert not (tmp_path / "config/astrbot_plugin_qing_chat_config.json").exists()
    assert not (tmp_path / "plugin_data").exists()


def test_import_keeps_secret_and_all_old_fields_local_and_current_values_win(tmp_path):
    setup_config(
        tmp_path,
        "astrbot_plugin_imagegen",
        {"api_key": "fixture-secret", "timeout": 300},
        bom=True,
    )
    setup_config(tmp_path, "astrbot_plugin_qing_media", {"imagegen": {"timeout": 420}})
    plans = migration.migrate(tmp_path, {"astrbot_plugin_imagegen"}, apply=True)
    new = json.loads(
        (tmp_path / "config/astrbot_plugin_qing_media_config.json").read_text()
    )
    assert new["imagegen"] == {
        "timeout": 420,
        "api_key": "fixture-secret",
        "enabled": True,
    }
    assert "fixture-secret" not in json.dumps(plans)
    assert (
        tmp_path / "config/astrbot_plugin_qing_media_config.json"
    ).stat().st_mode & 0o777 == 0o600
    assert new["render"]["enabled"] is False


def test_repeat_migration_never_reimports_stale_defaults_or_resets_state(tmp_path):
    setup_config(tmp_path, "tavily_result_limiter", {"chat_budget_seconds": 180})
    migration.migrate(tmp_path, {"tavily_result_limiter"}, apply=True)
    setup_config(tmp_path, "tavily_result_limiter", {"chat_budget_seconds": 900})
    assert all(
        p["status"] == "already_imported"
        for p in migration.migrate(tmp_path, set(), apply=True)
    )
    data = json.loads(
        (tmp_path / "config/astrbot_plugin_qing_chat_config.json").read_text()
    )
    assert data["conversation"]["chat_budget_seconds"] == 180
    assert not (tmp_path / "plugin_data/tavily_result_limiter").exists()


def test_bad_old_or_new_config_prevents_any_write(tmp_path):
    setup_config(tmp_path, "astrbot_plugin_qing_media", {"imagegen": "invalid"})
    with pytest.raises(ValueError):
        migration.migrate(tmp_path, set(), apply=True)
    assert not (tmp_path / "config/astrbot_plugin_qing_chat_config.json").exists()


def test_prior_enabled_setting_is_preserved(tmp_path):
    setup_config(tmp_path, "astrbot_plugin_memory_recall_fix", {"enabled": False})
    migration.migrate(tmp_path, {"astrbot_plugin_memory_recall_fix"}, apply=True)
    d = json.loads(
        (tmp_path / "config/astrbot_plugin_qing_chat_config.json").read_text()
    )
    assert d["memory_compat"]["enabled"] is False


@pytest.mark.parametrize(
    "selected,expected",
    [
        (["*"], ["*"]),
        ([], []),
        (
            ["astrbot_plugin_mention_llm"],
            ["astrbot_plugin_mention_llm", "astrbot_plugin_qing_chat"],
        ),
        (
            ["astrbot_plugin_imagegen", "astrbot_plugin_roll"],
            [
                "astrbot_plugin_imagegen",
                "astrbot_plugin_roll",
                "astrbot_plugin_qing_chat",
                "astrbot_plugin_qing_media",
            ],
        ),
    ],
)
def test_profile_migration_retains_per_feature_selection_and_is_idempotent(
    tmp_path, selected, expected
):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"plugin_set": selected, "other": {"keep": True}}))
    migration.migrate_profiles([p])
    assert json.loads(p.read_text())["plugin_set"] == selected
    migration.migrate_profiles([p], apply=True)
    assert json.loads(p.read_text()) == {
        "plugin_set": expected,
        "other": {"keep": True},
    }
    assert migration.migrate_profiles([p], apply=True) == []

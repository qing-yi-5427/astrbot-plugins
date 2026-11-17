"""One-time, local-only configuration import; preview is the default.

Run on the AstrBot host/container with its data directory. Credentials never
leave that directory. Existing new-suite values win and the import is idempotent.
Legacy state directories and KV namespaces remain in place for rollback.
"""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

GROUPS = {
    "astrbot_plugin_qing_chat": {
        "mention": "astrbot_plugin_mention_llm",
        "reaction": "astrbot_plugin_get_emoji_reply",
        "conversation": "tavily_result_limiter",
        "context_images": "astrbot_plugin_context_image_limiter",
        "memory_compat": "astrbot_plugin_memory_recall_fix",
        "roll": "astrbot_plugin_roll",
    },
    "astrbot_plugin_qing_media": {
        "imagegen": "astrbot_plugin_imagegen",
        "render": "astrbot_plugin_t2i_style",
        "image_source": "astrbot_plugin_qing_image_source",
    },
}


def read_object(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Configuration root must be an object")
    return value


def fill_missing(target, source):
    result = dict(target)
    for key, value in source.items():
        if key not in result:
            result[key] = value
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = fill_missing(result[key], value)
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate_profiles(paths, *, apply=False):
    prepared = []
    for path in paths:
        data = read_object(path)
        selected = data.get("plugin_set", ["*"])
        if not isinstance(selected, list) or not all(
            isinstance(n, str) for n in selected
        ):
            raise ValueError("Profile plugin_set must be an array of names")
        additions = []
        if selected != ["*"]:
            for suite, modules in GROUPS.items():
                if suite not in selected and set(selected).intersection(
                    modules.values()
                ):
                    additions.append(suite)
        if additions:
            # Retain the old names as the per-feature mask. Runtime policy uses
            # them even after old plugin source has been safely archived.
            prepared.append(
                (path, {**data, "plugin_set": selected + additions}, additions)
            )
    if apply:
        for path, data, _ in prepared:
            atomic_json(path, data)
    return [
        {"profile": path.name, "added_suites": additions}
        for path, _, additions in prepared
    ]


def migrate(data, active, *, apply=False):
    plans = []
    prepared = []
    for name, sections in GROUPS.items():
        target = data / "config" / (name + "_config.json")
        marker = data / "plugin_data" / name / "legacy-import-v1.json"
        if marker.exists():
            if not target.exists():
                raise ValueError(
                    "Import marker exists but suite configuration is missing"
                )
            read_object(target)
            plans.append({"suite": name, "status": "already_imported"})
            continue
        merged = read_object(target) if target.exists() else {}
        sources = {}
        for section, legacy in sections.items():
            old = data / "config" / (legacy + "_config.json")
            value = read_object(old) if old.exists() else {}
            value.setdefault("enabled", legacy in active)
            existing = merged.get(section, {})
            if not isinstance(existing, dict):
                raise ValueError("Suite feature configuration must be an object")
            merged[section] = fill_missing(existing, value)
            sources[legacy] = (
                hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None
            )
        plan = {
            "suite": name,
            "status": "ready",
            "sections": list(sections),
            "source_hashes": sources,
        }
        plans.append(plan)
        prepared.append((target, marker, merged, plan))
    # Both configurations are validated before writing either. Each file is
    # atomic. A crash between files is safe to rerun: explicit new values win.
    if apply:
        for target, marker, merged, plan in prepared:
            atomic_json(target, merged)
            atomic_json(marker, {"version": 1, **plan})
    return plans


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument(
        "--active-json",
        type=Path,
        required=True,
        help="JSON array of actually active legacy plugin names, from dashboard inventory",
    )
    parser.add_argument(
        "--profile-json",
        type=Path,
        action="append",
        default=[],
        help="Explicit additional AstrBot profile file; default cmd_config.json is checked automatically",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    active = json.loads(args.active_json.read_text(encoding="utf-8-sig"))
    if not isinstance(active, list) or not all(
        isinstance(name, str) for name in active
    ):
        raise ValueError("Active-plugin inventory must be an array of names")
    default = args.data.resolve() / "cmd_config.json"
    profiles = list(
        dict.fromkeys(([default] if default.exists() else []) + args.profile_json)
    )
    migrate_profiles(profiles)  # Validate before any writes.
    plans = migrate(args.data.resolve(), set(active), apply=args.apply)
    profile_plans = migrate_profiles(profiles, apply=args.apply)
    # No configuration values, credentials, URLs or session identities emitted.
    print(
        json.dumps(
            {"applied": args.apply, "plans": plans, "profile_plans": profile_plans},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

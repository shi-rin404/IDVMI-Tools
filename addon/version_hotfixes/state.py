from __future__ import annotations

import json
from pathlib import Path

STATE_FILE_NAME = "version_hotfixes.json"


def state_path(addon_root: Path) -> Path:
    return addon_root / "user" / STATE_FILE_NAME


def load_applied_hotfixes(addon_root: Path) -> set[str]:
    path = state_path(addon_root)
    if not path.is_file():
        return set()

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    applied = payload.get("applied", [])
    if not isinstance(applied, list):
        raise ValueError(f"Invalid hotfix state file: {path}")
    return {str(item) for item in applied}


def mark_hotfix_applied(addon_root: Path, hotfix_id: str) -> None:
    path = state_path(addon_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    applied = load_applied_hotfixes(addon_root)
    applied.add(hotfix_id)
    payload = {"applied": sorted(applied)}
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=4)
        file.write("\n")

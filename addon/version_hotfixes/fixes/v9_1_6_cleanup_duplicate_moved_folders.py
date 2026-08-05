from __future__ import annotations

import shutil
from pathlib import Path

STALE_PATHS = (
    Path("export_mod"),
    Path("extract_frame_dump"),
    Path("set_textures"),
    Path("_vendor"),
    Path("xxhash"),
)


def _ensure_inside_addon(target_path: Path, addon_root: Path) -> None:
    resolved_root = addon_root.resolve()
    if target_path.is_symlink():
        target_path.parent.resolve().relative_to(resolved_root)
        return
    target_path.resolve().relative_to(resolved_root)


def _remove_path(target_path: Path, addon_root: Path) -> int:
    if not target_path.exists() and not target_path.is_symlink():
        return 0

    _ensure_inside_addon(target_path, addon_root)
    if target_path.is_symlink() or target_path.is_file():
        target_path.unlink()
        return 1

    shutil.rmtree(target_path)
    return 1


def apply_hotfix(addon_root: Path) -> dict:
    removed = 0
    for relative in STALE_PATHS:
        removed += _remove_path(addon_root / relative, addon_root)
    return {"removed_paths": removed}

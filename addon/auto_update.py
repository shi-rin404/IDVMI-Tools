import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import bpy


GITHUB_REPO = "shi-rin404/IDVMI-Tools"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "IDVMI-Tools-Updater"

EXCLUDED_UPDATE_PARTS = {
    ".git",
    ".github",
    ".vscode",
    ".claude",
    ".codex",
    ".agents",
    "__pycache__",
    "remote_import_cache",
}
EXCLUDED_UPDATE_SUFFIXES = {
    ".pyc",
    ".pyo",
}
EXCLUDED_UPDATE_NAMES = {
    "direct_url.json",
    "export_per_material_log.txt",
    "import_per_material_log.txt",
}


def _addon_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _request_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, target_path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        with target_path.open("wb") as output:
            shutil.copyfileobj(response, output)


def _parse_version(value) -> tuple[int, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(int(part) for part in value)

    text = str(value).strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    parts = []
    for part in text.replace("-", ".").split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts or [0])


def _format_version(version: tuple[int, ...]) -> str:
    return "v" + ".".join(str(part) for part in version)


def _current_version() -> tuple[int, ...]:
    root_package = __package__.split(".", 1)[0]
    root_module = sys.modules.get(root_package)
    bl_info = getattr(root_module, "bl_info", {})
    return _parse_version(bl_info.get("version", (0, 0, 0)))


def _release_download_url(release: dict) -> str:
    for asset in release.get("assets", []):
        name = str(asset.get("name", "")).lower()
        url = asset.get("browser_download_url")
        if name.endswith(".zip") and url:
            return str(url)

    zipball_url = release.get("zipball_url")
    if zipball_url:
        return str(zipball_url)

    raise ValueError("Latest GitHub release does not provide a zip asset or zipball")


def _fetch_latest_release() -> dict:
    release = _request_json(LATEST_RELEASE_API)
    tag_name = release.get("tag_name") or release.get("name")
    if not tag_name:
        raise ValueError("Latest GitHub release does not include a version tag")

    latest_version = _parse_version(tag_name)
    return {
        "tag_name": str(tag_name),
        "latest_version": latest_version,
        "html_url": str(release.get("html_url", "")),
        "download_url": _release_download_url(release),
    }


def _update_scene_state(scene, release: dict, available: bool, status: str) -> None:
    scene.idvmi_update_latest_version = _format_version(release["latest_version"])
    scene.idvmi_update_latest_url = release["html_url"]
    scene.idvmi_update_download_url = release["download_url"]
    scene.idvmi_update_available = available
    scene.idvmi_update_status = status


def _zip_source_root(extract_root: Path) -> Path:
    if (extract_root / "__init__.py").is_file():
        return extract_root

    candidates = [
        path
        for path in extract_root.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]

    for path in extract_root.rglob("__init__.py"):
        parent = path.parent
        if parent.name.lower() == "idvmi-tools":
            return parent

    raise ValueError("Downloaded zip does not look like an IDVMI-Tools addon package")


def _safe_extract_zip(archive_path: Path, extract_root: Path) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    resolved_root = extract_root.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (extract_root / member.filename).resolve()
            try:
                target.relative_to(resolved_root)
            except ValueError:
                raise ValueError(f"Unsafe path in update zip: {member.filename}")
        archive.extractall(extract_root)


def _should_copy(path: Path) -> bool:
    if any(part in EXCLUDED_UPDATE_PARTS for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_UPDATE_SUFFIXES:
        return False
    if path.name in EXCLUDED_UPDATE_NAMES:
        return False
    return True


def _copy_update_tree(source_root: Path, target_root: Path) -> int:
    copied = 0
    for source_path in source_root.rglob("*"):
        relative = source_path.relative_to(source_root)
        if not _should_copy(relative):
            continue

        target_path = target_root / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied += 1
    return copied


def check_for_update(scene) -> dict:
    release = _fetch_latest_release()
    current_version = _current_version()
    latest_version = release["latest_version"]
    available = latest_version > current_version
    status = (
        f"Update available: {_format_version(latest_version)}"
        if available
        else f"Already up to date: {_format_version(current_version)}"
    )
    _update_scene_state(scene, release, available, status)
    return release


def install_latest_release(scene) -> tuple[str, int]:
    release = _fetch_latest_release()
    current_version = _current_version()
    latest_version = release["latest_version"]
    if latest_version <= current_version:
        _update_scene_state(
            scene,
            release,
            False,
            f"Already up to date: {_format_version(current_version)}",
        )
        return (_format_version(latest_version), 0)

    with tempfile.TemporaryDirectory(prefix="idvmi_update_") as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / "release.zip"
        extract_root = temp_root / "extract"
        _download_file(release["download_url"], archive_path)
        _safe_extract_zip(archive_path, extract_root)

        source_root = _zip_source_root(extract_root)
        copied = _copy_update_tree(source_root, _addon_root())

    _update_scene_state(
        scene,
        release,
        False,
        f"Installed {_format_version(latest_version)}. Restart Blender to load it.",
    )
    return (_format_version(latest_version), copied)


class IDVMI_OT_Check_Update(bpy.types.Operator):
    bl_idname = "idvmi.check_update"
    bl_label = "Check for Updates"

    def execute(self, context):
        try:
            release = check_for_update(context.scene)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            context.scene.idvmi_update_status = f"Update check failed: {exc}"
            self.report({"ERROR"}, context.scene.idvmi_update_status)
            return {"CANCELLED"}

        current = _format_version(_current_version())
        latest = _format_version(release["latest_version"])
        if context.scene.idvmi_update_available:
            self.report({"INFO"}, f"Update available: {current} -> {latest}")
        else:
            self.report({"INFO"}, f"Already up to date: {current}")
        return {"FINISHED"}


class IDVMI_OT_Install_Update(bpy.types.Operator):
    bl_idname = "idvmi.install_update"
    bl_label = "Install Latest Release"

    def execute(self, context):
        try:
            version, copied = install_latest_release(context.scene)
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            context.scene.idvmi_update_status = f"Update install failed: {exc}"
            self.report({"ERROR"}, context.scene.idvmi_update_status)
            return {"CANCELLED"}

        if copied == 0:
            self.report({"INFO"}, f"Already up to date: {version}")
        else:
            self.report({"INFO"}, f"Installed {version}. Restart Blender to load it.")
        return {"FINISHED"}


def _draw_update_panel(layout, scene) -> None:
    layout.label(text=f"Current: {_format_version(_current_version())}")
    if scene.idvmi_update_latest_version:
        layout.label(text=f"Latest: {scene.idvmi_update_latest_version}")
    layout.operator("idvmi.check_update", icon="FILE_REFRESH")
    row = layout.row()
    row.enabled = scene.idvmi_update_available
    row.operator("idvmi.install_update", icon="IMPORT")
    if scene.idvmi_update_status:
        layout.label(text=scene.idvmi_update_status)


class IDVMI_Update_tools(bpy.types.Panel):
    bl_label = "IDVMI Updates"
    bl_idname = "idvmi_updates"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Neox"

    def draw(self, context):
        _draw_update_panel(self.layout, context.scene)


class IDVMI_Update_tools_Migoto(bpy.types.Panel):
    bl_label = "IDVMI Updates"
    bl_idname = "idvmi_updates_migoto"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Migoto"

    def draw(self, context):
        _draw_update_panel(self.layout, context.scene)

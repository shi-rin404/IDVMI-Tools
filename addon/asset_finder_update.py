from __future__ import annotations

import hashlib
import io
import importlib
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import bpy


GITHUB_REPO = "shi-rin404/IDVMI-Tools"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "IDVMI-Asset-Finder-Updater"
SUPPORTED_API_VERSION = 1
ASSET_PREFIX = "asset-finder-api-"
LEGACY_ASSET_PREFIXES = ("idvmi-api-", "idvmi-asset-finder-")

_UPDATE_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_UPDATE_RUNNING = False
_AUTO_UPDATE_STARTED = False
_PENDING_STATE: dict[str, str] | None = None


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    manifest_nested_path: str = ""


@dataclass(frozen=True)
class AssetFinderManifest:
    version: tuple[int, ...]
    api_version: int
    min_addon_version: tuple[int, ...]
    sha256: str
    archive_name: str
    source_commit: str
    notes: str


def _addon_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _asset_lookup_root() -> Path:
    return _addon_root() / "neox_tools" / "asset_lookup"


def _asset_lookup_backup_root() -> Path:
    return _addon_root() / "neox_tools" / "asset_lookup.backup"


def _root_package_name() -> str:
    return __package__.split(".", 1)[0]


def _request_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _request_json(url: str) -> dict:
    return json.loads(_request_bytes(url, timeout=20).decode("utf-8"))


def _asset_bytes(asset: ReleaseAsset, timeout: int = 120) -> bytes:
    return _request_bytes(asset.url, timeout)


def _zip_payload_sha256(archive_path: Path, manifest_name: str = "") -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and name != manifest_name
            and "__pycache__" not in Path(name).parts
            and Path(name).suffix not in {".pyc", ".pyo"}
        )
        for name in names:
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(archive.read(name))
            digest.update(b"\0")
    return digest.hexdigest()


def _parse_version(value) -> tuple[int, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(int(part) for part in value)

    text = str(value).strip()
    if text.startswith(("v", "V")):
        text = text[1:]

    parts: list[int] = []
    for part in text.replace("-", ".").split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts or [0])


def _format_version(version: tuple[int, ...]) -> str:
    return "v" + ".".join(str(part) for part in version)


def _current_addon_version() -> tuple[int, ...]:
    root_module = sys.modules.get(_root_package_name())
    bl_info = getattr(root_module, "bl_info", {})
    return _parse_version(bl_info.get("version", (0, 0, 0)))


def _current_asset_finder_version() -> tuple[int, ...]:
    try:
        module = importlib.import_module(f"{_root_package_name()}.neox_tools.asset_lookup")
        return _parse_version(getattr(module, "__version__", "0.0.0"))
    except Exception:
        return (0, 0, 0)


def _current_asset_finder_api_version() -> int:
    try:
        module = importlib.import_module(f"{_root_package_name()}.neox_tools.asset_lookup")
        return int(getattr(module, "__api_version__", 0))
    except Exception:
        return 0


def _release_assets(release: dict) -> list[ReleaseAsset]:
    assets: list[ReleaseAsset] = []
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if name and url:
            assets.append(ReleaseAsset(name=name, url=url))
    return assets


def _accepted_prefixes() -> tuple[str, ...]:
    return (ASSET_PREFIX, *LEGACY_ASSET_PREFIXES)


def _select_manifest_asset(assets: list[ReleaseAsset]) -> ReleaseAsset:
    candidates = [
        asset
        for asset in assets
        if asset.name.lower().startswith(_accepted_prefixes())
        and asset.name.lower().endswith(".json")
    ]
    if not candidates:
        raise ValueError("Latest release does not include an IDVMI asset finder manifest")
    return sorted(candidates, key=lambda item: item.name)[-1]


def _select_archive_asset(assets: list[ReleaseAsset], manifest: AssetFinderManifest) -> ReleaseAsset:
    by_name = {asset.name: asset for asset in assets}
    if manifest.archive_name in by_name:
        return by_name[manifest.archive_name]

    candidates = [
        asset
        for asset in assets
        if asset.name.lower().startswith(_accepted_prefixes())
        and asset.name.lower().endswith(".zip")
    ]
    if not candidates:
        raise ValueError("Latest release does not include an IDVMI asset finder zip")
    return sorted(candidates, key=lambda item: item.name)[-1]


def _select_embedded_archive_asset(assets: list[ReleaseAsset]) -> ReleaseAsset | None:
    candidates = [
        asset
        for asset in assets
        if asset.name.lower().startswith(ASSET_PREFIX) and asset.name.lower().endswith(".zip")
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.name)[-1]


def _embedded_manifest_member(archive: zipfile.ZipFile, archive_name: str) -> str | None:
    expected = f"{Path(archive_name).stem}.json"
    names = [
        name
        for name in archive.namelist()
        if not name.endswith("/")
        and Path(name).name.lower().startswith(_accepted_prefixes())
        and Path(name).name.lower().endswith(".json")
    ]
    for name in names:
        if Path(name).name == expected:
            return name
    return sorted(names)[-1] if names else None


def _manifest_from_archive_bytes(data: bytes, archive_name: str) -> tuple[AssetFinderManifest, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest_member = _embedded_manifest_member(archive, archive_name)
        if manifest_member is None:
            raise ValueError("IDVMI API zip does not include an embedded manifest")
        return _parse_manifest(archive.read(manifest_member)), manifest_member


def _parse_manifest(data: bytes) -> AssetFinderManifest:
    raw = json.loads(data.decode("utf-8"))
    required = ("version", "api_version", "min_addon_version", "sha256", "archive_name")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"Asset finder manifest is missing: {', '.join(missing)}")

    return AssetFinderManifest(
        version=_parse_version(raw["version"]),
        api_version=int(raw["api_version"]),
        min_addon_version=_parse_version(raw["min_addon_version"]),
        sha256=str(raw["sha256"]).strip().lower(),
        archive_name=str(raw["archive_name"]).strip(),
        source_commit=str(raw.get("source_commit", "")).strip(),
        notes=str(raw.get("notes", "")).strip(),
    )


def _fetch_latest_asset_finder_release() -> tuple[dict, AssetFinderManifest, ReleaseAsset]:
    release = _request_json(LATEST_RELEASE_API)
    assets = _release_assets(release)
    archive_asset = _select_embedded_archive_asset(assets)
    if archive_asset is not None:
        try:
            manifest, manifest_member = _manifest_from_archive_bytes(
                _asset_bytes(archive_asset),
                archive_asset.name,
            )
            return release, manifest, ReleaseAsset(
                name=archive_asset.name,
                url=archive_asset.url,
                manifest_nested_path=manifest_member,
            )
        except Exception:
            pass

    manifest_asset = _select_manifest_asset(assets)
    manifest = _parse_manifest(_request_bytes(manifest_asset.url, timeout=20))
    archive_asset = _select_archive_asset(assets, manifest)
    return release, manifest, archive_asset


def _apply_scene_state(scene, state: dict[str, str]) -> None:
    scene.idvmi_asset_finder_version = state["version"]
    scene.idvmi_asset_finder_api_version = state["api_version"]
    if state.get("latest"):
        scene.idvmi_asset_finder_latest_version = state["latest"]
    scene.idvmi_asset_finder_status = state["status"]


def _update_scene_state(scene, *, latest: tuple[int, ...] | None = None, status: str) -> None:
    global _PENDING_STATE
    state = {
        "version": _format_version(_current_asset_finder_version()),
        "api_version": str(_current_asset_finder_api_version()),
        "latest": _format_version(latest) if latest is not None else "",
        "status": status,
    }
    if threading.current_thread() is threading.main_thread():
        _apply_scene_state(scene, state)
        return

    with _STATUS_LOCK:
        _PENDING_STATE = state


def _flush_pending_scene_state() -> None:
    global _PENDING_STATE
    with _STATUS_LOCK:
        state = _PENDING_STATE
        _PENDING_STATE = None
    if state is not None and bpy.context.scene is not None:
        try:
            _apply_scene_state(bpy.context.scene, state)
        except AttributeError:
            pass


def _safe_extract_zip(archive_path: Path, extract_root: Path) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    resolved_root = extract_root.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (extract_root / member.filename).resolve()
            try:
                target.relative_to(resolved_root)
            except ValueError:
                raise ValueError(f"Unsafe path in asset finder zip: {member.filename}")
        archive.extractall(extract_root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_asset_lookup_source(extract_root: Path) -> Path:
    direct = extract_root / "asset_lookup"
    if (direct / "assets.py").is_file():
        return direct

    candidates = [path for path in extract_root.rglob("asset_lookup") if (path / "assets.py").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return sorted(candidates, key=lambda item: len(item.parts))[0]
    raise ValueError("Asset finder zip does not contain asset_lookup/assets.py")


def _smoke_test_asset_lookup(source_root: Path, manifest: AssetFinderManifest) -> None:
    init_path = source_root / "__init__.py"
    assets_path = source_root / "assets.py"
    if not init_path.is_file() or not assets_path.is_file():
        raise ValueError("Asset finder package is missing __init__.py or assets.py")

    smoke_package = "_idvmi_asset_lookup_smoke"
    for name in list(sys.modules):
        if name == smoke_package or name.startswith(smoke_package + "."):
            sys.modules.pop(name, None)

    spec = importlib.util.spec_from_file_location(
        smoke_package,
        init_path,
        submodule_search_locations=[str(source_root)],
    )
    if spec is None or spec.loader is None:
        raise ValueError("Could not create asset finder smoke test import spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules[smoke_package] = module
    try:
        spec.loader.exec_module(module)
        if int(getattr(module, "__api_version__", 0)) != manifest.api_version:
            raise ValueError("Asset finder package API version does not match manifest")
        if _parse_version(getattr(module, "__version__", "0.0.0")) != manifest.version:
            raise ValueError("Asset finder package version does not match manifest")

        assets_module = importlib.import_module(f"{smoke_package}.assets")
        asset_index = getattr(assets_module, "AssetIndex", None)
        extracted_asset = getattr(assets_module, "ExtractedAsset", None)
        if asset_index is None or extracted_asset is None:
            raise ValueError("Asset finder package is missing AssetIndex or ExtractedAsset")

        for method_name in ("parse", "extract", "exists"):
            if not callable(getattr(asset_index, method_name, None)):
                raise ValueError(f"AssetIndex is missing required method: {method_name}")

        annotations = getattr(extracted_asset, "__annotations__", {})
        for field_name in ("request", "data", "source_archive"):
            if field_name not in annotations:
                raise ValueError(f"ExtractedAsset is missing required field: {field_name}")
    finally:
        for name in list(sys.modules):
            if name == smoke_package or name.startswith(smoke_package + "."):
                sys.modules.pop(name, None)


def _clear_loaded_asset_lookup_modules() -> None:
    root = f"{_root_package_name()}.neox_tools.asset_lookup"
    for name in list(sys.modules):
        if name == root or name.startswith(root + "."):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _copy_clean_tree(source_root: Path, target_root: Path) -> None:
    if target_root.exists():
        shutil.rmtree(target_root)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(source_root, target_root, ignore=ignore)


def _install_asset_lookup(source_root: Path) -> None:
    target_root = _asset_lookup_root()
    backup_root = _asset_lookup_backup_root()
    if backup_root.exists():
        shutil.rmtree(backup_root)

    if target_root.exists():
        shutil.copytree(target_root, backup_root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))

    try:
        _copy_clean_tree(source_root, target_root)
        _clear_loaded_asset_lookup_modules()
        importlib.import_module(f"{_root_package_name()}.neox_tools.asset_lookup.assets")
    except Exception:
        if target_root.exists():
            shutil.rmtree(target_root)
        if backup_root.exists():
            shutil.copytree(backup_root, target_root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        _clear_loaded_asset_lookup_modules()
        raise
    finally:
        if backup_root.exists():
            shutil.rmtree(backup_root)


def check_for_asset_finder_update(scene) -> tuple[AssetFinderManifest, bool]:
    _release, manifest, _asset = _fetch_latest_asset_finder_release()
    current = _current_asset_finder_version()
    if manifest.api_version != SUPPORTED_API_VERSION:
        raise ValueError(
            f"Unsupported asset finder API version: {manifest.api_version}; "
            f"expected {SUPPORTED_API_VERSION}"
        )
    if manifest.min_addon_version > _current_addon_version():
        raise ValueError(
            f"Asset finder {_format_version(manifest.version)} requires "
            f"IDVMI-Tools {_format_version(manifest.min_addon_version)} or newer"
        )

    available = manifest.version > current
    status = (
        f"Asset finder update available: {_format_version(current)} -> {_format_version(manifest.version)}"
        if available
        else f"Asset finder already up to date: {_format_version(current)}"
    )
    _update_scene_state(scene, latest=manifest.version, status=status)
    return manifest, available


def install_latest_asset_finder_release(scene) -> tuple[str, bool]:
    _release, manifest, archive_asset = _fetch_latest_asset_finder_release()
    current = _current_asset_finder_version()
    if manifest.version <= current:
        _update_scene_state(
            scene,
            latest=manifest.version,
            status=f"Asset finder already up to date: {_format_version(current)}",
        )
        return _format_version(current), False

    if manifest.api_version != SUPPORTED_API_VERSION:
        raise ValueError(
            f"Unsupported asset finder API version: {manifest.api_version}; "
            f"expected {SUPPORTED_API_VERSION}"
        )
    if manifest.min_addon_version > _current_addon_version():
        raise ValueError(
            f"Asset finder {_format_version(manifest.version)} requires "
            f"IDVMI-Tools {_format_version(manifest.min_addon_version)} or newer"
        )

    with tempfile.TemporaryDirectory(prefix="idvmi_asset_finder_update_") as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / manifest.archive_name
        extract_root = temp_root / "extract"
        archive_path.write_bytes(_asset_bytes(archive_asset))
        actual_sha256 = (
            _zip_payload_sha256(archive_path, archive_asset.manifest_nested_path)
            if archive_asset.manifest_nested_path
            else _sha256(archive_path)
        )
        if actual_sha256 != manifest.sha256:
            raise ValueError(
                f"Asset finder checksum mismatch: expected {manifest.sha256}, got {actual_sha256}"
            )
        _safe_extract_zip(archive_path, extract_root)
        source_root = _find_asset_lookup_source(extract_root)
        _smoke_test_asset_lookup(source_root, manifest)
        _install_asset_lookup(source_root)

    _update_scene_state(
        scene,
        latest=manifest.version,
        status=f"Installed asset finder {_format_version(manifest.version)}",
    )
    return _format_version(manifest.version), True


def auto_update_asset_finder(scene) -> None:
    global _UPDATE_RUNNING
    with _UPDATE_LOCK:
        if _UPDATE_RUNNING:
            return
        _UPDATE_RUNNING = True

    try:
        try:
            manifest, available = check_for_asset_finder_update(scene)
            if available:
                install_latest_asset_finder_release(scene)
            else:
                _update_scene_state(
                    scene,
                    latest=manifest.version,
                    status=f"Asset finder already up to date: {_format_version(_current_asset_finder_version())}",
                )
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            _update_scene_state(scene, status=f"Asset finder auto update failed: {exc}")
    finally:
        with _UPDATE_LOCK:
            _UPDATE_RUNNING = False


def is_update_running() -> bool:
    with _UPDATE_LOCK:
        return _UPDATE_RUNNING


def schedule_auto_update() -> None:
    global _AUTO_UPDATE_STARTED
    if _AUTO_UPDATE_STARTED:
        return
    _AUTO_UPDATE_STARTED = True

    def flush_status():
        _flush_pending_scene_state()
        return 1.0 if is_update_running() else None

    def run_once():
        scene = bpy.context.scene
        if scene is None:
            return 5.0
        thread = threading.Thread(
            target=auto_update_asset_finder,
            args=(scene,),
            name="IDVMIAssetFinderUpdate",
            daemon=True,
        )
        thread.start()
        bpy.app.timers.register(flush_status, first_interval=1.0)
        return None

    bpy.app.timers.register(run_once, first_interval=3.0)


def reset_auto_update_state() -> None:
    global _AUTO_UPDATE_STARTED
    _AUTO_UPDATE_STARTED = False


class IDVMI_OT_Check_Asset_Finder_Update(bpy.types.Operator):
    bl_idname = "idvmi.check_asset_finder_update"
    bl_label = "Check Asset Finder Update"

    def execute(self, context):
        try:
            _manifest, available = check_for_asset_finder_update(context.scene)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            _update_scene_state(context.scene, status=f"Asset finder update check failed: {exc}")
            self.report({"ERROR"}, context.scene.idvmi_asset_finder_status)
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            context.scene.idvmi_asset_finder_status if available else "Asset finder already up to date",
        )
        return {"FINISHED"}


class IDVMI_OT_Install_Asset_Finder_Update(bpy.types.Operator):
    bl_idname = "idvmi.install_asset_finder_update"
    bl_label = "Install Asset Finder Update"

    def execute(self, context):
        try:
            version, installed = install_latest_asset_finder_release(context.scene)
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            _update_scene_state(context.scene, status=f"Asset finder update install failed: {exc}")
            self.report({"ERROR"}, context.scene.idvmi_asset_finder_status)
            return {"CANCELLED"}

        if installed:
            self.report({"INFO"}, f"Installed asset finder {version}")
        else:
            self.report({"INFO"}, f"Asset finder already up to date: {version}")
        return {"FINISHED"}


def draw_asset_finder_update_panel(layout, scene) -> None:
    box = layout.box()
    box.label(text="Asset Finder")
    box.label(text=f"Installed: {scene.idvmi_asset_finder_version or _format_version(_current_asset_finder_version())}")
    if scene.idvmi_asset_finder_latest_version:
        box.label(text=f"Latest: {scene.idvmi_asset_finder_latest_version}")
    row = box.row()
    row.operator("idvmi.check_asset_finder_update", icon="FILE_REFRESH")
    row.operator("idvmi.install_asset_finder_update", icon="IMPORT")
    if scene.idvmi_asset_finder_status:
        box.label(text=scene.idvmi_asset_finder_status)

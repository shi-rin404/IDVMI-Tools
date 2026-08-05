from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
import urllib.request
from pathlib import Path
from types import ModuleType

from .state import load_applied_hotfixes, mark_hotfix_applied

GITHUB_REPO = "shi-rin404/IDVMI-Tools"
GITHUB_BRANCH = "master"
RAW_GITHUB_ROOT = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"
MANIFEST_RELATIVE_PATH = Path("addon") / "version_hotfixes" / "manifest.json"
FIXES_RELATIVE_DIR = Path("addon") / "version_hotfixes" / "fixes"
USER_AGENT = "IDVMI-Tools-Hotfixes"


def parse_version(value) -> tuple[int, ...]:
    text = str(value).strip()
    if text.startswith(("v", "V")):
        text = text[1:]

    parts = []
    for part in text.replace("-", ".").replace("_", ".").split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts or [0])


def format_version(version: tuple[int, ...]) -> str:
    return "v" + ".".join(str(part) for part in version)


def addon_root() -> Path:
    return Path(__file__).resolve().parents[2]


def manifest_path(root: Path | None = None) -> Path:
    return (root or addon_root()) / MANIFEST_RELATIVE_PATH


def _current_version() -> tuple[int, ...]:
    try:
        from ... import bl_info
    except ImportError:
        return (0,)
    return parse_version(bl_info.get("version", (0,)))


def load_manifest(path: Path | None = None) -> list[dict]:
    manifest_file = path or manifest_path()
    with manifest_file.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    if not isinstance(manifest, list):
        raise ValueError(f"Invalid hotfix manifest: {manifest_file}")
    return manifest


def _entry_version(entry: dict) -> tuple[int, ...]:
    try:
        return parse_version(entry["version"])
    except KeyError as exc:
        raise ValueError("Hotfix manifest entry is missing 'version'") from exc


def _entry_id(entry: dict) -> str:
    try:
        return str(entry["id"])
    except KeyError as exc:
        raise ValueError("Hotfix manifest entry is missing 'id'") from exc


def _entry_module(entry: dict) -> str:
    try:
        return str(entry["module"])
    except KeyError as exc:
        raise ValueError("Hotfix manifest entry is missing 'module'") from exc


def hotfixes_for_version(manifest: list[dict], version: tuple[int, ...]) -> list[dict]:
    return [entry for entry in manifest if _entry_version(entry) == version]


def hotfixes_in_range(
    manifest: list[dict],
    base_version: tuple[int, ...],
    target_version: tuple[int, ...],
) -> list[dict]:
    return [
        entry
        for entry in manifest
        if base_version < _entry_version(entry) <= target_version
    ]


def _import_local_hotfix(module_name: str) -> ModuleType:
    return importlib.import_module(f".fixes.{module_name}", package=__name__)


def _run_hotfix_module(module: ModuleType, root: Path) -> None:
    apply_hotfix = getattr(module, "apply_hotfix", None)
    if apply_hotfix is None:
        raise ValueError(f"Hotfix module does not define apply_hotfix(): {module.__name__}")
    apply_hotfix(root)


def _import_hotfix_from_path(module_path: Path, module_name_prefix: str) -> ModuleType:
    module_name = f"{module_name_prefix}_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load hotfix module: {module_path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_entries(entries: list[dict], *, root: Path, import_module) -> int:
    applied = load_applied_hotfixes(root)
    count = 0
    for entry in entries:
        hotfix_id = _entry_id(entry)
        if hotfix_id in applied:
            continue

        module = import_module(_entry_module(entry))
        _run_hotfix_module(module, root)
        mark_hotfix_applied(root, hotfix_id)
        applied.add(hotfix_id)
        count += 1
    return count


def run_local_version_hotfixes(version: tuple[int, ...] | None = None) -> int:
    root = addon_root()
    target_version = version or _current_version()
    manifest = load_manifest()
    fixes_dir = Path(__file__).resolve().parent / "fixes"
    entries = [
        entry
        for entry in hotfixes_for_version(manifest, target_version)
        if (fixes_dir / f"{_entry_module(entry)}.py").is_file()
    ]
    return _run_entries(entries, root=root, import_module=_import_local_hotfix)


def run_bundled_hotfixes(
    bundle_root: Path,
    base_version: tuple[int, ...],
    target_version: tuple[int, ...],
) -> int:
    root = addon_root()
    manifest = load_manifest(bundle_root / MANIFEST_RELATIVE_PATH)
    entries = [
        entry
        for entry in hotfixes_in_range(manifest, base_version, target_version)
        if (bundle_root / FIXES_RELATIVE_DIR / f"{_entry_module(entry)}.py").is_file()
    ]

    def import_bundled(module_name: str) -> ModuleType:
        module_path = bundle_root / FIXES_RELATIVE_DIR / f"{module_name}.py"
        return _import_hotfix_from_path(module_path, "idvmi_bundled_hotfix")

    return _run_entries(entries, root=root, import_module=import_bundled)


def _url_for_path(relative_path: Path) -> str:
    return f"{RAW_GITHUB_ROOT}/{relative_path.as_posix()}"


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_remote_manifest() -> list[dict]:
    payload = _request_bytes(_url_for_path(MANIFEST_RELATIVE_PATH))
    manifest = json.loads(payload.decode("utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("Remote hotfix manifest is invalid")
    return manifest


def _remote_hotfix_module_path(entry: dict, temp_root: Path) -> Path:
    module_path = FIXES_RELATIVE_DIR / f"{_entry_module(entry)}.py"
    target_path = temp_root / module_path.name
    target_path.write_bytes(_request_bytes(_url_for_path(module_path)))
    return target_path


def _import_remote_hotfix(module_path: Path) -> ModuleType:
    return _import_hotfix_from_path(module_path, "idvmi_remote_hotfix")


def run_missing_remote_hotfixes(
    base_version: tuple[int, ...],
    target_version: tuple[int, ...],
) -> int:
    root = addon_root()
    manifest = fetch_remote_manifest()
    entries = hotfixes_in_range(manifest, base_version, target_version)
    applied = load_applied_hotfixes(root)
    pending = [entry for entry in entries if _entry_id(entry) not in applied]
    if not pending:
        return 0

    with tempfile.TemporaryDirectory(prefix="idvmi_hotfixes_") as temp_dir:
        temp_root = Path(temp_dir)

        def import_remote(module_name: str) -> ModuleType:
            entry = next(item for item in pending if _entry_module(item) == module_name)
            return _import_remote_hotfix(_remote_hotfix_module_path(entry, temp_root))

        return _run_entries(pending, root=root, import_module=import_remote)

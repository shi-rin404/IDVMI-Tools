from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper
import bpy


GAME_DIR_RE = re.compile(r"^[A-Z]:\\Loading Bay Games\\Identity V\\?$", re.IGNORECASE)
LOCAL_PATHES_NAME = "local_pathes.json"
DEFAULT_LOCAL_PATHES = {"game_root": ""}
_GAME_EXECUTABLE_PROMPT_REQUESTED = False
_PENDING_GAME_ROOT_OPERATOR: tuple[str, dict] | None = None


class GameRootNotConfigured(FileNotFoundError):
    pass


def addon_root() -> Path:
    return Path(__file__).resolve().parents[2]


def defaults_local_pathes_path() -> Path:
    return addon_root() / "defaults" / LOCAL_PATHES_NAME


def user_local_pathes_path() -> Path:
    return addon_root() / "user" / LOCAL_PATHES_NAME


def ensure_user_local_pathes() -> Path:
    defaults_path = defaults_local_pathes_path()
    user_path = user_local_pathes_path()
    user_path.parent.mkdir(parents=True, exist_ok=True)

    if not defaults_path.is_file():
        defaults_path.parent.mkdir(parents=True, exist_ok=True)
        defaults_path.write_text(
            json.dumps(DEFAULT_LOCAL_PATHES, indent=4) + "\n",
            encoding="utf-8",
        )

    if not user_path.is_file():
        shutil.copyfile(defaults_path, user_path)

    return user_path


def load_local_pathes() -> dict:
    path = ensure_user_local_pathes()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = dict(DEFAULT_LOCAL_PATHES)
    if not isinstance(data, dict):
        data = dict(DEFAULT_LOCAL_PATHES)
    data.setdefault("game_root", "")
    return data


def save_local_pathes(data: dict) -> None:
    path = ensure_user_local_pathes()
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")


def save_game_root(game_root: str | Path) -> Path:
    root = normalize_game_root(game_root)
    validate_game_root(root)
    data = load_local_pathes()
    data["game_root"] = str(root)
    save_local_pathes(data)
    return root


def normalize_game_root(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.name.lower() == "dwrg.exe":
        candidate = candidate.parent
    return candidate.resolve(strict=False)


def validate_game_root(path: str | Path) -> Path:
    root = normalize_game_root(path)
    executable = root / "dwrg.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Identity V executable was not found: {executable}")
    if not (root / "res").is_dir():
        raise FileNotFoundError(f"Identity V res folder was not found: {root / 'res'}")
    if not (root / "Documents" / "res").is_dir():
        raise FileNotFoundError(
            f"Identity V Documents/res folder was not found: {root / 'Documents' / 'res'}"
        )
    return root


def _configured_game_root() -> Path | None:
    configured = os.environ.get("IDVMI_GAME_ROOT", "").strip()
    if not configured:
        return None
    return validate_game_root(configured)


def _saved_game_root() -> Path | None:
    saved = str(load_local_pathes().get("game_root", "")).strip()
    if not saved:
        return None
    try:
        return validate_game_root(saved)
    except FileNotFoundError:
        return None


def _iter_drive_roots():
    for drive_ord in range(ord("A"), ord("Z") + 1):
        yield Path(f"{chr(drive_ord)}:\\")


def _find_standard_install_root() -> Path | None:
    for drive_root in _iter_drive_roots():
        game_dir = drive_root / "Loading Bay Games" / "Identity V"
        if game_dir.is_dir() and GAME_DIR_RE.match(str(game_dir)):
            try:
                return validate_game_root(game_dir)
            except FileNotFoundError:
                continue
    return None


def get_game_root(*, prompt_on_missing: bool = False) -> Path:
    last_error: FileNotFoundError | None = None
    for resolver in (_configured_game_root, _saved_game_root, _find_standard_install_root):
        try:
            root = resolver()
        except FileNotFoundError as exc:
            last_error = exc
            continue
        if root is not None:
            if resolver is _find_standard_install_root:
                save_game_root(root)
            return root

    if prompt_on_missing:
        request_game_executable()
    raise GameRootNotConfigured("Identity V game root could not be detected") from last_error


def ensure_game_root_or_prompt(
    operator,
    context,
    *,
    retry_operator: str | None = None,
    retry_properties: dict | None = None,
) -> bool:
    try:
        get_game_root(prompt_on_missing=False)
        return True
    except GameRootNotConfigured:
        request_game_executable(
            retry_operator=retry_operator or getattr(operator, "bl_idname", ""),
            retry_properties=retry_properties,
        )
        operator.report(
            {"INFO"},
            "Identity V game root is missing. Select dwrg.exe to continue this operation.",
        )
        return False


def get_documents_mod_directory() -> str:
    try:
        mod_dir = get_game_root(prompt_on_missing=False) / "Documents" / "res" / "mod"
    except GameRootNotConfigured:
        return ""

    try:
        mod_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    return str(mod_dir)


def request_game_executable(
    *,
    retry_operator: str | None = None,
    retry_properties: dict | None = None,
) -> None:
    global _GAME_EXECUTABLE_PROMPT_REQUESTED
    global _PENDING_GAME_ROOT_OPERATOR
    if retry_operator:
        _PENDING_GAME_ROOT_OPERATOR = (retry_operator, dict(retry_properties or {}))
    if _GAME_EXECUTABLE_PROMPT_REQUESTED:
        return
    try:
        bpy.ops.idvmi.select_game_executable("INVOKE_DEFAULT")
        _GAME_EXECUTABLE_PROMPT_REQUESTED = True
    except Exception:
        return


def _run_pending_game_root_operator():
    global _PENDING_GAME_ROOT_OPERATOR
    pending = _PENDING_GAME_ROOT_OPERATOR
    _PENDING_GAME_ROOT_OPERATOR = None
    if pending is None:
        return None

    operator_id, properties = pending
    module_name, _, operator_name = operator_id.partition(".")
    if not module_name or not operator_name:
        return None

    try:
        operator_module = getattr(bpy.ops, module_name)
        operator_call = getattr(operator_module, operator_name)
        try:
            operator_call("INVOKE_DEFAULT", **properties)
        except TypeError:
            operator_call("EXEC_DEFAULT", **properties)
    except Exception as exc:
        print(f"[IDVMI] Failed to resume operator after game root selection: {operator_id}: {exc}")
    return None


class IDVMI_OT_Select_Game_Executable(bpy.types.Operator, ImportHelper):
    bl_idname = "idvmi.select_game_executable"
    bl_label = "Select Identity V Executable"
    bl_options = {"REGISTER"}

    filename_ext = ".exe"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(
        default="*.exe",
        options={"HIDDEN"},
        maxlen=255,
    )

    def invoke(self, context, event):
        self.filter_glob = "*.exe"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        global _GAME_EXECUTABLE_PROMPT_REQUESTED
        global _PENDING_GAME_ROOT_OPERATOR

        executable_path = Path(bpy.path.abspath(self.filepath))
        if executable_path.name.lower() != "dwrg.exe":
            self.report({"ERROR"}, "Please select dwrg.exe")
            _GAME_EXECUTABLE_PROMPT_REQUESTED = False
            _PENDING_GAME_ROOT_OPERATOR = None
            return {"CANCELLED"}

        try:
            root = save_game_root(executable_path)
        except Exception as exc:
            self.report({"ERROR"}, f"Invalid Identity V executable: {exc}")
            _GAME_EXECUTABLE_PROMPT_REQUESTED = False
            _PENDING_GAME_ROOT_OPERATOR = None
            return {"CANCELLED"}

        _GAME_EXECUTABLE_PROMPT_REQUESTED = False
        self.report({"INFO"}, f"Identity V game root saved: {root}")
        if _PENDING_GAME_ROOT_OPERATOR is not None:
            bpy.app.timers.register(_run_pending_game_root_operator, first_interval=0.1)
        return {"FINISHED"}

    def cancel(self, context):
        global _GAME_EXECUTABLE_PROMPT_REQUESTED
        global _PENDING_GAME_ROOT_OPERATOR
        _GAME_EXECUTABLE_PROMPT_REQUESTED = False
        _PENDING_GAME_ROOT_OPERATOR = None

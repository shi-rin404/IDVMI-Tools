from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .. import remote_import

GIM_PATH_PROPERTY = "NeoX:GimPath"
MTL_INDEX_PROPERTY = "NeoX:MtlIdx"

_BLENDER_DUPLICATE_SUFFIX_RE = re.compile(r"\.\d{3}$")
_MESH_NAME_RE = re.compile(r"^(?P<name>.+)_(?P<index>\d+)$")
_GIM_PATH_CACHE: dict[str, str | None] = {}


def _log(log, message: str) -> None:
    if log is None:
        return
    log.write(f"{message}\n")
    log.flush()


def _addon_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_asset_path(value: str) -> str:
    return str(value).strip().replace("\\", "/").strip("/")


def _parse_mesh_object_name(mesh_obj) -> tuple[str, int] | None:
    clean_name = _BLENDER_DUPLICATE_SUFFIX_RE.sub("", mesh_obj.name)
    match = _MESH_NAME_RE.fullmatch(clean_name)
    if match is None:
        return None
    return match.group("name"), int(match.group("index"))


def _load_character_list() -> dict:
    path = _addon_root() / "neox_tools" / "dataset" / "character_list.json"
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _first_three_name_parts(mesh_name: str) -> str:
    parts = mesh_name.split("_")
    return "_".join(parts[:3]) if len(parts) >= 3 else mesh_name


def _survivor_character_folders(gender: str, code: str) -> tuple[str, ...]:
    return (
        f"dm65_survivor_{code}",
        f"dm65_survivor_{gender}_{code}",
        f"h55_survivor_{code}",
        f"h55_survivor_{gender}_{code}",
    )


def _candidate_gim_paths(mesh_name: str) -> list[str]:
    characters = _load_character_list()
    skin_folder = _first_three_name_parts(mesh_name)
    candidates: list[str] = []

    def add(path: str) -> None:
        normalized = _normalize_asset_path(path)
        if normalized not in candidates:
            candidates.append(normalized)

    for code in characters.get("woman", []):
        for folder in _survivor_character_folders("w", code):
            base = f"chr/player/dm65_survivor_w/{folder}"
            add(f"{base}/{mesh_name}.gim")
            add(f"{base}/separate_dir/{skin_folder}/{mesh_name}.gim")

    for code in characters.get("man", []) + characters.get("men", []) + characters.get("male", []):
        for folder in _survivor_character_folders("m", code):
            base = f"chr/player/dm65_survivor_m/{folder}"
            add(f"{base}/{mesh_name}.gim")
            add(f"{base}/separate_dir/{skin_folder}/{mesh_name}.gim")

    girl_base = "chr/player/dm65_survivor_girl"
    add(f"{girl_base}/{mesh_name}.gim")
    add(f"{girl_base}/separate_dir/{skin_folder}/{mesh_name}.gim")

    puppet_base = "chr/player/dm65_survivor_puppet"
    add(f"{puppet_base}/{mesh_name}.gim")
    add(f"{puppet_base}/{skin_folder}/{mesh_name}.gim")
    add(f"{puppet_base}/separate_dir/{skin_folder}/{mesh_name}.gim")

    for character_name in characters.get("boss", []):
        base = f"chr/boss/{character_name}"
        add(f"{base}/{mesh_name}.gim")
        add(f"{base}/separate_dir/{skin_folder}/{mesh_name}.gim")

    return candidates


def _resolve_legacy_gim_path(asset_index, mesh_name: str) -> str | None:
    if mesh_name in _GIM_PATH_CACHE:
        return _GIM_PATH_CACHE[mesh_name]

    for candidate in _candidate_gim_paths(mesh_name):
        try:
            asset_index.extract(candidate)
        except (FileNotFoundError, LookupError):
            continue
        _GIM_PATH_CACHE[mesh_name] = candidate
        return candidate

    _GIM_PATH_CACHE[mesh_name] = None
    return None


def make_asset_index():
    return remote_import._make_asset_index()


def _gim_path_for_mesh(asset_index, mesh_obj, mesh_name: str) -> str | None:
    custom_path = str(mesh_obj.get(GIM_PATH_PROPERTY, "")).strip()
    if custom_path:
        return _normalize_asset_path(custom_path)
    return _resolve_legacy_gim_path(asset_index, mesh_name)


def _mtl_index_for_mesh(gim_root: ET.Element, mesh_obj, submesh_index: int) -> int:
    if MTL_INDEX_PROPERTY in mesh_obj:
        try:
            return int(mesh_obj[MTL_INDEX_PROPERTY])
        except (TypeError, ValueError):
            pass

    submesh = gim_root.find(f".//SubMesh/Sub{submesh_index}")
    if submesh is None:
        raise ValueError(f"Sub{submesh_index} was not found in source gim")

    mtl_idx = submesh.attrib.get("MtlIdx")
    if mtl_idx is None:
        raise ValueError(f"Sub{submesh_index} does not contain MtlIdx")
    return int(mtl_idx)


def _mtg_path_from_gim(asset_index, gim_root: ET.Element, gim_asset_path: str) -> str:
    mtg_file = gim_root.find(".//MtgFile")
    mtg_path = ""
    if mtg_file is not None:
        mtg_path = mtg_file.attrib.get("MtgPath", "").strip()
    if not mtg_path:
        return remote_import._replace_extension(gim_asset_path, ".mtg")
    return remote_import._resolve_reference(asset_index, mtg_path, gim_asset_path)


def _material_path_from_mtg(mtg_root: ET.Element, mtl_idx: int) -> str:
    material = mtg_root.find(f".//MaterialGroup/Material_{mtl_idx}")
    if material is None:
        raise ValueError(f"Material_{mtl_idx} was not found in material group")

    path = material.attrib.get("Path", "").strip()
    if not path:
        raise ValueError(f"Material_{mtl_idx} does not contain Path")
    return path


def _set_material_name(root: ET.Element, name: str) -> None:
    material = root.find(".//Material")
    if material is not None:
        material.attrib["Name"] = name


def load_original_material_template(
    mesh_obj,
    operator,
    asset_index=None,
    log=None,
) -> ET.Element | None:
    parsed = _parse_mesh_object_name(mesh_obj)
    if parsed is None:
        operator.report({"WARNING"}, f"Could not parse NeoX mesh name for material grab: {mesh_obj.name}")
        _log(log, f"    original material skipped: could not parse mesh name {mesh_obj.name}")
        return None
    else:
        mesh_name, submesh_index = parsed

    if asset_index is None:
        asset_index = make_asset_index()
    gim_asset_path = _gim_path_for_mesh(asset_index, mesh_obj, mesh_name)
    if not gim_asset_path:
        operator.report({"WARNING"}, f"Original gim could not be found for mesh: {mesh_obj.name}")
        _log(log, f"    original material skipped: gim not found for {mesh_obj.name}")
        return None

    _log(log, f"    original material gim: {gim_asset_path}")
    gim_root = remote_import._xml_root_from_bytes(
        asset_index.extract(gim_asset_path).data,
        ".gim",
        gim_asset_path,
    )
    mtl_idx = _mtl_index_for_mesh(gim_root, mesh_obj, submesh_index)
    _log(log, f"    original material mtl index: {mtl_idx}")

    mtg_asset_path = _mtg_path_from_gim(asset_index, gim_root, gim_asset_path)
    _log(log, f"    original material mtg: {mtg_asset_path}")
    mtg_root = remote_import._xml_root_from_bytes(
        asset_index.extract(mtg_asset_path).data,
        ".mtg",
        mtg_asset_path,
    )
    mtl_asset_path = remote_import._resolve_reference(
        asset_index,
        _material_path_from_mtg(mtg_root, mtl_idx),
        mtg_asset_path,
    )
    _log(log, f"    original material mtl: {mtl_asset_path}")
    material_root = remote_import._xml_root_from_bytes(
        asset_index.extract(mtl_asset_path).data,
        ".mtl",
        mtl_asset_path,
    )
    material_root = deepcopy(material_root)
    _set_material_name(material_root, mesh_obj.name)
    _log(log, "    original material template loaded; texture values will be replaced from Blender material nodes.")
    return material_root

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import posixpath
import re
import shutil
import struct
import threading
import time
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from .animation_export_ops import (
    DEFAULT_ACCUMULATION_FLAGS,
    VectorKey,
    build_data_payload,
    build_file_bytes,
    build_head_payload,
    build_name_payload,
    pack_section,
)
from .mod_exporter.xml_converter import convert_handler, io_handler, parse_handler


XXH64_PRIME_1 = 11400714785074694791
XXH64_PRIME_2 = 14029467366897019727
XXH64_PRIME_3 = 1609587929392839161
XXH64_PRIME_4 = 9650029242287828579
XXH64_PRIME_5 = 2870177450012600261
MASK64 = 0xFFFFFFFFFFFFFFFF
DUAL_FORM_PRESET_FORMAT = "idvmi.dual_form_trigger_list"
DUAL_FORM_PRESET_VERSION = 1
DUAL_FORM_PRESET_NONE = "__none__"
DUAL_FORM_WRITE_FLUSH_MAX_FILES = 128
DUAL_FORM_WRITE_FLUSH_MAX_BYTES = 32 * 1024 * 1024
DUAL_FORM_WRITE_WORKERS = 4
DUAL_FORM_BUILD_WORKERS = max(2, min(8, os.cpu_count() or 4))
_DUAL_FORM_THREAD_LOCAL = threading.local()


def _dual_form_worker_asset_index():
    asset_index = getattr(_DUAL_FORM_THREAD_LOCAL, "asset_index", None)
    if asset_index is None:
        from .remote_import import _make_asset_index

        asset_index = _make_asset_index()
        _DUAL_FORM_THREAD_LOCAL.asset_index = asset_index
    return asset_index


def _format_elapsed_min_sec_ms(elapsed_seconds: float) -> str:
    elapsed_ms = max(0, int(round(float(elapsed_seconds) * 1000.0)))
    minutes, remainder_ms = divmod(elapsed_ms, 60_000)
    seconds, milliseconds = divmod(remainder_ms, 1000)
    return f"{minutes:02d}:{seconds:02d}:{milliseconds:03d}"


class DualFormBuildContext:
    def __init__(
        self,
        *,
        max_buffer_files: int = DUAL_FORM_WRITE_FLUSH_MAX_FILES,
        max_buffer_bytes: int = DUAL_FORM_WRITE_FLUSH_MAX_BYTES,
    ) -> None:
        self.max_buffer_files = max_buffer_files
        self.max_buffer_bytes = max_buffer_bytes
        self.text_cache: dict[str, str] = {}
        self.binary_cache: dict[str, bytes] = {}
        self.resolved_reference_cache: dict[tuple[str, str, str], str] = {}
        self.text_cache_hits = 0
        self.text_cache_misses = 0
        self.binary_cache_hits = 0
        self.binary_cache_misses = 0
        self.resolve_cache_hits = 0
        self.resolve_cache_misses = 0
        self.flush_count = 0
        self.queued_write_count = 0
        self._pending_writes: list[tuple[Path, bytes]] = []
        self._pending_bytes = 0
        self._write_executor = ThreadPoolExecutor(
            max_workers=DUAL_FORM_WRITE_WORKERS,
            thread_name_prefix="dual-form-write",
        )
        self._write_futures = []

    def resolve_animation_reference(self, asset_index, base_reference: str, reference: str) -> str:
        key = ("animation", str(base_reference), _normalize_slashes(reference))
        cached = self.resolved_reference_cache.get(key)
        if cached is not None:
            self.resolve_cache_hits += 1
            return cached
        self.resolve_cache_misses += 1
        resolved = _resolve_animation_reference(asset_index, base_reference, reference)
        self.resolved_reference_cache[key] = resolved
        return resolved

    def resolve_dependency_reference(self, asset_index, animation_reference: str, dependency_reference: str) -> str:
        key = ("dependency", str(animation_reference), _normalize_slashes(dependency_reference))
        cached = self.resolved_reference_cache.get(key)
        if cached is not None:
            self.resolve_cache_hits += 1
            return cached
        self.resolve_cache_misses += 1
        resolved = _resolve_dependency_reference(asset_index, animation_reference, dependency_reference)
        self.resolved_reference_cache[key] = resolved
        return resolved

    def cached_text(self, key: str) -> str | None:
        cached = self.text_cache.get(key)
        if cached is not None:
            self.text_cache_hits += 1
        return cached

    def store_text(self, key: str, text: str) -> None:
        self.text_cache_misses += 1
        self.text_cache[key] = text

    def cached_binary(self, key: str) -> bytes | None:
        cached = self.binary_cache.get(key)
        if cached is not None:
            self.binary_cache_hits += 1
        return cached

    def store_binary(self, key: str, data: bytes) -> None:
        self.binary_cache_misses += 1
        self.binary_cache[key] = data

    def write_text(self, path: Path, text: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(path, text.encode(encoding))

    def write_bytes(self, path: Path, data: bytes) -> None:
        payload = bytes(data)
        self._pending_writes.append((Path(path), payload))
        self._pending_bytes += len(payload)
        self.queued_write_count += 1
        if (
            len(self._pending_writes) >= self.max_buffer_files
            or self._pending_bytes >= self.max_buffer_bytes
        ):
            self.flush()

    def flush(self) -> None:
        if not self._pending_writes:
            return
        pending = self._pending_writes
        self._pending_writes = []
        self._pending_bytes = 0
        self._write_futures.append(self._write_executor.submit(_write_pending_files, pending))
        self.flush_count += 1

    def wait_for_writes(self) -> None:
        if not self._write_futures:
            return
        futures = self._write_futures
        self._write_futures = []
        for future in futures:
            future.result()

    def finish(self) -> None:
        self.flush()
        self.wait_for_writes()
        self._write_executor.shutdown(wait=True)

    def shutdown(self, *, cancel_pending: bool = False) -> None:
        self._pending_writes = []
        self._pending_bytes = 0
        try:
            self._write_executor.shutdown(wait=False, cancel_futures=cancel_pending)
        except TypeError:
            self._write_executor.shutdown(wait=False)

    def flush_path(self, path: Path) -> None:
        target = Path(path).resolve(strict=False)
        if not any(write_path.resolve(strict=False) == target for write_path, _data in self._pending_writes):
            return
        self.flush()
        self.wait_for_writes()

    def summary(self) -> str:
        return (
            "Dual form build cache: "
            f"text hits={self.text_cache_hits}, text misses={self.text_cache_misses}, "
            f"binary hits={self.binary_cache_hits}, binary misses={self.binary_cache_misses}, "
            f"resolve hits={self.resolve_cache_hits}, resolve misses={self.resolve_cache_misses}, "
            f"writes={self.queued_write_count}, flushes={self.flush_count}"
        )


def _write_pending_files(pending: list[tuple[Path, bytes]]) -> None:
    for path, data in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _addon_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _dual_form_default_preset_folder() -> Path:
    return _addon_root() / "defaults" / "presets" / "dual_form"


def _dual_form_user_preset_folder() -> Path:
    return _addon_root() / "user" / "presets" / "dual_form"


def _dual_form_preset_folders(source_filter: str) -> list[tuple[str, Path]]:
    folders = [
        ("defaults", _dual_form_default_preset_folder()),
        ("user", _dual_form_user_preset_folder()),
    ]
    if source_filter == "defaults":
        return folders[:1]
    if source_filter == "user":
        return folders[1:]
    return folders


def _dual_form_preset_identifier(source: str, relative_path: Path) -> str:
    return f"{source}:{relative_path.as_posix()}"


def _dual_form_preset_path_from_identifier(identifier: str) -> Path | None:
    source, separator, relative_text = identifier.partition(":")
    if not separator or source not in {"defaults", "user"}:
        return None
    root = _dual_form_default_preset_folder() if source == "defaults" else _dual_form_user_preset_folder()
    relative_path = Path(*PurePosixPath(relative_text).parts)
    resolved = (root / relative_path).resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def _dual_form_preset_entries(source_filter: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for source, folder in _dual_form_preset_folders(source_filter):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.json"), key=lambda item: item.as_posix().lower()):
            try:
                relative_path = path.relative_to(folder)
            except ValueError:
                continue
            label = f"{source} > {relative_path.as_posix()}"
            entries.append(
                (
                    _dual_form_preset_identifier(source, relative_path),
                    label,
                    str(path),
                )
            )
    if entries:
        return entries
    return [(DUAL_FORM_PRESET_NONE, "No presets found", "No dual form preset JSON files were found")]


def dual_form_preset_items(scene, context):
    source_filter = getattr(scene, "neox_dual_form_preset_source_filter", "all")
    return _dual_form_preset_entries(source_filter)


def _load_dual_form_preset(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Preset root must be a JSON object")
    if payload.get("format") != DUAL_FORM_PRESET_FORMAT:
        raise ValueError("Preset format is not supported")
    if payload.get("version") != DUAL_FORM_PRESET_VERSION:
        raise ValueError("Preset version is not supported")
    preset_type = payload.get("type")
    if preset_type not in {"trigger_list", "regex"}:
        raise ValueError("Preset type must be 'trigger_list' or 'regex'")
    data = payload.get("data")
    if preset_type == "trigger_list":
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise ValueError("trigger_list preset data must be a list of strings")
    elif not isinstance(data, str):
        raise ValueError("regex preset data must be a string")
    return payload


def _write_dual_form_preset(path: Path, preset_type: str, data) -> None:
    payload = {
        "format": DUAL_FORM_PRESET_FORMAT,
        "version": DUAL_FORM_PRESET_VERSION,
        "type": preset_type,
        "data": data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _replace_dual_form_triggers(context, names: list[str]) -> int:
    triggers = context.scene.neox_dual_form_triggers
    triggers.clear()
    seen: set[str] = set()
    added = 0
    for name in names:
        text = name.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        item = triggers.add()
        item.name = text
        seen.add(key)
        added += 1
    context.scene.neox_dual_form_trigger_index = added - 1
    return added


def _rotl64(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (64 - bits))) & MASK64


def _xxh64_round(acc: int, lane: int) -> int:
    acc = (acc + lane * XXH64_PRIME_2) & MASK64
    acc = _rotl64(acc, 31)
    acc = (acc * XXH64_PRIME_1) & MASK64
    return acc


def _xxh64_merge(acc: int, lane: int) -> int:
    acc ^= _xxh64_round(0, lane)
    acc = (acc * XXH64_PRIME_1 + XXH64_PRIME_4) & MASK64
    return acc


def xxhash64_hex(text: str) -> str:
    data = text.encode("utf-8")
    length = len(data)
    offset = 0

    if length >= 32:
        v1 = (XXH64_PRIME_1 + XXH64_PRIME_2) & MASK64
        v2 = XXH64_PRIME_2
        v3 = 0
        v4 = (-XXH64_PRIME_1) & MASK64
        while offset <= length - 32:
            v1 = _xxh64_round(v1, struct.unpack_from("<Q", data, offset)[0])
            v2 = _xxh64_round(v2, struct.unpack_from("<Q", data, offset + 8)[0])
            v3 = _xxh64_round(v3, struct.unpack_from("<Q", data, offset + 16)[0])
            v4 = _xxh64_round(v4, struct.unpack_from("<Q", data, offset + 24)[0])
            offset += 32
        value = (
            _rotl64(v1, 1)
            + _rotl64(v2, 7)
            + _rotl64(v3, 12)
            + _rotl64(v4, 18)
        ) & MASK64
        value = _xxh64_merge(value, v1)
        value = _xxh64_merge(value, v2)
        value = _xxh64_merge(value, v3)
        value = _xxh64_merge(value, v4)
    else:
        value = XXH64_PRIME_5

    value = (value + length) & MASK64
    while offset <= length - 8:
        lane = struct.unpack_from("<Q", data, offset)[0]
        value ^= _xxh64_round(0, lane)
        value = (_rotl64(value, 27) * XXH64_PRIME_1 + XXH64_PRIME_4) & MASK64
        offset += 8
    if offset <= length - 4:
        value ^= struct.unpack_from("<I", data, offset)[0] * XXH64_PRIME_1
        value = (_rotl64(value, 23) * XXH64_PRIME_2 + XXH64_PRIME_3) & MASK64
        offset += 4
    while offset < length:
        value ^= data[offset] * XXH64_PRIME_5
        value = (_rotl64(value, 11) * XXH64_PRIME_1) & MASK64
        offset += 1

    value ^= value >> 33
    value = (value * XXH64_PRIME_2) & MASK64
    value ^= value >> 29
    value = (value * XXH64_PRIME_3) & MASK64
    value ^= value >> 32
    return f"{value:016x}"


def _normalize_slashes(path: str) -> str:
    return str(path).strip().replace("\\", "/")


def _documents_res_parts(path: Path) -> tuple[list[str], int]:
    parts = list(path.resolve(strict=False).parts)
    lower = [part.lower() for part in parts]
    for index in range(len(lower) - 1):
        if lower[index] == "documents" and lower[index + 1] == "res":
            return parts, index
    raise ValueError(f"Path is not inside Documents/res: {path}")


def _documents_res_relative(path: Path) -> str:
    parts, index = _documents_res_parts(path)
    return "/".join(parts[index + 2 :]).replace("\\", "/")


def _documents_res_root(path: Path) -> Path:
    parts, index = _documents_res_parts(path)
    return Path(*parts[: index + 2])


def _mod_root_relative(path: Path) -> str:
    relative = _documents_res_relative(path)
    pieces = [piece for piece in relative.split("/") if piece]
    if len(pieces) >= 2 and pieces[0].lower() == "mod":
        return "/".join(pieces[:2])
    return posixpath.dirname(relative)


def _safe_output_path(root: Path, relative_path: str) -> Path:
    normalized = posixpath.normpath(_normalize_slashes(relative_path))
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise ValueError(f"Path escapes output folder: {relative_path}")
    return root.joinpath(*PurePosixPath(normalized).parts)


def _relative_from_file(from_path: Path, target: str) -> str:
    from_dir = posixpath.dirname(_documents_res_relative(from_path))
    return posixpath.relpath(_normalize_slashes(target), start=from_dir).replace("\\", "/")


def _load_gim(path: Path) -> tuple[ET.Element, bool]:
    if parse_handler.typeFile(str(path)) == "Binary":
        element_tags, attribute_map = parse_handler.parseCustomBinFormat(str(path))
        return convert_handler.tagWrapper(element_tags, attribute_map)[0], True
    return ET.parse(path).getroot(), False


def _write_gim(path: Path, root: ET.Element, binary: bool) -> None:
    if binary:
        io_handler.ExportGim(
            str(path),
            convert_handler.xml_to_custom_bin(convert_handler.xml_to_bfs_list(root)),
        )
    else:
        io_handler.ExportUndecodedGim(str(path), root)


def _file_value(root: ET.Element, section: str) -> str:
    node = root.find(section)
    if node is None:
        return ""
    file_name = node.find("FileName")
    if file_name is None:
        return ""
    return _normalize_slashes(file_name.attrib.get("Value", ""))


def _set_file_value(root: ET.Element, section: str, value: str) -> None:
    node = root.find(section)
    if node is None:
        node = ET.SubElement(root, section)
    file_name = node.find("FileName")
    if file_name is None:
        file_name = ET.SubElement(node, "FileName")
    file_name.attrib["Value"] = value


def _resolve_reference_from_gim(gim_path: Path, reference: str) -> tuple[str, bool, Path | None]:
    reference = _normalize_slashes(reference)
    if not reference:
        raise ValueError(f"Empty gim reference in {gim_path}")
    if reference.lower().startswith("chr/"):
        return reference.strip("/"), True, None

    gim_relative_dir = posixpath.dirname(_documents_res_relative(gim_path))
    target_relative = posixpath.normpath(posixpath.join(gim_relative_dir, reference)).replace("\\", "/")
    mod_root = _mod_root_relative(gim_path)
    is_remote = not (
        target_relative == mod_root
        or target_relative.startswith(f"{mod_root}/")
    )

    if is_remote:
        return target_relative.strip("/"), True, None

    local_path = gim_path.parent / Path(reference.replace("/", os.sep))
    return _documents_res_relative(local_path), False, local_path


def _load_animconfig(asset_index, gim_path: Path, reference: str) -> tuple[ET.Element, str, bool, Path | None]:
    resolved, is_remote, local_path = _resolve_reference_from_gim(gim_path, reference)
    if is_remote:
        data = asset_index.extract(resolved).data
        return ET.fromstring(data.decode("utf-8-sig")), resolved, True, None
    return ET.parse(local_path).getroot(), resolved, False, local_path


def _load_skeleton_bone_names(
    asset_index,
    skeleton_reference: str,
    is_remote: bool,
    local_path: Path | None,
) -> tuple[list[str], int]:
    if is_remote:
        data = asset_index.extract(skeleton_reference).data
        source = skeleton_reference
    else:
        if local_path is None:
            raise ValueError(f"Local skeleton path is missing: {skeleton_reference}")
        data = local_path.read_bytes()
        source = str(local_path)

    try:
        skeleton = json.loads(data.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Skeleton is not valid JSON: {source}") from exc

    joint_names = skeleton.get("joint_names")
    parent_indices = skeleton.get("joint_parent_indices")
    if not isinstance(joint_names, list) or not all(isinstance(name, str) and name for name in joint_names):
        raise ValueError(f"Skeleton has no valid joint_names: {source}")
    if not isinstance(parent_indices, list) or len(parent_indices) != len(joint_names):
        raise ValueError(f"Skeleton joint_parent_indices does not match joint_names: {source}")

    root_indices = [
        index
        for index, parent_index in enumerate(parent_indices)
        if int(parent_index) == -1
    ]
    if not root_indices:
        raise ValueError(f"Skeleton has no root bone with parent index -1: {source}")
    return joint_names, root_indices[0]


def _resolve_animation_reference(
    asset_index,
    animconfig_reference: str,
    animation_reference: str,
) -> str:
    normalized = _normalize_slashes(animation_reference).strip("/")
    lowered = normalized.lower()
    if lowered.startswith("chr/") or lowered.startswith("mod/"):
        return normalized

    base_dir = posixpath.dirname(animconfig_reference.strip("/"))
    return posixpath.normpath(posixpath.join(base_dir, normalized)).replace("\\", "/")


def _write_animconfig(path: Path, root: ET.Element, build_context: DualFormBuildContext | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="\t")
    if build_context is None:
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=False)
        return
    build_context.write_bytes(path, ET.tostring(root, encoding="utf-8", xml_declaration=False))


def _safe_file_stem(value: str, fallback: str) -> str:
    stem = "".join(
        char if char.isalnum() or char in ("_", "-") else "_"
        for char in str(value).strip()
    ).strip("_")
    return stem or fallback


def _write_disabled_cpdanimation(
    path: Path,
    skeleton_path: str,
    bone_names: list[str],
    root_index: int,
    build_context: DualFormBuildContext | None = None,
) -> None:
    from mathutils import Vector

    class _Track:
        def __init__(self, name: str, is_root: bool):
            self.name = name
            self.position_keys = []
            self.scale_keys = [VectorKey(0, Vector((0.0, 0.0, 0.0)))] if is_root else []
            self.rotation_keys = []

    if not bone_names:
        raise ValueError("Disabled animation requires at least one skeleton bone")
    if root_index < 0 or root_index >= len(bone_names):
        raise ValueError(f"Invalid disabled animation root index: {root_index}")

    head = pack_section(
        b"HEAD",
        build_head_payload(30, 0.0, True, DEFAULT_ACCUMULATION_FLAGS),
    )
    tracks = [
        _Track(bone_name, index == root_index)
        for index, bone_name in enumerate(bone_names)
    ]
    data = pack_section(b"DATA", build_data_payload(tracks))
    name = pack_section(b"NAME", build_name_payload("disabled", bone_names))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_file_bytes(skeleton_path, head, data, name, None)
    if build_context is None:
        path.write_bytes(payload)
    else:
        build_context.write_bytes(path, payload)


def _write_disabled_cpdanimation_for_folder(
    folder: Path,
    skeleton_documents_res_path: str,
    bone_names: list[str],
    root_index: int,
    build_context: DualFormBuildContext | None = None,
) -> Path:
    cpdanimation_path = folder / "disabled.cpdanimation"
    skeleton_cpdanimation_relative_path = _relative_from_file(
        cpdanimation_path,
        skeleton_documents_res_path,
    )
    _write_disabled_cpdanimation(
        cpdanimation_path,
        skeleton_cpdanimation_relative_path,
        bone_names,
        root_index,
        build_context,
    )
    return cpdanimation_path


def _load_animation_text_for_copy(
    asset_index,
    output_path: Path,
    source_reference: str,
    animation_reference: str,
    build_context: DualFormBuildContext | None = None,
) -> tuple[str, str]:
    if build_context is None:
        official = _resolve_animation_reference(
            asset_index,
            source_reference,
            animation_reference,
        )
    else:
        official = build_context.resolve_animation_reference(asset_index, source_reference, animation_reference)
        cached_text = build_context.cached_text(official)
        if cached_text is not None:
            return cached_text, official
    try:
        data = asset_index.extract(official).data
    except Exception as exc:
        raise ValueError(f"Animation asset could not be found by asset finder: {official}") from exc

    try:
        text = data.decode("utf-8-sig")
    except Exception as exc:
        raise ValueError(f"Animation asset is not valid UTF-8 JSON text: {official}") from exc
    if build_context is not None:
        build_context.store_text(official, text)
    return text, official


def _localize_animation_dependency_text(raw_text: str, cpdanimation_path: Path) -> str:
    dependency = _documents_res_relative(cpdanimation_path)
    match = re.search(r'("Dependices"\s*:\s*)\[(.*?)\]', raw_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("Animation JSON has no _FileHeader.Dependices array")

    line_start = raw_text.rfind("\n", 0, match.start()) + 1
    line_indent = raw_text[line_start:match.start()]
    item_indent = f"{line_indent}\t"
    replacement = (
        f'{match.group(1)}[\n'
        f"{item_indent}{json.dumps(dependency, ensure_ascii=False)}\n"
        f"{line_indent}]"
    )
    return raw_text[:match.start()] + replacement + raw_text[match.end():]


def _write_disabled_animation_copy(
    asset_index,
    output_path: Path,
    animation_folder: Path,
    source_reference: str,
    animation_reference: str,
    animation_name: str,
    cpdanimation_path: Path,
    used_file_names: set[str],
    build_context: DualFormBuildContext | None = None,
) -> str:
    animation_text, official = _load_animation_text_for_copy(
        asset_index,
        output_path,
        source_reference,
        animation_reference,
        build_context,
    )
    animation_text = _localize_animation_dependency_text(animation_text, cpdanimation_path)

    stem = _safe_file_stem(animation_name, Path(official).stem or "disabled")
    file_name = f"{stem}.animation"
    counter = 1
    while file_name.lower() in used_file_names:
        file_name = f"{stem}_{counter}.animation"
        counter += 1
    used_file_names.add(file_name.lower())

    output_animation_path = animation_folder / file_name
    if build_context is None:
        output_animation_path.write_text(animation_text, encoding="utf-8")
    else:
        build_context.write_text(output_animation_path, animation_text, encoding="utf-8")
    return f"{animation_folder.name}/{file_name}"


def _prepare_disabled_animation_copy_text(
    source_reference: str,
    animation_reference: str,
    cpdanimation_path: Path,
) -> str:
    animation_text, _official = _load_animation_text_for_copy(
        _dual_form_worker_asset_index(),
        Path(),
        source_reference,
        animation_reference,
        None,
    )
    return _localize_animation_dependency_text(animation_text, cpdanimation_path)


def _write_prepared_text(path: Path, text: str, build_context: DualFormBuildContext | None) -> None:
    if build_context is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        build_context.write_text(path, text, encoding="utf-8")


def _find_animation_file_by_name(root: ET.Element, animation_name: str) -> str | None:
    target = animation_name.strip().lower()
    for animation in root.findall("./AnimationList/Animation"):
        if animation.attrib.get("Name", "").strip().lower() != target:
            continue
        file_name = animation.attrib.get("FileName", "").strip()
        if file_name:
            return file_name
    return None


def _rewrite_animconfig(
    asset_index,
    source_root: ET.Element,
    source_reference: str,
    output_path: Path,
    skeleton_path: str,
    skeleton_bone_names: list[str],
    skeleton_root_index: int,
    trigger_names: set[str],
    active_on_trigger: bool,
    build_context: DualFormBuildContext | None = None,
) -> None:
    output_root = output_path.parent
    animation_folder = output_root / f"animations_{output_path.stem}"
    animation_folder.mkdir(parents=True, exist_ok=True)
    disabled_cpdanimation_path = _write_disabled_cpdanimation_for_folder(
        animation_folder,
        skeleton_path,
        skeleton_bone_names,
        skeleton_root_index,
        build_context,
    )
    used_disabled_file_names: set[str] = set()
    disabled_jobs: list[tuple[ET.Element, Path, str, str, Path]] = []
    shared_disabled_animation_reference: str | None = None

    skin_animation_reference = _find_animation_file_by_name(source_root, "skin")
    if skin_animation_reference:
        output_animation_path = animation_folder / "disabled.animation"
        try:
            animation_text, _official = _load_animation_text_for_copy(
                asset_index,
                output_animation_path,
                source_reference,
                skin_animation_reference,
                build_context,
            )
            animation_text = _localize_animation_dependency_text(
                animation_text,
                disabled_cpdanimation_path,
            )
            _write_prepared_text(output_animation_path, animation_text, build_context)
            shared_disabled_animation_reference = f"{animation_folder.name}/{output_animation_path.name}"
        except Exception as exc:
            print(f"[DualForm] Shared disabled.animation fallback: {exc}")

    root = copy.deepcopy(source_root)
    for animation in root.findall("./AnimationList/Animation"):
        name = animation.attrib.get("Name", "").strip()
        file_name = animation.attrib.get("FileName", "").strip()
        if not file_name:
            continue

        is_trigger = name.lower() in trigger_names
        is_active = is_trigger if active_on_trigger else not is_trigger
        if is_active:
            try:
                if build_context is None:
                    official = _resolve_animation_reference(
                        asset_index,
                        source_reference,
                        file_name,
                    )
                else:
                    official = build_context.resolve_animation_reference(asset_index, source_reference, file_name)
                animation.attrib["FileName"] = _relative_from_file(output_path, official)
            except Exception:
                local_target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(source_reference), _normalize_slashes(file_name))
                ).replace("\\", "/")
                animation.attrib["FileName"] = posixpath.relpath(
                    local_target,
                    start=posixpath.dirname(_documents_res_relative(output_path)),
                ).replace("\\", "/")
        else:
            if shared_disabled_animation_reference is not None:
                animation.attrib["FileName"] = shared_disabled_animation_reference
                continue

            official = (
                _resolve_animation_reference(asset_index, source_reference, file_name)
                if build_context is None
                else build_context.resolve_animation_reference(asset_index, source_reference, file_name)
            )
            stem = _safe_file_stem(name, Path(official).stem or "disabled")
            output_file_name = f"{stem}.animation"
            counter = 1
            while output_file_name.lower() in used_disabled_file_names:
                output_file_name = f"{stem}_{counter}.animation"
                counter += 1
            used_disabled_file_names.add(output_file_name.lower())

            output_animation_path = animation_folder / output_file_name
            animation.attrib["FileName"] = f"{animation_folder.name}/{output_file_name}"
            disabled_jobs.append(
                (
                    animation,
                    output_animation_path,
                    source_reference,
                    file_name,
                    disabled_cpdanimation_path,
                )
            )

    if disabled_jobs:
        worker_count = min(DUAL_FORM_BUILD_WORKERS, len(disabled_jobs))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="dual-form-disabled") as executor:
            futures = {
                executor.submit(
                    _prepare_disabled_animation_copy_text,
                    source_reference,
                    animation_reference,
                    cpdanimation_path,
                ): output_animation_path
                for _animation, output_animation_path, source_reference, animation_reference, cpdanimation_path
                in disabled_jobs
            }
            for future in as_completed(futures):
                _write_prepared_text(futures[future], future.result(), build_context)

    _write_animconfig(output_path, root, build_context)


def _next_socket_index(socket_objects: ET.Element) -> int:
    max_index = -1
    for child in socket_objects:
        _prefix, separator, suffix = child.tag.rpartition("_")
        if separator and suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return max_index + 1


WRAPPER_TEMPLATE_DIR = Path(__file__).resolve().parent / "mod_exporter" / "tex_resource"
WRAPPER_TEX0_TEMPLATE = WRAPPER_TEMPLATE_DIR / "wrapper_Tex0.dds"
WRAPPER_MTL_TEMPLATE = WRAPPER_TEMPLATE_DIR / "wrapper.mtl"
WRAPPER_MTG_TEMPLATE = WRAPPER_TEMPLATE_DIR / "wrapper.mtg"


def _add_socket_object(owner_root: ET.Element, child_gim_path: Path) -> None:
    socket_objects = owner_root.find("SocketObject")
    if socket_objects is None:
        socket_objects = ET.SubElement(owner_root, "SocketObject")

    socket_index = _next_socket_index(socket_objects)
    object_name = child_gim_path.stem
    socket = ET.SubElement(
        socket_objects,
        f"Socket_{socket_index}",
        {
            "BindType": "7",
            "BindingBone": "",
            "BindingFlag": "2",
            "LocalPosition": "0.0000,0.0000,0.0000",
            "LocalRotation": "0.0000,0.0000,0.0000,1.0000",
            "LocalScale": "1.0000,1.0000,1.0000",
            "Name": object_name,
            "PlayRatePolicy": "1",
            "PreloadingLevel": "4294967295",
            "SubmeshSortIdx": "4294967295",
            "SyncVo": "false",
        },
    )
    ET.SubElement(
        socket,
        "Object",
        {
            "CastShadow": "true",
            "Id": xxhash64_hex(object_name),
            "Inherit": "263",
            "Loading": "4",
            "Name": object_name,
            "Uri": _documents_res_relative(child_gim_path),
        },
    )


def _root_bone_index(bone_parents: list[int]) -> int:
    for index, parent_index in enumerate(bone_parents):
        if int(parent_index) in (-1, 65535):
            return index
    raise ValueError("Wrapper mesh source skeleton has no root bone")


def _wrapper_collision_records(bone_count: int) -> list[dict[str, object]]:
    return [
        {
            "center": (0.0, 0.0, 0.0),
            "collision_x": 0.0,
            "collision_y": 0.0,
            "collision_z": 0.0,
            "bound_radius": 0.0,
        }
        for _index in range(bone_count)
    ]


def _flatten_matrix_record(matrix) -> list[float]:
    if hasattr(matrix, "reshape"):
        return [float(value) for value in matrix.reshape(-1)]
    return [float(value) for row in matrix for value in row]


def _mesh_reference_from_gim(asset_index, gim_path: Path, gim_root: ET.Element) -> bytes:
    mesh_reference = gim_root.attrib.get("Mesh", "").strip()
    if mesh_reference:
        resolved, is_remote, local_path = _resolve_reference_from_gim(gim_path, mesh_reference)
        if is_remote:
            return asset_index.extract(resolved).data
        if local_path is None or not local_path.is_file():
            raise ValueError(f"Local mesh referenced by gim was not found: {mesh_reference}")
        return local_path.read_bytes()

    local_mesh_path = gim_path.with_suffix(".mesh")
    if not local_mesh_path.is_file():
        raise ValueError(f"Main gim has no NeoX/@Mesh and local mesh was not found: {local_mesh_path}")
    return local_mesh_path.read_bytes()


def _build_wrapper_mesh_data(model: dict) -> tuple[dict, int]:
    if not model.get("mesh"):
        raise ValueError("Wrapper mesh source has no submeshes")
    if not model.get("face"):
        raise ValueError("Wrapper mesh source has no faces")
    if int(model["mesh"][0][1]) <= 0:
        raise ValueError("Wrapper mesh source first submesh has no faces")
    if not model.get("bone_name") or not model.get("bone_parent"):
        raise ValueError("Wrapper mesh source has no skeleton data")

    root_index = _root_bone_index(list(model["bone_parent"]))
    first_face = tuple(int(index) for index in model["face"][0])
    if len(first_face) != 3:
        raise ValueError(f"Wrapper mesh requires a triangle face, got {len(first_face)} vertices")

    vertex_count = int(model["mesh"][0][0])
    if any(index < 0 or index >= vertex_count for index in first_face):
        raise ValueError("First face does not belong to the first submesh")

    bone_names = [str(name) for name in model["bone_name"]]
    bone_parents = [
        65535 if int(parent_index) == -1 else int(parent_index)
        for parent_index in model["bone_parent"]
    ]
    root_name = bone_names[root_index]
    vertex_positions = [model["position"][index] for index in first_face]
    vertex_normals = [
        model.get("normal", [(0.0, 0.0, 1.0)] * len(model["position"]))[index]
        for index in first_face
    ]
    vertex_uvs = [
        model.get("uv", [(0.0, 0.0)] * len(model["position"]))[index]
        for index in first_face
    ]

    from .export_ops import encode_bone_weight_usage_mask

    mesh_data = {
        "bone_name": bone_names,
        "bone_parent": bone_parents,
        "bone_matrix": [_flatten_matrix_record(matrix) for matrix in model["bone_matrix"]],
        "bounding_info": _wrapper_collision_records(len(bone_names)),
        "bone_weight_usage": encode_bone_weight_usage_mask(bone_names, {root_name}),
        "mesh": [
            {
                "position": vertex_positions,
                "normal": vertex_normals,
                "tangent": [(1.0, 0.0, 0.0)] * 3,
                "face": [(0, 1, 2)],
                "uv": vertex_uvs,
                "vertex_joint": [[root_index, 65535, 65535, 65535] for _index in range(3)],
                "vertex_joint_weight": [[1.0, 0.0, 0.0, 0.0] for _index in range(3)],
            }
        ],
    }
    return mesh_data, root_index


class _WrapperArmatureMetadata:
    def __init__(self, lod_table: bytes):
        self._lod_table = bytes(lod_table or bytes(16))

    def get(self, key: str, default=None):
        if key == "NeoX:LODTable":
            return self._lod_table
        return default


def _write_wrapper_mesh(asset_index, gim_path: Path, gim_root: ET.Element, output_path: Path, operator) -> tuple[list[str], str]:
    from .import_ops import _parse_neox_mesh
    from .export_ops import export_neox_mesh

    source_mesh_data = _mesh_reference_from_gim(asset_index, gim_path, gim_root)
    model = _parse_neox_mesh(BytesIO(source_mesh_data), operator)
    if not model:
        raise ValueError("Wrapper source mesh could not be parsed")

    wrapper_mesh_data, root_index = _build_wrapper_mesh_data(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log = StringIO()
    ok = export_neox_mesh(
        output_path,
        wrapper_mesh_data,
        _WrapperArmatureMetadata(model.get("lod_data_table", bytes(16))),
        operator,
        log,
    )
    if not ok:
        raise ValueError("Wrapper mesh export failed")
    bone_names = list(wrapper_mesh_data["bone_name"])
    return bone_names, bone_names[root_index]


def _parent_prefixed_reference(reference: str) -> str:
    reference = str(reference).strip().replace("\\", "/")
    if not reference:
        return reference
    return posixpath.normpath(posixpath.join("..", reference)).replace("\\", "/")


def _remove_extra_wrapper_submeshes(wrapper_root: ET.Element) -> None:
    submesh = wrapper_root.find("SubMesh")
    if submesh is None:
        return
    first_submesh = None
    for child in list(submesh):
        if child.tag == "Sub0":
            first_submesh = child
            break
    submesh.clear()
    if first_submesh is None:
        first_submesh = ET.Element(
            "Sub0",
            {
                "BoundingCenter": "0.0000,0.0000,0.0000",
                "BoundingHalf": "0.0001,0.0001,0.0001",
                "ForceBatch": "false",
                "IsSkin4S": "false",
                "MtlIdx": "0",
                "Name": "wrapper",
                "RenderGroup": "0",
                "RenderOffset": "0",
                "ShadowBias": "0",
                "ShadowNormalBias": "0",
            },
        )
    else:
        first_submesh.attrib["MtlIdx"] = "0"
        first_submesh.attrib["Name"] = "wrapper"
    submesh.append(first_submesh)


def _remove_child(parent: ET.Element, child: ET.Element | None) -> None:
    if child is not None:
        parent.remove(child)


def _remove_loading_four_socket_objects(root: ET.Element) -> None:
    socket_objects = root.find("SocketObject")
    if socket_objects is None:
        return

    for socket in socket_objects:
        for child in list(socket):
            if child.tag != "Object":
                continue
            loading = child.attrib.get("Loading", "").strip()
            uri = child.attrib.get("Uri", "").strip().lower()
            if loading == "4" or uri.startswith("wwise"):
                socket.remove(child)


def _write_wrapper_material_files(wrapper_dir: Path) -> None:
    for template_path in (WRAPPER_TEX0_TEMPLATE, WRAPPER_MTL_TEMPLATE, WRAPPER_MTG_TEMPLATE):
        if not template_path.is_file():
            raise FileNotFoundError(f"Wrapper template file was not found: {template_path}")

    tex0_path = wrapper_dir / "Tex0.dds"
    mtl_path = wrapper_dir / "wrapper.mtl"
    mtg_path = wrapper_dir / "wrapper.mtg"

    shutil.copy2(WRAPPER_TEX0_TEMPLATE, tex0_path)

    mtl_root = ET.parse(WRAPPER_MTL_TEMPLATE).getroot()
    param_table = mtl_root.find(".//ParamTable")
    if param_table is None:
        material = mtl_root.find(".//Material")
        param_table_parent = material if material is not None else mtl_root
        param_table = ET.SubElement(param_table_parent, "ParamTable")
    tex0 = param_table.find("Tex0")
    if tex0 is None:
        tex0 = ET.SubElement(param_table, "Tex0")
    tex0.attrib["Value"] = _documents_res_relative(tex0_path)
    ET.indent(mtl_root, space="\t")
    ET.ElementTree(mtl_root).write(mtl_path, encoding="utf-8", xml_declaration=False)

    mtg_root = ET.parse(WRAPPER_MTG_TEMPLATE).getroot()
    material_group = mtg_root.find(".//MaterialGroup")
    if material_group is None:
        material_group = ET.SubElement(mtg_root, "MaterialGroup")
    material_group.attrib["MaterialCount"] = "1"
    for child in list(material_group):
        material_group.remove(child)
    ET.SubElement(material_group, "Material_0", {"Path": _documents_res_relative(mtl_path)})
    ET.indent(mtg_root, space="\t")
    ET.ElementTree(mtg_root).write(mtg_path, encoding="utf-8", xml_declaration=False)


def _replace_extension(path: str, extension: str) -> str:
    return f"{path.rsplit('.', 1)[0]}{extension}" if "." in path else f"{path}{extension}"


def _local_documents_res_path(gim_path: Path, documents_res_reference: str) -> Path | None:
    normalized = _normalize_slashes(documents_res_reference).strip("/")
    mod_root = _mod_root_relative(gim_path)
    if normalized == mod_root or normalized.startswith(f"{mod_root}/"):
        candidate = _documents_res_root(gim_path).joinpath(*normalized.split("/"))
        if candidate.is_file():
            return candidate
    return None


def _load_text_asset(
    asset_index,
    gim_path: Path,
    documents_res_reference: str,
    label: str,
    build_context: DualFormBuildContext | None = None,
) -> tuple[str, str]:
    normalized = _normalize_slashes(documents_res_reference).strip("/")
    if build_context is not None:
        cached_text = build_context.cached_text(normalized)
        if cached_text is not None:
            return cached_text, normalized

    local_path = _local_documents_res_path(gim_path, normalized)
    if local_path is not None:
        text = local_path.read_text(encoding="utf-8-sig")
        if build_context is not None:
            build_context.store_text(normalized, text)
        return text, normalized
    try:
        data = asset_index.extract(normalized).data
    except Exception as exc:
        raise ValueError(f"{label} could not be found by asset finder: {normalized}") from exc

    try:
        text = data.decode("utf-8-sig")
    except Exception as exc:
        raise ValueError(f"{label} is not valid UTF-8 text: {normalized}") from exc
    if build_context is not None:
        build_context.store_text(normalized, text)
    return text, normalized


def _load_binary_asset(
    asset_index,
    gim_path: Path,
    documents_res_reference: str,
    label: str,
    build_context: DualFormBuildContext | None = None,
) -> tuple[bytes, str]:
    normalized = _normalize_slashes(documents_res_reference).strip("/")
    if build_context is not None:
        cached_data = build_context.cached_binary(normalized)
        if cached_data is not None:
            return cached_data, normalized

    local_path = _local_documents_res_path(gim_path, normalized)
    if local_path is not None:
        data = local_path.read_bytes()
        if build_context is not None:
            build_context.store_binary(normalized, data)
        return data, normalized
    try:
        data = asset_index.extract(normalized).data
    except Exception as exc:
        raise ValueError(f"{label} could not be found by asset finder: {normalized}") from exc
    if build_context is not None:
        build_context.store_binary(normalized, data)
    return data, normalized


def _dependency_references_from_animation(raw_text: str) -> list[str]:
    match = re.search(r'"Dependices"\s*:\s*\[(.*?)\]', raw_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("Animation JSON has no _FileHeader.Dependices array")

    try:
        dependencies = json.loads(f"[{match.group(1)}]")
    except json.JSONDecodeError as exc:
        raise ValueError("Animation JSON has invalid _FileHeader.Dependices array") from exc

    if not isinstance(dependencies, list):
        raise ValueError("Animation JSON _FileHeader.Dependices is not a list")
    return [dependency for dependency in dependencies if isinstance(dependency, str) and dependency.strip()]


def _resolve_dependency_reference(asset_index, animation_asset_reference: str, dependency_reference: str) -> str:
    normalized = _normalize_slashes(dependency_reference).strip("/")
    if not normalized.lower().endswith(".cpdanimation"):
        normalized = _replace_extension(normalized, ".cpdanimation")

    try:
        asset_index.parse(normalized)
        return normalized
    except Exception:
        pass

    return posixpath.normpath(
        posixpath.join(posixpath.dirname(animation_asset_reference), normalized)
    ).replace("\\", "/")


def _localize_animation_dependencies_text(raw_text: str, dependency_paths: list[str]) -> str:
    match = re.search(r'("Dependices"\s*:\s*)\[(.*?)\]', raw_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("Animation JSON has no _FileHeader.Dependices array")

    line_start = raw_text.rfind("\n", 0, match.start()) + 1
    line_indent = raw_text[line_start:match.start()]
    item_indent = f"{line_indent}\t"
    rendered_items = [
        f"{item_indent}{json.dumps(path, ensure_ascii=False)}"
        for path in dependency_paths
    ]
    replacement = f'{match.group(1)}[\n' + ",\n".join(rendered_items) + f"\n{line_indent}]"
    return raw_text[:match.start()] + replacement + raw_text[match.end():]


def _prepare_wrapper_animation_text(
    main_gim_path: Path,
    animation_asset_reference: str,
) -> tuple[str, list[str]]:
    asset_index = _dual_form_worker_asset_index()
    animation_text, resolved_animation_reference = _load_text_asset(
        asset_index,
        main_gim_path,
        animation_asset_reference,
        "Wrapper animation",
        None,
    )
    dependency_references = _dependency_references_from_animation(animation_text)
    if not dependency_references:
        raise ValueError(f"Animation has no usable CPD dependencies: {resolved_animation_reference}")
    resolved_dependencies = [
        _resolve_dependency_reference(asset_index, resolved_animation_reference, dependency_reference)
        for dependency_reference in dependency_references
    ]
    return animation_text, resolved_dependencies


def _write_wrapper_animconfig(
    asset_index,
    operator,
    wrapper_dir: Path,
    source_animconfig_root: ET.Element,
    source_animconfig_reference: str,
    main_gim_path: Path,
    build_context: DualFormBuildContext | None = None,
) -> Path:
    wrapper_animconfig_path = wrapper_dir / "wrapper.animconfig"
    animation_folder = wrapper_dir / "animations_wrapper"
    wrapper_root = copy.deepcopy(source_animconfig_root)
    used_file_names: set[str] = set()
    animation_jobs: list[dict] = []
    animation_list = wrapper_root.find("./AnimationList")
    if animation_list is None:
        raise ValueError("Wrapper animconfig has no AnimationList element")

    for animation in list(animation_list.findall("./Animation")):
        name = animation.attrib.get("Name", "").strip()
        file_name = animation.attrib.get("FileName", "").strip()
        if not file_name:
            raise ValueError(f"Wrapper animation entry has no FileName: {name}")
        if build_context is None:
            animation_asset_reference = _resolve_animation_reference(
                asset_index,
                source_animconfig_reference,
                file_name,
            )
        else:
            animation_asset_reference = build_context.resolve_animation_reference(
                asset_index,
                source_animconfig_reference,
                file_name,
            )

        animation_stem = _safe_file_stem(name, Path(animation_asset_reference).stem or "animation")
        animation_file_name = f"{animation_stem}.animation"
        counter = 1
        while animation_file_name.lower() in used_file_names:
            animation_file_name = f"{animation_stem}_{counter}.animation"
            counter += 1
        used_file_names.add(animation_file_name.lower())

        animation_jobs.append(
            {
                "element": animation,
                "output_path": animation_folder / animation_file_name,
                "asset_reference": animation_asset_reference,
                "text": "",
                "dependencies": [],
            }
        )

    if not animation_jobs:
        raise ValueError("Wrapper animconfig has no AnimationList/Animation entries")

    worker_count = min(DUAL_FORM_BUILD_WORKERS, len(animation_jobs))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="dual-form-wrapper-animation") as executor:
        futures = {
            executor.submit(
                _prepare_wrapper_animation_text,
                main_gim_path,
                job["asset_reference"],
            ): index
            for index, job in enumerate(animation_jobs)
        }
        for future in as_completed(futures):
            job = animation_jobs[futures[future]]
            animation_text, dependency_references = future.result()
            job["text"] = animation_text
            job["dependencies"] = dependency_references

    kept_animation_count = 0
    for job in animation_jobs:
        output_animation_path = job["output_path"]
        if not job["dependencies"]:
            operator.report({"WARNING"}, f"Skipping the {output_animation_path.name}")
            animation_list.remove(job["element"])
            continue

        localized_animation_text = _localize_animation_dependencies_text(job["text"], job["dependencies"])
        if build_context is None:
            output_animation_path.parent.mkdir(parents=True, exist_ok=True)
            output_animation_path.write_text(localized_animation_text, encoding="utf-8")
        else:
            build_context.write_text(output_animation_path, localized_animation_text, encoding="utf-8")
        job["element"].attrib["FileName"] = _relative_from_file(
            wrapper_animconfig_path,
            _documents_res_relative(output_animation_path),
        )
        kept_animation_count += 1

    if kept_animation_count == 0:
        raise ValueError("Wrapper animconfig has no usable AnimationList/Animation entries")

    _write_animconfig(wrapper_animconfig_path, wrapper_root, build_context)
    return wrapper_animconfig_path


def _write_wrapper_gim(
    wrapper_gim_path: Path,
    source_root: ET.Element,
    source_binary: bool,
    main_gim_path: Path,
    dual_gim_path: Path,
    wrapper_animconfig_path: Path,
) -> None:
    wrapper_root = copy.deepcopy(source_root)
    wrapper_root.attrib.pop("Mesh", None)
    _remove_extra_wrapper_submeshes(wrapper_root)
    _remove_child(wrapper_root, wrapper_root.find("MtgFile"))
    _remove_loading_four_socket_objects(wrapper_root)

    skeleton_value = _file_value(wrapper_root, "SkeletonFile")
    _set_file_value(wrapper_root, "SkeletonFile", _parent_prefixed_reference(skeleton_value))
    _set_file_value(wrapper_root, "AnimationConfigFile", wrapper_animconfig_path.name)

    _add_socket_object(wrapper_root, main_gim_path)
    _add_socket_object(wrapper_root, dual_gim_path)
    _write_gim(wrapper_gim_path, wrapper_root, source_binary)


def _build_dual_form_wrapper(
    asset_index,
    main_gim_path: Path,
    main_root: ET.Element,
    main_binary: bool,
    source_animconfig_root: ET.Element,
    source_animconfig_reference: str,
    dual_gim_path: Path,
    operator,
    build_context: DualFormBuildContext | None = None,
) -> Path:
    wrapper_dir = main_gim_path.parent / "wrapper"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    _write_wrapper_mesh(
        asset_index,
        main_gim_path,
        main_root,
        wrapper_dir / "wrapper.mesh",
        operator,
    )
    _write_wrapper_material_files(wrapper_dir)
    wrapper_animconfig_path = _write_wrapper_animconfig(
        asset_index,
        operator,
        wrapper_dir,
        source_animconfig_root,
        source_animconfig_reference,
        main_gim_path,
        build_context,
    )
    wrapper_gim_path = wrapper_dir / "wrapper.gim"
    _write_wrapper_gim(
        wrapper_gim_path,
        main_root,
        main_binary,
        main_gim_path,
        dual_gim_path,
        wrapper_animconfig_path,
    )
    return wrapper_gim_path


class IDVMI_PG_Dual_Form_Trigger(bpy.types.PropertyGroup):
    name: StringProperty(name="Animation")


class IDVMI_UL_Dual_Form_Triggers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)


class IDVMI_OT_Dual_Form_Add_Trigger(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_add_trigger"
    bl_label = "Add Dual Form Trigger"

    def execute(self, context):
        text = context.scene.neox_dual_form_trigger_text.strip()
        if not text:
            return {"CANCELLED"}
        existing = {item.name.lower() for item in context.scene.neox_dual_form_triggers}
        if text.lower() not in existing:
            item = context.scene.neox_dual_form_triggers.add()
            item.name = text
            context.scene.neox_dual_form_trigger_index = len(context.scene.neox_dual_form_triggers) - 1
        context.scene.neox_dual_form_trigger_text = ""
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Remove_Trigger(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_remove_trigger"
    bl_label = "Remove Dual Form Trigger"

    def execute(self, context):
        index = context.scene.neox_dual_form_trigger_index
        triggers = context.scene.neox_dual_form_triggers
        if index < 0 or index >= len(triggers):
            return {"CANCELLED"}
        triggers.remove(index)
        context.scene.neox_dual_form_trigger_index = min(index, len(triggers) - 1)
        return {"FINISHED"}


def _cache_dual_form_animation_names(context, asset_index, gim_path: Path) -> list[str]:
    source = str(gim_path.resolve(strict=False))
    cache = context.scene.neox_dual_form_animation_name_cache
    if context.scene.neox_dual_form_animation_name_cache_source == source and len(cache) > 0:
        return [item.name for item in cache]

    gim_root, _gim_binary = _load_gim(gim_path)
    anim_root, _source_ref, _remote, _local = _load_animconfig(
        asset_index,
        gim_path,
        _file_value(gim_root, "AnimationConfigFile"),
    )

    seen: set[str] = set()
    names: list[str] = []
    for animation in anim_root.findall("./AnimationList/Animation"):
        name = animation.attrib.get("Name", "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)

    cache.clear()
    for name in names:
        item = cache.add()
        item.name = name
    context.scene.neox_dual_form_animation_name_cache_source = source
    return names


class IDVMI_OT_Dual_Form_Add_Regex_Triggers(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_add_regex_triggers"
    bl_label = "Add Dual Form Triggers by Regex"

    def execute(self, context):
        patterns = [
            line.strip()
            for line in context.scene.neox_dual_form_regex_text.splitlines()
            if line.strip()
        ]
        if not patterns:
            self.report({"ERROR"}, "Regex pattern list is empty")
            return {"CANCELLED"}

        main_gim_path = Path(bpy.path.abspath(context.scene.neox_dual_form_main_gim))
        if not main_gim_path.is_file():
            self.report({"ERROR"}, "Select a main model .gim file first")
            return {"CANCELLED"}

        expressions = []
        try:
            for pattern in patterns:
                expressions.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            self.report({"ERROR"}, f"Invalid regex: {exc}")
            return {"CANCELLED"}

        try:
            from .remote_import import _make_asset_index

            asset_index = _make_asset_index()
            names = _cache_dual_form_animation_names(context, asset_index, main_gim_path)
        except Exception as exc:
            self.report({"ERROR"}, f"Failed to cache animation names: {exc}")
            return {"CANCELLED"}

        existing = {item.name.lower() for item in context.scene.neox_dual_form_triggers}
        added = 0
        for name in names:
            if name.lower() in existing:
                continue
            if not any(expression.search(name) for expression in expressions):
                continue
            item = context.scene.neox_dual_form_triggers.add()
            item.name = name
            existing.add(name.lower())
            context.scene.neox_dual_form_trigger_index = len(context.scene.neox_dual_form_triggers) - 1
            added += 1

        if added == 0:
            self.report({"INFO"}, "No new animation names matched the regex")
        else:
            self.report({"INFO"}, f"Added {added} animation trigger(s) from {len(expressions)} regex pattern(s)")
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Cycle_Preset_Source(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_cycle_preset_source"
    bl_label = "Cycle Dual Form Preset Source"

    def execute(self, context):
        order = ["all", "defaults", "user"]
        current = context.scene.neox_dual_form_preset_source_filter
        try:
            next_value = order[(order.index(current) + 1) % len(order)]
        except ValueError:
            next_value = "all"
        context.scene.neox_dual_form_preset_source_filter = next_value
        self.report({"INFO"}, f"Preset source: {next_value}")
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Reload_Presets(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_reload_presets"
    bl_label = "Reload Dual Form Presets"

    def execute(self, context):
        items = _dual_form_preset_entries(context.scene.neox_dual_form_preset_source_filter)
        if items and items[0][0] != DUAL_FORM_PRESET_NONE:
            context.scene.neox_dual_form_preset_selector = items[0][0]
            self.report({"INFO"}, f"Reloaded {len(items)} preset(s)")
        else:
            self.report({"INFO"}, "No presets found")
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Open_Preset_Folder(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_open_preset_folder"
    bl_label = "Open Dual Form Preset Folder"

    def execute(self, context):
        source_filter = context.scene.neox_dual_form_preset_source_filter
        folders = [folder for _source, folder in _dual_form_preset_folders(source_filter)]
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.path_open(filepath=str(folder))
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Import_Preset(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_import_preset"
    bl_label = "Import Dual Form Trigger Preset"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        identifier = context.scene.neox_dual_form_preset_selector
        if identifier == DUAL_FORM_PRESET_NONE:
            self.report({"ERROR"}, "No dual form preset is selected")
            return {"CANCELLED"}

        preset_path = _dual_form_preset_path_from_identifier(identifier)
        if preset_path is None or not preset_path.is_file():
            self.report({"ERROR"}, "Selected dual form preset file was not found")
            return {"CANCELLED"}

        try:
            payload = _load_dual_form_preset(preset_path)
        except ValueError as exc:
            self.report({"ERROR"}, f"Invalid preset: {exc}")
            return {"CANCELLED"}

        preset_type = payload["type"]
        if preset_type == "trigger_list":
            added = _replace_dual_form_triggers(context, payload["data"])
            self.report({"INFO"}, f"Imported {added} trigger(s)")
        else:
            context.scene.neox_dual_form_regex_text = payload["data"]
            self.report({"INFO"}, "Imported regex preset")
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Export_Preset(bpy.types.Operator, ExportHelper):
    bl_idname = "idvmi_neox.dual_form_export_preset"
    bl_label = "Export Dual Form Trigger Preset"
    bl_options = {"REGISTER"}

    filename_ext = ".json"
    filter_glob: StringProperty(
        default="*.json",
        options={"HIDDEN"},
        maxlen=255,
    )

    def invoke(self, context, event):
        output_folder = _dual_form_user_preset_folder()
        output_folder.mkdir(parents=True, exist_ok=True)
        if not self.filepath:
            self.filepath = str(output_folder / "dual_form_trigger_preset.json")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        export_type = context.scene.neox_dual_form_preset_export_type
        output_path = Path(bpy.path.abspath(self.filepath))
        if output_path.suffix.lower() != ".json":
            output_path = output_path.with_suffix(".json")

        if export_type == "regex":
            regex_text = context.scene.neox_dual_form_regex_text.strip()
            if not regex_text:
                self.report({"ERROR"}, "Regex textbox is empty")
                return {"CANCELLED"}
            _write_dual_form_preset(output_path, "regex", regex_text)
            self.report({"INFO"}, f"Exported regex preset: {output_path}")
            return {"FINISHED"}

        trigger_names = [
            item.name.strip()
            for item in context.scene.neox_dual_form_triggers
            if item.name.strip()
        ]
        if not trigger_names:
            self.report({"ERROR"}, "Trigger list is empty")
            return {"CANCELLED"}

        _write_dual_form_preset(output_path, "trigger_list", trigger_names)
        self.report({"INFO"}, f"Exported trigger list preset: {output_path}")
        return {"FINISHED"}


class IDVMI_OT_Build_Dual_Form_Skin(bpy.types.Operator):
    bl_idname = "idvmi_neox.build_dual_form_skin"
    bl_label = "Build Dual Form Skin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        build_start = time.perf_counter()
        build_context = DualFormBuildContext()
        self.report({"INFO"}, "Dual form build started: 00:00:000")

        try:
            main_gim_path = Path(bpy.path.abspath(context.scene.neox_dual_form_main_gim))
            dual_gim_path = Path(bpy.path.abspath(context.scene.neox_dual_form_dual_gim))
            if not main_gim_path.is_file() or not dual_gim_path.is_file():
                raise ValueError("Select both main and dual form .gim files")

            from .remote_import import _make_asset_index

            asset_index = _make_asset_index()
            main_root, main_binary = _load_gim(main_gim_path)
            dual_root, dual_binary = _load_gim(dual_gim_path)
            main_anim_ref = _file_value(main_root, "AnimationConfigFile")
            dual_anim_ref = _file_value(dual_root, "AnimationConfigFile")
            main_skeleton, main_skeleton_remote, main_skeleton_local = _resolve_reference_from_gim(
                main_gim_path,
                _file_value(main_root, "SkeletonFile"),
            )
            dual_skeleton, dual_skeleton_remote, dual_skeleton_local = _resolve_reference_from_gim(
                dual_gim_path,
                _file_value(dual_root, "SkeletonFile"),
            )
            main_skeleton_bones, main_skeleton_root_index = _load_skeleton_bone_names(
                asset_index,
                main_skeleton,
                main_skeleton_remote,
                main_skeleton_local,
            )
            dual_skeleton_bones, dual_skeleton_root_index = _load_skeleton_bone_names(
                asset_index,
                dual_skeleton,
                dual_skeleton_remote,
                dual_skeleton_local,
            )

            main_anim_root, main_source_ref, _main_remote, _main_local = _load_animconfig(
                asset_index,
                main_gim_path,
                main_anim_ref,
            )
            dual_anim_root, dual_source_ref, _dual_remote, _dual_local = _load_animconfig(
                asset_index,
                dual_gim_path,
                dual_anim_ref,
            )

            trigger_names = {
                item.name.strip().lower()
                for item in context.scene.neox_dual_form_triggers
                if item.name.strip()
            }
            if not trigger_names:
                raise ValueError("Add at least one dual form trigger animation")

            wrapper_gim_path = _build_dual_form_wrapper(
                asset_index,
                main_gim_path,
                main_root,
                main_binary,
                main_anim_root,
                main_source_ref,
                dual_gim_path,
                self,
                build_context,
            )
            main_output_animconfig = main_gim_path.with_suffix(".animconfig")
            dual_output_animconfig = dual_gim_path.with_suffix(".animconfig")
            _rewrite_animconfig(
                asset_index,
                main_anim_root,
                main_source_ref,
                main_output_animconfig,
                main_skeleton,
                main_skeleton_bones,
                main_skeleton_root_index,
                trigger_names,
                active_on_trigger=False,
                build_context=build_context,
            )
            _rewrite_animconfig(
                asset_index,
                dual_anim_root,
                dual_source_ref,
                dual_output_animconfig,
                dual_skeleton,
                dual_skeleton_bones,
                dual_skeleton_root_index,
                trigger_names,
                active_on_trigger=True,
                build_context=build_context,
            )

            _set_file_value(main_root, "AnimationConfigFile", main_output_animconfig.name)
            _set_file_value(dual_root, "AnimationConfigFile", dual_output_animconfig.name)
            _write_gim(main_gim_path, main_root, main_binary)
            _write_gim(dual_gim_path, dual_root, dual_binary)
            build_context.finish()
            context.scene.neox_dual_form_animation_name_cache.clear()
            context.scene.neox_dual_form_animation_name_cache_source = ""
        except Exception as exc:
            build_context.shutdown(cancel_pending=True)
            elapsed = _format_elapsed_min_sec_ms(time.perf_counter() - build_start)
            self.report({"ERROR"}, f"Dual form build failed after {elapsed}: {exc}")
            return {"CANCELLED"}

        elapsed = _format_elapsed_min_sec_ms(time.perf_counter() - build_start)
        self.report({"INFO"}, build_context.summary())
        self.report({"INFO"}, f"Dual form build finished: {elapsed}")
        self.report({"INFO"}, f"Dual form skin built: {wrapper_gim_path}")
        return {"FINISHED"}

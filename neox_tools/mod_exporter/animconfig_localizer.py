from __future__ import annotations

import json
import posixpath
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET


def _normalize_relative_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    normalized = posixpath.normpath(normalized).replace("\\", "/")
    if normalized in ("", "."):
        raise ValueError("Path is empty")
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise ValueError(f"Path escapes the mod folder: {path}")
    return normalized


def _normalize_asset_path(path: str) -> str:
    return path.strip().replace("\\", "/").strip("/")


def _replace_extension(path: str, extension: str) -> str:
    stem = path.rsplit(".", 1)[0]
    return f"{stem}{extension}"


def _resolve_asset_reference(asset_index, reference: str, base_asset_path: str) -> str:
    normalized = _normalize_asset_path(reference)
    try:
        asset_index.parse(normalized)
        return normalized
    except Exception:
        base_dir = posixpath.dirname(_normalize_asset_path(base_asset_path))
        return posixpath.normpath(posixpath.join(base_dir, normalized)).replace("\\", "/")


def _safe_output_path(output_root: Path, relative_path: str) -> Path:
    normalized = _normalize_relative_path(relative_path)
    return output_root.joinpath(*PurePosixPath(normalized).parts)


def _documents_res_relative(path: Path) -> str:
    parts = path.resolve(strict=False).parts
    lower_parts = [part.lower() for part in parts]
    for index in range(len(lower_parts) - 1):
        if lower_parts[index] == "documents" and lower_parts[index + 1] == "res":
            return "/".join(parts[index + 2 :]).replace("\\", "/")
    raise ValueError(f"Path is not inside Documents/res: {path}")


def _relative_documents_res_reference(from_path: Path, to_documents_res_path: str) -> str:
    from_relative = _documents_res_relative(from_path)
    from_dir = posixpath.dirname(from_relative)
    target = _normalize_asset_path(to_documents_res_path)
    return posixpath.relpath(target, start=from_dir).replace("\\", "/")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _load_animation_json(data: bytes, asset_path: str) -> dict:
    try:
        return json.loads(data.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Animation asset is not valid JSON: {asset_path}") from exc


def _write_animation_json(path: Path, animation_data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(animation_data, file, indent="\t")
        file.write("\n")


def _write_animconfig(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="\t")
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=False)


def _localize_animation_dependencies(
    asset_index,
    animation_data: dict,
    animation_asset_path: str,
    animation_local_rel: str,
    export_path: Path,
) -> None:
    header = animation_data.get("_FileHeader")
    if not isinstance(header, dict):
        return

    dependencies = header.get("Dependices")
    if not isinstance(dependencies, list):
        return

    localized_dependencies: list[str] = []
    animation_local_dir = posixpath.dirname(animation_local_rel)

    for dependency in dependencies:
        if not isinstance(dependency, str) or not dependency.strip():
            localized_dependencies.append(dependency)
            continue

        dependency_asset_path = _resolve_asset_reference(
            asset_index,
            dependency,
            animation_asset_path,
        )
        dependency_asset_path = _replace_extension(dependency_asset_path, ".cpdanimation")
        dependency_data = asset_index.extract(dependency_asset_path).data
        dependency_local_rel = posixpath.join(
            animation_local_dir,
            posixpath.basename(dependency_asset_path),
        )
        dependency_output_path = _safe_output_path(export_path, dependency_local_rel)
        _write_bytes(dependency_output_path, dependency_data)
        localized_dependencies.append(_documents_res_relative(dependency_output_path))

    header["Dependices"] = localized_dependencies


def localize_remote_animconfig(
    animconfig_asset_path: str,
    export_path: str | Path,
    asset_index=None,
    skip_unnecessary_files: bool = True,
) -> str:
    if asset_index is None:
        from ..remote_import import _make_asset_index

        asset_index = _make_asset_index()
    export_root = Path(export_path)
    animconfig_asset_path = _normalize_asset_path(animconfig_asset_path)
    if not animconfig_asset_path.lower().endswith(".animconfig"):
        raise ValueError("Customize Remote File path must point to a .animconfig asset")

    animconfig_data = asset_index.extract(animconfig_asset_path).data
    try:
        animconfig_root = ET.fromstring(animconfig_data.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Remote animconfig is not valid XML: {animconfig_asset_path}") from exc

    animconfig_output_path = export_root / "main.animconfig"

    for animation in animconfig_root.findall("./AnimationList/Animation"):
        file_name = animation.attrib.get("FileName", "").strip()
        if not file_name:
            continue

        animation_local_rel = _normalize_relative_path(file_name)
        animation_asset_path = _resolve_asset_reference(
            asset_index,
            animation_local_rel,
            animconfig_asset_path,
        )
        if skip_unnecessary_files:
            animation.attrib["FileName"] = _relative_documents_res_reference(
                animconfig_output_path,
                animation_asset_path,
            )
            continue

        animation_data = asset_index.extract(animation_asset_path).data
        animation_json = _load_animation_json(animation_data, animation_asset_path)
        _localize_animation_dependencies(
            asset_index,
            animation_json,
            animation_asset_path,
            animation_local_rel,
            export_root,
        )

        animation_output_path = _safe_output_path(export_root, animation_local_rel)
        _write_animation_json(animation_output_path, animation_json)
        animation.attrib["FileName"] = animation_local_rel

    _write_animconfig(animconfig_output_path, animconfig_root)
    return _documents_res_relative(animconfig_output_path)

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .utils.game_dir_detector import check_game_directory
from .utils.gim_crypt import decode_gim_file


NEOX_BINARY_MAGIC = b"\xC1\x59\x41\x0D"
TEXTURE_TAGS = ("Tex0", "TexNormal", "TexMetal")
_CRYPTOGRAPHY_INSTALL_ATTEMPTED = False


@dataclass
class RemoteMaterialPackage:
    gim_asset_path: str
    mesh_asset_path: str
    mesh_data: bytes
    materials: list[dict[str, str]] = field(default_factory=list)
    submesh_mtl_indices: dict[int, int] = field(default_factory=dict)
    sockets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_remote_material_package(gim_asset_path: str, cache_root: Path) -> RemoteMaterialPackage:
    asset_index = _make_asset_index()
    gim_asset_path = _normalize_asset_path(gim_asset_path)
    if not gim_asset_path.lower().endswith(".gim"):
        raise ValueError("Remote import path must point to a .gim file")

    mesh_asset_path = _replace_extension(gim_asset_path, ".mesh")
    mtg_asset_path = _replace_extension(gim_asset_path, ".mtg")

    mesh_data = asset_index.extract(mesh_asset_path).data
    gim_root = _xml_root_from_bytes(asset_index.extract(gim_asset_path).data, ".gim", gim_asset_path)
    mtg_root = _xml_root_from_bytes(asset_index.extract(mtg_asset_path).data, ".mtg", mtg_asset_path)

    warnings: list[str] = []
    mtl_paths = _mtl_paths_from_mtg(mtg_root)
    materials: list[dict[str, str]] = []
    for mtl_path in mtl_paths:
        resolved_mtl_path = _resolve_reference(asset_index, mtl_path, mtg_asset_path)
        try:
            mtl_data = asset_index.extract(resolved_mtl_path).data
        except Exception as exc:
            warnings.append(f"MTL not found: {resolved_mtl_path} ({exc})")
            materials.append({})
            continue

        try:
            mtl_root = _xml_root_from_bytes(mtl_data, ".mtl", resolved_mtl_path)
        except Exception as exc:
            warnings.append(f"MTL could not be decoded: {resolved_mtl_path} ({exc})")
            materials.append({})
            continue

        material_textures: dict[str, str] = {}
        for tag, texture_reference in _texture_paths_from_mtl(mtl_root).items():
            try:
                extracted_texture = _extract_texture_with_fallback(
                    asset_index,
                    texture_reference,
                    resolved_mtl_path,
                )
            except Exception as exc:
                warnings.append(f"Texture not found: {texture_reference} ({exc})")
                continue

            material_textures[tag] = _write_asset_cache(
                cache_root,
                extracted_texture.request.archive.prefix,
                extracted_texture.request.normalized_path,
                extracted_texture.data,
            )

        materials.append(material_textures)

    return RemoteMaterialPackage(
        gim_asset_path=gim_asset_path,
        mesh_asset_path=mesh_asset_path,
        mesh_data=mesh_data,
        materials=materials,
        submesh_mtl_indices=_submesh_mtl_indices_from_gim(gim_root),
        sockets=_socket_data_from_gim(gim_root),
        warnings=warnings,
    )


def extract_remote_asset_to_cache(asset_path: str, cache_root: Path) -> str:
    asset_index = _make_asset_index()
    normalized = _normalize_asset_path(asset_path)
    extracted = asset_index.extract(normalized)
    return _write_asset_cache(
        cache_root,
        extracted.request.archive.prefix,
        extracted.request.normalized_path,
        extracted.data,
    )


def _make_asset_index():
    _add_vendor_path()

    if not _asset_lookup_has_aes():
        _install_cryptography_into_vendor()
        _reload_asset_lookup_modules()
        if not _asset_lookup_has_aes():
            raise RuntimeError(
                "cryptography was installed, but the built-in AES decoder is still unavailable"
            )

    from .asset_lookup.assets import AssetIndex

    return AssetIndex(_detect_game_root())


def _vendor_root() -> Path:
    return Path(__file__).resolve().parents[1] / "_vendor"


def _add_vendor_path() -> None:
    vendor = _vendor_root()
    vendor_text = str(vendor)
    if vendor_text not in sys.path:
        sys.path.insert(0, vendor_text)


def _asset_lookup_has_aes() -> bool:
    try:
        from .asset_lookup.filefinder.archive import codecs
    except Exception:
        return False
    return bool(getattr(codecs, "HAS_AES", False))


def _blender_python_executable() -> Path:
    candidates = []
    for root in (Path(sys.prefix), Path(sys.exec_prefix)):
        candidates.append(root / "bin" / "python.exe")
        candidates.append(root / "bin" / "python")

    executable = Path(sys.executable)
    if executable.name.lower().startswith("python"):
        candidates.append(executable)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find Blender's bundled Python executable for pip install"
    )


def _run_pip_command(args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    python_exe = _blender_python_executable()
    return subprocess.run(
        [str(python_exe), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _install_cryptography_into_vendor() -> None:
    global _CRYPTOGRAPHY_INSTALL_ATTEMPTED

    _add_vendor_path()
    try:
        import cryptography  # noqa: F401
        return
    except Exception:
        pass

    if _CRYPTOGRAPHY_INSTALL_ATTEMPTED:
        raise RuntimeError(
            "cryptography is still unavailable after the previous install attempt"
        )
    _CRYPTOGRAPHY_INSTALL_ATTEMPTED = True

    vendor = _vendor_root()
    vendor.mkdir(parents=True, exist_ok=True)

    ensurepip = _run_pip_command(["-m", "ensurepip", "--upgrade"], timeout=120)
    pip_install = _run_pip_command(
        [
            "-m",
            "pip",
            "install",
            "--upgrade",
            "cryptography",
            "--target",
            str(vendor),
        ],
        timeout=240,
    )

    if pip_install.returncode != 0:
        details = (pip_install.stderr or pip_install.stdout).strip()
        ensurepip_details = (ensurepip.stderr or ensurepip.stdout).strip()
        raise RuntimeError(
            "Failed to install cryptography into addon _vendor. "
            f"ensurepip={ensurepip.returncode}; pip={pip_install.returncode}; "
            f"details={details or ensurepip_details}"
        )

    importlib.invalidate_caches()
    _add_vendor_path()
    try:
        import cryptography  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            f"cryptography installed to {vendor}, but Blender could not import it: {exc}"
        ) from exc


def _reload_asset_lookup_modules() -> None:
    for module_name in (
        f"{__package__}.asset_lookup.filefinder.archive.codecs",
        f"{__package__}.asset_lookup.filefinder.archive.idx_wpk",
        f"{__package__}.asset_lookup.filefinder.core.paths",
        f"{__package__}.asset_lookup.assets",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            importlib.reload(module)


def _detect_game_root() -> Path:
    configured = os.environ.get("IDVMI_GAME_ROOT", "").strip()
    if configured:
        candidate = Path(configured)
        if (candidate / "res").is_dir() and (candidate / "Documents" / "res").is_dir():
            return candidate
        raise FileNotFoundError(
            f"IDVMI_GAME_ROOT does not point to an Identity V game root: {configured}"
        )

    detected = check_game_directory()
    candidates: list[Path] = []
    if detected:
        detected_path = Path(detected)
        candidates.extend([detected_path, *detected_path.parents])

    for candidate in candidates:
        if (candidate / "res").is_dir() and (candidate / "Documents" / "res").is_dir():
            return candidate
    raise FileNotFoundError("Identity V game root could not be detected")


def _normalize_asset_path(asset_path: str) -> str:
    return asset_path.strip().replace("\\", "/").strip("/")


def _replace_extension(asset_path: str, extension: str) -> str:
    stem = asset_path.rsplit(".", 1)[0]
    return f"{stem}{extension}"


def _xml_root_from_bytes(data: bytes, suffix: str, asset_path: str) -> ET.Element:
    if data.startswith(NEOX_BINARY_MAGIC):
        with tempfile.TemporaryDirectory(prefix="idvmi_neox_xml_") as temp_dir:
            temp_path = Path(temp_dir) / f"asset{suffix}"
            temp_path.write_bytes(data)
            return decode_gim_file(str(temp_path))

    try:
        text = data.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        head = data[:16].hex(" ")
        hint = _payload_decode_hint()
        raise UnicodeDecodeError(
            exc.encoding,
            exc.object,
            exc.start,
            exc.end,
            f"{exc.reason}; asset={asset_path}; size={len(data)}; head={head}; {hint}",
        ) from exc

    if not text.startswith("<"):
        head = data[:16].hex(" ")
        hint = _payload_decode_hint()
        raise ValueError(
            f"Expected NeoX XML for {asset_path}, got size={len(data)} head={head}; {hint}"
        )
    return ET.fromstring(text)


def _payload_decode_hint() -> str:
    try:
        from .asset_lookup.filefinder.archive import codecs
    except Exception:
        return "Built-in asset payload decoder status could not be inspected"

    if not getattr(codecs, "HAS_AES", False):
        return "Blender Python is missing cryptography, so AES-packed assets cannot be decoded"
    return "asset payload was not decoded to NeoX XML"


def _mtl_paths_from_mtg(root: ET.Element) -> list[str]:
    material_group = root.find(".//MaterialGroup")
    if material_group is None:
        return []

    indexed_paths: list[tuple[int, str]] = []
    fallback_paths: list[str] = []
    for child in material_group:
        path = child.attrib.get("Path", "").strip()
        if not path:
            continue
        match = re.fullmatch(r"Material_(\d+)", child.tag)
        if match:
            indexed_paths.append((int(match.group(1)), path))
        else:
            fallback_paths.append(path)

    if indexed_paths:
        return [path for _index, path in sorted(indexed_paths)]
    return fallback_paths


def _texture_paths_from_mtl(root: ET.Element) -> dict[str, str]:
    textures: dict[str, str] = {}
    for tag in TEXTURE_TAGS:
        element = root.find(f".//ParamTable/{tag}")
        if element is None:
            element = root.find(f".//{tag}")
        if element is None:
            continue
        value = element.attrib.get("Value", "").strip()
        if value:
            textures[tag] = value
    return textures


def _submesh_mtl_indices_from_gim(root: ET.Element) -> dict[int, int]:
    submesh = root.find(".//SubMesh")
    if submesh is None:
        return {}

    result: dict[int, int] = {}
    for child in submesh:
        match = re.fullmatch(r"Sub(\d+)", child.tag)
        if not match:
            continue
        mtl_idx = child.attrib.get("MtlIdx")
        if mtl_idx is None:
            continue
        try:
            result[int(match.group(1))] = int(mtl_idx)
        except ValueError:
            continue
    return result


def _float_list(value: str) -> list[float]:
    if not value:
        return []
    try:
        return [float(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError:
        return []


def _socket_data_from_gim(root: ET.Element) -> list[dict]:
    socket_objects = root.find(".//SocketObject")
    if socket_objects is None:
        return []

    sockets: list[dict] = []
    for element in socket_objects:
        attributes = dict(element.attrib)
        binding_bone = attributes.get("BindingBone", "").strip()

        sockets.append(
            {
                "tag": element.tag,
                "name": attributes.get("Name", ""),
                "parent_type": "bone" if binding_bone else "armature_origin",
                "binding_bone": binding_bone,
                "bind_type": attributes.get("BindType", ""),
                "binding_flag": attributes.get("BindingFlag", ""),
                "local_position": _float_list(attributes.get("LocalPosition", "")),
                "local_rotation_xyzw": _float_list(attributes.get("LocalRotation", "")),
                "local_scale": _float_list(attributes.get("LocalScale", "")),
                "attributes": attributes,
                "objects": [
                    {
                        "tag": child.tag,
                        "attributes": dict(child.attrib),
                    }
                    for child in element
                ],
            }
        )

    return sockets


def _resolve_reference(asset_index, reference: str, base_asset_path: str) -> str:
    normalized = _normalize_asset_path(reference)
    try:
        asset_index.parse(normalized)
        return normalized
    except Exception:
        base_dir = posixpath.dirname(_normalize_asset_path(base_asset_path))
        return posixpath.normpath(posixpath.join(base_dir, normalized)).replace("\\", "/")


def _tga_to_dds(asset_path: str) -> str:
    if asset_path.lower().endswith(".tga"):
        return f"{asset_path[:-4]}.dds"
    return asset_path


def _extract_texture_with_fallback(asset_index, texture_reference: str, base_asset_path: str):
    dds_asset_path = _resolve_reference(
        asset_index,
        _tga_to_dds(texture_reference),
        base_asset_path,
    )
    try:
        return asset_index.extract(dds_asset_path)
    except Exception as dds_error:
        original_asset_path = _resolve_reference(
            asset_index,
            texture_reference,
            base_asset_path,
        )
        if original_asset_path == dds_asset_path:
            raise
        try:
            return asset_index.extract(original_asset_path)
        except Exception as tga_error:
            raise FileNotFoundError(
                f"tried {dds_asset_path} ({dds_error}); "
                f"then {original_asset_path} ({tga_error})"
            ) from tga_error


def _write_asset_cache(cache_root: Path, archive_prefix: str, normalized_path: str, data: bytes) -> str:
    parts = [part for part in f"{archive_prefix}/{normalized_path}".split("/") if part]
    output_path = cache_root.joinpath(*parts)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return str(output_path)

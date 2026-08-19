from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .utils.game_root import get_game_root
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
    submesh_names: dict[int, str] = field(default_factory=dict)
    sockets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def import_object_name_from_package(package: RemoteMaterialPackage) -> str:
    asset_path = package.gim_asset_path or package.mesh_asset_path
    normalized = str(asset_path).replace("\\", "/")
    return posixpath.basename(normalized).rsplit(".", 1)[0]


@dataclass(frozen=True)
class _ResolvedLocalOrRemoteFile:
    path: Path
    identifier: str
    data: bytes | None = None


def build_remote_material_package(asset_path: str, cache_root: Path) -> RemoteMaterialPackage:
    asset_index = _make_asset_index()
    input_asset_path = _normalize_asset_path(asset_path)
    suffix = _asset_suffix(input_asset_path)
    if suffix not in {".gim", ".mesh", ".mtg"}:
        raise ValueError("Remote import path must point to a .gim, .mesh, or .mtg file")

    warnings: list[str] = []
    gim_asset_path = _replace_extension(input_asset_path, ".gim")
    mtg_asset_path = _replace_extension(input_asset_path, ".mtg")
    mesh_asset_path = _replace_extension(input_asset_path, ".mesh")
    gim_root = None

    try:
        gim_root = _xml_root_from_bytes(asset_index.extract(gim_asset_path).data, ".gim", gim_asset_path)
    except Exception as exc:
        warnings.append(f"GIM not found or could not be decoded; shader settings skipped: {gim_asset_path} ({exc})")
        gim_asset_path = ""

    if gim_root is not None:
        mesh_reference = gim_root.attrib.get("Mesh", "").strip()
        if mesh_reference:
            mesh_asset_path = _resolve_reference(asset_index, mesh_reference, gim_asset_path)
        mtg_path_from_gim = _mtg_path_from_gim(gim_root, gim_asset_path, asset_index)
        if mtg_path_from_gim:
            mtg_asset_path = mtg_path_from_gim

    mesh_data = asset_index.extract(mesh_asset_path).data

    materials: list[dict[str, str]] = []
    submesh_mtl_indices: dict[int, int] = {}
    submesh_names: dict[int, str] = {}
    sockets: list[dict] = []
    if gim_root is not None:
        submesh_mtl_indices = _submesh_mtl_indices_from_gim(gim_root)
        submesh_names = _submesh_names_from_gim(gim_root)
        sockets = _socket_data_from_gim(gim_root)
        try:
            mtg_root = _xml_root_from_bytes(asset_index.extract(mtg_asset_path).data, ".mtg", mtg_asset_path)
        except Exception as exc:
            warnings.append(f"MTG not found or could not be decoded; shader settings skipped: {mtg_asset_path} ({exc})")
        else:
            materials = _remote_materials_from_mtg(asset_index, mtg_root, mtg_asset_path, cache_root, warnings)

    return RemoteMaterialPackage(
        gim_asset_path=gim_asset_path,
        mesh_asset_path=mesh_asset_path,
        mesh_data=mesh_data,
        materials=materials,
        submesh_mtl_indices=submesh_mtl_indices,
        submesh_names=submesh_names,
        sockets=sockets,
        warnings=warnings,
    )


def build_local_material_package(input_path: str | Path, cache_root: Path) -> RemoteMaterialPackage:
    source_path = Path(input_path).resolve(strict=False)
    suffix = source_path.suffix.lower()
    if suffix not in {".gim", ".mesh", ".mtg"}:
        raise ValueError("Local import path must point to a .gim, .mesh, or .mtg file")

    warnings: list[str] = []
    selected_gim_path = _replace_local_extension(source_path, ".gim")
    selected_mtg_path = _replace_local_extension(source_path, ".mtg")
    selected_mesh_path = _replace_local_extension(source_path, ".mesh")

    if suffix == ".gim":
        selected_gim_path = source_path
    elif suffix == ".mtg":
        selected_mtg_path = source_path
    else:
        selected_mesh_path = source_path

    gim_root = None
    gim_identifier = ""
    mesh_identifier = str(selected_mesh_path)
    mesh_data = None
    resolver = _LocalReferenceResolver(cache_root)

    if selected_gim_path.is_file():
        gim_identifier = str(selected_gim_path)
        gim_root = _xml_root_from_bytes(selected_gim_path.read_bytes(), ".gim", str(selected_gim_path))
        inferred_gim_reference = _asset_reference_for_local_path(selected_gim_path)
        mesh_reference = gim_root.attrib.get("Mesh", "").strip()
        if mesh_reference:
            resolved_mesh = resolver.resolve_file(
                mesh_reference,
                selected_gim_path,
                inferred_gim_reference,
            )
            mesh_data = _resolved_file_bytes(resolved_mesh)
            mesh_identifier = resolved_mesh.identifier
        elif selected_mesh_path.is_file():
            mesh_data = selected_mesh_path.read_bytes()
            mesh_identifier = str(selected_mesh_path)
    else:
        warnings.append(f"GIM file was not found; shader settings skipped: {selected_gim_path}")

    if mesh_data is None:
        if not selected_mesh_path.is_file():
            inferred_mesh_reference = _asset_reference_for_local_path(selected_mesh_path)
            if inferred_mesh_reference:
                resolved_mesh = _resolve_local_or_remote_file(
                    inferred_mesh_reference,
                    source_path,
                    cache_root,
                )
                mesh_data = _resolved_file_bytes(resolved_mesh)
                mesh_identifier = resolved_mesh.identifier
            else:
                raise FileNotFoundError(f"Mesh file was not found: {selected_mesh_path}")
        else:
            mesh_data = selected_mesh_path.read_bytes()
            mesh_identifier = str(selected_mesh_path)

    materials: list[dict[str, str]] = []
    submesh_mtl_indices: dict[int, int] = {}
    submesh_names: dict[int, str] = {}
    sockets: list[dict] = []
    selected_mtg = _ResolvedLocalOrRemoteFile(selected_mtg_path, str(selected_mtg_path))
    if gim_root is not None:
        submesh_mtl_indices = _submesh_mtl_indices_from_gim(gim_root)
        submesh_names = _submesh_names_from_gim(gim_root)
        sockets = _socket_data_from_gim(gim_root)
        mtg_reference = _mtg_path_from_gim(gim_root, gim_identifier, None)
        if mtg_reference:
            selected_mtg = resolver.resolve_file(
                mtg_reference,
                selected_gim_path,
                _asset_reference_for_local_path(selected_gim_path),
            )

        if not selected_mtg.path.is_file():
            warnings.append(f"MTG file was not found; shader settings skipped: {selected_mtg.path}")
        else:
            mtg_root = _xml_root_from_bytes(
                _resolved_file_bytes(selected_mtg),
                ".mtg",
                selected_mtg.identifier,
            )
            materials = _local_materials_from_mtg(mtg_root, selected_mtg, cache_root, warnings)

    return RemoteMaterialPackage(
        gim_asset_path=gim_identifier,
        mesh_asset_path=mesh_identifier,
        mesh_data=mesh_data,
        materials=materials,
        submesh_mtl_indices=submesh_mtl_indices,
        submesh_names=submesh_names,
        sockets=sockets,
        warnings=warnings,
    )


def _remote_materials_from_mtg(asset_index, mtg_root: ET.Element, mtg_asset_path: str, cache_root: Path, warnings: list[str]) -> list[dict[str, str]]:
    materials: list[dict[str, str]] = []
    for mtl_path in _mtl_paths_from_mtg(mtg_root):
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

    return materials


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
    return Path(__file__).resolve().parents[1] / "builtin" / "_vendor"


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
    import_error = _cryptography_aes_import_error()
    if import_error is None:
        return

    if _CRYPTOGRAPHY_INSTALL_ATTEMPTED:
        raise RuntimeError(
            "cryptography AES support is still unavailable after the previous "
            f"install attempt: {import_error}"
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
            "Failed to install cryptography into addon builtin/_vendor. "
            f"ensurepip={ensurepip.returncode}; pip={pip_install.returncode}; "
            f"details={details or ensurepip_details}"
        )

    importlib.invalidate_caches()
    _add_vendor_path()
    import_error = _cryptography_aes_import_error()
    if import_error is not None:
        raise RuntimeError(
            f"cryptography installed to {vendor}, but Blender could not import AES support: {import_error}"
        )


def _cryptography_aes_import_error() -> str | None:
    try:
        from cryptography.hazmat.backends import default_backend as _aes_default_backend  # noqa: F401
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


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
    return get_game_root(prompt_on_missing=True)


def _normalize_asset_path(asset_path: str) -> str:
    return asset_path.strip().replace("\\", "/").strip("/")


def _asset_suffix(asset_path: str) -> str:
    normalized = _normalize_asset_path(asset_path)
    return Path(normalized).suffix.lower()


def _replace_extension(asset_path: str, extension: str) -> str:
    stem = asset_path.rsplit(".", 1)[0]
    return f"{stem}{extension}"


def _replace_local_extension(path: Path, extension: str) -> Path:
    return path.with_suffix(extension)


def _mtg_path_from_gim(root: ET.Element, gim_path: str, asset_index) -> str:
    mtg_file = root.find(".//MtgFile")
    mtg_path = ""
    if mtg_file is not None:
        mtg_path = mtg_file.attrib.get("MtgPath", "").strip()
    if not mtg_path:
        return _replace_extension(gim_path, ".mtg")
    if asset_index is None:
        return mtg_path
    return _resolve_reference(asset_index, mtg_path, gim_path)


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


def _submesh_names_from_gim(root: ET.Element) -> dict[int, str]:
    submesh = root.find(".//SubMesh")
    if submesh is None:
        return {}

    result: dict[int, str] = {}
    for child in submesh:
        match = re.fullmatch(r"Sub(\d+)", child.tag)
        if not match:
            continue
        name = child.attrib.get("Name", "").strip()
        if name:
            result[int(match.group(1))] = name
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


def _local_reference_text(reference: str) -> str:
    return str(reference).strip().strip("\"'").replace("\\", "/")


def _path_from_slash_text(path_text: str) -> Path:
    return Path(*[part for part in path_text.split("/") if part])


def _documents_res_root_for(path: Path) -> Path | None:
    parts = path.resolve(strict=False).parts
    lower_parts = [part.lower() for part in parts]
    for index in range(len(lower_parts) - 1):
        if lower_parts[index] == "documents" and lower_parts[index + 1] == "res":
            return Path(*parts[: index + 2])
    return None


def _asset_output_root_for(path: Path) -> Path | None:
    roots = {"chr", "fx", "lut", "material", "shader", "scene"}
    parts = path.resolve(strict=False).parts
    for index, part in enumerate(parts):
        if part.lower() in roots:
            return Path(*parts[:index])
    return None


def _asset_reference_for_local_path(path: Path) -> str | None:
    asset_output_root = _asset_output_root_for(path)
    if asset_output_root is None:
        return None
    try:
        return path.resolve(strict=False).relative_to(asset_output_root).as_posix()
    except ValueError:
        return None


def _local_file_candidates(reference: str, base_file: Path) -> list[Path]:
    reference_text = _local_reference_text(reference)
    if not reference_text:
        return []

    candidates: list[Path] = []

    def add(candidate: Path) -> None:
        resolved = candidate.resolve(strict=False)
        if resolved not in candidates:
            candidates.append(resolved)

    reference_path = Path(reference_text)
    if reference_path.is_absolute():
        add(reference_path)

    relative_reference = _path_from_slash_text(reference_text)
    add(base_file.parent / relative_reference)

    documents_res_root = _documents_res_root_for(base_file)
    if documents_res_root is not None:
        add(documents_res_root / relative_reference)

    asset_output_root = _asset_output_root_for(base_file)
    if asset_output_root is not None:
        add(asset_output_root / relative_reference)

    return candidates


class _LocalReferenceResolver:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self._asset_index = None

    def _remote_asset_index(self):
        if self._asset_index is None:
            self._asset_index = _make_asset_index()
        return self._asset_index

    def _remote_base_identifier(self, base_file: Path, base_identifier: str | None) -> str | None:
        if base_identifier and not Path(_local_reference_text(base_identifier)).is_absolute():
            return _normalize_asset_path(base_identifier)
        return _asset_reference_for_local_path(base_file)

    def _write_remote_extract(self, extracted, identifier: str) -> _ResolvedLocalOrRemoteFile:
        path = _cache_output_path(
            self.cache_root,
            extracted.request.archive.prefix,
            extracted.request.normalized_path,
        )
        try:
            _write_asset_cache_to_path(path, extracted.data)
        except OSError:
            return _ResolvedLocalOrRemoteFile(path, identifier, extracted.data)
        return _ResolvedLocalOrRemoteFile(path, identifier, extracted.data)

    def _remote_reference_for(
        self,
        reference: str,
        base_file: Path,
        base_identifier: str | None,
    ) -> str | None:
        normalized = _normalize_asset_path(reference)
        if not normalized or Path(normalized).is_absolute():
            return None

        remote_base = self._remote_base_identifier(base_file, base_identifier)
        if remote_base:
            asset_index = self._remote_asset_index()
            return _resolve_reference(asset_index, normalized, remote_base)
        return normalized

    def resolve_file(
        self,
        reference: str,
        base_file: Path,
        base_identifier: str | None = None,
    ) -> _ResolvedLocalOrRemoteFile:
        remote_error: Exception | None = None
        try:
            remote_reference = self._remote_reference_for(reference, base_file, base_identifier)
            if remote_reference:
                asset_index = self._remote_asset_index()
                extracted = asset_index.extract(remote_reference)
                return self._write_remote_extract(extracted, remote_reference)
        except Exception as exc:
            remote_error = exc

        for candidate in _local_file_candidates(reference, base_file):
            if candidate.is_file():
                return _ResolvedLocalOrRemoteFile(candidate, str(candidate))

        normalized = _normalize_asset_path(reference)
        if Path(normalized).is_absolute():
            raise FileNotFoundError(f"Local file was not found: {reference}")

        if remote_error is not None:
            raise FileNotFoundError(
                f"Remote asset was not found and no local fallback matched: {reference} ({remote_error})"
            ) from remote_error

        raise FileNotFoundError(f"File was not found: {reference}")

    def resolve_texture(self, reference: str, base_file: Path, base_identifier: str) -> _ResolvedLocalOrRemoteFile:
        remote_error: Exception | None = None
        try:
            remote_base = self._remote_base_identifier(base_file, base_identifier)
            normalized = _normalize_asset_path(reference)
            if normalized and not Path(normalized).is_absolute() and remote_base:
                asset_index = self._remote_asset_index()
                extracted = _extract_texture_with_fallback(asset_index, reference, remote_base)
                return self._write_remote_extract(extracted, extracted.request.normalized_path)
        except Exception as exc:
            remote_error = exc

        candidates = [_tga_to_dds(reference), reference]
        seen: set[str] = set()
        for candidate_reference in candidates:
            if candidate_reference in seen:
                continue
            seen.add(candidate_reference)
            for candidate in _local_file_candidates(candidate_reference, base_file):
                if candidate.is_file():
                    return _ResolvedLocalOrRemoteFile(candidate, str(candidate))

        if remote_error is not None:
            raise FileNotFoundError(
                f"Remote texture was not found and no local fallback matched: {reference} ({remote_error})"
            ) from remote_error

        raise FileNotFoundError(f"Texture was not found: {reference}")


def _resolve_local_or_remote_file(reference: str, base_file: Path, cache_root: Path) -> _ResolvedLocalOrRemoteFile:
    return _LocalReferenceResolver(cache_root).resolve_file(reference, base_file)


def _resolved_file_bytes(resolved_file: _ResolvedLocalOrRemoteFile) -> bytes:
    if resolved_file.data is not None:
        return resolved_file.data
    return resolved_file.path.read_bytes()


def _local_materials_from_mtg(
    mtg_root: ET.Element,
    mtg_file: _ResolvedLocalOrRemoteFile,
    cache_root: Path,
    warnings: list[str],
) -> list[dict[str, str]]:
    resolver = _LocalReferenceResolver(cache_root)
    materials: list[dict[str, str]] = []
    for mtl_path in _mtl_paths_from_mtg(mtg_root):
        try:
            resolved_mtl = resolver.resolve_file(mtl_path, mtg_file.path, mtg_file.identifier)
        except Exception as exc:
            warnings.append(f"MTL not found: {mtl_path} ({exc})")
            materials.append({})
            continue

        try:
            mtl_root = _xml_root_from_bytes(
                _resolved_file_bytes(resolved_mtl),
                ".mtl",
                resolved_mtl.identifier,
            )
        except Exception as exc:
            warnings.append(f"MTL could not be decoded: {resolved_mtl.path} ({exc})")
            materials.append({})
            continue

        material_textures: dict[str, str] = {}
        for tag, texture_reference in _texture_paths_from_mtl(mtl_root).items():
            try:
                resolved_texture = resolver.resolve_texture(
                    texture_reference,
                    resolved_mtl.path,
                    resolved_mtl.identifier,
                )
            except Exception as exc:
                warnings.append(f"Texture not found: {texture_reference} ({exc})")
                continue

            material_textures[tag] = str(resolved_texture.path)

        materials.append(material_textures)

    return materials


def _write_asset_cache(cache_root: Path, archive_prefix: str, normalized_path: str, data: bytes) -> str:
    output_path = _cache_output_path(cache_root, archive_prefix, normalized_path)
    _write_asset_cache_to_path(output_path, data)
    return str(output_path)


def _cache_output_path(cache_root: Path, archive_prefix: str, normalized_path: str) -> Path:
    parts = [part for part in f"{archive_prefix}/{normalized_path}".split("/") if part]
    return cache_root.joinpath(*parts)


def _write_asset_cache_to_path(output_path: Path, data: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)

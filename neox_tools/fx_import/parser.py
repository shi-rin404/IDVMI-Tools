from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
import json
from pathlib import Path
from typing import Any

from .logger import FxImportLogger
from .neox_bpse import BPSEError, from_bytes as bpse_from_bytes


SKIP_CURVE_RECURSION_KEYS = {
    "_affectors",
    "_children",
    "_emitters",
    "_render_objs",
    "_sprite_obj",
    "_sub_systems",
}


class BpseParseError(ValueError):
    """Raised when a BPSE JSON file cannot be interpreted as an FX system."""


@dataclass
class BpseCurve:
    key: str
    times: list[float] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)
    constant: Any = None
    interpolation: str = "linear"
    raw: dict[str, Any] | None = None


@dataclass
class BpseRenderObject:
    name: str
    texture_path: str | None = None
    template_path: str | None = None
    material_name: str | None = None
    transparent_mode: int | None = None
    render_priority_offset: float | None = None
    curves: dict[str, BpseCurve] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BpseEmitter:
    name: str
    sys_uid: int | None = None
    start_time: float = 0.0
    abs_age: float | None = None
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    render_objects: list[BpseRenderObject] = field(default_factory=list)
    curves: dict[str, BpseCurve] = field(default_factory=dict)
    affectors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BpseTrack:
    track_type: int | None = None
    start_pos: tuple[float, float, float] | None = None
    end_pos: tuple[float, float, float] | None = None
    end_angle: float | None = None
    time_len: float | None = None
    scale_curves: list[BpseCurve] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BpseSubsystem:
    name: str
    sys_uid: int | None = None
    start_time: float = 0.0
    abs_age: float | None = None
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_euler: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    emitters: list[BpseEmitter] = field(default_factory=list)
    render_objects: list[BpseRenderObject] = field(default_factory=list)
    children: list["BpseSubsystem"] = field(default_factory=list)
    tracks: list[BpseTrack] = field(default_factory=list)
    curves: dict[str, BpseCurve] = field(default_factory=dict)
    affectors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BpseFxScene:
    name: str
    source_path: Path
    abs_age: float | None
    loop: bool
    root: BpseSubsystem
    textures: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _logged_function(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        logger = kwargs.get("logger")
        if logger is None:
            return function(*args, **kwargs)
        with logger.scope(function.__name__, summary=_argument_summary(args, kwargs)):
            return function(*args, **kwargs)
    return wrapper


@_logged_function
def parse_bpse_fx(path: str | Path, *, logger: FxImportLogger | None = None) -> BpseFxScene:
    source_path = Path(path)
    payload = _load_fx_payload(source_path, logger=logger)

    if not isinstance(payload, dict):
        raise BpseParseError("BPSE/PSE root must be an object")

    body = payload.get("Body")
    if not isinstance(body, dict):
        raise BpseParseError("BPSE JSON is missing Body object")

    root_component = body.get("_component")
    if not isinstance(root_component, dict):
        raise BpseParseError("BPSE JSON is missing Body._component")

    if root_component.get("TypeName") != "ParticleComponentSystem":
        raise BpseParseError(
            "Body._component must be ParticleComponentSystem, "
            f"got {root_component.get('TypeName')!r}"
        )

    warnings: list[str] = []
    root_system = _parse_subsystem(
        wrapper=body,
        component=root_component,
        fallback_name=source_path.stem,
        warnings=warnings,
        logger=logger,
    )
    _attach_unassigned_render_objects(root_system, root_component, warnings, logger=logger)
    textures = sorted(_collect_string_values(root_system, "texture_path"))
    templates = sorted(_collect_string_values(root_system, "template_path"))
    if logger is not None:
        logger.write(
            "PARSED BPSE FX",
            name=source_path.name,
            textures=len(textures),
            templates=len(templates),
            warnings=len(warnings),
        )
    return BpseFxScene(
        name=_fx_scene_name(source_path),
        source_path=source_path,
        abs_age=_to_float(body.get("AbsAge")),
        loop=bool(body.get("_loop", False)),
        root=root_system,
        textures=textures,
        templates=templates,
        warnings=warnings,
    )


def _load_fx_payload(source_path: Path, *, logger: FxImportLogger | None = None) -> Any:
    suffix = source_path.suffix.lower()
    if suffix == ".bpse":
        try:
            document = bpse_from_bytes(source_path.read_bytes())
        except (OSError, EOFError, BPSEError, UnicodeDecodeError) as exc:
            raise BpseParseError(f"Could not read binary BPSE file: {exc}") from exc
        if logger is not None:
            logger.write("LOADED binary BPSE", filepath=source_path, magic=document.magic.hex())
        return document.root

    if suffix not in {".json", ".pse"}:
        raise BpseParseError(f"Expected .json, .pse, or .bpse file: {source_path}")

    try:
        with source_path.open("r", encoding="utf-8-sig") as json_file:
            payload = json.load(json_file)
    except json.JSONDecodeError as exc:
        raise BpseParseError(f"Invalid BPSE/PSE JSON: {exc}") from exc
    except OSError as exc:
        raise BpseParseError(f"Could not read BPSE/PSE JSON: {exc}") from exc

    if _looks_like_exported_bpse_document(payload):
        if logger is not None:
            logger.write("LOADED exported BPSE JSON wrapper", filepath=source_path)
        return payload["root"]
    if logger is not None:
        logger.write("LOADED FX JSON", filepath=source_path)
    return payload


def _looks_like_exported_bpse_document(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("magic"), str)
        and "root" in payload
    )


def _fx_scene_name(source_path: Path) -> str:
    name = source_path.name
    if name.lower().endswith(".bpse.json"):
        return name[:-5]
    return source_path.stem


@_logged_function
def _parse_subsystem(
    *,
    wrapper: dict[str, Any],
    component: dict[str, Any],
    fallback_name: str,
    warnings: list[str],
    logger: FxImportLogger | None = None,
) -> BpseSubsystem:
    subsystem = BpseSubsystem(
        name=_name_from(component, fallback_name),
        sys_uid=_to_int(component.get("_sys_uid")),
        start_time=_to_float(wrapper.get("StartTime"), 0.0) or 0.0,
        abs_age=_to_float(wrapper.get("AbsAge")),
        position=_vector3(wrapper.get("Position"), fallback=component.get("Position")),
        rotation_euler=_vector3(wrapper.get("Euler"), fallback=component.get("Euler")),
        scale=_vector3(wrapper.get("Scale"), fallback=component.get("Scale"), default=(1.0, 1.0, 1.0)),
        curves=_curves_from_mapping(component),
        affectors=_component_type_names(component.get("_affectors")),
        tracks=_tracks_from_wrapper(wrapper),
        raw=component,
    )

    for index, emitter_wrapper in enumerate(_iter_dicts(component.get("_emitters"))):
        emitter_component = emitter_wrapper.get("_component")
        if not isinstance(emitter_component, dict):
            warnings.append(f"Emitter {index} skipped: missing _component")
            continue
        subsystem.emitters.append(
            _parse_emitter(
                wrapper=emitter_wrapper,
                component=emitter_component,
                fallback_name=f"{subsystem.name}_Emitter_{index}",
                warnings=warnings,
                logger=logger,
            )
        )

    for index, render_obj in enumerate(_render_objects_from_component(component)):
        subsystem.render_objects.append(
            _parse_render_object(
                render_obj,
                fallback_name=f"{subsystem.name}_Render_{index}",
                logger=logger,
            )
        )

    child_wrappers = [
        ("Subsystem", child_wrapper)
        for child_wrapper in _iter_dicts(component.get("_sub_systems"))
    ] + [
        ("Child", child_wrapper)
        for child_wrapper in _iter_dicts(component.get("_children"))
    ]
    for index, (child_kind, child_wrapper) in enumerate(child_wrappers):
        child_component = child_wrapper.get("_component")
        if not isinstance(child_component, dict):
            warnings.append(f"{child_kind} {index} skipped: missing _component")
            continue
        subsystem.children.append(
            _parse_subsystem(
                wrapper=child_wrapper,
                component=child_component,
                fallback_name=f"{subsystem.name}_{child_kind}_{index}",
                warnings=warnings,
                logger=logger,
            )
        )

    if logger is not None:
        logger.write(
            "SUBSYSTEM parsed",
            name=subsystem.name,
            emitters=len(subsystem.emitters),
            render_objects=len(subsystem.render_objects),
            children=len(subsystem.children),
            tracks=len(subsystem.tracks),
        )
    return subsystem


@_logged_function
def _parse_emitter(
    *,
    wrapper: dict[str, Any],
    component: dict[str, Any],
    fallback_name: str,
    warnings: list[str],
    logger: FxImportLogger | None = None,
) -> BpseEmitter:
    emitter = BpseEmitter(
        name=_name_from(component, fallback_name),
        sys_uid=_to_int(component.get("_sys_uid")),
        start_time=_to_float(wrapper.get("StartTime"), 0.0) or 0.0,
        abs_age=_to_float(wrapper.get("AbsAge")),
        position=_vector3(component.get("Position")),
        scale=_vector3(component.get("Scale"), default=(1.0, 1.0, 1.0)),
        curves=_curves_from_mapping(component),
        affectors=_component_type_names(component.get("_affectors")),
        raw=component,
    )

    for index, render_obj in enumerate(_find_dicts_in_lists(component, "_render_objs")):
        emitter.render_objects.append(
            _parse_render_object(
                render_obj,
                fallback_name=f"{emitter.name}_Render_{index}",
                logger=logger,
            )
        )
    if logger is not None:
        logger.write(
            "EMITTER parsed",
            name=emitter.name,
            render_objects=len(emitter.render_objects),
            curves=len(emitter.curves),
        )
    return emitter


@_logged_function
def _parse_render_object(
    data: dict[str, Any],
    fallback_name: str,
    *,
    logger: FxImportLogger | None = None,
) -> BpseRenderObject:
    render_effect = _first_dict(data.get("_render_effect"))
    effect_component = _first_dict(render_effect.get("_component") if render_effect else None)
    render_object = BpseRenderObject(
        name=_name_from(data, fallback_name),
        texture_path=_find_first_string(data, "_texture"),
        template_path=_find_first_string(data, "_template"),
        material_name=_string_or_none(data.get("_mtl_name")),
        transparent_mode=_to_int(_find_first_value(data, "_transparent_mode")),
        render_priority_offset=_to_float(data.get("_render_priority_offset")),
        curves=_curves_from_mapping(data) | _curves_from_mapping(effect_component or {}),
        raw=data,
    )
    if logger is not None:
        logger.write(
            "RENDER_OBJECT parsed",
            name=render_object.name,
            texture=render_object.texture_path,
            template=render_object.template_path,
            curves=len(render_object.curves),
        )
    return render_object


def _curves_from_mapping(mapping: dict[str, Any]) -> dict[str, BpseCurve]:
    curves: dict[str, BpseCurve] = {}
    for key, value in _iter_curve_candidates(mapping):
        curve = _curve_from_value(key, value)
        if curve is not None:
            curves.setdefault(key, curve)
    return curves


def _iter_curve_candidates(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            if key in SKIP_CURVE_RECURSION_KEYS:
                continue
            yield from _iter_curve_candidates(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_curve_candidates(child)


def _curve_from_value(key: str, value: Any) -> BpseCurve | None:
    if not isinstance(value, dict) or "_curve_new" not in value:
        return None

    curve_data = value.get("_curve_data")
    if not isinstance(curve_data, dict):
        return BpseCurve(key=key, constant=_extract_scalar_or_vector(value), raw=value)

    times = [_to_float(item, 0.0) or 0.0 for item in curve_data.get("Times", [])]
    values = [
        _extract_scalar_or_vector(item, default=curve_data.get("ConstVal"))
        for item in curve_data.get("Values", [])
    ]
    return BpseCurve(
        key=key,
        times=times,
        values=values,
        constant=_extract_scalar_or_vector(value, default=curve_data.get("ConstVal")),
        interpolation="linear",
        raw=value,
    )


def _extract_scalar_or_vector(value: Any, default: Any = None) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list) and value and all(isinstance(item, (int, float)) for item in value):
        return [float(item) for item in value]
    if isinstance(value, dict):
        for key in ("Value", "_value", "value"):
            if key in value:
                return _extract_scalar_or_vector(value[key], default=default)
        values = value.get("Values")
        if isinstance(values, list) and len(values) == 1:
            return _extract_scalar_or_vector(values[0], default=default)
    if default is not None and default is not value:
        return _extract_scalar_or_vector(default)
    return None


def _component_type_names(value: Any) -> list[str]:
    names: list[str] = []
    for item in _iter_dicts(value):
        component = item.get("_component")
        if isinstance(component, dict):
            type_name = component.get("TypeName")
            if isinstance(type_name, str):
                names.append(type_name)
    return names


def _find_first_string(value: Any, key: str) -> str | None:
    if isinstance(value, dict):
        found = _string_or_none(value.get(key))
        if found is not None:
            return found
        for child in value.values():
            found = _find_first_string(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_string(child, key)
            if found is not None:
                return found
    return None


def _find_first_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_first_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_value(child, key)
            if found is not None:
                return found
    return None


def _find_dicts_in_lists(value: Any, key: str):
    if isinstance(value, dict):
        child = value.get(key)
        if isinstance(child, list):
            for item in child:
                if isinstance(item, dict):
                    yield item
        for nested in value.values():
            yield from _find_dicts_in_lists(nested, key)
    elif isinstance(value, list):
        for nested in value:
            yield from _find_dicts_in_lists(nested, key)


def _render_objects_from_component(component: dict[str, Any]):
    sprite_obj = component.get("_sprite_obj")
    if isinstance(sprite_obj, dict):
        yield from _find_dicts_in_lists(sprite_obj, "_render_objs")
    direct_render_objects = component.get("_render_objs")
    if isinstance(direct_render_objects, list):
        for item in direct_render_objects:
            if isinstance(item, dict):
                yield item


def _tracks_from_wrapper(wrapper: dict[str, Any]) -> list[BpseTrack]:
    track_group = wrapper.get("TrackAnimationGroup")
    if not isinstance(track_group, dict):
        return []

    tracks: list[BpseTrack] = []
    for track_wrapper in _iter_dicts(track_group.get("m_tracks")):
        track_data = track_wrapper.get("_track")
        if not isinstance(track_data, dict):
            continue

        scale_curves = []
        scale_key_frame = track_data.get("m_scale_key_frame")
        if isinstance(scale_key_frame, dict):
            for index, curve_data in enumerate(_iter_dicts(scale_key_frame.get("DAs"))):
                curve = _curve_from_value(f"m_scale_key_frame_{index}", curve_data)
                if curve is not None:
                    scale_curves.append(curve)

        tracks.append(
            BpseTrack(
                track_type=_to_int(track_wrapper.get("_type")),
                start_pos=_vector3_or_none(track_data.get("m_start_pos")),
                end_pos=_vector3_or_none(track_data.get("m_end_pos")),
                end_angle=_to_float(track_data.get("m_end_angle")),
                time_len=_to_float(track_data.get("m_time_len")),
                scale_curves=scale_curves,
                raw=track_data,
            )
        )
    return tracks


@_logged_function
def _attach_unassigned_render_objects(
    root_system: BpseSubsystem,
    root_component: dict[str, Any],
    warnings: list[str],
    *,
    logger: FxImportLogger | None = None,
) -> None:
    assigned_raw_ids = {id(render_object.raw) for render_object in _iter_render_objects(root_system)}
    unassigned_count = 0
    for render_obj in _find_dicts_in_lists(root_component, "_render_objs"):
        if id(render_obj) in assigned_raw_ids:
            continue
        root_system.render_objects.append(
            _parse_render_object(
                render_obj,
                fallback_name=f"{root_system.name}_UnassignedRender_{unassigned_count}",
                logger=logger,
            )
        )
        assigned_raw_ids.add(id(render_obj))
        unassigned_count += 1
    if unassigned_count:
        warnings.append(
            f"{unassigned_count} FX render object(s) were outside the parsed subsystem hierarchy and were attached to the root"
        )
    if logger is not None:
        logger.write("UNASSIGNED render objects attached", count=unassigned_count)


def _collect_string_values(root: BpseSubsystem, attribute: str) -> set[str]:
    values: set[str] = set()
    for subsystem in _iter_subsystems(root):
        for render_object in subsystem.render_objects:
            value = getattr(render_object, attribute)
            if value:
                values.add(value)
        for emitter in subsystem.emitters:
            for render_object in emitter.render_objects:
                value = getattr(render_object, attribute)
                if value:
                    values.add(value)
    return values


def _iter_render_objects(root: BpseSubsystem):
    for subsystem in _iter_subsystems(root):
        yield from subsystem.render_objects
        for emitter in subsystem.emitters:
            yield from emitter.render_objects


def _iter_subsystems(root: BpseSubsystem):
    yield root
    for child in root.children:
        yield from _iter_subsystems(child)


def _iter_dicts(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def _first_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _name_from(mapping: dict[str, Any], fallback: str) -> str:
    name = mapping.get("_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return fallback


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _to_float(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _vector3(
    value: Any,
    *,
    fallback: Any = None,
    default: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    parsed = _vector3_or_none(value)
    if parsed is not None:
        return parsed
    parsed = _vector3_or_none(fallback)
    if parsed is not None:
        return parsed
    return default


def _vector3_or_none(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _argument_summary(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    if "fallback_name" in kwargs:
        return str(kwargs["fallback_name"])
    if args:
        first = args[0]
        if isinstance(first, (str, Path)):
            return str(first)
        if isinstance(first, BpseSubsystem):
            return first.name
    if "path" in kwargs:
        return str(kwargs["path"])
    return ""

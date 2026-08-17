from __future__ import annotations

from functools import wraps
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

from .asset_resolver import FxAssetResolver
from .logger import FxImportLogger
from .parser import BpseCurve, BpseEmitter, BpseFxScene, BpseRenderObject, BpseSubsystem, BpseTrack

ANIMATED_CURVES = {
    "_age",
    "_anim_speed",
    "_height",
    "_hw_ratio",
    "_spin_base",
    "_spin_speed",
    "_start_delay",
    "_uni_dimension",
    "_uni_scale",
    "_width",
    "_value",
}

RUNTIME_METADATA_CURVES = {
    "_da_emission_height",
    "_da_emit_uneven_delta_time",
    "_emission_radius",
    "_emit_rate",
    "_end_frame",
    "_first_frame_id",
    "_plane_dir_degree",
    "_ref_thick_len",
    "_speed_max",
    "_speed_min",
    "_start_frame",
}

MATERIAL_METADATA_CURVES = {
    "_rot_angle",
    "_rot_center_x",
    "_rot_center_y",
    "_u_mirror",
    "_u_offset",
    "_u_scale",
    "_v_mirror",
    "_v_offset",
    "_v_scale",
}

SUPPORTED_CURVES = ANIMATED_CURVES | RUNTIME_METADATA_CURVES | MATERIAL_METADATA_CURVES

METADATA_CURVES = RUNTIME_METADATA_CURVES | MATERIAL_METADATA_CURVES


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
def import_bpse_fx(
    fx_scene: BpseFxScene,
    context,
    operator,
    *,
    logger: FxImportLogger | None = None,
) -> bpy.types.Collection:
    cache_root = Path(__file__).resolve().parents[1] / "remote_import_cache" / "fx"
    resolver = FxAssetResolver(cache_root, logger=logger)
    collection = _new_child_collection(context.scene.collection, _unique_name(f"FX_{fx_scene.name}"), logger=logger)
    collection["NeoX:FX:Source"] = str(fx_scene.source_path)
    collection["NeoX:FX:Loop"] = bool(fx_scene.loop)
    if fx_scene.abs_age is not None:
        collection["NeoX:FX:AbsAge"] = float(fx_scene.abs_age)

    warnings: list[str] = list(fx_scene.warnings)
    root_obj = _create_empty(fx_scene.root.name, collection, "PLAIN_AXES", logger=logger)
    root_obj["NeoX:FX:Kind"] = "Root"
    root_obj["NeoX:FX:Source"] = str(fx_scene.source_path)
    _apply_subsystem(fx_scene.root, root_obj, collection, context, operator, resolver, warnings, logger=logger)

    for warning in resolver.warnings:
        _append_unique(warnings, warning)
    unsupported_curve_keys = _unsupported_curve_keys(fx_scene.root)
    if unsupported_curve_keys:
        warnings.append(
            "Unsupported FX curve keys stored as metadata: "
            + ", ".join(unsupported_curve_keys[:16])
        )
    _report_limited(operator, "WARNING", warnings, "FX import warning")
    if logger is not None:
        logger.write("BUILDER complete", collection=collection.name, warnings=len(warnings))
    return collection


@_logged_function
def _apply_subsystem(
    subsystem: BpseSubsystem,
    parent_obj,
    collection,
    context,
    operator,
    resolver: FxAssetResolver,
    warnings: list[str],
    *,
    logger: FxImportLogger | None = None,
) -> None:
    _apply_common_metadata(parent_obj, "Subsystem", subsystem.name, subsystem.sys_uid, subsystem.start_time, subsystem.abs_age)
    _set_transform(parent_obj, subsystem.position, subsystem.rotation_euler, subsystem.scale)
    _store_raw_summary(parent_obj, subsystem.curves, subsystem.affectors, warnings)
    _animate_object(
        parent_obj,
        subsystem.start_time,
        subsystem.abs_age,
        subsystem.curves,
        context.scene,
        warnings,
        tracks=subsystem.tracks,
        logger=logger,
    )

    for render_object in subsystem.render_objects:
        _create_render_proxy(render_object, parent_obj, collection, context, operator, resolver, warnings, logger=logger)

    for emitter in subsystem.emitters:
        emitter_obj = _create_empty(emitter.name, collection, "SINGLE_ARROW", logger=logger)
        emitter_obj.parent = parent_obj
        _apply_emitter(emitter, emitter_obj, collection, context, operator, resolver, warnings, logger=logger)

    for child in subsystem.children:
        child_obj = _create_empty(child.name, collection, "PLAIN_AXES", logger=logger)
        child_obj.parent = parent_obj
        _apply_subsystem(child, child_obj, collection, context, operator, resolver, warnings, logger=logger)


@_logged_function
def _apply_emitter(
    emitter: BpseEmitter,
    emitter_obj,
    collection,
    context,
    operator,
    resolver: FxAssetResolver,
    warnings: list[str],
    *,
    logger: FxImportLogger | None = None,
) -> None:
    _apply_common_metadata(emitter_obj, "Emitter", emitter.name, emitter.sys_uid, emitter.start_time, emitter.abs_age)
    _set_transform(emitter_obj, emitter.position, (0.0, 0.0, 0.0), emitter.scale)
    _store_raw_summary(emitter_obj, emitter.curves, emitter.affectors, warnings)
    _animate_object(emitter_obj, emitter.start_time, emitter.abs_age, emitter.curves, context.scene, warnings, logger=logger)

    for render_object in emitter.render_objects:
        _create_render_proxy(render_object, emitter_obj, collection, context, operator, resolver, warnings, logger=logger)


@_logged_function
def _create_render_proxy(
    render_object: BpseRenderObject,
    parent_obj,
    collection,
    context,
    operator,
    resolver: FxAssetResolver,
    warnings: list[str],
    *,
    logger: FxImportLogger | None = None,
) -> None:
    proxy = _create_plane(render_object.name, collection, logger=logger)
    proxy.parent = parent_obj
    proxy["NeoX:FX:Kind"] = "RenderObject"
    proxy["NeoX:FX:Name"] = render_object.name
    if render_object.texture_path:
        proxy["NeoX:FX:Texture"] = render_object.texture_path
    if render_object.template_path:
        proxy["NeoX:FX:Template"] = render_object.template_path
    if render_object.transparent_mode is not None:
        proxy["NeoX:FX:TransparentMode"] = int(render_object.transparent_mode)
    if render_object.render_priority_offset is not None:
        proxy["NeoX:FX:RenderPriorityOffset"] = float(render_object.render_priority_offset)

    texture_file = resolver.resolve_texture(render_object.texture_path) if render_object.texture_path else None
    if texture_file is None and render_object.texture_path:
        warnings.append(f"Using placeholder FX material for missing texture: {render_object.texture_path}")
    proxy.data.materials.append(_material_for_render_object(render_object, texture_file, warnings, logger=logger))

    if render_object.template_path:
        if resolver.can_resolve_gim(render_object.template_path):
            _try_import_gim_template(render_object, proxy, collection, operator, warnings, logger=logger)
        else:
            warnings.append(f"Using plane placeholder for missing GIM template: {render_object.template_path}")

    _store_curve_names(proxy, render_object.curves, warnings)
    _animate_object(proxy, 0.0, None, render_object.curves, context.scene, warnings, logger=logger)


@_logged_function
def _try_import_gim_template(
    render_object: BpseRenderObject,
    parent_obj,
    target_collection,
    operator,
    warnings: list[str],
    *,
    logger: FxImportLogger | None = None,
) -> None:
    try:
        from ..remote_import import build_remote_material_package
        from ..import_ops import _parse_neox_mesh, import_per_material
        from io import BytesIO

        before_names = set(bpy.data.objects.keys())
        cache_root = Path(__file__).resolve().parents[1] / "remote_import_cache" / "fx_gim"
        package = build_remote_material_package(render_object.template_path, cache_root)
        model = _parse_neox_mesh(BytesIO(package.mesh_data), operator)
        if not model:
            warnings.append(f"FX GIM template mesh could not be decoded: {render_object.template_path}")
            return
        imported = import_per_material(model, f"FX_{render_object.name}_GIM", operator, package)
        if not imported:
            warnings.append(f"FX GIM template import failed: {render_object.template_path}")
            return
        imported_count = 0
        for object_name in set(bpy.data.objects.keys()) - before_names:
            imported_obj = bpy.data.objects.get(object_name)
            if imported_obj is not None:
                imported_obj.parent = parent_obj
                imported_obj["NeoX:FX:TemplateSource"] = render_object.template_path
                _move_object_to_collection(imported_obj, target_collection)
                imported_count += 1
        if logger is not None:
            logger.write(
                "FX GIM template imported",
                template=render_object.template_path,
                object_count=imported_count,
                target_collection=target_collection.name,
            )
    except SystemExit as exc:
        if logger is not None:
            logger.exception("SYSTEM_EXIT blocked in FX GIM template import", exc)
        warnings.append(
            f"FX GIM template import skipped because the NeoX decoder tried to exit Blender: "
            f"{render_object.template_path}"
        )
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        if logger is not None:
            logger.exception("FX GIM template import skipped", exc)
        warnings.append(f"FX GIM template import skipped: {render_object.template_path} ({exc})")


@_logged_function
def _material_for_render_object(
    render_object: BpseRenderObject,
    texture_file: str | None,
    warnings: list[str],
    *,
    logger: FxImportLogger | None = None,
):
    material_name = _unique_name(f"FX_{render_object.name}_Material")
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    material.blend_method = "BLEND"
    if hasattr(material, "show_transparent_back"):
        material.show_transparent_back = True
    if hasattr(material, "use_screen_refraction"):
        material.use_screen_refraction = False

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Alpha"].default_value = 0.55 if texture_file is None else 1.0
        bsdf.inputs["Base Color"].default_value = (0.9, 0.65, 0.2, 0.55) if texture_file is None else (1.0, 1.0, 1.0, 1.0)

    if texture_file:
        texture = nodes.new("ShaderNodeTexImage")
        texture.location = (-400, 0)
        try:
            texture.image = bpy.data.images.load(texture_file, check_existing=True)
        except TypeError:
            try:
                texture.image = bpy.data.images.load(texture_file)
            except RuntimeError as exc:
                warnings.append(f"FX texture could not be loaded by Blender: {texture_file} ({exc})")
                return material
        except RuntimeError as exc:
            warnings.append(f"FX texture could not be loaded by Blender: {texture_file} ({exc})")
            return material
        if texture.image is not None:
            texture.image.alpha_mode = "CHANNEL_PACKED"
        if bsdf is not None:
            links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
            links.new(texture.outputs["Alpha"], bsdf.inputs["Alpha"])
    return material


@_logged_function
def _animate_object(
    obj,
    start_time: float,
    abs_age: float | None,
    curves: dict[str, BpseCurve],
    scene,
    warnings: list[str],
    *,
    tracks: list[BpseTrack] | None = None,
    logger: FxImportLogger | None = None,
) -> None:
    fps = scene.render.fps / scene.render.fps_base
    start_frame = max(1, int(round(start_time * fps)) + 1)
    end_frame = int(round((start_time + abs_age) * fps)) + 1 if abs_age is not None else None

    obj.hide_viewport = True
    obj.hide_render = True
    obj.keyframe_insert("hide_viewport", frame=max(1, start_frame - 1))
    obj.keyframe_insert("hide_render", frame=max(1, start_frame - 1))
    obj.hide_viewport = False
    obj.hide_render = False
    obj.keyframe_insert("hide_viewport", frame=start_frame)
    obj.keyframe_insert("hide_render", frame=start_frame)
    if end_frame is not None and end_frame > start_frame:
        obj.keyframe_insert("hide_viewport", frame=end_frame)
        obj.keyframe_insert("hide_render", frame=end_frame)
        obj.hide_viewport = True
        obj.hide_render = True
        obj.keyframe_insert("hide_viewport", frame=end_frame + 1)
        obj.keyframe_insert("hide_render", frame=end_frame + 1)
        obj.hide_viewport = False
        obj.hide_render = False

    scale_curve = _first_curve(curves, "_uni_scale", "_uni_dimension", "_width", "_height")
    if scale_curve is not None:
        _insert_scalar_scale_keys(obj, scale_curve, start_frame, fps, warnings)

    spin_curve = _first_curve(curves, "_spin_base", "_spin_speed")
    if spin_curve is not None:
        _insert_spin_keys(obj, spin_curve, start_frame, fps, warnings)

    for track in tracks or []:
        _apply_track_animation(obj, track, start_frame, fps, abs_age, warnings)


def _insert_scalar_scale_keys(obj, curve: BpseCurve, start_frame: int, fps: float, warnings: list[str]) -> None:
    samples = _curve_samples(curve, warnings)
    if not samples:
        return
    for time_value, sample in samples:
        scalar = _as_float(sample)
        if scalar is None:
            continue
        obj.scale = (scalar, scalar, scalar)
        obj.keyframe_insert("scale", frame=start_frame + int(round(time_value * fps)))


def _insert_spin_keys(obj, curve: BpseCurve, start_frame: int, fps: float, warnings: list[str]) -> None:
    samples = _curve_samples(curve, warnings)
    if not samples:
        return
    for time_value, sample in samples:
        angle_degrees = _as_float(sample)
        if angle_degrees is None:
            continue
        obj.rotation_euler[2] = angle_degrees * 0.017453292519943295
        obj.keyframe_insert("rotation_euler", frame=start_frame + int(round(time_value * fps)))


def _apply_track_animation(
    obj,
    track: BpseTrack,
    start_frame: int,
    fps: float,
    abs_age: float | None,
    warnings: list[str],
) -> None:
    duration = track.time_len if track.time_len is not None else abs_age
    if duration is None:
        duration = 1.0
        warnings.append(f"{obj.name} has FX track without m_time_len; using 1 second preview duration")
    end_frame = start_frame + max(1, int(round(duration * fps)))

    if track.track_type == 1 and track.end_pos is not None:
        base_location = obj.location.copy()
        start_offset = _game_vector_to_blender(track.start_pos) if track.start_pos is not None else Vector((0.0, 0.0, 0.0))
        end_offset = _game_vector_to_blender(track.end_pos)
        obj.location = base_location + start_offset
        obj.keyframe_insert("location", frame=start_frame)
        obj.location = base_location + end_offset
        obj.keyframe_insert("location", frame=end_frame)
        warnings.append(f"Track type 1 on {obj.name} imported as linear position approximation")
        return

    if track.track_type == 2 and track.end_angle is not None:
        start_rotation = obj.rotation_euler.copy()
        obj.keyframe_insert("rotation_euler", frame=start_frame)
        obj.rotation_euler = start_rotation
        obj.rotation_euler[2] += math.radians(track.end_angle)
        obj.keyframe_insert("rotation_euler", frame=end_frame)
        warnings.append(f"Track type 2 on {obj.name} imported as Z rotation approximation")
        return

    if track.track_type == 4 and track.scale_curves:
        _insert_vector_scale_keys(obj, track.scale_curves, start_frame, fps, warnings)
        warnings.append(f"Track type 4 on {obj.name} imported as scale keyframe approximation")
        return

    warnings.append(f"Unsupported FX track type on {obj.name}: {track.track_type}")


def _insert_vector_scale_keys(obj, curves: list[BpseCurve], start_frame: int, fps: float, warnings: list[str]) -> None:
    axis_samples = [_curve_samples(curve, warnings) for curve in curves[:3]]
    if not axis_samples:
        return
    times = sorted({time_value for samples in axis_samples for time_value, _sample in samples})
    for time_value in times:
        scale_values = []
        for axis, samples in enumerate(axis_samples):
            sample = _sample_at_or_before(samples, time_value)
            scalar = _as_float(sample)
            scale_values.append(scalar if scalar is not None else obj.scale[axis])
        while len(scale_values) < 3:
            scale_values.append(scale_values[-1] if scale_values else 1.0)
        obj.scale = tuple(scale_values[:3])
        obj.keyframe_insert("scale", frame=start_frame + int(round(time_value * fps)))


def _sample_at_or_before(samples: list[tuple[float, object]], time_value: float):
    previous = samples[0][1] if samples else None
    for sample_time, sample in samples:
        if sample_time > time_value:
            break
        previous = sample
    return previous


def _curve_samples(curve: BpseCurve, warnings: list[str]) -> list[tuple[float, object]]:
    if curve.times and curve.values and len(curve.times) == len(curve.values):
        warnings.append(f"Curve {curve.key} imported with linear keyframe approximation")
        return list(zip(curve.times, curve.values))
    if curve.constant is not None:
        return [(0.0, curve.constant)]
    return []


def _apply_common_metadata(obj, kind: str, name: str, sys_uid: int | None, start_time: float, abs_age: float | None) -> None:
    obj["NeoX:FX:Kind"] = kind
    obj["NeoX:FX:Name"] = name
    obj["NeoX:FX:StartTime"] = float(start_time)
    if abs_age is not None:
        obj["NeoX:FX:AbsAge"] = float(abs_age)
    if sys_uid is not None:
        obj["NeoX:FX:SysUid"] = int(sys_uid)


def _store_raw_summary(obj, curves: dict[str, BpseCurve], affectors: list[str], warnings: list[str]) -> None:
    _store_curve_names(obj, curves, warnings)
    if affectors:
        obj["NeoX:FX:Affectors"] = json.dumps(affectors, separators=(",", ":"))
        warnings.append(f"{obj.name} has unsupported FX affectors stored as metadata: {', '.join(sorted(set(affectors)))}")


def _store_curve_names(obj, curves: dict[str, BpseCurve], warnings: list[str]) -> None:
    if not curves:
        return
    obj["NeoX:FX:Curves"] = json.dumps(sorted(curves), separators=(",", ":"))
    metadata_curves = sorted(key for key in curves if key in METADATA_CURVES)
    if metadata_curves:
        obj["NeoX:FX:MetadataCurves"] = json.dumps(metadata_curves, separators=(",", ":"))
    runtime_metadata = sorted(key for key in curves if key in RUNTIME_METADATA_CURVES)
    if runtime_metadata:
        obj["NeoX:FX:RuntimeMetadataCurves"] = json.dumps(runtime_metadata, separators=(",", ":"))
    material_metadata = sorted(key for key in curves if key in MATERIAL_METADATA_CURVES)
    if material_metadata:
        obj["NeoX:FX:MaterialMetadataCurves"] = json.dumps(material_metadata, separators=(",", ":"))
    for key in sorted(curves):
        obj[f"NeoX:FX:Curve:{key}"] = json.dumps(
            _curve_metadata(curves[key]),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def _curve_metadata(curve: BpseCurve) -> dict[str, object]:
    metadata: dict[str, object] = {
        "interpolation": curve.interpolation,
        "constant": curve.constant,
    }
    if curve.times:
        metadata["times"] = curve.times
    if curve.values:
        metadata["values"] = curve.values
    if isinstance(curve.raw, dict):
        type_name = curve.raw.get("TypeName")
        if type_name is not None:
            metadata["type_name"] = str(type_name)
        if "_curve_new" in curve.raw:
            metadata["curve_new"] = bool(curve.raw.get("_curve_new"))
    return metadata


def _unsupported_curve_keys(root: BpseSubsystem) -> list[str]:
    unsupported: set[str] = set()
    for subsystem in _iter_subsystems(root):
        unsupported.update(key for key in subsystem.curves if key not in SUPPORTED_CURVES)
        for emitter in subsystem.emitters:
            unsupported.update(key for key in emitter.curves if key not in SUPPORTED_CURVES)
            for render_object in emitter.render_objects:
                unsupported.update(key for key in render_object.curves if key not in SUPPORTED_CURVES)
        for render_object in subsystem.render_objects:
            unsupported.update(key for key in render_object.curves if key not in SUPPORTED_CURVES)
    return sorted(unsupported)


def _iter_subsystems(root: BpseSubsystem):
    yield root
    for child in root.children:
        yield from _iter_subsystems(child)


def _set_transform(
    obj,
    position: tuple[float, float, float],
    rotation_euler: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> None:
    obj.location = _game_vector_to_blender(position)
    obj.rotation_euler = _game_euler_to_blender(rotation_euler)
    obj.scale = scale


def _game_vector_to_blender(value: tuple[float, float, float]) -> Vector:
    return Vector((value[0], -value[2], value[1]))


def _game_euler_to_blender(value: tuple[float, float, float]):
    return (math.radians(value[0]), math.radians(-value[2]), math.radians(value[1]))


@_logged_function
def _create_empty(
    name: str,
    collection,
    display_type: str,
    *,
    logger: FxImportLogger | None = None,
):
    obj = bpy.data.objects.new(_unique_name(name), None)
    obj.empty_display_type = display_type
    obj.empty_display_size = 0.5
    collection.objects.link(obj)
    return obj


@_logged_function
def _create_plane(
    name: str,
    collection,
    *,
    logger: FxImportLogger | None = None,
):
    mesh = bpy.data.meshes.new(_unique_name(f"{name}_Mesh"))
    vertices = [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)]
    faces = [(0, 1, 2, 3)]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(_unique_name(name), mesh)
    collection.objects.link(obj)
    return obj


@_logged_function
def _new_child_collection(
    parent_collection,
    name: str,
    *,
    logger: FxImportLogger | None = None,
):
    collection = bpy.data.collections.new(name)
    parent_collection.children.link(collection)
    return collection


def _move_object_to_collection(obj, target_collection) -> None:
    if target_collection.objects.get(obj.name) is None:
        target_collection.objects.link(obj)
    for collection in list(obj.users_collection):
        if collection != target_collection:
            collection.objects.unlink(obj)


def _first_curve(curves: dict[str, BpseCurve], *keys: str) -> BpseCurve | None:
    for key in keys:
        curve = curves.get(key)
        if curve is not None:
            return curve
    return None


def _as_float(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list) and value:
        try:
            return float(value[0])
        except (TypeError, ValueError):
            return None
    return None


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _report_limited(operator, level: str, messages: list[str], prefix: str) -> None:
    unique_messages = []
    for message in messages:
        if message not in unique_messages:
            unique_messages.append(message)
    for message in unique_messages[:12]:
        operator.report({level}, message)
    remaining = len(unique_messages) - 12
    if remaining > 0:
        operator.report({level}, f"{remaining} more {prefix}(s); see object metadata for details")


def _unique_name(base: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in str(base)).strip()
    return safe or "FX"


def _argument_summary(args: tuple, kwargs: dict) -> str:
    if args:
        first = args[0]
        if isinstance(first, BpseFxScene):
            return first.name
        if isinstance(first, BpseSubsystem):
            return first.name
        if isinstance(first, BpseEmitter):
            return first.name
        if isinstance(first, BpseRenderObject):
            return first.name
        if hasattr(first, "name"):
            return str(first.name)
        if isinstance(first, str):
            return first
    if "name" in kwargs:
        return str(kwargs["name"])
    return ""

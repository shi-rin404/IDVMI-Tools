from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from .blender_builder import import_bpse_fx
from .logger import FxImportLogger
from .parser import BpseParseError, parse_bpse_fx


SUPPORTED_FX_EXTENSIONS = {".json", ".pse", ".bpse"}


class IDVMI_OT_Import_Neox_FX(bpy.types.Operator, ImportHelper):
    bl_idname = "idvmi_neox.import_fx"
    bl_label = "Import NeoX FX"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".bpse"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(
        default="*.json;*.pse;*.bpse",
        options={"HIDDEN"},
        maxlen=255,
    )

    def invoke(self, context, event):
        self.filter_glob = "*.json;*.pse;*.bpse"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        fx_path = Path(bpy.path.abspath(self.filepath or context.scene.neox_fx_selector))
        log_path = Path(__file__).resolve().parent / "fx_import_debug.log"
        logger = FxImportLogger(log_path)
        logger.write("START FX import operator", filepath=fx_path)
        if fx_path.suffix.lower() not in SUPPORTED_FX_EXTENSIONS:
            logger.write("CANCEL invalid extension", filepath=fx_path)
            logger.close()
            self.report({"ERROR"}, f"Expected a .json, .pse, or .bpse file: {fx_path}")
            return {"CANCELLED"}
        if not fx_path.is_file():
            logger.write("CANCEL file not found", filepath=fx_path)
            logger.close()
            self.report({"ERROR"}, f"File not found: {fx_path}")
            return {"CANCELLED"}

        try:
            with logger.scope("parse_bpse_fx", filepath=fx_path):
                fx_scene = parse_bpse_fx(fx_path, logger=logger)
        except BpseParseError as exc:
            logger.exception("CANCEL parse error", exc)
            logger.close()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        try:
            with logger.scope("import_bpse_fx", scene=fx_scene.name):
                collection = import_bpse_fx(fx_scene, context, self, logger=logger)
        except SystemExit as exc:
            logger.exception("CANCEL blocked SystemExit", exc)
            logger.close()
            self.report({"ERROR"}, "FX import blocked an unexpected SystemExit. See FX import log.")
            self.report({"ERROR"}, f"FX import log: {log_path}")
            return {"CANCELLED"}
        except Exception as exc:
            logger.exception("CANCEL import exception", exc)
            logger.close()
            self.report({"ERROR"}, f"FX import failed: {type(exc).__name__}: {exc}")
            self.report({"ERROR"}, f"FX import log: {log_path}")
            return {"CANCELLED"}

        context.scene.neox_fx_selector = str(fx_path)
        logger.write("FINISH FX import operator", collection=collection.name)
        logger.close()
        self.report({"INFO"}, f"FX import OK -> {collection.name}")
        self.report({"INFO"}, f"FX import log: {log_path}")
        return {"FINISHED"}

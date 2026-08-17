from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .logger import FxImportLogger


EXPECTED_ASSET_ERRORS = (OSError, ValueError, RuntimeError, ImportError, KeyError)


@dataclass
class FxAssetResolver:
    cache_root: Path
    logger: FxImportLogger | None = None
    warnings: list[str] = field(default_factory=list)
    _asset_index: object | None = None

    def resolve_texture(self, asset_path: str) -> str | None:
        if self.logger is not None:
            self.logger.write("ENTER resolve_texture", asset_path=asset_path)
        try:
            extracted = self._asset_index_or_create().extract(_normalize_asset_path(asset_path))
        except EXPECTED_ASSET_ERRORS as exc:
            self.warnings.append(f"FX texture not found: {asset_path} ({exc})")
            if self.logger is not None:
                self.logger.write("EXIT resolve_texture missing", asset_path=asset_path, error=exc)
            return None

        resolved_path = _write_asset_cache(
            self.cache_root,
            extracted.request.archive.prefix,
            extracted.request.normalized_path,
            extracted.data,
        )
        if self.logger is not None:
            self.logger.write("EXIT resolve_texture", asset_path=asset_path, resolved_path=resolved_path)
        return resolved_path

    def can_resolve_gim(self, asset_path: str) -> bool:
        if self.logger is not None:
            self.logger.write("ENTER can_resolve_gim", asset_path=asset_path)
        try:
            self._asset_index_or_create().extract(_normalize_asset_path(asset_path))
        except EXPECTED_ASSET_ERRORS as exc:
            self.warnings.append(f"FX GIM template not found: {asset_path} ({exc})")
            if self.logger is not None:
                self.logger.write("EXIT can_resolve_gim missing", asset_path=asset_path, error=exc)
            return False
        if self.logger is not None:
            self.logger.write("EXIT can_resolve_gim", asset_path=asset_path)
        return True

    def _asset_index_or_create(self):
        if self.logger is not None:
            self.logger.write("ENTER _asset_index_or_create", has_cached_index=self._asset_index is not None)
        try:
            if self._asset_index is None:
                from ..remote_import import _make_asset_index

                self._asset_index = _make_asset_index()
        except Exception as exc:
            if self.logger is not None:
                self.logger.exception("ERROR _asset_index_or_create", exc)
            raise
        else:
            if self.logger is not None:
                self.logger.write("EXIT _asset_index_or_create")
            return self._asset_index


def _normalize_asset_path(asset_path: str) -> str:
    return asset_path.strip().replace("\\", "/").strip("/")


def _write_asset_cache(cache_root: Path, archive_prefix: str, normalized_path: str, data: bytes) -> str:
    parts = [part for part in f"{archive_prefix}/{normalized_path}".split("/") if part]
    output_path = cache_root.joinpath(*parts)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return str(output_path)

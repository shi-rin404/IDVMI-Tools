from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .filefinder.archive.idx_wpk import ArchiveIndexCache, extract_matching_entries
from .filefinder.core.paths import (
    ParsedInput,
    discover_archives,
    parse_asset_path,
    resolve_thy_path,
)
from .filefinder.lookup.thy import ThyLookupTable


_THY_LOOKUP_TABLE_CACHE: dict[str, ThyLookupTable] = {}


@dataclass(frozen=True)
class ExtractedAsset:
    request: ParsedInput
    data: bytes
    source_archive: Path


class AssetIndex:
    def __init__(self, game_root: Path) -> None:
        self.game_root = game_root
        self.archives = discover_archives(game_root)
        if not self.archives:
            raise FileNotFoundError(
                f"No common .idx archives were found in {game_root / 'res'} "
                f"and {game_root / 'Documents' / 'res'}"
            )
        self._thy_cache: dict[str, ThyLookupTable] = {}
        self._index_cache = ArchiveIndexCache()

    def parse(self, raw_path: str) -> ParsedInput:
        return parse_asset_path(raw_path, self.archives)

    def extract(self, raw_path: str) -> ExtractedAsset:
        if self._is_tga_path(raw_path):
            dds_path = self._replace_extension(raw_path, ".dds")
            try:
                return self._extract_one(dds_path)
            except Exception as dds_error:
                try:
                    return self._extract_one(raw_path)
                except Exception as tga_error:
                    raise FileNotFoundError(
                        f"Asset was not found as DDS or TGA: "
                        f"{dds_path} ({dds_error}); {raw_path} ({tga_error})"
                    ) from tga_error
        return self._extract_one(raw_path)

    def exists(self, raw_path: str) -> bool:
        try:
            self.extract(raw_path)
        except Exception:
            return False
        return True

    def _extract_one(self, raw_path: str) -> ExtractedAsset:
        request = self.parse(raw_path)
        lookup = self._lookup(request)
        remaining = {lookup.hash128_hex}
        for idx_path in request.archive.idx_paths:
            found = extract_matching_entries(
                idx_path,
                remaining,
                index_cache=self._index_cache,
            )
            match = found.get(lookup.hash128_hex)
            if match is not None:
                return ExtractedAsset(
                    request=request,
                    data=match.data,
                    source_archive=idx_path,
                )
        raise FileNotFoundError(
            f"Asset was resolved in THY but not found in IDX/WPK: {raw_path}"
        )

    def _lookup(self, request: ParsedInput):
        table = self._thy_cache.get(request.archive.stem)
        if table is None:
            table = _cached_thy_lookup_table(
                resolve_thy_path(self.game_root, request.archive.stem)
            )
            self._thy_cache[request.archive.stem] = table
        return table.lookup(request.normalized_path)

    @staticmethod
    def _is_tga_path(raw_path: str) -> bool:
        return raw_path.strip().replace("\\", "/").lower().endswith(".tga")

    @staticmethod
    def _replace_extension(raw_path: str, extension: str) -> str:
        normalized = raw_path.strip()
        stem = normalized.rsplit(".", 1)[0]
        return f"{stem}{extension}"


def _cached_thy_lookup_table(thy_path: Path) -> ThyLookupTable:
    cache_key = str(thy_path.resolve(strict=False))
    table = _THY_LOOKUP_TABLE_CACHE.get(cache_key)
    if table is None:
        table = ThyLookupTable(thy_path)
        _THY_LOOKUP_TABLE_CACHE[cache_key] = table
    return table

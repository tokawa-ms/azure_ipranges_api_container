"""Locked, atomic persistence for the Azure Service Tags cache."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from azure_ipranges_api.config import Settings
from azure_ipranges_api.models import CacheMetadata


class CacheStore:
    """Manage the fixed cache files below ``Settings.data_dir``."""

    SERVICE_TAGS_FILENAME = "service-tags.json"
    METADATA_FILENAME = "metadata.json"
    UPDATE_LOCK_FILENAME = "update.lock"

    def __init__(self, settings: Settings) -> None:
        if not isinstance(settings, Settings):
            raise TypeError("settings must be a Settings instance")

        self.data_dir = settings.data_dir
        self.service_tags_path = self._managed_path(self.SERVICE_TAGS_FILENAME)
        self.metadata_path = self._managed_path(self.METADATA_FILENAME)
        self.lock_path = self._managed_path(self.UPDATE_LOCK_FILENAME)
        self._file_lock = FileLock(str(self.lock_path))

    def _managed_path(self, filename: str) -> Path:
        if Path(filename).name != filename or filename in {"", ".", ".."}:
            raise ValueError("cache filenames must be simple names")

        path = self.data_dir / filename
        if path.parent != self.data_dir:
            raise ValueError("cache path must be below DATA_DIR")
        return path

    def _assert_safe_to_read(self, path: Path) -> None:
        if path not in {self.service_tags_path, self.metadata_path, self.lock_path}:
            raise ValueError("unmanaged cache path")
        if path.is_symlink():
            raise ValueError(f"refusing to follow cache symlink: {path.name}")

    def ensure_directory(self) -> None:
        """Create ``DATA_DIR`` when it does not already exist."""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.data_dir.is_dir():
            raise NotADirectoryError(self.data_dir)

    @contextmanager
    def update_lock(self, timeout: float = -1) -> Iterator[None]:
        """Acquire the cross-process cache update lock."""

        self.ensure_directory()
        self._assert_safe_to_read(self.lock_path)
        with self._file_lock.acquire(timeout=timeout):
            yield

    def json_exists(self) -> bool:
        self._assert_safe_to_read(self.service_tags_path)
        return self.service_tags_path.is_file()

    def metadata_exists(self) -> bool:
        self._assert_safe_to_read(self.metadata_path)
        return self.metadata_path.is_file()

    def exists(self) -> bool:
        """Return whether both members of the cache pair exist."""

        return self.json_exists() and self.metadata_exists()

    def read_json(self) -> bytes:
        """Read the persisted Service Tags document without decoding it."""

        self._assert_safe_to_read(self.service_tags_path)
        return self.service_tags_path.read_bytes()

    def read_json_bytes(self) -> bytes:
        return self.read_json()

    def read_metadata(self) -> bytes:
        """Read the persisted metadata without decoding it."""

        self._assert_safe_to_read(self.metadata_path)
        return self.metadata_path.read_bytes()

    def read_metadata_bytes(self) -> bytes:
        return self.read_metadata()

    def read_metadata_text(self) -> str:
        """Read persisted metadata as UTF-8 text."""

        return self.read_metadata().decode("utf-8")

    @staticmethod
    def sha256(content: bytes) -> str:
        """Calculate a lowercase SHA-256 hex digest."""

        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        return hashlib.sha256(content).hexdigest()

    def content_matches_metadata(
        self,
        content: bytes,
        metadata: CacheMetadata,
    ) -> bool:
        """Verify that metadata and Service Tags bytes belong together."""

        if not isinstance(metadata, CacheMetadata):
            raise TypeError("metadata must be CacheMetadata")
        return self.sha256(content) == metadata.sha256

    def write(self, validated_json: bytes, metadata: CacheMetadata) -> None:
        """Stage both files, then atomically replace each cache member."""

        if not isinstance(validated_json, bytes):
            raise TypeError("validated_json must be bytes")
        if not isinstance(metadata, CacheMetadata):
            raise TypeError("metadata must be CacheMetadata")
        if not self.content_matches_metadata(validated_json, metadata):
            raise ValueError("metadata SHA-256 does not match validated JSON")

        self.ensure_directory()
        metadata_bytes = metadata.model_dump_json().encode("utf-8")
        json_temp: Path | None = None
        metadata_temp: Path | None = None

        try:
            json_temp = self._stage(self.service_tags_path, validated_json)
            metadata_temp = self._stage(self.metadata_path, metadata_bytes)

            os.replace(json_temp, self.service_tags_path)
            json_temp = None
            os.replace(metadata_temp, self.metadata_path)
            metadata_temp = None
        finally:
            self._remove_temporary(json_temp)
            self._remove_temporary(metadata_temp)

    def _stage(self, target: Path, content: bytes) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.data_dir,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
        except BaseException:
            self._remove_temporary(temporary_path)
            raise
        return temporary_path

    @staticmethod
    def _remove_temporary(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

"""Comprehensive tests for CacheStore and CacheLoader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.cache import CacheStore
from azure_ipranges_api.data.loader import CacheLoader, CacheLoadError, CacheLoadErrorReason
from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument, ServiceTagProperties, ServiceTagValue


class TestCacheStoreInitialization:
    """Test CacheStore initialization and path management."""

    def test_cache_store_requires_settings_instance(self, temp_data_dir: Path) -> None:
        """CacheStore should require a Settings instance."""
        with pytest.raises(TypeError, match="Settings instance"):
            CacheStore("not a settings object")

    def test_cache_store_sets_fixed_paths(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """CacheStore should establish fixed cache file paths."""
        cache = CacheStore(settings_with_temp_dir)
        assert cache.data_dir == settings_with_temp_dir.data_dir
        assert cache.service_tags_path.name == "service-tags.json"
        assert cache.metadata_path.name == "metadata.json"
        assert cache.lock_path.name == "update.lock"

    def test_cache_store_paths_are_immutable(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Cache paths should be under the data directory."""
        cache = CacheStore(settings_with_temp_dir)
        assert cache.service_tags_path.parent == settings_with_temp_dir.data_dir
        assert cache.metadata_path.parent == settings_with_temp_dir.data_dir
        assert cache.lock_path.parent == settings_with_temp_dir.data_dir

    def test_invalid_cache_filename_rejected(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Invalid cache filenames should be rejected."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(ValueError, match="cache filenames"):
            cache._managed_path("../../../etc/passwd")

    def test_empty_filename_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Empty filenames should be rejected."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(ValueError, match="cache filenames"):
            cache._managed_path("")

    def test_dot_filename_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Single-dot filenames should be rejected."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(ValueError, match="cache filenames"):
            cache._managed_path(".")

    def test_double_dot_filename_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Double-dot filenames should be rejected."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(ValueError, match="cache filenames"):
            cache._managed_path("..")


class TestCacheStoreDirectoryManagement:
    """Test cache directory creation and management."""

    def test_ensure_directory_creates_missing_directory(
        self, temp_data_dir: Path
    ) -> None:
        """ensure_directory should create data_dir if it doesn't exist."""
        nested_dir = temp_data_dir / "nested" / "deep" / "cache"
        settings = Settings(data_dir=nested_dir)
        cache = CacheStore(settings)

        assert not nested_dir.exists()
        cache.ensure_directory()
        assert nested_dir.is_dir()

    def test_ensure_directory_idempotent(self, settings_with_temp_dir: Settings) -> None:
        """ensure_directory should be safe to call multiple times."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        cache.ensure_directory()
        assert settings_with_temp_dir.data_dir.is_dir()

    def test_ensure_directory_fails_if_path_is_file(
        self, temp_data_dir: Path
    ) -> None:
        """ensure_directory should fail if data_dir path is a file."""
        file_path = temp_data_dir / "file.txt"
        file_path.write_text("not a directory")
        settings = Settings(data_dir=file_path)
        cache = CacheStore(settings)

        with pytest.raises((NotADirectoryError, FileExistsError)):
            cache.ensure_directory()


class TestCacheStoreFileExistence:
    """Test file existence checks."""

    def test_json_exists_false_when_missing(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """json_exists should return False when file is missing."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        assert not cache.json_exists()

    def test_json_exists_true_when_present(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """json_exists should return True when file exists."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        cache.service_tags_path.write_bytes(b"{}")
        assert cache.json_exists()

    def test_metadata_exists_false_when_missing(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """metadata_exists should return False when file is missing."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        assert not cache.metadata_exists()

    def test_metadata_exists_true_when_present(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """metadata_exists should return True when file exists."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        cache.metadata_path.write_bytes(b"{}")
        assert cache.metadata_exists()

    def test_exists_false_when_either_missing(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """exists should return False if either file is missing."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        cache.service_tags_path.write_bytes(b"{}")
        assert not cache.exists()

        cache.metadata_path.write_bytes(b"{}")
        assert cache.exists()

        cache.service_tags_path.unlink()
        assert not cache.exists()

    def test_exists_true_when_both_present(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """exists should return True only when both files exist."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        cache.service_tags_path.write_bytes(b"{}")
        cache.metadata_path.write_bytes(b"{}")
        assert cache.exists()

    def test_symlink_rejected_for_json(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Symlinks should be refused for service tags file."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        real_file = settings_with_temp_dir.data_dir / "real_file.json"
        real_file.write_bytes(b"{}")
        cache.service_tags_path.symlink_to(real_file)

        with pytest.raises(ValueError, match="symlink"):
            cache.json_exists()

    def test_symlink_rejected_for_metadata(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Symlinks should be refused for metadata file."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        real_file = settings_with_temp_dir.data_dir / "real_metadata.json"
        real_file.write_bytes(b"{}")
        cache.metadata_path.symlink_to(real_file)

        with pytest.raises(ValueError, match="symlink"):
            cache.metadata_exists()


class TestCacheStoreFileReading:
    """Test reading cache files."""

    def test_read_json_returns_bytes(self, settings_with_temp_dir: Settings) -> None:
        """read_json should return raw bytes without decoding."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        content = b'{"test": "value"}'
        cache.service_tags_path.write_bytes(content)

        assert cache.read_json() == content
        assert cache.read_json_bytes() == content

    def test_read_json_missing_raises_error(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """read_json should raise FileNotFoundError when file is missing."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        with pytest.raises(FileNotFoundError):
            cache.read_json()

    def test_read_metadata_returns_bytes(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """read_metadata should return raw bytes without decoding."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        content = b'{"fetched_at": "2024-01-01T00:00:00Z"}'
        cache.metadata_path.write_bytes(content)

        assert cache.read_metadata() == content
        assert cache.read_metadata_bytes() == content

    def test_read_metadata_text_decodes_utf8(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """read_metadata_text should decode as UTF-8."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        text = '{"test": "value with unicode: é"}'
        cache.metadata_path.write_bytes(text.encode("utf-8"))

        assert cache.read_metadata_text() == text

    def test_read_metadata_missing_raises_error(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """read_metadata should raise FileNotFoundError when file is missing."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        with pytest.raises(FileNotFoundError):
            cache.read_metadata()


class TestCacheSHA256:
    """Test SHA-256 checksum calculation."""

    def test_sha256_correctness(self, settings_with_temp_dir: Settings) -> None:
        """SHA-256 should compute correct digests."""
        cache = CacheStore(settings_with_temp_dir)
        content = b"test content"
        expected = hashlib.sha256(content).hexdigest()
        assert cache.sha256(content) == expected

    def test_sha256_lowercase(self, settings_with_temp_dir: Settings) -> None:
        """SHA-256 digest should be lowercase."""
        cache = CacheStore(settings_with_temp_dir)
        digest = cache.sha256(b"test")
        assert digest == digest.lower()

    def test_sha256_non_bytes_rejected(self, settings_with_temp_dir: Settings) -> None:
        """sha256 should require bytes input."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(TypeError, match="bytes"):
            cache.sha256("not bytes")

    def test_sha256_empty_bytes(self, settings_with_temp_dir: Settings) -> None:
        """SHA-256 of empty bytes should compute correctly."""
        cache = CacheStore(settings_with_temp_dir)
        empty_digest = cache.sha256(b"")
        # SHA-256 of empty string
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert empty_digest == expected

    def test_sha256_length(self, settings_with_temp_dir: Settings) -> None:
        """SHA-256 digest should be 64 hex characters."""
        cache = CacheStore(settings_with_temp_dir)
        digest = cache.sha256(b"test content")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestCacheStoreMetadataValidation:
    """Test metadata validation against content."""

    def test_content_matches_metadata_valid(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """content_matches_metadata should return True for matching content."""
        cache = CacheStore(settings_with_temp_dir)
        assert cache.content_matches_metadata(
            sample_service_tags_json, sample_cache_metadata
        )

    def test_content_matches_metadata_invalid(
        self,
        settings_with_temp_dir: Settings,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """content_matches_metadata should return False for mismatched content."""
        cache = CacheStore(settings_with_temp_dir)
        different_content = b"different content"
        assert not cache.content_matches_metadata(
            different_content, sample_cache_metadata
        )

    def test_content_matches_metadata_non_bytes_rejected(
        self,
        settings_with_temp_dir: Settings,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """content_matches_metadata should require bytes content."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(TypeError, match="bytes"):
            cache.content_matches_metadata(
                "not bytes", sample_cache_metadata
            )

    def test_content_matches_metadata_non_metadata_rejected(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
    ) -> None:
        """content_matches_metadata should require CacheMetadata."""
        cache = CacheStore(settings_with_temp_dir)
        with pytest.raises(TypeError, match="CacheMetadata"):
            cache.content_matches_metadata(sample_service_tags_json, {})


class TestCacheStoreWrite:
    """Test atomic write operations."""

    def test_write_creates_both_files(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should create both JSON and metadata files."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()

        cache.write(sample_service_tags_json, sample_cache_metadata)

        assert cache.json_exists()
        assert cache.metadata_exists()
        assert cache.service_tags_path.read_bytes() == sample_service_tags_json

    def test_write_metadata_serialization(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should serialize metadata to JSON correctly."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()

        cache.write(sample_service_tags_json, sample_cache_metadata)

        metadata_bytes = cache.read_metadata()
        parsed = json.loads(metadata_bytes)
        assert parsed["source_url"] == sample_cache_metadata.source_url
        assert parsed["filename"] == sample_cache_metadata.filename

    def test_write_non_bytes_rejected(
        self,
        settings_with_temp_dir: Settings,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should require bytes for JSON content."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        with pytest.raises(TypeError, match="bytes"):
            cache.write("not bytes", sample_cache_metadata)

    def test_write_non_metadata_rejected(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
    ) -> None:
        """write should require CacheMetadata."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()
        with pytest.raises(TypeError, match="CacheMetadata"):
            cache.write(sample_service_tags_json, {})

    def test_write_checksum_mismatch_rejected(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should reject content that doesn't match metadata checksum."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()

        # Create metadata with wrong checksum
        wrong_checksum = "0000000000000000000000000000000000000000000000000000000000000000"
        bad_metadata = CacheMetadata(
            source_url=sample_cache_metadata.source_url,
            filename=sample_cache_metadata.filename,
            fetched_at=sample_cache_metadata.fetched_at,
            sha256=wrong_checksum,
            change_number=sample_cache_metadata.change_number,
            tag_count=sample_cache_metadata.tag_count,
            prefix_count=sample_cache_metadata.prefix_count,
        )

        with pytest.raises(ValueError, match="SHA-256"):
            cache.write(sample_service_tags_json, bad_metadata)

    def test_write_replaces_existing_files_atomically(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should atomically replace existing cache files."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()

        # Write initial content
        initial_content = b'{"initial": "content"}'
        # Compute correct sha256 for initial content to pass validation
        correct_sha = hashlib.sha256(initial_content).hexdigest()
        initial_metadata = CacheMetadata(
            source_url="https://example.com/initial.json",
            filename="initial.json",
            fetched_at=datetime.now(timezone.utc),
            sha256=correct_sha,
            change_number=1,
            tag_count=1,
            prefix_count=1,
        )
        cache.write(initial_content, initial_metadata)

        # Write new content
        cache.write(sample_service_tags_json, sample_cache_metadata)

        # Verify new content was written
        assert cache.read_json() == sample_service_tags_json

    def test_write_cleans_up_temporary_files_on_success(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should clean up temporary files after successful write."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()

        cache.write(sample_service_tags_json, sample_cache_metadata)

        # Check for temporary files
        temp_files = list(settings_with_temp_dir.data_dir.glob(".*.tmp"))
        assert len(temp_files) == 0

    def test_write_preserves_prior_cache_on_staging_failure(
        self,
        settings_with_temp_dir: Settings,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """write should preserve existing cache if staging fails."""
        cache = CacheStore(settings_with_temp_dir)
        cache.ensure_directory()

        # Write initial content
        cache.service_tags_path.write_bytes(b"original content")
        cache.metadata_path.write_bytes(b"{}")

        # Mock _stage to fail on first call
        original_stage = cache._stage
        call_count = [0]

        def failing_stage(target: Path, content: bytes) -> Path:
            call_count[0] += 1
            if call_count[0] == 1:
                raise IOError("Write failed")
            return original_stage(target, content)

        with patch.object(cache, "_stage", side_effect=failing_stage):
            with pytest.raises(IOError):
                cache.write(sample_service_tags_json, sample_cache_metadata)

        # Original content should still exist
        assert cache.service_tags_path.read_bytes() == b"original content"


class TestCacheStoreUpdateLock:
    """Test update lock functionality."""

    def test_update_lock_context_manager(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """update_lock should work as a context manager."""
        cache = CacheStore(settings_with_temp_dir)
        with cache.update_lock():
            assert cache.lock_path.exists()

    def test_update_lock_ensures_directory(
        self, temp_data_dir: Path
    ) -> None:
        """update_lock should create data_dir if it doesn't exist."""
        nested_dir = temp_data_dir / "nested" / "cache"
        settings = Settings(data_dir=nested_dir)
        cache = CacheStore(settings)

        with cache.update_lock():
            assert nested_dir.is_dir()

    def test_update_lock_timeout(self, settings_with_temp_dir: Settings) -> None:
        """update_lock should support timeout parameter."""
        cache = CacheStore(settings_with_temp_dir)
        # Just test that timeout parameter is accepted
        with cache.update_lock(timeout=1.0):
            pass


class TestCacheLoaderInitialization:
    """Test CacheLoader initialization."""

    def test_cache_loader_requires_store(self) -> None:
        """CacheLoader should accept a store protocol."""
        store = MagicMock()
        loader = CacheLoader(store)
        assert loader._store is store


class TestCacheLoaderErrors:
    """Test cache loading error handling."""

    def test_missing_json_raises_error(self) -> None:
        """Missing JSON file should raise MISSING_DATA error."""
        store = MagicMock()
        store.read_json.return_value = None
        store.read_metadata.return_value = b"{}"

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.MISSING_DATA

    def test_missing_metadata_raises_error(self) -> None:
        """Missing metadata file should raise MISSING_DATA error."""
        store = MagicMock()
        store.read_json.return_value = b"{}"
        store.read_metadata.return_value = None

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.MISSING_DATA

    def test_file_not_found_raises_error(self) -> None:
        """FileNotFoundError should be converted to MISSING_DATA error."""
        store = MagicMock()
        store.read_json.side_effect = FileNotFoundError()

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.MISSING_DATA

    def test_invalid_json_raises_error(self) -> None:
        """Invalid JSON should raise INVALID_JSON error."""
        store = MagicMock()
        store.read_json.return_value = b"not valid json {"
        store.read_metadata.return_value = b'{"fetched_at": "2024-01-01T00:00:00Z"}'

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.INVALID_JSON

    def test_invalid_schema_raises_error(self) -> None:
        """Invalid schema should raise INVALID_SCHEMA error."""
        store = MagicMock()
        # Valid JSON but missing required Service Tags fields
        store.read_json.return_value = b'{"invalid": "schema"}'
        store.read_metadata.return_value = b'{"fetched_at": "2024-01-01T00:00:00Z"}'

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.INVALID_SCHEMA

    def test_invalid_metadata_raises_error(self) -> None:
        """Invalid metadata should raise INVALID_METADATA error."""
        valid_json = b'{"changeNumber": 1, "values": []}'
        store = MagicMock()
        store.read_json.return_value = valid_json
        store.read_metadata.return_value = b"not valid json {"

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.INVALID_METADATA

    def test_checksum_mismatch_raises_error(
        self,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """Mismatched checksum should raise CHECKSUM_MISMATCH error."""
        store = MagicMock()
        store.read_json.return_value = sample_service_tags_json
        # Create metadata with wrong checksum
        wrong_metadata = CacheMetadata(
            source_url="https://example.com/file.json",
            filename="file.json",
            fetched_at=datetime.now(timezone.utc),
            sha256="0000000000000000000000000000000000000000000000000000000000000000",
            change_number=100,
            tag_count=3,
            prefix_count=7,
        )
        store.read_metadata.return_value = wrong_metadata.model_dump_json().encode()

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.CHECKSUM_MISMATCH

    def test_metadata_content_mismatch_raises_error(
        self,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """Mismatched tag/prefix counts should raise METADATA_CONTENT_MISMATCH."""
        store = MagicMock()
        store.read_json.return_value = sample_service_tags_json
        # Create metadata with wrong counts
        wrong_metadata = CacheMetadata(
            source_url=sample_cache_metadata.source_url,
            filename=sample_cache_metadata.filename,
            fetched_at=sample_cache_metadata.fetched_at,
            sha256=sample_cache_metadata.sha256,
            change_number=sample_cache_metadata.change_number,
            tag_count=999,  # Wrong count
            prefix_count=sample_cache_metadata.prefix_count,
        )
        store.read_metadata.return_value = wrong_metadata.model_dump_json().encode()

        loader = CacheLoader(store)
        with pytest.raises(CacheLoadError) as exc_info:
            loader.load()
        assert exc_info.value.reason == CacheLoadErrorReason.METADATA_CONTENT_MISMATCH


class TestCacheLoaderSuccessfulLoad:
    """Test successful cache loading."""

    def test_load_returns_loaded_cache(
        self,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """load should return a LoadedCache with valid data."""
        store = MagicMock()
        store.read_json.return_value = sample_service_tags_json
        store.read_metadata.return_value = (
            sample_cache_metadata.model_dump_json().encode()
        )

        loader = CacheLoader(store)
        loaded = loader.load()

        assert loaded.document.change_number == 100
        assert len(loaded.document.values) == 3
        assert loaded.metadata == sample_cache_metadata
        assert loaded.raw_json == sample_service_tags_json

    def test_load_with_string_json(self, sample_cache_metadata: CacheMetadata) -> None:
        """load should handle string JSON input."""
        json_str = '{"changeNumber": 100, "values": []}'
        # Calculate SHA256 of the UTF-8 encoded string to match what the loader expects
        import hashlib
        correct_sha = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
        
        store = MagicMock()
        store.read_json.return_value = json_str
        
        # Create metadata with correct SHA256 and correct counts
        metadata = CacheMetadata(
            source_url="https://example.com/file.json",
            filename="file.json",
            fetched_at=datetime.now(timezone.utc),
            sha256=correct_sha,
            change_number=100,
            tag_count=0,  # Empty values list
            prefix_count=0,
        )
        store.read_metadata.return_value = metadata.model_dump_json().encode()

        loader = CacheLoader(store)
        loaded = loader.load()

        assert loaded.raw_json == json_str.encode("utf-8")

    def test_load_with_string_metadata(
        self,
        sample_service_tags_json: bytes,
        sample_cache_metadata: CacheMetadata,
    ) -> None:
        """load should handle string metadata input."""
        import hashlib
        correct_sha = hashlib.sha256(sample_service_tags_json).hexdigest()
        
        store = MagicMock()
        store.read_json.return_value = sample_service_tags_json
        
        # Create metadata with correct SHA256 and counts matching the sample JSON
        metadata = CacheMetadata(
            source_url=sample_cache_metadata.source_url,
            filename=sample_cache_metadata.filename,
            fetched_at=sample_cache_metadata.fetched_at,
            sha256=correct_sha,
            change_number=100,
            tag_count=3,
            prefix_count=6,
        )
        # Return metadata as string (JSON-encoded)
        store.read_metadata.return_value = metadata.model_dump_json()

        loader = CacheLoader(store)
        loaded = loader.load()

        assert loaded.metadata.sha256 == correct_sha


class TestCacheLoadErrorReason:
    """Test CacheLoadError reason enum."""

    def test_error_reason_message(self) -> None:
        """CacheLoadError should include reason message."""
        error = CacheLoadError(CacheLoadErrorReason.MISSING_DATA)
        assert str(error) == "missing_data"
        assert error.reason == CacheLoadErrorReason.MISSING_DATA

    def test_error_requires_valid_reason(self) -> None:
        """CacheLoadError should require a valid reason."""
        with pytest.raises(TypeError, match="CacheLoadErrorReason"):
            CacheLoadError("not a reason")

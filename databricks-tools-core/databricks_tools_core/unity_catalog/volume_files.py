"""
Volume Files - Unity Catalog Volume File Operations

Functions for working with files in Unity Catalog Volumes.
Uses Databricks Files API via SDK (w.files).

Volume paths use the format: /Volumes/<catalog>/<schema>/<volume>/<path>
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, List, Optional

from ..auth import get_workspace_client


def normalise_last_modified(value: Any) -> Optional[str]:
    """
    Normalise a Files API ``last_modified`` value to an ISO 8601 string.

    The Databricks Files API returns this field in THREE different shapes
    depending on which endpoint produced it, and the difference is not
    documented anywhere obvious:

    * ``w.files.list_directory_contents()`` -> ``int``, epoch **milliseconds**
      (e.g. ``1784028450000``)
    * ``w.files.get_metadata()`` -> ``str``, an RFC 7231 HTTP-date
      (e.g. ``'Sun, 09 Aug 2026 21:48:17 GMT'``) taken straight off the
      ``Last-Modified`` response header
    * some SDK versions / endpoints -> ``datetime``

    Calling ``.isoformat()`` unconditionally therefore crashes with
    ``'str' object has no attribute 'isoformat'`` on the ``get_metadata``
    path -- which is exactly what made ``get_volume_file_info`` fail 100% of
    the time (fixed 2026-08-10).

    Returns an ISO 8601 string for every recognised shape, ``None`` for
    ``None``, and -- deliberately -- the original value stringified if it
    matches nothing known. Never raises: a metadata nicety must not be able
    to fail the call that carries it.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat()

    # bool is an int subclass; guard so True doesn't become 1970.
    if isinstance(value, int) and not isinstance(value, bool):
        # Heuristic: the Files API uses milliseconds. Anything below ~1e11 is
        # far more plausible as seconds (1e11 s is year 5138; 1e11 ms is 1973).
        seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # RFC 7231 HTTP-date, e.g. 'Sun, 09 Aug 2026 21:48:17 GMT'
        try:
            return parsedate_to_datetime(text).isoformat()
        except (TypeError, ValueError):
            pass
        # Already ISO 8601? Round-trip it so the output shape is consistent.
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
        # Unrecognised, but real: hand it back rather than lose it.
        return text

    return str(value)


@dataclass
class VolumeFileInfo:
    """Information about a file or directory in a volume."""

    name: str
    path: str
    is_directory: bool
    file_size: Optional[int] = None
    last_modified: Optional[str] = None


@dataclass
class VolumeUploadResult:
    """Result from uploading a file to a volume."""

    local_path: str
    volume_path: str
    success: bool
    error: Optional[str] = None


@dataclass
class VolumeDownloadResult:
    """Result from downloading a file from a volume."""

    volume_path: str
    local_path: str
    success: bool
    error: Optional[str] = None


def list_volume_files(volume_path: str, max_results: Optional[int] = None) -> List[VolumeFileInfo]:
    """
    List files and directories in a volume path.

    Args:
        volume_path: Path in volume (e.g., "/Volumes/catalog/schema/volume/folder")
        max_results: Optional maximum number of results to return (None = no limit)

    Returns:
        List of VolumeFileInfo objects

    Raises:
        Exception: If path doesn't exist or access denied

    Example:
        >>> files = list_volume_files("/Volumes/main/default/my_volume/data")
        >>> for f in files:
        ...     print(f"{f.name}: {'dir' if f.is_directory else 'file'}")
    """
    w = get_workspace_client()

    # Ensure path ends with / for directory listing
    if not volume_path.endswith("/"):
        volume_path = volume_path + "/"

    results = []
    for entry in w.files.list_directory_contents(volume_path):
        # int (epoch ms) here, str (HTTP-date) from get_metadata, datetime on
        # some SDK versions — normalise_last_modified() handles all three so the
        # two read paths agree on one output shape.
        last_modified = normalise_last_modified(entry.last_modified)

        # DirectoryEntry declares name / path / is_directory as Optional, while
        # VolumeFileInfo requires str / str / bool. Coerce rather than pass None
        # through a field the dataclass says is non-optional.
        entry_path = entry.path or ""
        results.append(
            VolumeFileInfo(
                name=entry.name or Path(entry_path).name,
                path=entry_path,
                is_directory=bool(entry.is_directory),
                file_size=entry.file_size,
                last_modified=last_modified,
            )
        )
        # Early exit if we've hit the limit
        if max_results is not None and len(results) >= max_results:
            break

    return results


def upload_to_volume(local_path: str, volume_path: str, overwrite: bool = True) -> VolumeUploadResult:
    """
    Upload a local file to a Unity Catalog volume.

    Args:
        local_path: Path to local file
        volume_path: Target path in volume (e.g., "/Volumes/catalog/schema/volume/file.csv")
        overwrite: Whether to overwrite existing file (default: True)

    Returns:
        VolumeUploadResult with success status

    Example:
        >>> result = upload_to_volume(
        ...     local_path="/tmp/data.csv",
        ...     volume_path="/Volumes/main/default/my_volume/data.csv"
        ... )
        >>> if result.success:
        ...     print("Upload complete")
    """
    if not os.path.exists(local_path):
        return VolumeUploadResult(
            local_path=local_path,
            volume_path=volume_path,
            success=False,
            error=f"Local file not found: {local_path}",
        )

    if not os.path.isfile(local_path):
        return VolumeUploadResult(
            local_path=local_path,
            volume_path=volume_path,
            success=False,
            error=f"Path is not a file: {local_path}",
        )

    try:
        w = get_workspace_client()

        # Use upload_from for direct file-to-volume upload.
        # pyright resolves `w.files` to the FilesAPI base, but at runtime it is
        # FilesExt (databricks.sdk.mixins.files), which is where upload_from /
        # download_to are actually defined. Verified on SDK 0.120.0:
        #   FilesAPI.upload_from -> False, FilesExt.upload_from -> True
        #   WorkspaceClient.files -> FilesExt
        # so the call is correct and the finding is a stub inaccuracy. Narrow
        # ignore rather than a blanket one, so real attribute errors still surface.
        w.files.upload_from(  # pyright: ignore[reportAttributeAccessIssue]
            file_path=volume_path, source_path=local_path, overwrite=overwrite
        )

        return VolumeUploadResult(local_path=local_path, volume_path=volume_path, success=True)

    except Exception as e:
        return VolumeUploadResult(local_path=local_path, volume_path=volume_path, success=False, error=str(e))


def download_from_volume(volume_path: str, local_path: str, overwrite: bool = True) -> VolumeDownloadResult:
    """
    Download a file from a Unity Catalog volume to local path.

    Args:
        volume_path: Path in volume (e.g., "/Volumes/catalog/schema/volume/file.csv")
        local_path: Target local file path
        overwrite: Whether to overwrite existing local file (default: True)

    Returns:
        VolumeDownloadResult with success status

    Example:
        >>> result = download_from_volume(
        ...     volume_path="/Volumes/main/default/my_volume/data.csv",
        ...     local_path="/tmp/downloaded.csv"
        ... )
        >>> if result.success:
        ...     print("Download complete")
    """
    # Check if local file exists and overwrite is False
    if os.path.exists(local_path) and not overwrite:
        return VolumeDownloadResult(
            volume_path=volume_path,
            local_path=local_path,
            success=False,
            error=f"Local file already exists: {local_path}",
        )

    try:
        w = get_workspace_client()

        # Create parent directory if needed
        parent_dir = str(Path(local_path).parent)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)

        # Use download_to for direct volume-to-file download.
        # Same FilesExt-vs-FilesAPI stub inaccuracy as upload_from above.
        w.files.download_to(  # pyright: ignore[reportAttributeAccessIssue]
            file_path=volume_path, destination=local_path, overwrite=overwrite
        )

        return VolumeDownloadResult(volume_path=volume_path, local_path=local_path, success=True)

    except Exception as e:
        return VolumeDownloadResult(volume_path=volume_path, local_path=local_path, success=False, error=str(e))


def delete_volume_file(volume_path: str) -> None:
    """
    Delete a file from a Unity Catalog volume.

    Args:
        volume_path: Path to file in volume (e.g., "/Volumes/catalog/schema/volume/file.csv")

    Raises:
        Exception: If file doesn't exist or access denied

    Example:
        >>> delete_volume_file("/Volumes/main/default/my_volume/old_data.csv")
    """
    w = get_workspace_client()
    w.files.delete(volume_path)


def delete_volume_directory(volume_path: str) -> None:
    """
    Delete an empty directory from a Unity Catalog volume.

    Note: Directory must be empty. Delete all contents first.

    Args:
        volume_path: Path to directory in volume

    Raises:
        Exception: If directory not empty, doesn't exist, or access denied

    Example:
        >>> delete_volume_directory("/Volumes/main/default/my_volume/old_folder/")
    """
    w = get_workspace_client()
    w.files.delete_directory(volume_path)


def create_volume_directory(volume_path: str) -> None:
    """
    Create a directory in a Unity Catalog volume.

    Creates parent directories as needed (like mkdir -p).
    Idempotent - succeeds if directory already exists.

    Args:
        volume_path: Path for new directory (e.g., "/Volumes/catalog/schema/volume/new_folder")

    Example:
        >>> create_volume_directory("/Volumes/main/default/my_volume/data/2024/01")
    """
    w = get_workspace_client()
    w.files.create_directory(volume_path)


def get_volume_file_metadata(volume_path: str) -> VolumeFileInfo:
    """
    Get metadata for a file in a Unity Catalog volume.

    Args:
        volume_path: Path to file in volume

    Returns:
        VolumeFileInfo with file metadata

    Raises:
        Exception: If file doesn't exist or access denied

    Example:
        >>> info = get_volume_file_metadata("/Volumes/main/default/my_volume/data.csv")
        >>> print(f"Size: {info.file_size} bytes")
    """
    w = get_workspace_client()
    metadata = w.files.get_metadata(volume_path)

    return VolumeFileInfo(
        name=Path(volume_path).name,
        path=volume_path,
        is_directory=False,
        file_size=metadata.content_length,
        # NOT .isoformat() — get_metadata returns an RFC 7231 HTTP-date STRING,
        # not a datetime, so the old unconditional call crashed every time.
        last_modified=normalise_last_modified(metadata.last_modified),
    )

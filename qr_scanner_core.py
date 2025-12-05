#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}


class QRDependencyError(RuntimeError):
    pass


def _ensure_qr_dependencies():
    try:
        from pyzbar import pyzbar
        from PIL import Image
    except ImportError as e:
        raise QRDependencyError(
            "pyzbar and Pillow are required for QR scanning.\n"
            "Install with:\n"
            "  pip install pyzbar Pillow\n\n"
            "Note: pyzbar requires the native 'zbar' library.\n"
            "On Windows: install zbar from a prebuilt binary (e.g. via vcpkg or\n"
            "            downloadable installers) and ensure it's on PATH.\n"
            "On macOS:   brew install zbar\n"
            "On Linux:   sudo apt-get install libzbar0  # or distro equivalent\n"
        ) from e


@dataclass
class ScanOptions:
    recursive: bool = True
    dry_run: bool = False
    preserve_timestamps: bool = True


@dataclass
class FileScanResult:
    src_path: Path
    had_qr: bool
    moved: bool
    dest_path: Optional[Path]
    error: Optional[str] = None
    qr_values: Optional[List[str]] = None


ProgressCallback = Callable[[int, int, FileScanResult], None]
LogCallback = Callable[[str, Path], None]
CancelCallback = Callable[[], bool]


def iter_image_files(root: Path, recursive: bool) -> List[Path]:
    files: List[Path] = []
    if recursive:
        for dirpath, _, filenames in os.walk(root):
            d = Path(dirpath)
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext in IMAGE_EXTENSIONS:
                    files.append(d / name)
    else:
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                files.append(entry)
    return files


def safe_move_with_suffix(
    src: Path,
    dest_dir: Path,
    preserve_timestamps: bool,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)

    base = src.name
    stem = src.stem
    suffix = src.suffix

    candidate = dest_dir / base
    counter = 1
    while candidate.exists():
        candidate = dest_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    stat_res = src.stat()

    moved_path_str = shutil.move(str(src), str(candidate))
    moved_path = Path(moved_path_str)

    if preserve_timestamps:
        os.utime(
            moved_path,
            (stat_res.st_atime, stat_res.st_mtime),
        )

    return moved_path


def scan_and_move_qr(
    root: Path,
    options: ScanOptions,
    on_progress: Optional[ProgressCallback] = None,
    on_log: Optional[LogCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> Dict[str, int]:
    _ensure_qr_dependencies()
    from pyzbar import pyzbar
    from PIL import Image

    root = root.resolve()
    files = iter_image_files(root, options.recursive)
    total = len(files)

    stats = {
        "total": total,
        "with_qr": 0,
        "moved": 0,
        "no_qr": 0,
        "errors": 0,
        "skipped": 0,
    }

    def log(message: str, file_path: Path):
        nonlocal root
        if on_log is None:
            return
        if options.recursive:
            log_dir = file_path.parent
        else:
            log_dir = root
        on_log(message, log_dir)

    for idx, path in enumerate(files, start=1):
        if is_cancelled is not None and is_cancelled():
            stats["skipped"] += total - idx + 1
            break

        result = FileScanResult(
            src_path=path,
            had_qr=False,
            moved=False,
            dest_path=None,
            error=None,
            qr_values=None,
        )

        try:
            try:
                img = Image.open(path)
            except Exception as e:
                msg = f"ERROR: Cannot open image {path}: {e}"
                result.error = str(e)
                stats["errors"] += 1
                log(msg, path)
                if on_progress:
                    on_progress(idx, total, result)
                continue

            try:
                decoded = pyzbar.decode(img)
            finally:
                img.close()

            if not decoded:
                stats["no_qr"] += 1
                log(f"NO QR: {path}", path)
                if on_progress:
                    on_progress(idx, total, result)
                continue

            result.had_qr = True
            stats["with_qr"] += 1
            qr_values = []
            for d in decoded:
                try:
                    qr_values.append(d.data.decode("utf-8", errors="replace"))
                except Exception:
                    qr_values.append(repr(d.data))
            result.qr_values = qr_values

            dest_dir = path.parent / "qr"

            if options.dry_run:
                msg = f"DRY RUN: Would move {path} -> {dest_dir}"
                log(msg, path)
            else:
                try:
                    moved_path = safe_move_with_suffix(
                        path, dest_dir, options.preserve_timestamps
                    )
                    result.moved = True
                    result.dest_path = moved_path
                    stats["moved"] += 1
                    msg = f"MOVED: {path} -> {moved_path}"
                    log(msg, path)
                except Exception as e:
                    result.error = str(e)
                    stats["errors"] += 1
                    msg = f"ERROR: Failed to move {path}: {e}"
                    log(msg, path)

            if options.dry_run and result.dest_path is None:
                hypothetical = dest_dir / path.name
                result.dest_path = hypothetical

        except Exception as e:
            result.error = str(e)
            stats["errors"] += 1
            log(f"ERROR: Unexpected error processing {path}: {e}", path)

        if on_progress:
            on_progress(idx, total, result)

    return stats


def append_log_line(log_dir: Path, line: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "qrscan.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


__all__ = [
    "ScanOptions",
    "FileScanResult",
    "scan_and_move_qr",
    "safe_move_with_suffix",
    "append_log_line",
    "QRDependencyError",
]


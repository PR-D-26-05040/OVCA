#!/usr/bin/env python3
"""Copy only .npz and .txt members from CC3M tar shards."""

from __future__ import annotations

import argparse
import os
import tarfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


KEPT_SUFFIXES = {".npz", ".txt"}
TAR_BLOCK_SIZE = 512
TAR_RECORD_SIZE = 10240
LOCAL_PAX_TYPES = {b"x", b"L", b"K"}


def read_exact(file_object, size: int) -> bytes:
    data = file_object.read(size)
    if len(data) != size:
        raise tarfile.ReadError("Unexpected end of tar archive")
    return data


def padded_size(size: int) -> int:
    return (size + TAR_BLOCK_SIZE - 1) // TAR_BLOCK_SIZE * TAR_BLOCK_SIZE


def member_name(header: bytes) -> str:
    name = header[0:100].split(b"\0", 1)[0]
    prefix = header[345:500].split(b"\0", 1)[0]
    raw_name = prefix + (b"/" if prefix else b"") + name
    return raw_name.decode("utf-8", errors="surrogateescape")


def copy_bytes(source, destination, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise tarfile.ReadError("Unexpected end of tar member")
        destination.write(chunk)
        remaining -= len(chunk)


def stream_filter_tar(source_path: Path, temporary: Path) -> int:
    """Copy selected raw tar records without extracting or rebuilding members."""
    kept = 0
    pending_local_headers = bytearray()
    with source_path.open("rb") as source, temporary.open("wb") as destination:
        while True:
            header = read_exact(source, TAR_BLOCK_SIZE)
            if header == bytes(TAR_BLOCK_SIZE):
                break

            size = tarfile.nti(header[124:136])
            if not isinstance(size, int) or size < 0:
                raise tarfile.ReadError(f"Invalid member size in {source_path}")
            stored_size = padded_size(size)
            member_type = header[156:157]

            if member_type in LOCAL_PAX_TYPES:
                pending_local_headers.extend(header)
                pending_local_headers.extend(read_exact(source, stored_size))
                continue

            if member_type == b"g":
                destination.write(header)
                copy_bytes(source, destination, stored_size)
                continue

            name = member_name(header)
            keep = member_type in {b"", b"0"} and Path(name).suffix.lower() in KEPT_SUFFIXES
            if keep:
                destination.write(pending_local_headers)
                destination.write(header)
                copy_bytes(source, destination, stored_size)
                kept += 1
            else:
                source.seek(stored_size, os.SEEK_CUR)
            pending_local_headers.clear()

        destination.write(bytes(TAR_BLOCK_SIZE * 2))
        remainder = destination.tell() % TAR_RECORD_SIZE
        if remainder:
            destination.write(bytes(TAR_RECORD_SIZE - remainder))
    return kept


def output_is_complete(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < TAR_BLOCK_SIZE * 2:
        return False
    try:
        with path.open("rb") as archive:
            archive.seek(-TAR_BLOCK_SIZE * 2, os.SEEK_END)
            return archive.read(TAR_BLOCK_SIZE * 2) == bytes(TAR_BLOCK_SIZE * 2)
    except OSError:
        return False


def filter_archive(source: str, destination_dir: str) -> tuple[str, str, int, int]:
    source_path = Path(source)
    destination = Path(destination_dir) / source_path.name

    if output_is_complete(destination):
        return source_path.name, "skipped", 0, destination.stat().st_size

    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        kept = stream_filter_tar(source_path, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return source_path.name, "created", kept, destination.stat().st_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Directory containing source .tar files")
    parser.add_argument("destination", type=Path, help="Directory for filtered .tar files")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Process only the first N archives")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = sorted(args.source.glob("*.tar"))
    if args.limit is not None:
        sources = sources[: args.limit]
    if not sources:
        raise SystemExit(f"No .tar files found in {args.source}")

    args.destination.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    completed = 0
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(filter_archive, str(source), str(args.destination)): source
            for source in sources
        }
        for future in as_completed(futures):
            name, status, kept, size = future.result()
            completed += 1
            total_bytes += size
            print(
                f"[{completed}/{len(sources)}] {status}: {name} "
                f"({kept} members, {size / 1024 / 1024:.1f} MiB)",
                flush=True,
            )

    print(f"Output total: {total_bytes / 1024 / 1024 / 1024:.2f} GiB")


if __name__ == "__main__":
    main()

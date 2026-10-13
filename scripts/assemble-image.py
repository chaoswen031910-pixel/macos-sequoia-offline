#!/usr/bin/env python3
"""Assemble and verify the downloaded image; never writes to physical disks."""
import argparse
import gzip
import hashlib
import io
import json
import pathlib
import shutil

BUFFER = 4 * 1024 * 1024


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(BUFFER), b""):
            digest.update(data)
    return digest.hexdigest()


class PartsReader(io.RawIOBase):
    def __init__(self, paths):
        super().__init__()
        self.paths = iter(paths)
        self.stream = None
        self.digest = hashlib.sha256()
        self.total = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self.stream is None:
                try:
                    self.stream = next(self.paths).open("rb")
                except StopIteration:
                    return 0
            count = self.stream.readinto(buffer)
            if count:
                self.digest.update(memoryview(buffer)[:count])
                self.total += count
                return count
            self.stream.close()
            self.stream = None

    def close(self):
        if self.stream is not None:
            self.stream.close()
        super().close()


def assemble(manifest_path, parts_dir, output):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("version") != "15.8.1" or manifest.get("build") != "24H32":
        raise ValueError("Unexpected installer manifest")
    if output.suffix.lower() != ".img" or output.exists():
        raise ValueError("Output must be a new .img file")
    expected_image = manifest["raw_image"]
    if not isinstance(expected_image["size"], int) or not 0 < expected_image["size"] <= 30_765_219_840:
        raise ValueError("Image size is outside target USB capacity")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < expected_image["size"] + 1024 ** 3:
        raise ValueError("Not enough free space for verified raw image")
    paths = []
    for index, part in enumerate(manifest["parts"], 1):
        expected_name = f"sequoia-15.8.1.img.gz.part{index:03d}"
        if part["name"] != expected_name:
            raise ValueError("Unexpected part name or ordering")
        path = parts_dir / expected_name
        if path.stat().st_size != part["size"] or file_hash(path) != part["sha256"]:
            raise ValueError(f"Corrupt or incomplete part: {expected_name}")
        print(f"Verified {expected_name}", flush=True)
        paths.append(path)
    if not paths or len(paths) > 12:
        raise ValueError("Unexpected part count")
    partial = output.with_suffix(".img.partial")
    if partial.exists():
        raise ValueError("Partial output already exists; inspect it before retrying")
    digest = hashlib.sha256()
    total = 0
    raw_parts = PartsReader(paths)
    with raw_parts, io.BufferedReader(raw_parts) as compressed:
        with gzip.GzipFile(fileobj=compressed, mode="rb") as source, partial.open("xb") as target:
            while True:
                data = source.read(BUFFER)
                if not data:
                    break
                total += len(data)
                if total > expected_image["size"]:
                    raise ValueError("Expanded image exceeds expected size")
                target.write(data)
                digest.update(data)
        # Ensure the compressed source is consumed completely for its checksum.
        if compressed.read(1):
            raise ValueError("Unexpected trailing compressed data")
        if raw_parts.total != manifest["compressed_image"]["size"] or raw_parts.digest.hexdigest() != manifest["compressed_image"]["sha256"]:
            raise ValueError("Compressed image checksum mismatch")
    if total != expected_image["size"] or digest.hexdigest() != expected_image["sha256"]:
        raise ValueError("Raw image checksum mismatch; partial output was preserved")
    partial.rename(output)
    print(f"Verified image saved: {output.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--parts", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    assemble(args.manifest, args.parts, args.output)

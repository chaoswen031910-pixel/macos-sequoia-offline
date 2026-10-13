#!/usr/bin/env python3
"""Validate installer metadata, raw GPT images, and downloadable image parts."""
import argparse
import hashlib
import json
import pathlib
import plistlib
import struct
import uuid
import zlib

VERSION = "15.8.1"
BUILD = "24H32"
BLOCK = 4 * 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for data in iter(lambda: stream.read(BLOCK), b""):
            digest.update(data)
    return digest.hexdigest()


def verify_assets(root):
    matches = []
    for path in pathlib.Path(root).rglob("*.xml"):
        if "MobileAsset" not in path.name:
            continue
        try:
            metadata = plistlib.loads(path.read_bytes())
        except (ValueError, plistlib.InvalidFileException):
            continue
        for asset in metadata.get("Assets", []):
            if isinstance(asset, dict) and asset.get("OSVersion") == VERSION and asset.get("Build") == BUILD:
                matches.append({"file": str(path), "version": VERSION, "build": BUILD})
    if not matches:
        raise ValueError(f"SharedSupport metadata does not confirm {VERSION} ({BUILD})")
    print(json.dumps(matches, indent=2))


def validate_gpt(path):
    path = pathlib.Path(path)
    size = path.stat().st_size
    if size % 512:
        raise ValueError("Raw image is not aligned to 512-byte sectors")
    with path.open("rb") as stream:
        mbr = stream.read(512)
        if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE:
            raise ValueError("Missing GPT protective MBR")
        header = bytearray(stream.read(512))
        if header[:8] != b"EFI PART":
            raise ValueError("Missing GPT header")
        header_len = struct.unpack_from("<I", header, 12)[0]
        expected_crc = struct.unpack_from("<I", header, 16)[0]
        if not 92 <= header_len <= 512:
            raise ValueError("Invalid GPT header size")
        struct.pack_into("<I", header, 16, 0)
        if zlib.crc32(header[:header_len]) & 0xFFFFFFFF != expected_crc:
            raise ValueError("GPT header checksum mismatch")
        current, backup, first, last = struct.unpack_from("<QQQQ", header, 24)
        if current != 1 or backup != size // 512 - 1 or not 1 < first <= last < backup:
            raise ValueError("GPT image geometry mismatch")
        entries_lba, count, entry_size, entries_crc = struct.unpack_from("<QIII", header, 72)
        if count > 4096 or not 128 <= entry_size <= 4096 or count * entry_size > size:
            raise ValueError("Unexpected GPT partition table size")
        stream.seek(entries_lba * 512)
        entries = stream.read(count * entry_size)
        if zlib.crc32(entries) & 0xFFFFFFFF != entries_crc:
            raise ValueError("GPT partition table checksum mismatch")
        partitions = []
        for index in range(count):
            entry = entries[index * entry_size:(index + 1) * entry_size]
            if entry[:16] == b"\0" * 16:
                continue
            start, end = struct.unpack_from("<QQ", entry, 32)
            if not first <= start <= end <= last:
                raise ValueError("Partition outside image")
            partitions.append({"type": str(uuid.UUID(bytes_le=entry[:16])), "start_lba": start,
                               "end_lba": end, "name": entry[56:128].decode("utf-16-le").rstrip("\0")})
        ordered = sorted(partitions, key=lambda item: item["start_lba"])
        if any(a["end_lba"] >= b["start_lba"] for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Overlapping partitions")
        types = {part["type"] for part in partitions}
        required = {"c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "48465300-0000-11aa-aa11-00306543ecac",
                    "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"}
        if not required <= types:
            raise ValueError("Expected EFI, FAT32 support, and HFS+ installer partitions")
        stream.seek(backup * 512)
        back = bytearray(stream.read(512))
        if back[:8] != b"EFI PART":
            raise ValueError("Missing backup GPT")
        back_len, back_crc = struct.unpack_from("<II", back, 12)
        struct.pack_into("<I", back, 16, 0)
        if not 92 <= back_len <= 512 or zlib.crc32(back[:back_len]) & 0xFFFFFFFF != back_crc:
            raise ValueError("Backup GPT checksum mismatch")
        if struct.unpack_from("<QQ", back, 24) != (backup, current):
            raise ValueError("Backup GPT header geometry mismatch")
    return {"name": path.name, "size": size, "sha256": sha256(path), "partitions": partitions}


def split_image(gzip_path, raw_metadata, package_metadata, output):
    gzip_path = pathlib.Path(gzip_path)
    output = pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=True)
    parts = []
    # Keep each independently downloadable artifact below 2 GiB.
    part_limit = 1900 * 1024 * 1024
    with gzip_path.open("rb") as source:
        index = 1
        while True:
            first = source.read(min(BLOCK, part_limit))
            if not first:
                break
            name = f"sequoia-15.8.1.img.gz.part{index:03d}"
            dest = output / name
            digest = hashlib.sha256()
            written = 0
            with dest.open("xb") as target:
                data = first
                while data:
                    target.write(data)
                    digest.update(data)
                    written += len(data)
                    if written >= part_limit:
                        break
                    data = source.read(min(BLOCK, part_limit - written))
            parts.append({"name": name, "size": written, "sha256": digest.hexdigest()})
            index += 1
    if not 1 <= len(parts) <= 12:
        raise ValueError("Unexpected image part count")
    manifest = {"schema": 1, "version": VERSION, "build": BUILD,
                "source_package": json.loads(pathlib.Path(package_metadata).read_text()),
                "raw_image": json.loads(pathlib.Path(raw_metadata).read_text()),
                "compressed_image": {"size": gzip_path.stat().st_size, "sha256": sha256(gzip_path)},
                "parts": parts}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"parts": len(parts), "compressed_bytes": gzip_path.stat().st_size}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    assets = sub.add_parser("assets")
    assets.add_argument("root")
    gpt = sub.add_parser("gpt")
    gpt.add_argument("image")
    split = sub.add_parser("split")
    for arg in ("gzip", "raw_metadata", "package_metadata", "output"):
        split.add_argument(arg)
    args = parser.parse_args()
    if args.command == "assets":
        verify_assets(args.root)
    elif args.command == "gpt":
        print(json.dumps(validate_gpt(args.image), indent=2))
    else:
        split_image(args.gzip, args.raw_metadata, args.package_metadata, args.output)

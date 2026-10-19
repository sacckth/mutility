from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass


class DiskError(Exception):
    pass


@dataclass(frozen=True)
class FatPartition:
    device: str
    volume_name: str
    fat_label: str
    size: int
    protocol: str
    media_name: str
    content: str


def raw_node(device_node: str) -> str:
    prefix = "/dev/"
    if not device_node.startswith(prefix + "disk"):
        return device_node
    return prefix + "r" + device_node[len(prefix) :]


def preferred_device(device_node: str) -> str:
    raw = raw_node(device_node)
    if raw != device_node and os.path.exists(raw):
        return raw
    return device_node


def fat_label(info: dict) -> str | None:
    filesystem_type = str(info.get("FilesystemType") or "").casefold()
    visible = str(info.get("FilesystemUserVisibleName") or info.get("FilesystemName") or "")
    compact = "".join(character for character in visible.upper() if character.isalnum())
    if filesystem_type != "msdos" and not compact.startswith("MSDOS"):
        return None
    for label in ("FAT32", "FAT16", "FAT12"):
        if label in compact:
            return label
    return "FAT"


def partitions_from_listing(listing: dict, infos: dict[str, dict]) -> list[FatPartition]:
    found: list[FatPartition] = []
    for disk in listing.get("AllDisksAndPartitions", []):
        whole = str(disk.get("DeviceIdentifier") or "")
        parent = infos.get(whole, {})
        media_name = str(parent.get("MediaName") or parent.get("IORegistryEntryName") or whole)
        protocol = str(parent.get("BusProtocol") or "")
        if "Partitions" in disk:
            parts = disk.get("Partitions") or []
        else:
            parts = [disk]
        for part in parts:
            identifier = str(part.get("DeviceIdentifier") or "")
            if not identifier or identifier == whole and "Partitions" in disk:
                continue
            info = infos.get(identifier, {})
            label = fat_label(info)
            if label is None:
                continue
            found.append(
                FatPartition(
                    device=str(info.get("DeviceNode") or f"/dev/{identifier}"),
                    volume_name=str(info.get("VolumeName") or part.get("VolumeName") or ""),
                    fat_label=label,
                    size=int(info.get("Size") or part.get("Size") or 0),
                    protocol=protocol,
                    media_name=media_name,
                    content=str(info.get("Content") or part.get("Content") or ""),
                )
            )
    found.sort(key=lambda item: (item.content == "EFI", -item.size, item.device))
    return found


def discover_fat_partitions() -> list[FatPartition]:
    if shutil.which("diskutil") is None:
        raise DiskError("finding inserted disks requires macOS diskutil; pass a device or image path")
    listing = _plist(["diskutil", "list", "-plist", "external", "physical"])
    identifiers: list[str] = []
    for disk in listing.get("AllDisksAndPartitions", []):
        whole = disk.get("DeviceIdentifier")
        if whole:
            identifiers.append(str(whole))
        if "Partitions" in disk:
            identifiers.extend(
                str(part.get("DeviceIdentifier"))
                for part in disk.get("Partitions") or []
                if part.get("DeviceIdentifier")
            )
        elif whole:
            identifiers.append(str(whole))
    infos = {identifier: _plist(["diskutil", "info", "-plist", identifier]) for identifier in identifiers}
    partitions = partitions_from_listing(listing, infos)
    return [
        FatPartition(
            device=preferred_device(item.device),
            volume_name=item.volume_name,
            fat_label=item.fat_label,
            size=item.size,
            protocol=item.protocol,
            media_name=item.media_name,
            content=item.content,
        )
        for item in partitions
    ]


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1000


def format_partitions(partitions: list[FatPartition]) -> str:
    rows = []
    for index, part in enumerate(partitions, start=1):
        rows.append(
            (
                str(index),
                part.volume_name or "Untitled",
                part.fat_label,
                format_size(part.size),
                part.protocol,
                part.media_name,
                part.device,
            )
        )
    headers = ("#", "Name", "Type", "Size", "Bus", "Disk", "Device")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)).rstrip()

    lines = [line(headers), line(tuple("-" * width for width in widths))]
    lines.extend(line(row) for row in rows)
    return "\n".join(lines)


def choose_partition(partitions: list[FatPartition], interactive: bool, input_fn, output) -> FatPartition | None:
    if not partitions:
        output.write("No FAT partitions found on inserted disks.\n")
        return None
    if len(partitions) == 1:
        chosen = partitions[0]
        output.write(f"Using {chosen.volume_name or 'Untitled'} ({chosen.device}).\n")
        return chosen
    output.write("Inserted FAT partitions:\n")
    output.write(format_partitions(partitions) + "\n")
    if not interactive:
        output.write("Pass one of these devices explicitly.\n")
        return None
    prompt = f"Select a partition [1-{len(partitions)}]: "
    while True:
        try:
            answer = input_fn(prompt).strip()
        except EOFError:
            output.write("\n")
            return None
        if answer.lower() in {"q", "quit", "exit"}:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(partitions):
            return partitions[int(answer) - 1]
        output.write(f"Enter a number from 1 to {len(partitions)}.\n")


def _plist(args: list[str]) -> dict:
    result = subprocess.run(args, check=False, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip() or "diskutil failed"
        raise DiskError(message)
    try:
        loaded = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as exc:
        raise DiskError(f"diskutil returned unreadable output: {exc}") from exc
    if not isinstance(loaded, dict):
        raise DiskError("diskutil returned unexpected output")
    return loaded

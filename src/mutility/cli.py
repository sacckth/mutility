from __future__ import annotations

import argparse
import os
import subprocess
import sys

from mutility import __version__
from mutility.backend import MTools, MToolsError
from mutility.disks import DiskError, choose_partition, discover_fat_partitions, format_partitions
from mutility.onboarding import command_is_installed, path_hint, setup_instructions
from mutility.shell import Shell


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mutility",
        description="Open a shell of ordinary file commands on a FAT volume.",
    )
    parser.add_argument(
        "device",
        nargs="?",
        help="FAT device or image, such as /dev/rdisk4s2; omit to choose an inserted partition",
    )
    parser.add_argument(
        "-c",
        "--command",
        action="append",
        help="run a command instead of opening the shell; repeat for several commands",
    )
    parser.add_argument("--list", action="store_true", help="list inserted FAT partitions and exit")
    parser.add_argument("--setup", action="store_true", help="show how to add mutility to PATH")
    parser.add_argument("--ro", action="store_true", help="do not modify the volume")
    parser.add_argument("--version", action="version", version=f"mutility {__version__}")
    args = parser.parse_args(argv)

    if args.setup:
        print(setup_instructions(), end="")
        return 0

    if args.list:
        try:
            partitions = discover_fat_partitions()
        except DiskError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if partitions:
            print(format_partitions(partitions))
        else:
            print("No FAT partitions found on inserted disks.")
        return 0

    if args.device is None:
        try:
            partitions = discover_fat_partitions()
        except DiskError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        chosen = choose_partition(
            partitions,
            interactive=sys.stdin.isatty() and args.command is None,
            input_fn=input,
            output=sys.stderr,
        )
        if chosen is None:
            return 1
        device = chosen.device
    else:
        device = args.device if args.device.startswith("/dev/") else os.path.abspath(args.device)
        if not os.path.exists(device):
            print(f"no such device or image: {device}", file=sys.stderr)
            return 1
        if os.path.isdir(device):
            print("expected a device node or FAT image file", file=sys.stderr)
            return 1

    sudo = False
    if hasattr(os, "geteuid") and os.geteuid() != 0 and not os.access(device, os.R_OK):
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except subprocess.CalledProcessError:
            return 1
        sudo = True

    backend = MTools(device, sudo=sudo)
    try:
        backend.run_text("mdir", ["-f", "::/"])
    except MToolsError as exc:
        print(str(exc), file=sys.stderr)
        if not str(exc).startswith("cannot find "):
            print("the target does not look like a FAT filesystem", file=sys.stderr)
        return 1

    shell = Shell(backend, sys.stdout, sys.stderr, readonly=args.ro)
    if args.command is not None:
        return shell.run_commands(args.command)
    if not sys.stdin.isatty():
        return shell.run_commands(sys.stdin.read().splitlines())

    print(f"mutility: {device}", file=sys.stderr)
    print("Volume paths are normal paths. Mac paths use ~/... or local:PATH.", file=sys.stderr)
    print("Type help or ? to list commands.", file=sys.stderr)
    if not command_is_installed():
        print(path_hint(), file=sys.stderr)
    return shell.interact()

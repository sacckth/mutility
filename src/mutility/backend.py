from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


SEARCH_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


def find_program(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in SEARCH_PATHS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class MToolsError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Entry:
    path: str
    is_dir: bool


def normalize_fat(path: str) -> str:
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def to_mtools(path: str) -> str:
    path = normalize_fat(path)
    if path == "/":
        return "::/"
    return "::" + path


def parse_bare(line: str) -> Entry | None:
    text = line.strip()
    if not text.startswith("::"):
        return None
    raw = text[2:]
    if not raw.startswith("/"):
        raw = "/" + raw
    is_dir = raw.endswith("/")
    return Entry(normalize_fat(raw), is_dir)


def password_required(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == 0:
        return False
    stderr = result.stderr or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    return "password is required" in stderr.lower()


class MTools:
    def __init__(self, device: str, sudo: bool = False) -> None:
        self.device = device
        self.sudo = sudo

    def execute(self, command: list[str], *, text: bool = False) -> subprocess.CompletedProcess:
        if not self.sudo:
            return subprocess.run(command, check=False, stdin=subprocess.DEVNULL, capture_output=True, text=text)
        cached = subprocess.run(
            ["sudo", "-n", *command],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=text,
        )
        if not password_required(cached):
            return cached
        return subprocess.run(["sudo", *command], check=False, capture_output=True, text=text)

    def run(self, program: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        path = find_program(program)
        if path is None:
            raise MToolsError(f"cannot find {program}; install mtools")
        return self.execute([path, "-i", self.device, *args])

    def run_text(self, program: str, args: list[str]) -> str:
        result = self.run(program, args)
        stdout = result.stdout.decode("utf-8", "replace")
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            message = stderr or stdout.strip() or f"{program} failed"
            raise MToolsError(message, result.returncode or 1)
        return stdout

    def run_bytes(self, program: str, args: list[str]) -> bytes:
        result = self.run(program, args)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            stdout = result.stdout.decode("utf-8", "replace").strip()
            raise MToolsError(stderr or stdout or f"{program} failed", result.returncode or 1)
        return result.stdout

    def list_dir(self, path: str, all_entries: bool = True) -> list[Entry]:
        args = ["-b", "-f"]
        if all_entries:
            args.append("-a")
        args.append(to_mtools(path))
        stdout = self.run_text("mdir", args)
        entries = []
        for line in stdout.splitlines():
            entry = parse_bare(line)
            if entry is not None:
                entries.append(entry)
        return entries

    def entry(self, path: str) -> Entry | None:
        path = normalize_fat(path)
        if path == "/":
            return Entry("/", True)
        parent = os.path.dirname(path) or "/"
        name = os.path.basename(path)
        try:
            entries = self.list_dir(parent)
        except MToolsError:
            return None
        for item in entries:
            if os.path.basename(item.path).casefold() == name.casefold():
                return item
        return None

    def kind(self, path: str) -> str:
        item = self.entry(path)
        if item is None:
            return "missing"
        return "dir" if item.is_dir else "file"

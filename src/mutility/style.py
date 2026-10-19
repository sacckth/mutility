from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

DIRECTORY = "1;34"
ARCHIVE = "1;31"
IMAGE = "1;35"
AUDIO = "0;36"
VIDEO = "1;36"
EXECUTABLE = "1;32"
DOCUMENT = "0;33"
PROMPT_NAME = "1;36"
DIRTY = "1;33"
ERROR = "1;31"
DIM = "2"

EXTENSIONS = {
    ".7z": ARCHIVE,
    ".aac": AUDIO,
    ".avi": VIDEO,
    ".bat": EXECUTABLE,
    ".bmp": IMAGE,
    ".bz2": ARCHIVE,
    ".com": EXECUTABLE,
    ".exe": EXECUTABLE,
    ".flac": AUDIO,
    ".gif": IMAGE,
    ".gz": ARCHIVE,
    ".heic": IMAGE,
    ".jpeg": IMAGE,
    ".jpg": IMAGE,
    ".m4a": AUDIO,
    ".mkv": VIDEO,
    ".mov": VIDEO,
    ".mp3": AUDIO,
    ".mp4": VIDEO,
    ".pdf": DOCUMENT,
    ".png": IMAGE,
    ".rar": ARCHIVE,
    ".tar": ARCHIVE,
    ".tgz": ARCHIVE,
    ".tif": IMAGE,
    ".tiff": IMAGE,
    ".wav": AUDIO,
    ".webp": IMAGE,
    ".zip": ARCHIVE,
}


def colors_enabled(stream) -> bool:
    choice = os.environ.get("MUTILITY_COLOR", "").casefold()
    if choice == "always":
        return True
    if choice == "never" or os.environ.get("NO_COLOR") is not None:
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def paint(text: str, code: str, enabled: bool) -> str:
    if not enabled or not code:
        return text
    return f"\033[{code}m{text}\033[0m"


def color_entry(name: str, enabled: bool) -> str:
    if name.endswith("/"):
        return paint(name, DIRECTORY, enabled)
    extension = os.path.splitext(name)[1].casefold()
    return paint(name, EXTENSIONS.get(extension, ""), enabled)


def color_mdir(text: str, enabled: bool) -> str:
    if not text:
        return text
    if not enabled:
        return text if text.endswith("\n") else text + "\n"
    colored = []
    for line in text.splitlines():
        if "<DIR>" in line:
            colored.append(paint(line, DIRECTORY, True))
        elif line.strip().startswith("Volume") or "bytes" in line:
            colored.append(paint(line, DIM, True))
        else:
            colored.append(line)
    return "\n".join(colored) + "\n"


def render_prompt(cwd: str, needs_sync: bool, status: int, enabled: bool) -> str:
    if enabled:
        rendered = _starship_prompt(cwd, needs_sync, status)
        if rendered:
            return rendered
    return _fallback_prompt(cwd, needs_sync, status, enabled)


def _fallback_prompt(cwd: str, needs_sync: bool, status: int, enabled: bool) -> str:
    path = paint(cwd, "1;36", enabled)
    line = f"mutility {path}"
    if needs_sync:
        line += " " + paint("sync", DIRTY, enabled)
    arrow = paint("❯", "1;31" if status else "1;32", enabled)
    return f"\n{line}\n{arrow} "


def _starship_prompt(cwd: str, needs_sync: bool, status: int) -> str | None:
    starship = shutil.which("starship")
    config = Path(__file__).with_name("starship.toml")
    if starship is None or not config.is_file():
        return None
    env = os.environ.copy()
    env["TERM"] = env.get("TERM") if env.get("TERM") not in (None, "", "dumb") else "xterm-256color"
    env["STARSHIP_SHELL"] = "ion"
    env["STARSHIP_CONFIG"] = str(config)
    env.pop("MUTILITY_NEEDS_SYNC", None)
    if needs_sync:
        env["MUTILITY_NEEDS_SYNC"] = "1"
    try:
        result = subprocess.run(
            [starship, "prompt", "--logical-path", cwd, "--status", str(status)],
            check=False,
            capture_output=True,
            env=env,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.decode("utf-8", "replace")

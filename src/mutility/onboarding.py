from __future__ import annotations

import os
from pathlib import Path

REPOSITORY = "https://github.com/sacckth/mutility"
RELEASE = "v0.1.0"


def project_directory() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "mutility").is_dir():
            return parent
    return Path.home() / "wk" / "mutility"


def tool_directory() -> Path:
    override = os.environ.get("UV_TOOL_BIN_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "bin"


def command_is_installed() -> bool:
    candidate = tool_directory() / "mutility"
    return candidate.is_file() and os.access(candidate, os.X_OK)


def setup_instructions() -> str:
    root = project_directory()
    destination = tool_directory()
    return (
        f"Install mutility {RELEASE} from GitHub and add it to your PATH:\n"
        "\n"
        f"  uv tool install git+{REPOSITORY}@{RELEASE}\n"
        "  uv tool update-shell\n"
        "\n"
        f"Omit @{RELEASE} to install the latest commit from the default branch.\n"
        "\n"
        "Open a new terminal. uv tool update-shell adds the tool directory\n"
        f"({destination}) to your shell startup file when it is missing.\n"
        "Then run:\n"
        "\n"
        "  mutility\n"
        "\n"
        "To use this checkout, so local edits apply without reinstalling:\n"
        "\n"
        f"  cd {root}\n"
        "  uv tool install --editable .\n"
        "  uv tool update-shell\n"
    )


def path_hint() -> str:
    return (
        "mutility is not on your PATH yet. Install it with:\n"
        f"  uv tool install git+{REPOSITORY}@{RELEASE}\n"
        "  uv tool update-shell\n"
        "Open a new terminal afterward. Run mutility --setup for details."
    )

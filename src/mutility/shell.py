from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from mutility.backend import Entry, MTools, MToolsError, normalize_fat, to_mtools
from mutility.style import (
    ERROR,
    color_entry,
    color_mdir,
    colors_enabled,
    paint,
    readline_safe,
    render_prompt,
)


class UsageError(Exception):
    pass


class ShellExit(Exception):
    pass


@dataclass(frozen=True)
class Location:
    kind: str
    path: str


ALIASES = {"?": "help"}

COMMANDS = (
    "?",
    "attrib",
    "cat",
    "cd",
    "cp",
    "df",
    "du",
    "eject",
    "exit",
    "find",
    "help",
    "lcd",
    "lpwd",
    "ls",
    "mkdir",
    "mv",
    "pwd",
    "quit",
    "rm",
    "rmdir",
    "sync",
    "touch",
)

HELP = """\
Volume paths are normal paths. Mac paths use ~/... or local:PATH.
The prompt is rendered by Starship when it is installed.
A yellow sync marker means the volume has unflushed writes. Run sync or eject before unplugging it.
The arrow is green after a successful command and red after a failed one.

ls [-l] [-a] [-R] [path...]     list the volume
cp [-r] source... dest          copy, including between the Mac and the volume
mv source... dest               move or rename
rm [-r] [-f] path...            delete volume files
mkdir [-p] directory...         create directories
rmdir directory...              remove empty directories
cat path...                     print files
touch path...                   create an empty file when it is missing
cd [path]                       change the volume directory
pwd                             print the volume directory
lcd [path]                      change the Mac directory used by local: paths
lpwd                            print the Mac directory
du [-a] [-s] [path]             show space in FAT clusters
find [path] [-name pattern]     list files recursively
df                              show volume size
attrib [+r|-r|+h|-h|+s|-s|+a|-a] path...
sync                            flush writes
eject                           eject a /dev/disk device and leave the shell
!command                        run a command on the Mac
help, ?                         show this help
exit                            leave the shell
"""


def split_statements(line: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char == ";":
            text = "".join(current).strip()
            if text:
                parts.append(text)
            current = []
            continue
        current.append(char)
    if quote:
        raise UsageError("unclosed quote")
    text = "".join(current).strip()
    if text:
        parts.append(text)
    return parts


def split_flags(argv: list[str], allowed: set[str]) -> tuple[set[str], list[str]]:
    flags: set[str] = set()
    rest: list[str] = []
    ended = False
    for arg in argv:
        if ended or arg == "-" or not arg.startswith("-"):
            rest.append(arg)
            continue
        if arg == "--":
            ended = True
            continue
        if arg.startswith("--"):
            raise UsageError(f"unknown option: {arg}")
        for char in arg[1:]:
            if char not in allowed:
                raise UsageError(f"unknown option: -{char}")
            flags.add(char)
    return flags, rest


class Shell:
    def __init__(
        self,
        backend: MTools,
        stdout,
        stderr,
        readonly: bool = False,
        local_cwd: str | None = None,
    ) -> None:
        self.backend = backend
        self.stdout = stdout
        self.stderr = stderr
        self.readonly = readonly
        self.cwd = "/"
        self.previous = "/"
        self.needs_sync = False
        self.last_status = 0
        self.local_cwd = os.path.abspath(local_cwd or os.getcwd())

    def run_commands(self, lines) -> int:
        code = 0
        for line in lines:
            try:
                status = self.run_line(line)
            except ShellExit:
                return code
            if status != 0:
                code = status
        return code

    def run_line(self, line: str) -> int:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return 0
        try:
            statements = split_statements(stripped)
        except UsageError as exc:
            self.last_status = 1
            return self._fail(str(exc))
        status = 0
        for statement in statements:
            try:
                status = self.run_statement(statement)
            except ShellExit:
                raise
            except UsageError as exc:
                status = self._fail(str(exc))
        self.last_status = status
        return status

    def run_statement(self, statement: str) -> int:
        if statement.startswith("!"):
            completed = subprocess.run(statement[1:], shell=True)
            return completed.returncode
        try:
            argv = shlex.split(statement)
        except ValueError as exc:
            return self._fail(str(exc))
        if not argv:
            return 0
        command = ALIASES.get(argv[0], argv[0])
        handler = getattr(self, f"do_{command}", None)
        if handler is None:
            return self._fail(f"unknown command: {command}")
        return handler(argv[1:])

    def interact(self) -> int:
        import readline

        history = os.path.expanduser("~/.mutility_history")
        try:
            readline.read_history_file(history)
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)
        readline.set_completer_delims(" \t\n;")
        if "libedit" in (readline.__doc__ or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        readline.set_completer(self._complete)
        try:
            while True:
                try:
                    line = input(readline_safe(self.prompt()))
                except EOFError:
                    self.stdout.write("\n")
                    return 0
                except KeyboardInterrupt:
                    self.stdout.write("\n")
                    continue
                try:
                    self.run_line(line)
                except ShellExit:
                    return 0
        finally:
            try:
                readline.write_history_file(history)
            except OSError:
                pass

    def complete_token(self, before: str, token: str) -> list[str]:
        if before.strip() == "" or before.rstrip().endswith(";"):
            return [name for name in COMMANDS if name.startswith(token)]
        return self._complete_path(token)

    def prompt(self) -> str:
        return render_prompt(
            self.cwd,
            self.needs_sync,
            self.last_status,
            colors_enabled(self.stdout),
        )

    def do_help(self, argv: list[str]) -> int:
        self.stdout.write(HELP)
        return 0

    def do_exit(self, argv: list[str]) -> int:
        raise ShellExit

    def do_quit(self, argv: list[str]) -> int:
        raise ShellExit

    def do_pwd(self, argv: list[str]) -> int:
        if argv:
            return self._usage("pwd")
        self.stdout.write(self.cwd + "\n")
        return 0

    def do_lpwd(self, argv: list[str]) -> int:
        if argv:
            return self._usage("lpwd")
        self.stdout.write(self.local_cwd + "\n")
        return 0

    def do_lcd(self, argv: list[str]) -> int:
        if len(argv) > 1:
            return self._usage("lcd [path]")
        raw = argv[0] if argv else "~"
        path = self._host_path(raw if raw.startswith("~") or raw.startswith("/") else "local:" + raw)
        if not os.path.isdir(path):
            return self._fail(f"not a directory: {path}")
        self.local_cwd = path
        return 0

    def do_cd(self, argv: list[str]) -> int:
        if len(argv) > 1:
            return self._usage("cd [path]")
        token = argv[0] if argv else "/"
        if token == "-":
            token = self.previous
        try:
            loc = self.resolve(token)
        except UsageError as exc:
            return self._fail(str(exc))
        if loc.kind != "fat":
            return self._fail("cd stays on the volume; use lcd for a Mac directory")
        item = self.backend.entry(loc.path)
        if item is None:
            return self._fail(f"no such directory: {loc.path}")
        if not item.is_dir:
            return self._fail(f"not a directory: {item.path}")
        self.previous = self.cwd
        self.cwd = item.path
        return 0

    def do_ls(self, argv: list[str]) -> int:
        try:
            flags, args = split_flags(argv, {"l", "a", "R"})
            targets = self._targets(args)
        except UsageError as exc:
            return self._fail(str(exc))
        if any(item.kind != "fat" for item in targets):
            return self._fail("ls lists the volume; use !ls for Mac files")
        enabled = colors_enabled(self.stdout)
        if "l" in flags or "R" in flags:
            command = ["-f"]
            if "a" in flags:
                command.append("-a")
            if "R" in flags:
                command.append("-/")
            command.extend(to_mtools(item.path) for item in targets)
            try:
                stdout = self.backend.run_text("mdir", command)
            except MToolsError as exc:
                return self._fail(str(exc))
            self.stdout.write(color_mdir(stdout, enabled))
            return 0
        status = 0
        directories = [item for item in targets if self.backend.kind(item.path) == "dir"]
        for target in targets:
            kind = self.backend.kind(target.path)
            if kind == "missing":
                status = self._fail(f"no such file: {target.path}")
                continue
            if kind == "file":
                self.stdout.write(color_entry(os.path.basename(target.path), enabled) + "\n")
                continue
            if len(directories) > 1 or (directories and len(targets) > 1):
                self.stdout.write(paint(target.path + ":", "1", enabled) + "\n")
            try:
                entries = self.backend.list_dir(target.path, all_entries="a" in flags)
            except MToolsError as exc:
                status = self._fail(str(exc))
                continue
            for name in self._names(entries):
                self.stdout.write(color_entry(name, enabled) + "\n")
        return status

    def do_cat(self, argv: list[str]) -> int:
        if not argv:
            return self._usage("cat path...")
        try:
            targets = self._expand_many(argv)
        except UsageError as exc:
            return self._fail(str(exc))
        status = 0
        for target in targets:
            if target.kind == "host":
                try:
                    with open(target.path, "rb") as handle:
                        self._write_bytes(handle.read())
                except OSError as exc:
                    status = self._fail(str(exc))
                continue
            if self.backend.kind(target.path) == "dir":
                status = self._fail(f"is a directory: {target.path}")
                continue
            try:
                self._write_bytes(self.backend.run_bytes("mtype", [to_mtools(target.path)]))
            except MToolsError as exc:
                status = self._fail(str(exc))
        return status

    def do_mkdir(self, argv: list[str]) -> int:
        try:
            flags, args = split_flags(argv, {"p"})
        except UsageError as exc:
            return self._fail(str(exc))
        if not args:
            return self._usage("mkdir [-p] directory...")
        if self.readonly:
            return self._ro()
        status = 0
        for arg in args:
            try:
                locations = self.expand(arg)
            except UsageError as exc:
                status = self._fail(str(exc))
                continue
            for loc in locations:
                if loc.kind != "fat":
                    status = self._fail("mkdir creates volume directories")
                    continue
                result = self._mkdir(loc.path, parents="p" in flags)
                if result != 0:
                    status = result
        return status

    def do_rmdir(self, argv: list[str]) -> int:
        if not argv:
            return self._usage("rmdir directory...")
        if self.readonly:
            return self._ro()
        return self._delete_many(argv, directories_only=True, recursive=False, force=False)

    def do_rm(self, argv: list[str]) -> int:
        try:
            flags, args = split_flags(argv, {"r", "R", "f"})
        except UsageError as exc:
            return self._fail(str(exc))
        if not args:
            return self._usage("rm [-r] [-f] path...")
        if self.readonly:
            return self._ro()
        return self._delete_many(
            args,
            directories_only=False,
            recursive=bool(flags & {"r", "R"}),
            force="f" in flags,
        )

    def do_cp(self, argv: list[str]) -> int:
        try:
            flags, args = split_flags(argv, {"r", "R", "f"})
            if len(args) < 2:
                return self._usage("cp [-r] source... dest")
            sources = self._expand_many(args[:-1])
            dest = self.resolve(args[-1])
        except UsageError as exc:
            return self._fail(str(exc))
        if self.readonly and dest.kind == "fat":
            return self._ro()
        recursive = bool(flags & {"r", "R"})
        if len({item.kind for item in sources}) != 1:
            return self._fail("all sources must be on the same side")
        if sources[0].kind == "host" and dest.kind == "host":
            return self._cp_host(sources, dest, recursive)
        return self._mcopy(sources, dest, recursive)

    def do_mv(self, argv: list[str]) -> int:
        try:
            _flags, args = split_flags(argv, {"f"})
            if len(args) < 2:
                return self._usage("mv source... dest")
            sources = self._expand_many(args[:-1])
            dest = self.resolve(args[-1])
        except UsageError as exc:
            return self._fail(str(exc))
        involves_volume = dest.kind == "fat" or any(item.kind == "fat" for item in sources)
        if self.readonly and involves_volume:
            return self._ro()
        if len({item.kind for item in sources}) != 1:
            return self._fail("all sources must be on the same side")
        if sources[0].kind == "fat" and dest.kind == "fat":
            command = ["-D", "o"]
            command.extend(to_mtools(item.path) for item in sources)
            command.append(to_mtools(dest.path))
            status = self._mtools("mmove", command)
            if status == 0:
                self.needs_sync = True
            return status
        if sources[0].kind == "host" and dest.kind == "host":
            return self._mv_host(sources, dest)
        status = self._mcopy(sources, dest, True)
        if status != 0:
            return status
        return self._remove_copied(sources)

    def do_touch(self, argv: list[str]) -> int:
        if not argv:
            return self._usage("touch path...")
        if self.readonly:
            return self._ro()
        try:
            targets = self._expand_many(argv)
        except UsageError as exc:
            return self._fail(str(exc))
        status = 0
        for target in targets:
            if target.kind != "fat":
                status = self._fail("touch creates volume files")
                continue
            kind = self.backend.kind(target.path)
            if kind == "dir":
                status = self._fail(f"is a directory: {target.path}")
                continue
            if kind == "file":
                continue
            with tempfile.NamedTemporaryFile() as handle:
                result = self._mtools("mcopy", ["-n", "-D", "o", handle.name, to_mtools(target.path)])
            if result == 0:
                self.needs_sync = True
            else:
                status = result
        return status

    def do_du(self, argv: list[str]) -> int:
        try:
            flags, args = split_flags(argv, {"a", "s"})
            targets = self._targets(args)
        except UsageError as exc:
            return self._fail(str(exc))
        if any(item.kind != "fat" for item in targets):
            return self._fail("du measures the volume")
        command = []
        if "a" in flags:
            command.append("-a")
        if "s" in flags:
            command.append("-s")
        command.extend(to_mtools(item.path) for item in targets)
        return self._mtools("mdu", command)

    def do_find(self, argv: list[str]) -> int:
        path = self.cwd
        pattern = None
        args = list(argv)
        try:
            if args and args[0] != "-name":
                loc = self.resolve(args[0])
                if loc.kind != "fat":
                    return self._fail("find searches the volume")
                path = loc.path
                args = args[1:]
            if args:
                if len(args) == 2 and args[0] == "-name":
                    pattern = args[1]
                else:
                    return self._usage("find [path] [-name pattern]")
        except UsageError as exc:
            return self._fail(str(exc))
        try:
            stdout = self.backend.run_text("mdir", ["-/", "-b", "-a", "-f", to_mtools(path)])
        except MToolsError as exc:
            return self._fail(str(exc))
        rows = [path + ("/" if self.backend.kind(path) == "dir" and path != "/" else "")]
        for line in stdout.splitlines():
            entry = _entry_from_line(line)
            if entry is None or entry.path == path:
                continue
            shown = entry.path + ("/" if entry.is_dir else "")
            rows.append(shown)
        for shown in rows:
            name = shown.rstrip("/").split("/")[-1] or "/"
            if pattern is not None and not fnmatch.fnmatch(name.casefold(), pattern.casefold()):
                continue
            self.stdout.write(shown + "\n")
        return 0

    def do_df(self, argv: list[str]) -> int:
        if argv:
            return self._usage("df")
        try:
            stdout = self.backend.run_text("mdir", ["-f", "::/"])
        except MToolsError as exc:
            return self._fail(str(exc))
        for line in stdout.splitlines():
            compact = line.strip()
            if compact.startswith("Volume") or "bytes" in compact or compact.endswith("files"):
                self.stdout.write(line.rstrip() + "\n")
        return 0

    def do_attrib(self, argv: list[str]) -> int:
        if not argv:
            return self._usage("attrib [+r|-r|+h|-h|+s|-s|+a|-a] path...")
        ops = []
        paths = []
        for arg in argv:
            if len(arg) == 2 and arg[0] in "+-" and arg[1] in "rahs":
                ops.append(arg)
            else:
                paths.append(arg)
        if not paths:
            return self._usage("attrib [+r|-r|+h|-h|+s|-s|+a|-a] path...")
        if ops and self.readonly:
            return self._ro()
        try:
            targets = self._expand_many(paths)
        except UsageError as exc:
            return self._fail(str(exc))
        if any(item.kind != "fat" for item in targets):
            return self._fail("attrib changes volume files")
        status = self._mtools("mattrib", [*ops, *[to_mtools(item.path) for item in targets]])
        if status == 0 and ops:
            self.needs_sync = True
        return status

    def do_sync(self, argv: list[str]) -> int:
        if argv:
            return self._usage("sync")
        completed = subprocess.run(["sync"])
        if completed.returncode == 0:
            self.needs_sync = False
        return completed.returncode

    def do_eject(self, argv: list[str]) -> int:
        if argv:
            return self._usage("eject")
        match = re.fullmatch(r"/dev/r?disk(\d+)(?:s\d+)?", self.backend.device)
        if match is None:
            return self._fail("eject works on /dev/disk devices, not image files")
        if subprocess.run(["sync"], check=False).returncode == 0:
            self.needs_sync = False
        command = ["diskutil", "eject", "/dev/disk" + match.group(1)]
        completed = self.backend.execute(command, text=True)
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            return self._fail(message or "eject failed")
        if completed.stdout:
            self.stdout.write(completed.stdout)
        raise ShellExit

    def resolve(self, token: str) -> Location:
        if token.startswith("local:"):
            return Location("host", self._host_path(token))
        if token == "~" or token.startswith("~/"):
            return Location("host", os.path.abspath(os.path.expanduser(token)))
        fat = token[2:] if token.startswith("::") else token
        if fat and not fat.startswith("/"):
            fat = self.cwd.rstrip("/") + "/" + fat
        return Location("fat", normalize_fat(fat or "/"))

    def expand(self, token: str) -> list[Location]:
        loc = self.resolve(token)
        if loc.kind == "host":
            if not _has_glob(loc.path):
                return [loc]
            matches = sorted(glob.glob(loc.path))
            if not matches:
                raise UsageError(f"no matches: {token}")
            return [Location("host", match) for match in matches]
        parts = [part for part in loc.path.split("/") if part]
        if any(_has_glob(part) for part in parts[:-1]):
            raise UsageError("glob is only supported in the final path component")
        pattern = parts[-1] if parts else ""
        if not _has_glob(pattern):
            return [loc]
        parent = "/" + "/".join(parts[:-1])
        matches = []
        for entry in self.backend.list_dir(parent):
            name = os.path.basename(entry.path)
            if name.startswith(".") and not pattern.startswith("."):
                continue
            if fnmatch.fnmatch(name.casefold(), pattern.casefold()):
                matches.append(Location("fat", entry.path))
        if not matches:
            raise UsageError(f"no matches: {token}")
        return sorted(matches, key=lambda item: item.path.casefold())

    def _complete(self, text: str, state: int):
        import readline

        if state == 0:
            before = readline.get_line_buffer()[: readline.get_begidx()]
            self._matches = self.complete_token(before, text)
        if state < len(self._matches):
            return self._matches[state]
        return None

    def _complete_path(self, token: str) -> list[str]:
        try:
            if token.startswith("local:") or token == "~" or token.startswith("~/"):
                return self._complete_host(token)
            return self._complete_fat(token)
        except (MToolsError, UsageError, OSError):
            return []

    def _complete_fat(self, token: str) -> list[str]:
        if token.endswith("/"):
            directory = self.resolve(token).path
            partial = ""
            display = token
        elif "/" in token:
            parent, partial = token.rsplit("/", 1)
            directory = self.resolve(parent + "/").path
            display = parent + "/"
        else:
            directory = self.cwd
            partial = token
            display = ""
        matches = []
        for entry in self.backend.list_dir(directory, all_entries=False):
            name = os.path.basename(entry.path)
            if partial and not name.casefold().startswith(partial.casefold()):
                continue
            suffix = "/" if entry.is_dir else ""
            matches.append(display + name + suffix)
        return sorted(matches, key=str.casefold)

    def _complete_host(self, token: str) -> list[str]:
        prefix = "local:" if token.startswith("local:") else ""
        raw = token[len("local:") :] if prefix else token
        if raw.endswith("/"):
            directory = self._host_path(prefix + raw)
            partial = ""
            display = token
        else:
            parent, partial = os.path.split(raw)
            base = (prefix + parent + "/") if parent else prefix
            directory = self._host_path(base or ("~" if raw.startswith("~") else "local:"))
            display = base
        if not os.path.isdir(directory):
            return []
        matches = []
        for name in os.listdir(directory):
            if partial and not name.casefold().startswith(partial.casefold()):
                continue
            path = os.path.join(directory, name)
            suffix = "/" if os.path.isdir(path) else ""
            matches.append(display + name + suffix)
        return sorted(matches, key=str.casefold)

    def _targets(self, args: list[str]) -> list[Location]:
        if not args:
            return [Location("fat", self.cwd)]
        return self._expand_many(args)

    def _expand_many(self, args: list[str]) -> list[Location]:
        found = []
        for arg in args:
            found.extend(self.expand(arg))
        return found

    def _mkdir(self, path: str, parents: bool) -> int:
        path = normalize_fat(path)
        if path == "/":
            return 0 if parents else self._fail("cannot create /")
        if parents:
            current = ""
            for part in path.strip("/").split("/"):
                current += "/" + part
                kind = self.backend.kind(current)
                if kind == "dir":
                    continue
                if kind == "file":
                    return self._fail(f"not a directory: {current}")
                try:
                    self.backend.run_text("mmd", [to_mtools(current)])
                except MToolsError as exc:
                    return self._fail(str(exc))
                self.needs_sync = True
            return 0
        if self.backend.kind(path) != "missing":
            return self._fail(f"already exists: {path}")
        parent = os.path.dirname(path) or "/"
        if self.backend.kind(parent) != "dir":
            return self._fail(f"parent does not exist: {path}")
        try:
            self.backend.run_text("mmd", [to_mtools(path)])
        except MToolsError as exc:
            return self._fail(str(exc))
        self.needs_sync = True
        return 0

    def _delete_many(self, args: list[str], directories_only: bool, recursive: bool, force: bool) -> int:
        try:
            targets = self._expand_many(args)
        except UsageError as exc:
            return self._fail(str(exc))
        status = 0
        for target in targets:
            if target.kind != "fat":
                status = self._fail("this command deletes volume files; use !rm for Mac files")
                continue
            if target.path == "/":
                status = self._fail("refusing to remove the volume root")
                continue
            kind = self.backend.kind(target.path)
            if kind == "missing":
                if not force:
                    status = self._fail(f"no such file: {target.path}")
                continue
            if directories_only and kind != "dir":
                status = self._fail(f"not a directory: {target.path}")
                continue
            if kind == "dir" and not recursive and not directories_only:
                status = self._fail(f"is a directory: {target.path}")
                continue
            result = self._delete(target.path, directory=kind == "dir")
            if result != 0:
                status = result
        return status

    def _delete(self, path: str, directory: bool) -> int:
        spec = to_mtools(path)
        if directory:
            status = self._mtools("mdeltree", [spec])
        else:
            self.backend.run("mattrib", ["-r", spec])
            status = self._mtools("mdel", [spec])
        if status == 0:
            self.needs_sync = True
        return status

    def _remove_copied(self, sources: list[Location]) -> int:
        status = 0
        for source in sources:
            if source.kind == "host":
                try:
                    if os.path.isdir(source.path) and not os.path.islink(source.path):
                        shutil.rmtree(source.path)
                    else:
                        os.remove(source.path)
                except OSError as exc:
                    status = self._fail(str(exc))
                continue
            kind = self.backend.kind(source.path)
            if kind == "missing":
                continue
            result = self._delete(source.path, directory=kind == "dir")
            if result != 0:
                status = result
        return status

    def _mcopy(self, sources: list[Location], dest: Location, recursive: bool) -> int:
        command = ["-n", "-D", "o"]
        if recursive:
            command.append("-s")
        command.extend(self._spec(item) for item in sources)
        command.append(self._spec(dest))
        status = self._mtools("mcopy", command)
        if status == 0 and dest.kind == "fat":
            self.needs_sync = True
        return status

    def _cp_host(self, sources: list[Location], dest: Location, recursive: bool) -> int:
        if len(sources) > 1 and not os.path.isdir(dest.path):
            return self._fail(f"not a directory: {dest.path}")
        for source in sources:
            target = dest.path
            if os.path.isdir(dest.path):
                target = os.path.join(dest.path, os.path.basename(source.path))
            try:
                if os.path.isdir(source.path) and not os.path.islink(source.path):
                    if not recursive:
                        return self._fail(f"omitting directory: {source.path}")
                    shutil.copytree(source.path, target, dirs_exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy(source.path, target)
            except OSError as exc:
                return self._fail(str(exc))
        return 0

    def _mv_host(self, sources: list[Location], dest: Location) -> int:
        if len(sources) > 1 and not os.path.isdir(dest.path):
            return self._fail(f"not a directory: {dest.path}")
        for source in sources:
            target = dest.path
            if os.path.isdir(dest.path):
                target = os.path.join(dest.path, os.path.basename(source.path))
            try:
                shutil.move(source.path, target)
            except OSError as exc:
                return self._fail(str(exc))
        return 0

    def _spec(self, loc: Location) -> str:
        if loc.kind == "fat":
            return to_mtools(loc.path)
        return loc.path

    def _host_path(self, token: str) -> str:
        raw = token[len("local:") :] if token.startswith("local:") else token
        if raw == "":
            raw = "."
        raw = os.path.expanduser(raw)
        if not os.path.isabs(raw):
            raw = os.path.join(self.local_cwd, raw)
        return os.path.abspath(raw)

    def _mtools(self, program: str, args: list[str]) -> int:
        try:
            stdout = self.backend.run_text(program, args)
        except MToolsError as exc:
            return self._fail(str(exc))
        if stdout:
            self.stdout.write(stdout if stdout.endswith("\n") else stdout + "\n")
        return 0

    def _names(self, entries: list[Entry]) -> list[str]:
        names = []
        for entry in entries:
            name = os.path.basename(entry.path)
            if name in (".", ".."):
                continue
            names.append(name + ("/" if entry.is_dir else ""))
        return sorted(names, key=str.casefold)

    def _write_bytes(self, data: bytes) -> None:
        buffer = getattr(self.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(data)
            buffer.flush()
            return
        self.stdout.write(data.decode("utf-8", "surrogateescape"))

    def _usage(self, text: str) -> int:
        return self._fail("usage: " + text)

    def _ro(self) -> int:
        return self._fail("volume is read-only")

    def _fail(self, text: str) -> int:
        message = text.rstrip()
        self.stderr.write(paint(message, ERROR, colors_enabled(self.stderr)) + "\n")
        return 1


def _has_glob(text: str) -> bool:
    return any(char in text for char in "*?[")


def _entry_from_line(line: str) -> Entry | None:
    from mutility.backend import parse_bare

    return parse_bare(line)

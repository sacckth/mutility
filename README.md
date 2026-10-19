# mutility

`mutility` provides a small Unix-like shell for FAT12, FAT16, and FAT32
volumes that macOS cannot mount. It translates familiar file commands to
[`mtools`](https://www.gnu.org/software/mtools/) without mounting the volume.

It does not support exFAT, NTFS, ext2/3/4, or other filesystems.

## Requirements

- Python 3.9 or newer
- [`uv`](https://docs.astral.sh/uv/)
- `mtools` (`brew install mtools` on macOS)
- macOS `diskutil`, when discovering inserted disks

## Install

Install the command from GitHub and add it to your `PATH`:

```sh
uv tool install git+https://github.com/sacckth/mutility
uv tool update-shell
```

Open a new terminal. `uv tool update-shell` adds uv's tool directory, usually
`~/.local/bin`, to your shell startup file. Then run `mutility` from any
directory.

`mutility --setup` prints these steps again. Until the command is installed,
an interactive session also prints them.

To work on a local checkout:

```sh
git clone https://github.com/sacckth/mutility
cd mutility
uv sync
uv tool install --editable .
uv tool update-shell
```

## Use

With no device, `mutility` lists FAT partitions on inserted disks. If only one
is present, it opens that partition. If several are present, it asks you to
choose:

```sh
uv run mutility
```

List the choices without opening a shell:

```sh
uv run mutility --list
```

You can still pass a FAT partition device or disk image:

```sh
uv run mutility /dev/rdisk4s2
```

`mutility` asks for administrator authentication when the device is not
readable by the current user.

Inside the shell, ordinary paths refer to the FAT volume. Paths beginning
with `~/` and `local:` refer to the Mac:

```text
mutility / 
❯ ls
mutility /documents sync
❯ cp ~/fu.it /documents/
mutility:/> cat /documents/fu.it
mutility:/> cp -r /documents local:backup
mutility:/> exit
```

Supported commands include `ls`, `cp`, `mv`, `rm`, `mkdir`, `rmdir`, `cat`,
`touch`, `cd`, `pwd`, `find`, `du`, `df`, `attrib`, `sync`, and `eject`.
Prefix a command with `!` to run it on the Mac.

Use `help` or `?` inside the shell:

```text
mutility:/> help
mutility:/> ?
```

Use `--help` for command-line options:

```sh
uv run mutility --help
```

Run one or more commands without opening the interactive shell:

```sh
uv run mutility /dev/rdisk4s2 \
  -c 'mkdir backup' \
  -c 'cp ~/fu.it /backup/' \
  -c 'ls /backup'
```

A scripted command does not guess when more than one FAT partition is
inserted. Pass the device shown by `--list`.

Use `--ro` to prevent commands that modify the FAT volume:

```sh
uv run mutility --ro /dev/rdisk4s2
```

The prompt uses [Starship](https://starship.rs/) when `starship` is installed,
with the same two-line layout otherwise. Directories are blue in `ls`, and
common file types are colored as well. Plain output is used when the output
is not a terminal or `NO_COLOR` is set.

A yellow `sync` means a copy, move, or delete has not been flushed. The arrow
is green after success and red after a failed command:

```text
mutility /documents sync
❯
```

Flush writes and eject before removing a physical drive:

```text
mutility /
❯ sync
mutility /
❯ eject
```

## Test

The tests create temporary FAT images and do not access a physical disk:

```sh
uv run python -m unittest discover -s tests -v
```

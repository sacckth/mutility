import io
import os
import subprocess
import sys
import tempfile
import unittest

from mutility.backend import MTools, find_program, normalize_fat
from mutility.shell import Shell


class _Tty(io.StringIO):
    def isatty(self):
        return True


class FindProgramTest(unittest.TestCase):
    def test_finds_mtools_and_reports_missing(self):
        found = find_program("mdir")
        self.assertTrue(found and found.endswith("/mdir"))
        self.assertIsNone(find_program("mutility-not-a-program"))


class NormalizeTest(unittest.TestCase):
    def test_parent_segments(self):
        self.assertEqual(normalize_fat("/a/../b/./c"), "/b/c")
        self.assertEqual(normalize_fat("/../.."), "/")


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.tmp.name, "disk.img")
        subprocess.check_call(
            ["dd", "if=/dev/zero", f"of={self.image}", "bs=1M", "count=4", "status=none"]
        )
        subprocess.check_call(["mformat", "-i", self.image, "-v", "TEST", "::"])
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.shell = Shell(
            MTools(self.image),
            self.stdout,
            self.stderr,
            local_cwd=self.tmp.name,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_shell(self, line):
        self.stdout.seek(0)
        self.stdout.truncate()
        self.stderr.seek(0)
        self.stderr.truncate()
        code = self.shell.run_line(line)
        return code, self.stdout.getvalue(), self.stderr.getvalue()

    def test_directory_and_file_roundtrip(self):
        note = os.path.join(self.tmp.name, "note.txt")
        with open(note, "w", encoding="utf-8") as handle:
            handle.write("hello\n")
        self.assertEqual(self.run_shell("mkdir -p work/nested")[0], 0)
        code, out, err = self.run_shell("cp local:note.txt work/nested/note.txt")
        self.assertEqual((code, err), (0, ""), err)
        code, out, err = self.run_shell("cd work/nested")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_shell("pwd")
        self.assertEqual(out, "/work/nested\n")
        code, out, err = self.run_shell("ls")
        self.assertEqual(out, "note.txt\n")
        code, out, err = self.run_shell("cat note.txt")
        self.assertEqual(out, "hello\n")

    def test_names_with_spaces_and_rename(self):
        self.assertEqual(self.run_shell('mkdir "my dir"')[0], 0)
        source = os.path.join(self.tmp.name, "my file.txt")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write("spaced\n")
        code, _out, err = self.run_shell('cp "local:my file.txt" "my dir/my file.txt"')
        self.assertEqual(code, 0, err)
        code, _out, err = self.run_shell('mv "my dir/my file.txt" "my dir/renamed.txt"')
        self.assertEqual(code, 0, err)
        code, out, err = self.run_shell('cat "my dir/renamed.txt"')
        self.assertEqual((code, out), (0, "spaced\n"), err)

    def test_recursive_copy_and_delete(self):
        folder = os.path.join(self.tmp.name, "srcdir", "nested")
        os.makedirs(folder)
        with open(os.path.join(folder, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("nested\n")
        code, _out, err = self.run_shell("cp -r local:srcdir /")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_shell("find / -name a.txt")
        self.assertIn("/srcdir/nested/a.txt", out)
        self.assertEqual(code, 0, err)
        code, _out, err = self.run_shell("rm -r srcdir")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_shell("ls")
        self.assertNotIn("srcdir", out)

    def test_copy_back_to_mac_and_move_off_volume(self):
        with open(os.path.join(self.tmp.name, "one.txt"), "w", encoding="utf-8") as handle:
            handle.write("one\n")
        self.run_shell("cp local:one.txt /one.txt")
        code, _out, err = self.run_shell("cp /one.txt local:copied.txt")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.tmp.name, "copied.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "one\n")
        code, _out, err = self.run_shell("mv /one.txt local:moved.txt")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_shell("ls")
        self.assertNotIn("one.txt", out)
        with open(os.path.join(self.tmp.name, "moved.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "one\n")

    def test_refuse_root_delete_and_readonly(self):
        code, _out, err = self.run_shell("rm -r /")
        self.assertEqual(code, 1)
        self.assertIn("volume root", err)
        readonly = Shell(MTools(self.image), self.stdout, self.stderr, readonly=True)
        self.stdout.seek(0)
        self.stdout.truncate()
        self.stderr.seek(0)
        self.stderr.truncate()
        code = readonly.run_line("mkdir blocked")
        self.assertEqual(code, 1)
        self.assertIn("read-only", self.stderr.getvalue())

    def test_glob_touch_and_rmdir(self):
        self.assertEqual(self.run_shell("touch a.txt b.txt")[0], 0)
        code, out, err = self.run_shell("ls *.txt")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "a.txt\nb.txt\n")
        self.assertEqual(self.run_shell("mkdir empty")[0], 0)
        self.assertEqual(self.run_shell("rmdir empty")[0], 0)
        code, _out, err = self.run_shell("rmdir a.txt")
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)

    def test_prompt_marks_unflushed_writes(self):
        self.assertNotIn("sync", self.shell.prompt())
        code, _out, err = self.run_shell("mkdir docs")
        self.assertEqual(code, 0, err)
        self.assertIn("sync", self.shell.prompt())
        self.assertIn("❯", self.shell.prompt())
        code, _out, err = self.run_shell("sync")
        self.assertEqual(code, 0, err)
        self.assertNotIn("sync", self.shell.prompt())

    def test_ls_colors_directories_and_images(self):
        previous = os.environ.pop("NO_COLOR", None)
        try:
            self.assertEqual(self.run_shell("mkdir pictures")[0], 0)
            self.assertEqual(self.run_shell("touch photo.jpg notes.txt")[0], 0)
            tty = _Tty()
            self.shell.stdout = tty
            code = self.shell.run_line("ls")
            out = tty.getvalue()
        finally:
            self.shell.stdout = self.stdout
            if previous is not None:
                os.environ["NO_COLOR"] = previous
        self.assertEqual(code, 0)
        self.assertIn("\033[1;34mpictures/", out)
        self.assertIn("\033[1;35mphoto.jpg", out)
        self.assertIn("notes.txt\n", out)

    def test_question_mark_shows_help(self):
        code, help_out, err = self.run_shell("help")
        self.assertEqual(code, 0, err)
        code, alias_out, err = self.run_shell("?")
        self.assertEqual((code, err), (0, ""), err)
        self.assertEqual(alias_out, help_out)
        self.assertIn("?", self.shell.complete_token("", "?"))

    def test_completion_lists_volume_names(self):
        self.assertEqual(self.run_shell("mkdir docs")[0], 0)
        self.assertIn("docs/", self.shell.complete_token("ls ", "do"))
        self.assertIn("mkdir", self.shell.complete_token("", "mk"))


class CommandLineTest(unittest.TestCase):
    def test_module_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = os.path.join(tmp, "disk.img")
            subprocess.check_call(
                ["dd", "if=/dev/zero", f"of={image}", "bs=1M", "count=4", "status=none"]
            )
            subprocess.check_call(["mformat", "-i", image, "-v", "TEST", "::"])
            env = os.environ.copy()
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            env["PYTHONPATH"] = os.path.join(root, "src")
            completed = subprocess.run(
                [sys.executable, "-m", "mutility", image, "-c", "mkdir work", "-c", "pwd", "-c", "ls"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "/\nwork/\n")


if __name__ == "__main__":
    unittest.main()

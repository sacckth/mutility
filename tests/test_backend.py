import subprocess
import unittest
from unittest.mock import patch

from mutility.backend import MTools, password_required


def completed(code: int, stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout="", stderr=stderr)


class SudoRetryTest(unittest.TestCase):
    def test_password_required_recognizes_sudo_refusal(self):
        self.assertTrue(password_required(completed(1, "sudo: a password is required")))
        self.assertFalse(password_required(completed(0, "")))

    def test_expired_sudo_ticket_prompts_and_retries(self):
        backend = MTools("/dev/rdisk4s2", sudo=True)
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[1] == "-n":
                return completed(1, "sudo: a password is required")
            return completed(0, "")

        with patch("mutility.backend.subprocess.run", side_effect=fake_run):
            result = backend.execute(["mdir", "-i", "/dev/rdisk4s2", "::/"], text=True)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0][:2], ["sudo", "-n"])
        self.assertEqual(calls[1][0], "sudo")
        self.assertNotIn("-n", calls[1])

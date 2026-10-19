import shutil
import unittest
from unittest.mock import patch

from mutility.style import readline_safe, render_prompt


class PromptTest(unittest.TestCase):
    def test_fallback_matches_the_starship_layout(self):
        with patch("mutility.style.shutil.which", return_value=None):
            clean = render_prompt("/docs", False, 0, False)
            dirty = render_prompt("/docs", True, 0, False)
            failed = render_prompt("/docs", False, 1, True)
        self.assertEqual(clean, "\n💾 mutility in /docs\n❯ ")
        self.assertEqual(dirty, "\n💾 mutility in /docs ⚠ sync\n❯ ")
        self.assertIn("\033[1;31m❯", failed)

    def test_readline_safe_marks_color_codes_invisible(self):
        self.assertEqual(
            readline_safe("\033[1;32m❯\033[0m "),
            "\001\033[1;32m\002❯\001\033[0m\002 ",
        )
        self.assertEqual(readline_safe("\n💾 mutility in /\n❯ "), "\n💾 mutility in /\n❯ ")

    @unittest.skipUnless(shutil.which("starship"), "starship is not installed")
    def test_starship_renders_the_volume_prompt(self):
        rendered = render_prompt("/photos", True, 1, True)
        self.assertIn("\033[1;35m💾 mutility", rendered)
        self.assertIn("/photos", rendered)
        self.assertIn("⚠ sync", rendered)
        self.assertIn("\033[1;33m", rendered)
        self.assertIn("\033[1;31m❯", rendered)


if __name__ == "__main__":
    unittest.main()

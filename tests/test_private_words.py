import contextlib
import importlib.util
import io
import os
import unittest
from pathlib import Path

from tests.helpers import GitRepo

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_private_words.py"
spec = importlib.util.spec_from_file_location("check_private_words", SCRIPT)
private_words = importlib.util.module_from_spec(spec)
spec.loader.exec_module(private_words)


class PrivateWordsTests(GitRepo):
    def setUp(self):
        super().setUp()
        self.list_file = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "info" / "private-words"
        self.list_file.parent.mkdir(exist_ok=True)

    def check(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = private_words.main(["--repo", str(self.root)])
        return code, out.getvalue() + err.getvalue()

    def test_the_list_takes_patterns_and_skips_comments_and_blank_lines(self):
        self.list_file.write_text("# the employer\nAcmeCorp\n\n\\bxyz\\b\n")
        self.assertEqual([p.pattern for p in private_words.load_patterns(self.list_file)], ["AcmeCorp", r"\bxyz\b"])

    def test_finds_words_in_tracked_and_new_files_ignoring_case(self):
        self.list_file.write_text("acmecorp\n")
        self.commit("Add notes", {"notes.md": "hello\nmade at ACMECORP\n"})
        (self.root / "draft.txt").write_text("acmecorp again\n")
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertIn("notes.md:2", output)
        self.assertIn("draft.txt:1", output)

    def test_ignored_and_binary_files_are_skipped(self):
        self.list_file.write_text("acmecorp\n")
        self.commit("Ignore logs", {".gitignore": "*.log\n"})
        (self.root / "debug.log").write_text("acmecorp\n")
        (self.root / "image.bin").write_bytes(b"\x00\x01acmecorp\x00")
        self.assertEqual(self.check()[0], 0)

    def test_word_boundaries_avoid_false_positives(self):
        self.list_file.write_text("\\bxyz\\b\n")
        self.commit("Words", {"a.txt": "xyzzy and abcxyz\n"})
        self.assertEqual(self.check()[0], 0)
        (self.root / "b.txt").write_text("the xyz team\n")
        self.assertEqual(self.check()[0], 1)

    def test_a_clean_repo_passes(self):
        self.list_file.write_text("acmecorp\n")
        code, output = self.check()
        self.assertEqual(code, 0)
        self.assertIn("no private words", output)

    def test_without_a_list_it_says_where_to_create_it(self):
        code, output = self.check()
        self.assertEqual(code, 2)
        self.assertIn(os.path.join("info", "private-words"), output)  # backslashes on Windows


if __name__ == "__main__":
    unittest.main()

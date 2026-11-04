import re
import shlex
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "clt"
COMMANDS = {"login", "me", "issue", "GET", "POST", "PUT", "api"}
FLAGS = {"--json", "--read-only"}


def recipe_lines(text):
    blocks = re.findall(r"```(?:bash)?\n(.*?)```", text, re.S)
    return [line.strip() for block in blocks for line in block.splitlines() if line.strip().startswith("clt jira")]


def command_words(line):
    words = [w for w in shlex.split(line.split(" #")[0])[2:] if w not in FLAGS]
    return words


class SkillTests(unittest.TestCase):
    def test_skill_mentions_jira_and_points_to_its_reference(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
        description = next(line for line in front.splitlines() if line.startswith("description:"))
        self.assertIn("Jira", description)
        self.assertIn("references/jira.md", text)

    def test_reference_is_small(self):
        self.assertLess(len((SKILL / "references" / "jira.md").read_text(encoding="utf-8")), 8000)

    def test_every_recipe_uses_json_and_a_known_command(self):
        lines = recipe_lines((SKILL / "references" / "jira.md").read_text(encoding="utf-8"))
        self.assertTrue(lines)
        dry_runs = set()
        for line in lines:
            with self.subTest(line=line):
                self.assertIn("--json", line)
                words = command_words(line)
                self.assertIn(words[0], COMMANDS)
                if words[0] in ("POST", "PUT"):
                    if "--dry-run" in words:
                        dry_runs.add((words[0], words[1]))
                    else:
                        self.assertIn((words[0], words[1]), dry_runs)


if __name__ == "__main__":
    unittest.main()

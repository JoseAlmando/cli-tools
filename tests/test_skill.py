import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src" / "cli_tools" / "skill"
REFERENCES = {"jira": "references/jira.md", "azdo": "references/azure-devops.md", "github": "references/github.md",
              "taiga": "references/taiga.md", "gitea": "references/gitea.md"}
COMMANDS = {"login", "me", "issue", "mine", "wiql", "GET", "POST", "PUT", "PATCH", "DELETE", "api"}
WRITES = ("POST", "PUT", "PATCH", "DELETE")
FLAGS = {"--json", "--read-only"}


def recipe_lines(text, tool):
    blocks = re.findall(r"```(?:bash)?\n(.*?)```", text, re.S)
    return [line.strip() for block in blocks for line in block.splitlines()
            if line.strip().startswith(f"clt {tool} ")]


def command_words(line):
    return [w for w in shlex.split(line.split(" #")[0])[2:] if w not in FLAGS]


class PackagedSkillTests(unittest.TestCase):
    def test_the_package_carries_the_skill(self):
        import cli_tools
        folder = cli_tools.skill_dir()
        self.assertTrue((folder / "SKILL.md").is_file())
        self.assertTrue((folder / "references" / "jira.md").is_file())
        self.assertEqual(folder.parent, Path(cli_tools.__file__).resolve().parent)

    def test_package_data_declares_the_skill(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        table = re.search(r"^\[tool\.setuptools\.package-data\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
        self.assertIsNotNone(table)
        self.assertIn('"skill/*.md"', table.group(1))
        self.assertIn('"skill/references/*.md"', table.group(1))


class SkillTests(unittest.TestCase):
    def test_skill_mentions_each_tool_and_points_to_its_reference(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
        description = next(line for line in front.splitlines() if line.startswith("description:"))
        for name in ("Jira", "Azure DevOps"):
            self.assertIn(name, description)
        for reference in REFERENCES.values():
            self.assertIn(reference, text)

    def test_references_are_small(self):
        for reference in REFERENCES.values():
            with self.subTest(reference=reference):
                self.assertLess(len((SKILL / reference).read_text(encoding="utf-8")), 8000)

    def test_every_recipe_uses_json_and_a_known_command(self):
        for tool, reference in REFERENCES.items():
            lines = recipe_lines((SKILL / reference).read_text(encoding="utf-8"), tool)
            self.assertTrue(lines, reference)
            dry_runs = set()
            for line in lines:
                with self.subTest(line=line):
                    self.assertIn("--json", line)
                    words = command_words(line)
                    self.assertIn(words[0], COMMANDS)
                    if words[0] in WRITES:
                        if "--dry-run" in words:
                            dry_runs.add((words[0], words[1]))
                        else:
                            self.assertIn((words[0], words[1]), dry_runs)

    def test_the_task_table_is_safe_and_points_to_real_references(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        table = re.search(r"## Find the task here first\n(.*?)\n## ", text, re.S).group(1)
        rows = [line for line in table.splitlines() if line.startswith("| ") and not line.startswith("| Task")]
        self.assertGreater(len(rows), 10)
        for row in rows:
            with self.subTest(row=row):
                for command in re.findall(r"`(clt (?!<tool>)[^`]+)`", row.replace("\\|", "|")):
                    self.assertIn("--json", command)
                    if command.split()[1] != "release":
                        method = command_words(command.split(" | ")[0])[0]
                        self.assertIn("--dry-run" if method in WRITES else "--read-only", command)
                for reference in re.findall(r"`(references/[^`]+)`", row):
                    self.assertTrue((SKILL / reference).is_file(), reference)


if __name__ == "__main__":
    unittest.main()

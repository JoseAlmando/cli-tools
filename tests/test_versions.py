import tempfile
import unittest
from pathlib import Path

from cli_tools.commands.release import versions
from cli_tools.core.errors import ToolError

PYPROJECT = '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "demo"\nversion = "1.2.3"\n\n[tool.x]\nversion = "9.9.9"\n'
CSPROJ = '<Project Sdk="Microsoft.NET.Sdk">\r\n  <PropertyGroup>\r\n    <Version>1.2.3</Version>\r\n  </PropertyGroup>\r\n</Project>\r\n'


class BumpTests(unittest.TestCase):
    def test_each_kind_raises_its_part(self):
        self.assertEqual(versions.bump("1.2.3", "fix"), "1.2.4")
        self.assertEqual(versions.bump("1.2.3", "feat"), "1.3.0")
        self.assertEqual(versions.bump("1.2.3", "major"), "2.0.0")

    def test_suggestion_follows_conventional_prefixes(self):
        self.assertEqual(versions.suggest_bump(["Fix x"]), "fix")
        self.assertEqual(versions.suggest_bump(["feat: x", "fix: y"]), "feat")
        self.assertEqual(versions.suggest_bump(["feat(api)!: x"]), "major")
        self.assertEqual(versions.suggest_bump(["x\n\nBREAKING CHANGE: y"]), "major")
        self.assertEqual(versions.suggest_bump([]), "fix")


class FindVersionFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_python_reads_and_writes_the_project_table_only(self):
        path = self.write("pyproject.toml", PYPROJECT)
        found = versions.find_version_file(self.root)
        self.assertEqual((found.path, found.kind, found.read()), (path, "python", "1.2.3"))
        found.write("1.3.0")
        self.assertEqual(path.read_text(), PYPROJECT.replace('version = "1.2.3"', 'version = "1.3.0"'))

    def test_a_single_quoted_python_version_is_read_and_written(self):
        path = self.write("pyproject.toml", "[project]\nname = 'demo'\nversion = '1.2.3'\n")
        found = versions.find_version_file(self.root)
        self.assertEqual(found.read(), "1.2.3")
        found.write("1.3.0")
        self.assertEqual(path.read_text(), "[project]\nname = 'demo'\nversion = '1.3.0'\n")

    def test_a_byte_order_mark_is_read_and_kept(self):
        for name, text in (("VERSION", "﻿1.2.3\n"), ("pyproject.toml", '﻿[project]\nversion = "1.2.3"\n')):
            with self.subTest(name=name):
                path = self.write(name, text)
                found = versions.find_version_file(self.root, override=name if name == "VERSION" else None)
                self.assertEqual(found.read(), "1.2.3")
                found.write("1.3.0")
                self.assertEqual(path.read_bytes(), text.replace("1.2.3", "1.3.0").encode("utf-8"))
                path.unlink()

    def test_a_dynamic_python_version_suggests_version_file(self):
        self.write("pyproject.toml", '[project]\nname = "demo"\ndynamic = ["version"]\n')
        with self.assertRaisesRegex(ToolError, "--version-file"):
            versions.find_version_file(self.root)

    def test_directory_build_props_wins_over_a_csproj(self):
        props = self.write("Directory.Build.props", "<Project><PropertyGroup><Version>2.0.0</Version></PropertyGroup></Project>")
        self.write("src/App/App.csproj", CSPROJ)
        found = versions.find_version_file(self.root)
        self.assertEqual((found.path, found.kind, found.read()), (props, "dotnet", "2.0.0"))

    def test_the_single_csproj_with_a_version_skipping_bin_and_obj(self):
        path = self.write("src/App/App.csproj", CSPROJ)
        self.write("src/App/bin/Debug/Copy.csproj", CSPROJ)
        self.write("src/App/obj/Other.csproj", CSPROJ)
        self.write("tests/App.Tests/App.Tests.csproj", '<Project Sdk="Microsoft.NET.Sdk"></Project>')
        self.assertEqual(versions.find_version_file(self.root).path, path)

    def test_version_prefix_counts_as_the_version(self):
        self.write("App.csproj", "<Project><PropertyGroup><VersionPrefix>0.4.0</VersionPrefix></PropertyGroup></Project>")
        self.assertEqual(versions.find_version_file(self.root).read(), "0.4.0")

    def test_several_csproj_versions_point_to_directory_build_props(self):
        self.write("A/A.csproj", CSPROJ)
        self.write("B/B.csproj", CSPROJ)
        with self.assertRaisesRegex(ToolError, "Directory.Build.props") as caught:
            versions.find_version_file(self.root)
        self.assertIn("A.csproj", str(caught.exception))
        self.assertIn("B.csproj", str(caught.exception))

    def test_a_csproj_that_is_not_utf8_is_skipped_or_named(self):
        legacy = self.root / "Legacy" / "Legacy.csproj"
        legacy.parent.mkdir()
        legacy.write_bytes("<Project><Company>Señor</Company><Version>1.0.0</Version></Project>".encode("cp1252"))
        path = self.write("App/App.csproj", CSPROJ)
        self.assertEqual(versions.find_version_file(self.root).path, path)
        with self.assertRaisesRegex(ToolError, "Legacy.csproj.*UTF-8"):
            versions.find_version_file(self.root, "Legacy/Legacy.csproj").read()

    def test_nothing_found_lists_what_was_looked_for(self):
        with self.assertRaisesRegex(ToolError, "pyproject.toml.*Directory.Build.props.*csproj"):
            versions.find_version_file(self.root)

    def test_an_override_can_be_a_plain_version_file(self):
        path = self.write("VERSION", "0.9.1\n")
        found = versions.find_version_file(self.root, "VERSION")
        self.assertEqual((found.kind, found.read()), ("plain", "0.9.1"))
        found.write("0.9.2")
        self.assertEqual(path.read_bytes(), b"0.9.2\n")

    def test_crlf_line_endings_are_kept_byte_for_byte(self):
        path = self.write("App.csproj", CSPROJ)
        versions.find_version_file(self.root).write("1.3.0")
        self.assertEqual(path.read_bytes(), CSPROJ.replace("1.2.3", "1.3.0").encode("utf-8"))

    def test_versions_that_are_not_major_minor_patch_name_the_file_and_value(self):
        for bad in ("1.2", "v1.2.3", "1.2.3-beta"):
            with self.subTest(bad=bad):
                self.write("pyproject.toml", f'[project]\nname = "demo"\nversion = "{bad}"\n')
                with self.assertRaises(ToolError) as caught:
                    versions.find_version_file(self.root).read()
                self.assertIn("pyproject.toml", str(caught.exception))
                self.assertIn(bad, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

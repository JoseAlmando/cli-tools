"""The direction of the imports inside cli_tools (see docs/architecture.md)."""
import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "cli_tools"


def internal_imports():
    """{module: {imported cli_tools modules}} for every module of the package."""
    found = {}
    for path in PACKAGE.rglob("*.py"):
        module = ".".join(("cli_tools",) + path.relative_to(PACKAGE).with_suffix("").parts).replace(".__init__", "")
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("cli_tools"):
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names if alias.name.startswith("cli_tools"))
        found[module] = names
    return found


def area(name):
    """core, tools.<tool>, commands, or the top level (cli, registry, ...)."""
    parts = name.split(".")
    if parts[1:2] == ["tools"] and len(parts) > 2:
        return "tools." + parts[2]
    return parts[1] if len(parts) > 1 and parts[1] in ("core", "commands") else "top"


class ImportDirectionTests(unittest.TestCase):
    def test_core_imports_only_core(self):
        for module, names in internal_imports().items():
            if area(module) == "core":
                with self.subTest(module=module):
                    self.assertEqual({n for n in names if area(n) != "core"}, set())

    def test_a_tool_imports_only_core_and_itself(self):
        for module, names in internal_imports().items():
            if area(module).startswith("tools."):
                with self.subTest(module=module):
                    self.assertEqual({n for n in names if area(n) not in ("core", area(module))}, set())

    def test_only_the_registry_names_tools_for_clt(self):
        imports = internal_imports()
        for module in ("cli_tools.cli", "cli_tools.registry", "cli_tools.__main__", "cli_tools"):
            with self.subTest(module=module):
                self.assertEqual({n for n in imports[module] if area(n).startswith("tools.") or area(n) == "commands"},
                                 set())


if __name__ == "__main__":
    unittest.main()

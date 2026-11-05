## What and why

<!-- What this changes, and the problem it solves. Link the issue: "Closes #12". -->

## Checklist

- [ ] Tests pass: `PYTHONPATH=src python3 -m unittest discover -s tests -t .` (and new behaviour has tests)
- [ ] `uvx ruff@0.17.3 check .` is clean
- [ ] Branch and commits follow the conventions in [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] Still standard library only, and nothing that breaks Windows
- [ ] No tokens, passwords or private server names anywhere in the change
- [ ] README, help text or the skill (`src/cli_tools/skill/`) updated if users or agents see something different

# Agent setup (MCP and skill install) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Third PR of issue #47: `clt jira mcp` / `clt azdo mcp` print or run the command that connects the official MCP server to Claude Code, Codex or Gemini CLI, and `clt skill install` puts clt's skill where each agent reads it, kept current by `clt update`.

**Architecture:** The skill moves into the package (`src/cli_tools/skill/`, shipped as package data; `skills/clt` becomes a link to it). A new clt command, `commands/skill.py` (`clt skill install | uninstall | refresh`), copies it (or links it with `--link`) and marks each copy with `.clt-skill`. `core/mcp.py` builds an agent's `mcp add` command for an HTTP or a stdio server and prints or runs it; `tools/jira` and `tools/azdo` each add an `mcp` command with their server and the next step to take.

**Tech Stack:** Python 3.9+ standard library (`shutil`, `subprocess`, `shlex`, `pathlib`, `argparse`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-04-jira-azure-devops-design.md`, sections "`clt <tool> mcp --agent claude|codex|gemini [--install]`" and "`clt skill install --agent claude|codex|gemini [--link]`".

**Branch:** `feature/agent-setup`, from `main` once #50 is merged.

## Global Constraints

- Python 3.9+, standard library only; works on Windows; English in code, help, messages, docs and tests.
- No comments or docstrings in new code (the owner's rule).
- clt never writes an agent's configuration file and never puts a token in an MCP command. Subprocesses take argument lists, never `shell=True`.
- Tests never touch the network, `~/.config`, the real home or the real agent CLIs: a temporary `HOME`/`USERPROFILE`, `shutil.which` and `subprocess.run` faked.
- `--read-only` blocks every write: `mcp --install`, `skill install`, `skill uninstall`, `skill refresh`. Printing an MCP command is always allowed.
- `python scripts/check_private_words.py` prints `OK` before every commit; commits go through the owner's approval prompt; never push or open the PR without the owner's OK.
- Checks before each commit, each one stopping the chain on failure: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`, the same on Python 3.9 with `uv run --no-project --python 3.9`, `uvx ruff check .`, `python scripts/check_private_words.py`.

## Decisions this plan adds to the spec

- Agent folders: `claude` → `~/.claude/skills/clt`; `codex` and `gemini` → `~/.agents/skills/clt` (the same folder: installed once, reported for both).
- The marker `.clt-skill` holds `{"version": "<clt version>"}` as JSON.
- A target is clt's when it has the marker, or is a link whose resolved path ends in `skills/clt` or `cli_tools/skill`. Anything else (a folder, a file, a broken link elsewhere) stops with `ToolError` naming the path, and is not touched.
- `clt skill refresh` re-copies every target that has the marker; links are left alone (they already follow their source). `clt update`, after a successful install on Linux and macOS, runs the newly installed `clt skill refresh` (found with `shutil.which("clt")`); on Windows, where the install finishes in the background, it prints `then run: clt skill refresh`.
- `--link` on Windows without the privilege to create links: `ToolError` that says to drop `--link`.
- MCP commands are exactly the spec's table; on Windows the stdio command `npx …` becomes `cmd /c npx …`. The printed form uses `shlex.join` (`subprocess.list2cmdline` on Windows).
- `--install` when the agent's CLI is not on the PATH: `ToolError` with the command to run by hand. When the agent's CLI fails, clt exits with its code.
- `clt azdo mcp` takes the org from `--org`, `AZDO_ORG` or the saved session (no token needed); `clt jira mcp` needs no site.
- `--json`: `mcp` prints `{"agent", "command", "then", "installed"}`; `skill` prints `{"action", "targets": [{"agent", "path", "how"}]}`.

## Review Focus

- **The target already exists and is not clt's** (a user's own `clt` folder, a plain file, a link to somewhere else): install and uninstall stop, name the path, and change nothing → test in Task 2.
- **Codex and Gemini share `~/.agents/skills/clt`**: `install --agent codex --agent gemini` copies once; `uninstall --agent codex` says it also removes Gemini's → test in Task 2.
- **An agent CLI missing or failing on `--install`**: a clear message with the command to run by hand, exit 1; the agent's own exit code when it fails → test in Task 4.
- **Windows**: `npx` wrapped as `cmd /c npx`, the printed command quoted for `cmd`, `--link` without privilege explained → tests in Task 2 and Task 4 with `on_windows` patched.
- **The installed package really contains the skill** (package data forgotten): CI installs clt with `uv tool install` and runs `clt skill install --agent codex` into a temporary home, then checks `SKILL.md` and `references/` are there → Task 5.

---

### Task 1: The skill inside the package

**Files:**
- Move: `skills/clt/SKILL.md`, `skills/clt/references/*.md` → `src/cli_tools/skill/` (with `git mv`), then `skills/clt` becomes a relative symlink to `../src/cli_tools/skill`
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]` `cli_tools = ["skill/*.md", "skill/references/*.md"]`), `tests/test_skill.py` (reads `src/cli_tools/skill`)
- Test: `tests/test_skill.py`

**Interfaces:**
- Produces: `cli_tools.skill_dir() -> pathlib.Path` in `src/cli_tools/__init__.py`, the folder holding `SKILL.md`.

- [ ] **Step 1: Write the failing tests:** `test_the_package_carries_the_skill` (`skill_dir() / "SKILL.md"` and `skill_dir() / "references" / "jira.md"` exist and `skill_dir()` is inside the `cli_tools` package folder); `test_package_data_declares_the_skill` (`pyproject.toml` lists `skill/*.md` and `skill/references/*.md` under `[tool.setuptools.package-data]`). Point `SKILL` in `tests/test_skill.py` at `skill_dir()`.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_skill -v` → FAIL (`skill_dir` missing).
- [ ] **Step 3: Move** the files, add `skill_dir()`, the package data and the symlink.
- [ ] **Step 4: Run** the full suite → PASS; `git ls-files -s skills/clt` shows mode `120000`.
- [ ] **Step 5: Commit** `refactor: ship the skill inside the package`.

### Task 2: `clt skill install | uninstall | refresh`

**Files:**
- Create: `src/cli_tools/commands/skill.py`
- Modify: `src/cli_tools/registry.py` (`("skill", "cli_tools.commands.skill", "install clt's skill for Claude Code, Codex or Gemini CLI")`, after `update`)
- Test: `tests/test_skill_command.py`, `tests/test_contracts.py` (help snapshot, usage)

**Interfaces:**
- Consumes: `cli_tools.skill_dir()`.
- Produces: `AGENTS = {"claude": (".claude", "skills", "clt"), "codex": (".agents", "skills", "clt"), "gemini": (".agents", "skills", "clt")}`; `target(agent: str) -> Path`; `is_clt(path: Path) -> bool`; `install(agents: list, link: bool) -> list[dict]`; `uninstall(agents: list) -> list[dict]`; `refresh() -> list[dict]` (dicts `{"agent", "path", "how"}`); `main(argv, ctx=None, prog="clt skill") -> int`.

- [ ] **Step 1: Write the failing tests** (a temporary home through `HOME` and `USERPROFILE`; `shutil.which` patched):
  - `test_install_copies_and_marks`: `--agent claude` → `~/.claude/skills/clt/SKILL.md` and `references/jira.md` exist; `.clt-skill` holds the version.
  - `test_install_without_agent_uses_the_clis_on_the_path`: only `codex` on the PATH → only `~/.agents/skills/clt`; none on the PATH → `ToolError` naming `--agent`.
  - `test_codex_and_gemini_share_one_folder`: both agents → one copy, two entries in the JSON `targets`.
  - `test_reinstall_replaces_a_clt_copy` (old file inside it disappears) and `test_a_foreign_target_is_left_alone` (a folder without marker, a plain file, a link to another folder → `ToolError` with the path, contents unchanged).
  - `test_link` (`--link` → a link to `skill_dir()`, no marker) and `test_link_without_privilege` (`os.symlink` raising `OSError` → `ToolError` saying to drop `--link`).
  - `test_uninstall_removes_only_clt_targets` (copy and link removed; a foreign folder refused; a missing one reported, exit 0) and `test_uninstall_codex_mentions_gemini`.
  - `test_refresh_recopies_marked_copies_only` (a marked copy with a stale file is refreshed; a link is untouched; nothing installed → `targets` empty).
  - `test_read_only_blocks_every_action` → `ReadOnlyError` for install, uninstall and refresh.
  - Contracts: `clt --help` snapshot gains `skill`; `usage: clt skill ` in the usage test.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_skill_command tests.test_contracts -v` → FAIL (`Unknown tool 'skill'`).
- [ ] **Step 3: Implement** `commands/skill.py` with subcommands `install [--agent A ...] [--link]`, `uninstall --agent A ...`, `refresh`; copies with `shutil.copytree` into a sibling temporary folder, then swap, so a failed copy never leaves half a skill.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(skill): install clt's skill for Claude Code, Codex and Gemini CLI`.

### Task 3: `clt update` refreshes the copies

**Files:**
- Modify: `src/cli_tools/commands/update.py`
- Test: `tests/test_update.py`

- [ ] **Step 1: Write the failing tests:** after a successful install (fake `subprocess.run` returning 0) with `shutil.which("clt")` → `/x/clt`, a second call runs `["/x/clt", "skill", "refresh"]`; when the install fails, no refresh; on Windows (patched), stderr says `clt skill refresh`; when `which` finds no `clt`, stderr says to run it.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_update -v` → FAIL.
- [ ] **Step 3: Implement** in `update.main`, after `subprocess.run(command)`.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(update): refresh the installed skill copies after an update`.

### Task 4: `clt jira mcp` and `clt azdo mcp`

**Files:**
- Create: `src/cli_tools/core/mcp.py`
- Modify: `src/cli_tools/tools/jira/cli.py`, `src/cli_tools/tools/azdo/cli.py`
- Test: `tests/test_mcp.py`, `tests/test_contracts.py` (command lists)

**Interfaces:**
- Produces: `AGENTS = ("claude", "codex", "gemini")`; `http_command(agent, name, url) -> list[str]`; `stdio_command(agent, name, command: list[str]) -> list[str]`; `shown(command: list[str]) -> str`; `add_mcp_parser(sub, func, extra=None)` (adds `mcp --agent {claude,codex,gemini} [--install]`); `run_mcp(args, ctx, command: list[str], then: str) -> int`.

- [ ] **Step 1: Write the failing tests:**
  - The six commands of the spec's table, exactly: `http_command("claude", "atlassian", "https://mcp.atlassian.com/v1/mcp") == ["claude", "mcp", "add", "--scope", "user", "--transport", "http", "atlassian", "https://mcp.atlassian.com/v1/mcp"]`, `["codex", "mcp", "add", "atlassian", "--url", …]`, `["gemini", "mcp", "add", "--scope", "user", "--transport", "http", "atlassian", …]`; `stdio_command("claude", "azure-devops", ["npx", "-y", "@azure-devops/mcp", "example"]) == ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", "azure-devops", "--", "npx", "-y", "@azure-devops/mcp", "example"]`, codex `["codex", "mcp", "add", "azure-devops", "--", "npx", …]`, gemini `["gemini", "mcp", "add", "--scope", "user", "azure-devops", "npx", …]`.
  - `test_windows_wraps_npx`: with Windows patched, `clt azdo mcp --agent codex` prints `cmd /c npx -y @azure-devops/mcp example`.
  - `test_print_only`: `clt jira mcp --agent codex` prints the command and `codex mcp login atlassian`, runs nothing, exit 0; `--json` gives `{"agent", "command", "then", "installed": false}`.
  - `test_install_runs_the_agent_cli`: `--install` with `which("codex")` found → `subprocess.run` called once with the list, no `shell`; its exit code 3 → clt returns 3.
  - `test_install_without_the_cli`: `which` → `None` → `ToolError` containing the printed command.
  - `test_read_only_blocks_install_not_print`.
  - `test_azdo_mcp_needs_an_org`: none configured → the "No organization configured" error; `--org example` works without a token; the saved session's org is used.
  - Contracts: `mcp` in the command lists of `clt jira` and `clt azdo`.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_mcp -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `core/mcp.py`, then the `mcp` command in both tools (`then` texts from the spec: `/mcp` in Claude Code to sign in; `codex mcp login atlassian`; `/mcp auth atlassian` in Gemini; for Azure DevOps "sign in in the browser on first use; needs Node.js 20+", and the help mentions the remote server `https://mcp.dev.azure.com/<org>` for tenants that have it).
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(mcp): connect the official Jira and Azure DevOps MCP servers to an agent`.

### Task 5: Docs and the CI check of the packaged skill

**Files:**
- Modify: `README.md` (MCP section; the skill installed with `clt skill install` instead of `ln -s`), `docs/architecture.md` (tree: `skill/`, `core/mcp.py`, `commands/skill.py`), `AGENTS.md` (the skill lives in `src/cli_tools/skill/`), `src/cli_tools/skill/SKILL.md` (one line: MCP servers are set up by the user with `clt <tool> mcp`, never by the agent), `.github/workflows/tests.yml` (smoke test: `HOME` set to a temporary folder, `clt skill install --agent codex`, check `SKILL.md` and `references/jira.md`).
- Test: the suite and the CI smoke test.

- [ ] **Step 1:** Update the docs and the workflow.
- [ ] **Step 2: Run** the full suite, ruff, the private-words check; install the branch into a fresh venv and run the smoke lines by hand → all OK.
- [ ] **Step 3: Commit** `docs: MCP setup and skill install` and `ci: check the packaged skill installs`.

### Task 6: Hand-off

- [ ] Final whole-branch review (executing-plans), fixes in one pass.
- [ ] A test prompt for the owner's agent on the work machine in `agent-inbox`: `clt skill install`, `clt jira mcp --agent codex` (print, then `--install` with the owner's OK), `clt azdo mcp --agent codex`, and checking that Codex lists the servers (`codex mcp list`).

"""Where each agent CLI reads skills, and the hint to install clt's; light enough for clt --help."""
import shutil
import sys
from pathlib import Path

from cli_tools.core import output
from cli_tools.core.output import info

SKILL_FOLDERS = {"claude": (".claude", "skills", "clt"), "codex": (".agents", "skills", "clt"),
                 "gemini": (".agents", "skills", "clt")}


def skill_target(agent):
    return Path.home().joinpath(*SKILL_FOLDERS[agent])


def without_skill():
    """The agent CLIs on the PATH that have no skill folder at all (clt's or the user's own)."""
    return [a for a in SKILL_FOLDERS
            if shutil.which(a) and not (skill_target(a).exists() or skill_target(a).is_symlink())]


def recommend_skill(ctx):
    """Suggest installing the skill, for a person only: never with --json or without a terminal."""
    if ctx.json_mode or not sys.stderr.isatty():
        return
    agents = without_skill()
    if agents:
        command = "clt skill install " + " ".join(f"--agent {a}" for a in agents)
        one = len(agents) == 1
        info(f"{', '.join(agents)} {'has' if one else 'have'} no clt skill yet; "
             f"to teach {'it' if one else 'them'} clt, run: {output.err.bold(command)}")

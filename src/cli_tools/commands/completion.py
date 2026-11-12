"""clt completion: Tab completion for bash, zsh, fish and PowerShell, read from clt's own parsers so it never goes
stale. The script each shell loads only passes the words typed so far back to clt, which answers with candidates."""
import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cli_tools import registry
from cli_tools.core import api, context, output
from cli_tools.core.errors import ToolError

# The words typed after "clt", the last one being the word under the cursor, each followed by this separator. They
# come in the environment, not as arguments, so clt's own flags (--json, --read-only) reach this code untouched.
WORDS_VAR = "CLT_COMPLETE"
SEPARATOR = "\x1f"

BASH = r"""# clt completion for bash: add to ~/.bashrc
#   eval "$(clt completion bash)"
_clt() {
    local IFS=$'\n' typed="" i
    # By index: bash 3.2 (macOS) joins a quoted "${COMP_WORDS[@]:1:n}" into one word when IFS has no space
    for ((i = 1; i <= COMP_CWORD; i++)); do typed+="${COMP_WORDS[i]}"$'\x1f'; done
    COMPREPLY=($(CLT_COMPLETE="$typed" clt completion 2>/dev/null))
}
complete -o default -F _clt clt
"""

ZSH = r"""# clt completion for zsh: add to ~/.zshrc (after compinit)
#   eval "$(clt completion zsh)"
_clt() {
    local -a found
    found=("${(@f)$(CLT_COMPLETE="${(pj:\x1f:)words[2,CURRENT]}"$'\x1f' clt completion 2>/dev/null)}")
    compadd -a found
}
compdef _clt clt
"""

FISH = r"""# clt completion for fish: save as ~/.config/fish/completions/clt.fish
#   clt completion fish > ~/.config/fish/completions/clt.fish
function __clt_complete
    set -l words (commandline -opc)[2..-1] (commandline -ct)
    CLT_COMPLETE=(string join \x1f -- $words)\x1f clt completion 2>/dev/null
end
complete -c clt -f -a '(__clt_complete)'
"""

POWERSHELL = r"""# clt completion for PowerShell: add to your profile ($PROFILE)
#   clt completion powershell | Out-String | Invoke-Expression
Register-ArgumentCompleter -Native -CommandName clt, clt.exe -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $words = @($commandAst.CommandElements | Where-Object { $_.Extent.EndOffset -le $cursorPosition } |
        Select-Object -Skip 1 | ForEach-Object { $_.Extent.Text })
    if ($wordToComplete -eq '') { $words += '' }
    $env:CLT_COMPLETE = (($words | ForEach-Object { $_ + [char]0x1f }) -join '')
    try {
        clt completion 2>$null | ForEach-Object {
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }
    } finally {
        Remove-Item Env:CLT_COMPLETE -ErrorAction SilentlyContinue
    }
}
"""

SCRIPTS = {"bash": BASH, "zsh": ZSH, "fish": FISH, "powershell": POWERSHELL}


def _subparsers(parser):
    return next((action for action in parser._actions if isinstance(action, argparse._SubParsersAction)), None)


def _takes_a_value(action):
    return action.nargs != 0 and not isinstance(action, (argparse._StoreConstAction, argparse._CountAction,
                                                         argparse._HelpAction, argparse._VersionAction))


def _tool_parser(name):
    module = importlib.import_module(registry.find(name))
    return module.build_parser(f"clt {registry.canonical(name)}")


def candidates(words):
    """What can go in place of the last word, given the words typed before it after "clt"."""
    *before, current = words or [""]
    if not before:
        names = [name for name, _, _ in registry.TOOLS] + list(registry.ALIASES) + ["--help", "--version"]
        return sorted(name for name in names if name.startswith(current))
    if registry.find(before[0]) is None:
        return []
    parser, positional, pending = _tool_parser(before[0]), 0, None
    for word in before[1:]:
        if pending is not None:
            pending = None
            continue
        if word.startswith("-"):
            action = parser._option_string_actions.get(word)
            pending = action if action is not None and _takes_a_value(action) else None
            continue
        sub = _subparsers(parser)
        if sub is not None and word in sub.choices:
            parser, positional = sub.choices[word], 0
        elif sub is not None and word.upper() in api.METHODS and "api" in sub.choices:
            parser, positional = sub.choices["api"], 1  # "clt <tool> GET path" is the api command
        else:
            positional += 1
    if pending is not None:
        found = [str(choice) for choice in pending.choices or ()]
    elif current.startswith("-"):
        found = [option for action in parser._actions for option in action.option_strings]
        found += list(context.GLOBAL_FLAGS)
    else:
        found = []
        sub = _subparsers(parser)
        if sub is not None:
            found += list(sub.choices)
            if "api" in sub.choices:
                found += list(api.METHODS)
        positionals = [action for action in parser._actions if not action.option_strings
                       and not isinstance(action, argparse._SubParsersAction)]
        if positional < len(positionals) and positionals[positional].choices:
            found += [str(choice) for choice in positionals[positional].choices]
    return sorted(set(option for option in found if option.startswith(current)))


# The line --install adds to a shell's startup file; fish gets its own completions file instead
LINES = {"bash": 'eval "$(clt completion bash)"', "zsh": 'eval "$(clt completion zsh)"',
         "powershell": "clt completion powershell | Out-String | Invoke-Expression"}
COMMENT = "# Tab completion for clt (added by clt completion {shell} --install)"
COMPINIT = "autoload -Uz compinit && compinit"


def powershell_profiles():
    """$PROFILE of each PowerShell on the PATH (pwsh, and Windows PowerShell): asked to PowerShell itself, since
    Documents may be redirected (OneDrive) or localized."""
    found = []
    for program in ("pwsh", "powershell"):
        path = shutil.which(program)
        if not path:
            continue
        try:
            done = subprocess.run([path, "-NoProfile", "-NonInteractive", "-Command", "$PROFILE"], capture_output=True,
                                  text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        profile = done.stdout.strip()
        if done.returncode == 0 and profile and Path(profile) not in found:
            found.append(Path(profile))
    return found


def startup_files(shell):
    home = Path.home()
    if shell == "bash":
        return [home / ".bashrc"]
    if shell == "zsh":
        return [Path(os.environ.get("ZDOTDIR") or home) / ".zshrc"]
    return powershell_profiles()


def add_lines(path, lines):
    """Appends the lines to the file (made if missing) in the file's own line endings; False if they are there."""
    raw = path.read_bytes().decode("utf-8") if path.is_file() else ""
    if lines[-1] in raw:
        return False
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw if not raw or raw.endswith("\n") else raw + newline
    text += (newline if text else "") + newline.join(lines) + newline
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return True


def install(shell):
    """[(path, "added" | "already")] for the files that load the completion."""
    if shell == "fish":
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "fish" / "completions" / "clt.fish"
        same = path.is_file() and path.read_bytes() == FISH.encode("utf-8")
        if not same:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(FISH.encode("utf-8"))
        return [(path, "already" if same else "added")]
    files = startup_files(shell)
    if not files:
        raise ToolError(f"No PowerShell found on the PATH; add this line to $PROFILE by hand:\n  {LINES[shell]}")
    results = []
    for path in files:
        lines = [COMMENT.format(shell=shell), LINES[shell]]
        if shell == "zsh" and "compinit" not in (path.read_text(encoding="utf-8") if path.is_file() else ""):
            lines.insert(1, COMPINIT)  # compdef needs it; frameworks such as oh-my-zsh run it themselves
        results.append((path, "added" if add_lines(path, lines) else "already"))
    return results


def build_parser(prog):
    parser = argparse.ArgumentParser(prog=prog, formatter_class=output.HelpFormatter,
                                     description="Print the Tab completion script for a shell, or install it.",
                                     epilog=f"""install it once (then open a new terminal):
  {prog} bash --install         # a line in ~/.bashrc
  {prog} zsh --install          # a line in ~/.zshrc (or $ZDOTDIR/.zshrc), after compinit
  {prog} fish --install         # ~/.config/fish/completions/clt.fish
  {prog} powershell --install   # a line in $PROFILE of each PowerShell found (pwsh, Windows PowerShell)

or load it in this session only:
  bash, zsh   eval "$({prog} bash)"
  fish        {prog} fish | source
  powershell  {prog} powershell | Out-String | Invoke-Expression
""")
    parser.add_argument("shell", choices=list(SCRIPTS), metavar="SHELL", help="bash, zsh, fish or powershell")
    parser.add_argument("--install", action="store_true", help="load it in every new terminal of that shell")
    return parser


def write_out(text):
    """To stdout with \\n line endings on Windows too: bash's eval and its completion lists break on a \\r."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")
    sys.stdout.write(text)


def main(argv, ctx=None, prog="clt completion"):
    ctx = ctx or context.CLIContext.from_env()
    typed = os.environ.get(WORDS_VAR)
    if typed is not None and not argv:  # called by a shell's completion script
        try:
            found = candidates(typed.split(SEPARATOR)[:-1])
        except Exception:  # a broken completion must never print a traceback into the prompt
            found = []
        write_out("".join(f"{option}\n" for option in found))
        return 0
    args = build_parser(prog).parse_args(argv)
    if not args.install:
        write_out(SCRIPTS[args.shell])
        return 0
    ctx.require_writes(f"completion {args.shell} --install")
    results = install(args.shell)
    if ctx.json_mode:
        print(json.dumps({"shell": args.shell, "files": [{"path": str(path), "action": action}
                                                          for path, action in results]}, ensure_ascii=False))
    for path, action in [] if ctx.json_mode else results:
        output.info(output.err.green("✓ ") + (f"Added to {path}" if action == "added" else f"Already in {path}"))
    if not ctx.json_mode and any(action == "added" for _, action in results):
        output.info(f"Open a new {args.shell} terminal (or reload the file) for Tab to complete clt.")
    return 0

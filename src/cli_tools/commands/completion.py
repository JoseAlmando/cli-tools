"""clt completion: Tab completion for bash, zsh, fish and PowerShell, read from clt's own parsers so it never goes
stale. The script each shell loads only passes the words typed so far back to clt, which answers with candidates."""
import argparse
import importlib
import os
import sys

from cli_tools import registry
from cli_tools.core import api, context, output

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


def build_parser(prog):
    parser = argparse.ArgumentParser(prog=prog, formatter_class=output.HelpFormatter,
                                     description="Print the Tab completion script for a shell.",
                                     epilog=f"""load it once per session, or add the line to the shell's startup file:
  bash        eval "$({prog} bash)"                       # ~/.bashrc
  zsh         eval "$({prog} zsh)"                        # ~/.zshrc, after compinit
  fish        {prog} fish > ~/.config/fish/completions/clt.fish
  powershell  {prog} powershell | Out-String | Invoke-Expression   # $PROFILE
""")
    parser.add_argument("shell", choices=list(SCRIPTS))
    return parser


def main(argv, ctx=None, prog="clt completion"):
    typed = os.environ.get(WORDS_VAR)
    if typed is not None and not argv:  # called by a shell's completion script
        try:
            found = candidates(typed.split(SEPARATOR)[:-1])
        except Exception:  # a broken completion must never print a traceback into the prompt
            found = []
        sys.stdout.write("".join(f"{option}\n" for option in found))
        return 0
    sys.stdout.write(SCRIPTS[build_parser(prog).parse_args(argv).shell])
    return 0

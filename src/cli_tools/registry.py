"""Every command clt runs: its name, the module with its main(), and the line clt --help shows.

clt imports a command's module only when that command runs, so adding a tool means adding its package and one
line here."""

TOOLS = (
    ("taiga", "cli_tools.tools.taiga.cli", "client for the Taiga API"),
    ("gitea", "cli_tools.tools.gitea.cli", "client for the Gitea API, with OAuth2 login in the browser"),
    ("github", "cli_tools.tools.github.cli", "client for the GitHub API, with a personal access token"),
    ("jira", "cli_tools.tools.jira.cli", "client for the Jira Cloud API, with an API token"),
    ("azdo", "cli_tools.tools.azdo.cli", "client for the Azure DevOps Services API, with a personal access token"),
    ("release", "cli_tools.commands.release.cli", "bump the version, update the changelog, tag and publish a release"),
    ("update", "cli_tools.commands.update", "install the latest release of clt, or a given version"),
)

ALIASES = {"t": "taiga", "gt": "gitea", "gh": "github", "j": "jira", "ad": "azdo"}


def canonical(name):
    return ALIASES.get(name, name)


def find(name):
    """The module path of the command called name (or one of its aliases), or None."""
    name = canonical(name)
    return next((module for command, module, _ in TOOLS if command == name), None)

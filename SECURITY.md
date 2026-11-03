# Security policy

clt handles access tokens for Taiga, Gitea and GitHub, so security reports are very welcome.

## Reporting a problem

Please **do not open a public issue**. Report it privately through
[GitHub's security advisories](https://github.com/JoseAlmando/cli-tools/security/advisories/new), with:

- what an attacker could do, and the conditions it needs;
- the steps or the command that show it (with fake tokens and `example.com` hosts);
- the clt version (`clt --version`) and your system.

This is a personal project maintained in spare time: expect an answer within a week, and a fix as soon as it is understood.
You will be credited in the release notes unless you prefer otherwise.

## Supported versions

Only the latest release gets fixes. `clt update` installs it.

## What counts

For example: a token or password printed, logged or sent to a host other than its own API; credentials written with
permissions that let other users read them; redirects or pagination links that make clt contact another server; anything
that lets `--read-only` send a write.

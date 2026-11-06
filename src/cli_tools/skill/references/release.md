# clt release

`clt release` runs inside the project's repo. Never push or publish without the user's go-ahead, and never use `--skip-tests` unless the user asks for it.

```bash
clt release --json --dry-run --bump <fix|feat|major>          # show the plan to the user
clt release --json --bump <fix|feat|major> --yes --no-push    # tests, version, CHANGELOG.md, commit and tag, locally
clt release --json --publish                                  # only after the user agrees: push and publish
```

If `--publish` fails halfway, running it again finishes the job; it skips what is already done.

The release commit and the annotated tag's message are `chore(release): X.Y.Z` (older releases used `Release X.Y.Z`; both are left out of the next changelog). With Conventional Commits prefixes, the changelog groups the commits under *Breaking changes*, *Features*, *Fixes* and *Other*.

`clt update --check` is a read; `clt update [VERSION]` reinstalls clt itself, so run it only when the user asks.

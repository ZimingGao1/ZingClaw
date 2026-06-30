---
name: github
description: "Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries."
metadata: {"nanobot":{"requires":{"bins":["gh"]}}}
---

# GitHub Skill

Use the `exec` tool to run the `gh` CLI for GitHub work.

Always specify `--repo owner/repo` when not already inside the target git repository, or use URLs directly.

## Pull Requests

Check CI status on a PR:

```bash
gh pr checks 55 --repo owner/repo
```

List recent workflow runs:

```bash
gh run list --repo owner/repo --limit 10
```

View a run and see which steps failed:

```bash
gh run view <run-id> --repo owner/repo
```

View logs for failed steps only:

```bash
gh run view <run-id> --repo owner/repo --log-failed
```

## API for advanced queries

The `gh api` command is useful for data not exposed by simpler subcommands.

Get PR fields with JSON filtering:

```bash
gh api repos/owner/repo/pulls/55 --jq ".title, .state, .user.login"
```

## JSON output

Most `gh` commands support `--json` for structured output and `--jq` for filtering:

```bash
gh issue list --repo owner/repo --json number,title --jq ".[] | \"\\(.number): \\(.title)\""
```

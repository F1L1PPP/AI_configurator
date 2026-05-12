# /checkpoint

Daily save point: lint → test → commit → push → annotated backup tag.

## Usage

```
/checkpoint "feat(cli-agent): add show_vlan_brief"
```

The argument is the conventional commit message. Required.

## What it does

1. Runs `ruff check .` — aborts on any error.
2. Runs `pytest -q` — aborts on any failure.
3. `git add -A` and commits with the provided message.
4. `git push` to origin.
5. Creates an annotated tag `backup-YYYYMMDD-HHMMSS` pointing at HEAD.
6. Pushes the tag to origin.

## Invocation

On Windows (dev box):

```powershell
scripts\checkpoint.ps1 "feat(cli-agent): add show_vlan_brief"
```

On Linux/macOS (CI):

```bash
bash scripts/checkpoint.sh "feat(cli-agent): add show_vlan_brief"
```

## Rules

- Never run this without a commit message argument.
- If lint or tests fail, the script exits before `git add`. Fix the issue first.
- The backup tag is **informational only** — it is not a release tag and must never be moved.
- Only I (Filip) create release tags (`v0.x.x-*`). The script never creates release tags.

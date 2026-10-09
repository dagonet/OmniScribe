This file is imported at the end of `CLAUDE.md`; where the two conflict, this file wins. It is loaded at session start, always on — a new or edited version here is picked up at the NEXT session start, not the current one.

What belongs here: project rules, plugin routing blocks (context-mode and similar), and the MCP servers this project relies on (name, purpose, main-thread only) — `CLAUDE.local.md` is no longer shipped, so list them here; the user-level `mcp-usage` skill covers the occasional procedures. Nothing here should duplicate a `paths:`-scoped rules file. Shared, committed project rules belong in `project-instructions.md`; machine-local ones belong in `CLAUDE.local.md` — the migration folds nothing from one into the other, and no precedence between them is claimed, because none exists to claim.


# Project Notes (panoscribe)

**Green-CI merge gate (definition of done):** a PR may be merged ONLY after its head SHA shows a successful GitHub Actions run — check via `gh_workflow_list` / `github_workflow_run_wait` and require `conclusion=success` before merging. Local green is insufficient: platform-specific failures (e.g. Linux-only import errors) never surface on Windows. After merging, confirm main's push run is also green. If CI is red for an unrelated reason, fix CI first — never merge on top of red. The PO includes this gate in every dev spawn prompt's merge instructions and re-checks Actions status at every release.

**Verify a run's jobs, not its conclusion:** a workflow run in which every job is skipped still reports `success`. After any publish/release dispatch, confirm at job level (`github_check_runs_for_sha`) that the specific job you needed actually ran.

**Gate artifact location:** `bash hooks/run-gate.sh` writes into `<git common dir>/gate/` — inside `.git`, shared by every worktree of the repo, so an artifact minted in an agent worktree is visible to a merge from the main checkout and vice versa. The file is `last-pass.<HEAD sha>.json` when the gated tree equals `HEAD^{tree}`, and `last-pass.tree-<tree>.json` when it differs — which is always the case for the gate a commit runs before the commit object exists (toolkit v4.3.1+, so parallel PRs from one parent keep their own artifacts). `tree` is the matching key either way; `sha` is advisory. When checking for a fresh gate, glob `last-pass.*.json` — never "the newest file in the directory", where a `last-precommit-noop` diagnostic is often newer. `gate-before-merge.sh` accepts an artifact for 3600 s, and up to 24 h if tree and environment are unchanged. A commit that introduces NEW files must `git add` them in a prior call: untracked files enter neither hash, so a chained add-and-commit mints an artifact matching neither tree.

**Bootstrap is `uv sync --extra dev --extra api`** — test/dev tooling lives in `[project.optional-dependencies]`; bare `uv sync` skips `pytest-cov` and the gate fails with an opaque pytest argument error. Release sequence: `docs/release-process.md`. Right after a release, `uv` may serve a stale index for the new version — see `docs/troubleshooting.md`.

**Compact — also preserve:** team configuration (team name, active teammates and their roles).

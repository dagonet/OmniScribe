# panoscribe — Project State

## Current Sprint

_No active sprint._

## Phase Status

- **Phase 1 (MVP):** Complete — transcription pipeline
- **Phase 2 (OCR):** Complete — frame sampling, scene-change detection
- **Phase 3 (Platform):** Complete — platform profiles, UI filter, OCR wire-in
- **Phase 4 (Merge):** Complete — ASR↔OCR merge with dedup
- **Phase 5 (Polish):** Complete — LLM cleanup, batch processing, Docker containerization
- **Phase 6 (Advanced):** Partially complete — FastAPI mode and translation shipped; Web UI, speaker diarization, and browser extension not started

## Toolkit

**claude-code-toolkit v4.4.2** (`9dd3b8b9`), synced 2026-10-10 — **recorded in the same PR as the sync**. v4.4.2 only rewords `hooks/git-pre-push.sh`'s installer texts: in v4.4.0/v4.4.1 the literal push verb on one of its code lines made the gate's script-body scan refuse the install (and any command merely naming it) on the protected branch, so this clone's shim was installed by the user from a plain `!` command on 2026-10-09. v4.4.x adds a native git pre-push hook (`hooks/git-pre-push.sh`) that refuses pushes to a protected branch from ANY client (terminal, IDE, GUI); it is not wired through `settings.json` but installed **once per clone** with `bash hooks/git-pre-push.sh --install` (writes a shim to `.git/hooks/pre-push`; `git push --no-verify` is the one deliberate escape). The git gates now also refuse `$'...'`/`$"..."` quoting in a command — use plain quotes and `printf`. Hook registrations moved to an exec form, and a new session-start `verify-hooks.sh` reports missing hook scripts. From v4.3.1: v4.3.1 bounds the per-commit run: `pre-commit-test.sh` stops and REFUSES a commit whose run exceeds `**Test timeout**` (unset = 540 s), and here — no `**Test**`, so the commit runs the full Gate — that budget applies to the Gate. If a commit is refused for the budget, add `- **Test timeout**: <seconds>` (30–3300) to `PROJECT_CONTEXT.md`. From v4.3.0: two new hooks: `deny-hang-shapes.sh` (refuses `cd <repo> && cmd1; cmd2`-shaped commands — use `git -C <dir>` or a script file) and `model-floor.sh` (a sub-agent spawn with no model gets the project default, `sonnet`). Three new `PROJECT_CONTEXT.md` keys are opt-in and NOT added here: `**Test paths**`, `**Gate extra**`, `**Subagent default model**` — the sync never rewrites this `once`-class file, so add them by hand from the template's commented examples if wanted. Manifest is **v4**: `CLAUDE.md` is template-owned and byte-identical, project content lives in `.claude/project-instructions.md`, agent tool extensions in `.claude/agent-grants.json`. `template_verify` post_commit: **24 PASS, 0 FAIL, 0 SKIP, 7 INFO**.

> **Record the sync in the same PR as the sync.** This line went stale three times — after #142/#143, #144/#145, and #147/#148 — because the sync and its record were separate commits. `template_verify` cannot catch it: `PROJECT_STATE.md` is `once`-class project prose and sits outside every one of its checks. Writing the habit down was not enough on its own; **v4.1.0 is the first sync where the record ships in the same commit**, which is the only thing that has ever actually closed it.

Sync history since v3.0.0:

| Version | Commit | PR | Note |
|---|---|---|---|
| v3.0.2 | `60e4b0d` | #137 | |
| v3.0.3 | `86561fe` | #138 | |
| v3.0.4 | `1450034` | #139 | |
| — | — | #140 | Fixed `placeholders.BUILD_COMMAND`, which held the *test* command; repaired the rendered damage at `CLAUDE.md:88` |
| v4.0.0 | `89dcaee` | #141 | Manifest v2 → v3; sync server now ships from the toolkit checkout |
| — | — | #142 | Declared `**Gate-checked branches**`/`**PO write surface**`/`**Post-edit build**` (all `none`, measured); removed `**Test Command**` |
| v4.0.1 | `a97235bd` | #143 | `lastSynced*` pair dropped; gate artifacts moved into `.git/gate/`; `**Gate Command**` → `**Gate**` |
| — | — | #144 | Brought this file up to v4.0.1; retired the hand-kept "permanent deviations" list, which described the v2 keep-mine model |
| v4.0.2 | `39135e78` | #145 | `require-skills-block.sh` fails closed on an Agent payload with no prompt; `post-edit-build.sh` treats `None` as `none`; adopted the v4.0.2 `project.md` seed |
| — | — | #146 | Brought this file up to v4.0.2 |
| v4.0.3 | `150627de` | #147 | Guard hooks read a script argument's first 16 KB; expired gate artifact accepted within 24 h on tree + environment identity |
| v4.1.0 | `038f247f` | #149 | Manifest v3 → v4; `CLAUDE.md` becomes template-owned and byte-identical; PROJECT-CUSTOM region moves to `.claude/project-instructions.md`; `agent-grants.json`; new `deny-claude-md-writes.sh` hook. Sync **and** record in one PR |
| — | — | #150 | Marked three `PROJECT_STATE.md` statements superseded by v4.1.0 |
| v4.1.1 | `eef42779` | #151 | v4.1.0 rollout patch: four enforcement scripts updated; the `#20` dropped-`reason` repair applied by hand |
| — | — | #152–#154 | Worktree base moved under `g:/git/.worktrees/panoscribe`; `dist` artifact retention 3 days; `.claude/worktrees/` gitignored; always-on gate-artifact rule corrected |
| v4.2.0 | `d95cf57c` | #155 | Carries v4.1.2's nine template changes (8 hook scripts + `AGENT_TEAM.md`): backslash-continuation join, whole-line comment strip before the verb scan, filesystem case-fold probe, byte-measured `cmd_len`, widened gate fingerprint. No conflicts, no local edits, no migration; the hand-restored `reason` annotation survived finalize |
| v4.4.2 | `9dd3b8b9` | — | One update: `hooks/git-pre-push.sh` (installer texts no longer carry the push verb on a code line; a `core.hooksPath` equal to the default hooks dir counts as unset). No conflicts, no local edits, no new or deleted files, `settings.json` unchanged |
| v4.4.1 | `2cea6570` | #158 | 15 updates (10 hooks, `lib/json.sh`, `lib/git-cmd.sh`, `coder.md`, `python-coder.md`, `settings.json` re-registered in exec form plus a SessionStart `verify-hooks.sh`) and 3 new files (`hooks/git-pre-push.sh`, `hooks/verify-hooks.sh`, `hooks/lib/agent-model.sh`). No conflicts, no local edits, no migration. Session restarted first (new skill body; server 4.4.1) |
| v4.3.1 | `b8608a80` | #157 | Eight updates: `lib/json.sh`, `lib/git-cmd.sh`, `gate-before-merge.sh`, `no-push-main.sh`, `pre-commit-test.sh` (per-commit time budget, 540 s default, also caps the Gate fallback), `run-gate.sh`, `settings.json` (pre-commit hook timeout 3360 s), `AGENT_TEAM.md`. No conflicts, no local edits, no migration. MCP server reconnected first (it held 4.3.0) |
| v4.3.0 | `3a901fe3` | #156 | New hooks `deny-hang-shapes.sh` and `model-floor.sh` (wired in `settings.json`); `pre-commit-test.sh`, `run-gate.sh`, `agent-budget-warn.sh`, `lib/git-cmd.sh`, `AGENT_TEAM.md` updated. No conflicts, no local edits, no migration. MCP server restarted first (it held 4.2.0). Three opt-in `PROJECT_CONTEXT.md` keys left unset |

### v4.1.1 — the rollout patch, and one repair that no sync could do

Four enforcement scripts updated, nothing else: `hooks/lib/git-cmd.sh`, `hooks/run-gate.sh`, `hooks/gate-before-merge.sh`, `hooks/deny-claude-md-writes.sh`. `settings.json` was IDENTICAL, so no hook was added, removed or re-matched and the model-bump rule did not fire — but all four change enforcement *behaviour*, which is worth saying out loud even when the mechanical rule stays quiet.

**The `#20` repair, applied by hand, because a migration is a one-time transform.** We migrated to v4 under v4.1.0, before the fix, so `migrate_v3_to_v4` rebuilt entries instead of transforming them and `PROJECT_CONTEXT.md` lost its `"reason": "Project-specific config"` annotation. Re-running the migration cannot undo it (`template_migrate_manifest` on a v4 manifest is the idempotent no-op), so the value was restored by hand from `<backup_dir>/template-manifest.json.pre-migration`. Scope was exactly one entry: `deletedAcknowledged` was absent entirely (so the blanked-hash arm never applied) and no entry carried a blanked hash. `classes_and_hashes` PASSes with the restored key, so a descriptive annotation on a `once` entry is tolerated.

**`manifest_migration` replaces the frozen `migration_required`.** The load response now carries `{"from": 4, "to": 4, "required": false}` — computed against the current template — where the old field answered only the v2→v3 question and was silent about everything since. Read the new one; the old is removed in v4.2.

- **A probe that measures its own plumbing reports `0 0 0` on a perfectly live gate.** Our first run of the step-3 positive control returned `0 0 0`, the skill's own signature for *enforcement is GONE*. It was the instrument. The probe read `echo "... [$(basename "$PAYLOAD")] exit=$?"` — the command substitution runs during expansion of that same argument list and overwrites `$?` before `$?` is expanded, so every arm reported `basename`'s status, which is always 0. Re-run with `rc=$?` captured on its own line: **`2 0 0`**. The shipped example uses a plain `[$PAYLOAD]` and is correct — the defect was introduced by prettifying the output. **Uniformity across arms that are supposed to differ is the tell**, exactly as v4.1.1 says; a single exit code never is.
- **The v4.1.1 data-file probe shape works.** Payloads in their own files under the temp root, probe script carrying no git verb: it ran to completion instead of self-blocking the way the v4.0.3-era probe did. That is `#11` confirmed on a real consumer, not a fixture.
- **`project_md_seed_current`'s new third arm false-positives on prose that explains the retirement.** It fired here with *"header references PROJECT-CUSTOM, a region v4.1.0 removed"* — on the historical note added in #149 that says the region was retired. Our guidance sentences were already correctly repointed. Measured two-sided by changing ONLY the note and leaving the guidance untouched: with the literal token present → INFO *"header references PROJECT-CUSTOM"*; with it reworded → INFO *"seed is current"*. So the arm keys on the bare token anywhere in the header, not on where the guidance actually points, and its remedy told us to do what was already done. Same class as a sweep firing on documentation of the thing it searches for. Reported upstream.
- **`enforce-delegation.sh` falsely denies a multi-line `git add --`** when the path list is backslash-continued and a continued line begins with `hooks/run-gate.sh` — the hook splits on newlines before joining continuations, so that segment does not start with `git`. Keep the whole `git add --` list on ONE line. Fix lands in v4.1.2. (Measured by another consumer at `eef4277`, not here; we followed the workaround rather than reproducing it.)

### v4.1.0 — manifest v3 → v4, and where project content lives now

`CLAUDE.md` is now **template-owned and byte-identical to the rendered template**, for everyone including the PO. It is no longer an editable file. The 1692-byte PROJECT-CUSTOM region moved verbatim into **`.claude/project-instructions.md`** (`once`-class, ours permanently, imported by `CLAUDE.md`'s last line `@.claude/project-instructions.md`). Delivery is at **session start** — a new or edited version is picked up at the NEXT session start, not the current one.

**`hooks/deny-claude-md-writes.sh` is the mechanism that replaces discipline.** Measured here on the installed hook, 12 synthetic payloads under Git Bash: root `CLAUDE.md` via all four editing tools → deny (2); `docs/CLAUDE.md`, `CLAUDE.local.md`, `.claude/project-instructions.md`, a missing `file_path`, and a `Bash` payload → allow (0); **garbage stdin and empty stdin → deny (2)**, so it fails closed. An unparseable manifest or a missing JSON parser also exits 2. *"Survives `bypassPermissions`" is upstream's claim and is NOT measured here* — it is the load-bearing half of the argument, so it is recorded as unverified rather than restated as fact. **Stated non-goal, confirmed against the code:** it dispatches on `tool_name` only, so a `Bash` payload carrying `sed -i s/a/b/ CLAUDE.md` exits 0. The backstop is the next sync's `claude_md_identical` FAIL plus overwrite — a hand-edit is loud and self-healing instead of a permanent silent deviation, which is exactly the failure this project had under the v2 keep-mine model.

**`.claude/rules/project.md` had to be hand-corrected, and that is the `once`-class trap firing for real.** Both its "always-on rules belong in ..." sentences still named `CLAUDE.md`'s PROJECT-CUSTOM region — a region this release deletes, in a file `deny-claude-md-writes.sh` now refuses writes to. The file is **unscoped**, so it loads at EVERY session start at `CLAUDE.md` priority: every future session would have been handed an always-on instruction pointing somewhere nonexistent and write-denied. Upstream corrected its own seed in v4.1.0 (`templates/python/.claude/rules/project.md:19` now names `.claude/project-instructions.md`), but the file is `once`-class, so **that correction can never arrive by sync** — it had to be made by hand, and this PR was the only moment it would have been noticed. Found by the `code-reviewer` pass, which is the first one a template-sync PR has had in five releases.

**`project_md_seed_current` reported INFO "seed is current" while our seed was demonstrably stale** — line 19 differed from the shipped seed in exactly the way R-G was written to surface. R-G predicts an un-migrated consumer reads "older"; we read "current" *after* migrating, with a stale line. A false green on the one check pointed at this drift. Reported upstream.

**Defect #17 (upstream v4.1.1) was avoided here by SEQUENCING, not by remediation.** `migrate_v3_to_v4` stores current-template hashes for template-owned files it does not write, so a consumer who migrates first sees `.claude/settings.json`, `AGENT_TEAM.md` and `hooks/enforce-delegation.sh` reported as `LOCAL_EDITED` with `template_changed: false` — genuine template updates wearing a local-edit label, and "keep mine" on that label would freeze v4.0.3 content for the enforcement wiring. The skill's own v3-window rule avoids it: **"sync everything else, migrate, then `CLAUDE.md` applies."** Applying those three while still honestly labelled `TEMPLATE_UPDATED` meant the migration stored hashes over a disk that already held that content. Verified by hash equality before migrating, not assumed:

| path | apply result | previewed v4 manifest | post-migration status |
|---|---|---|---|
| `.claude/settings.json` | `66f45cfd…` | `66f45cfd…` | IDENTICAL |
| `AGENT_TEAM.md` | `048a7245…` | `048a7245…` | IDENTICAL |
| `hooks/enforce-delegation.sh` | `b0f03feb…` | `b0f03feb…` | IDENTICAL |

`local_edited` was **0** immediately after `migrated: true`. Upstream's remediation (re-apply all three with `backup_dir` in I2 order) is for a consumer who migrated first; the ordering fix is strictly better and costs nothing.

**The migration refusal paths did not apply**, both checked before writing: `out_of_region_diff` was empty (our `CLAUDE.md` was byte-identical to the synced template outside the region) and `.claude/project-instructions.md` did not exist. Either one would have refused with nothing written.

- **`region.sh --body` emits one more trailing newline than the migration's stored body.** A byte-compare of the captured region against `project-instructions.md` fails by exactly one `\n` on a *correct* migration — the extractor's own trailing newline, not project content. Every content byte survived; all five paragraphs plus the team-config line verified present. Same off-by-one family as `region_bytes` reporting 1693 where `--body` reports 1692.
- **`migration_required` is a v2-only field and reads `false` on a current server.** Measured here on 4.1.0 @ `038f247` *in the same call* where `template_verify` FAILed `status_clean` with `MIGRATION_REQUIRED`. Two fields, one response, opposite answers. `registered_tools` containing `template_migrate_manifest` is likewise necessary but not sufficient — v4.0.x already registered it. The only honest step-0 gate is `server_commit` == toolkit HEAD, then `template_verify`.
- **The `2 0 0` probe self-blocked again**, this time via `gate-before-merge.sh` rather than `no-push-main`: `Gate artifact expired (156461s old, max 3600s)`. Past both the TTL and the 24 h tree+env window, so it correctly refused instead of accepting on tree identity. Per the skill's own rule that is the positive result. Arm (a) — `bash -n` over all 17 scripts, 0 failures — is the arm that still carries information.
- **`PROJECT_CONTEXT.md` lost its `"reason": "Project-specific config"` annotation** in the v3 → v4 rewrite, reported in no field: `unknown_keys`, `unknown_file_keys` and `superseded_keys_dropped` were all empty. Harmless here (descriptive prose, not a resolution), but it is a silent drop and is reported upstream.

### v4.0.3 — what changed here

Five enforcement files: `hooks/lib/git-cmd.sh`, `gate-before-merge.sh`, `no-push-main.sh`, `pre-commit-test.sh`, `run-gate.sh`. Toolkit HEAD was seven docs-only commits past the tag; tracked-tree diff verified clean, so step 1b case 2 — `template_commit 150627de`, `template_version v4.0.3`.

- **Guard hooks now read a script argument's first 16 KB.** A git verb inside a script is gated exactly as if typed. Measured here two-sided, and the instrument is the tree-keyed gate record, not `cmd_len`:

  | arm | body | gate record |
  |---|---|---|
  | control | no git verb | `elapsed` UNCHANGED at 178 — no Gate ran |
  | test | `false && git commit -m unreachable` | `elapsed` 63 — fresh Gate run |

  The verb is present as text but unreachable, so nothing executes; the guard reads it regardless. **`cmd_len` on `last-precommit-noop` cannot be used as the instrument**: the PreToolUse hook writes that record *before* the command runs, so any call that reads it reads its own record (observed drifting 489 → 400 → 883 purely from inspection commands). The tree-keyed record is safe because only commit-segment commands write it.

- **Consequence for probes.** The skill's standing "put the probe in a script file" remedy no longer hides git verbs from the guard, so the routine `2 0 0` `no-push-main` probe self-blocks from inside a session. Per the skill's own rule that is the positive result, not a failure; the `2 0 0` line is retired from the report format in toolkit v4.0.4. Do not reach for a wrapper script or a runtime-assembled verb to "pass" it — those are the bypass class `deny-secret-reads` exists to prevent. **[Superseded by v4.1.1]** — the probe's payloads now live in separate data files outside the repo, so the probe script carries no git verb and `2 0 0` runs cleanly from inside a session again (measured on every sync since, most recently v4.4.1).

- **The 24 h tree+env artifact acceptance fired for real** on the #147 merge: the artifact was 2.5 h old, well past the 3600 s TTL, sha and tree both matching HEAD, and the merge was allowed on tree identity across a reboot. The commit message for #147 says that arm was "not exercised by this sync" — it was exercised minutes later, by the merge.

### v4.0.2 — what changed here

- **`require-skills-block.sh` now fails closed.** It guards on `tool_name` and refuses an Agent payload carrying no `tool_input.prompt`. Verified two-sided on the installed hook: Agent-without-prompt → exit 2, Bash payload → exit 0. The second arm is the one that matters — a guard refusing everything passes the positive arm identically to one that works.
- **`post-edit-build.sh` treats a literal `None` as `none`.** No effect here; panoscribe declares lowercase `none`. The behaviour change is measured at the toolkit end (`test-hooks.sh` fixture pair, pre-fix failure reproduced by reversion), not on this tree.
- **Adopted the v4.0.2 `.claude/rules/project.md` seed.** It carries the sentence this project's own measurement produced — a new or edited rules file is picked up at the NEXT session start — plus a worked `paths:` frontmatter example the hand-written header lacked.
- **`registered_tools`** joins `capabilities` in the load response, and it is the stronger instrument: `capabilities` says what the loaded modules support, `registered_tools` says what the process can actually dispatch. A pre-release server once advertised `template_verify` with a nine-tool registry.

### v4.0.1 — what changed here

- **Legacy version labels gone.** `lastSyncedVersion`/`lastSyncedVersionOf` were client-stamped by the old skill step 7b and duplicated the server-written `template_version`/`template_commit`. Step 7b is deleted upstream, so `superseded_keys_dropped` removed them for good rather than re-adding them next sync. `unknown_keys` is now empty.
- **Gate artifacts moved** to `<common git dir>/gate/last-pass.<sha>.json` — shared across worktrees, and inside `.git`, so no longer a working-tree object `git add -A` could sweep up. The three legacy `.gate/` files were removed **by name** after checking `**Log Path**`; another consumer had 585 KB of unrecoverable logs behind the same instruction when it said to delete the directory.
- **`**Gate Command**` renamed to `**Gate**`.** v4.0.1 normalised `Gate` and `Test` to the short spelling while keeping `Build`/`Format`/`Lint Command` long. The hooks accept both; the rename cleared the one `template_verify` FAIL (`declared_keys: deprecated_spelling`). **Do not** shorten Build/Format/Lint — the template declares those long, and renaming creates the same mismatch in the other direction.

**CORRECTION carried in #143:** the long-standing note that `**Test**: none` is eval'd as a command and blocks every commit is **wrong, and has been since toolkit v3.0.3**. `hooks/pre-commit-test.sh` treats a trimmed, case-insensitive `none` as *not declared* and falls through to the Gate — identical to absent. It came from the template's own stale comment, which is `once`-class here and so would never have self-corrected. `**Test**` stays absent because absent is unambiguous, not because `none` is unsafe.

**Rules-file delivery, measured 2026-09-17.** An *unscoped* `.claude/rules/*.md` (no `paths:` key) IS delivered to every subagent at spawn, with full content, at `CLAUDE.md` priority — `.claude/rules/project.md` arrives; `python.md` (scoped `**/*.py`) does not. But a rules file **created or edited mid-session does not reach a subagent spawned later in that session** — measured twice, untracked and staged, so index-tracked-ness is not the gate. Whether the gate is "committed to HEAD" or "present at session start" is unresolved. Practical rule: after touching a rules file, assume nothing reads it until the next session.

### v4.0.0 — manifest v2 → v3 ownership classes

Three `keep-mine` resolutions were dropped by the migration. A dropped resolution is a loss **only when the incoming class is weaker**:

| Path | Incoming class | Effect |
|---|---|---|
| `PROJECT_CONTEXT.md` | `once` | **Stronger** than `keep-mine` — never overwritten again |
| `PROJECT_STATE.md` | `once` | **Stronger** |
| `CLAUDE.md` | `template` | The only real one; resolved by **accepting the template** |

`CLAUDE.md`'s two out-of-region deviations were both superseded by v4.0.0 (it ships the single-entry-point Quick Start and an equivalent Required-Skills sentence). The PROJECT-CUSTOM region is preserved by the region mechanism, independently of ownership class. **[Superseded by v4.1.0]** — for `CLAUDE.md` that region no longer exists; the region mechanism still applies to `AGENT_TEAM.md` and the seven agent files, which keep theirs.

`CLAUDE.local.md` is now **project-owned** — dropped from manifest tracking as project-class, untouched on disk.

**`once` cuts both ways.** A `once` file is never clobbered and, by the same mechanism, never re-diffed — so template-side *additions* never arrive. `PROJECT_CONTEXT.md` therefore keeps the `**Gate Command**` / `**Test Command**` spellings (the hooks still accept both) and will **not** receive the newly declared `**Gate-checked branches**`, `**PO write surface**` or `**Post-edit build**` keys without a hand edit. `**Gate-checked branches**` is the one with teeth: its absence is why `gate-before-merge.sh`'s gate-checked arm has never once fired here. **[Superseded]** — `**Gate**` was renamed to the short spelling in v4.0.1 (see above), and `PROJECT_CONTEXT.md` now declares all three keys (`**Gate-checked branches**`, `**PO write surface**` and `**Post-edit build**`, each set to `none`).

**`.claude/rules/project.md` loads at launch.** A rules file with no `paths:` key is delivered at every session start at `CLAUDE.md` priority — only `paths:`-scoped ones are lazy. The migration wrote a diff of `CLAUDE.md`'s out-of-region hunks there on the opposite assumption; the body was replaced with an inert note in #141, and the always-on rules stayed in `CLAUDE.md`'s PROJECT-CUSTOM region as the single source. Fixed upstream in toolkit v4.0.1. **[Superseded by v4.1.0]** — the single source is now `.claude/project-instructions.md`; `.claude/rules/project.md` was hand-repointed at it in #149, since a `once`-class file can never receive that correction by sync.

The v3.0.0 consolidation retired three agents — **spawn the successor, not the retired name**:

| Retired | Spawn instead | Why it is safe |
|---|---|---|
| `requirements-engineer` | `architect` | absorbed it; carries `brainstorming` (`AGENT_TEAM.md:405`, PO routing at `:65`) |
| `test-writer` | `tester` | absorbed it; carries `test-driven-development` (`:404`) |
| `doc-generator` | `coder` | absorbed it |

Absorb, not rename — the survivors gained the skills, so no capability was lost. Seven agent files remain.

**Deviation tracking is now the ownership class, not a hand-kept list.** Under manifest v3 the old "permanent deviations" list (`PROJECT_CONTEXT.md`, `CLAUDE.md`, `PROJECT_STATE.md`) is obsolete: `PROJECT_CONTEXT.md`, `PROJECT_STATE.md` and `VERIFICATION_PLAYBOOK.md` are `once` (kept by the class, no annotation needed), and `CLAUDE.md` is `template` with its project content held in the PROJECT-CUSTOM region **[Superseded by v4.1.0]** — `CLAUDE.md` is still `template`-class, but it now holds no project content at all: that content lives in `.claude/project-instructions.md`, and `hooks/deny-claude-md-writes.sh` refuses writes to `CLAUDE.md` outright. `hooks/run-gate.sh` left the old list back in PR #131 — the #98 pytest-cov preflight moved out to repo-root `preflight.sh`, wired through the **Gate Command** under the toolkit's terminal contract, so the hook is byte-identical to the template and auto-updates.

`preflight.sh` itself stays project-owned at the repo root and is **not** manifest-tracked: it runs as the first command of the Gate Command precisely so it does not have to live inside `hooks/run-gate.sh`, which syncs verbatim and would force a hand-merge on every release.

## Backlog

_See GitHub Issues._

# Claude Code Agent Team Setup

## Version

v2.0

> Rationale: toolkit `docs/design-rationale.md#agent_teammd`

---

## How to Use This Document

Look-up reference, not a read-through — load on demand (`CLAUDE.md` -> *Session Bootstrap*). `PROJECT_CONTEXT.md` defines the tech stack, commands, and **task source mode** — read that first, then look up your mode in the **Mode Behavior Table** below.

---

## Session Initialization

1. **Auto-assume PO role** — every session starts with the PO active.
2. **Validate PROJECT_CONTEXT.md** — surface gaps to the user before proceeding; if it doesn't exist, create it per the Appendix pointer.
3. **Load context** — read MEMORY.md and the current task source to see where the project left off.

---

## CRITICAL: Sub-Agent Tool Limitations

**A sub-agent only has the tools its `tools:` frontmatter lists** — a platform limitation, not a config error. Git needs `Bash`; GitHub writes need the matching `mcp__MCP_DOCKER__*` tool. **`.claude/agents/<name>.md` is the source of truth — read the target agent's `tools:` line before putting an operation in its spawn prompt.**

### Agents that can do their own git + GitHub I/O:
`coder`/`<lang>-coder` (`Bash` + PR tools); `code-reviewer` (`Bash`, review-write); `tester` (`Bash`, issue-comment).

### Agents without `Bash`:
`architect` — cannot commit, push, create PRs, merge, or post comments.

### The rule that covers every tool, not just `Bash`
`Explore`/`code-reviewer` hold neither `Edit` nor `Write`. `Explore`/`architect`/`ops` hold no GitHub MCP tool. `isolation: worktree` (`coder`, `tester`, `<lang>-coder`) means those agents cannot reach the main checkout for a sync.

**PO responsibility:** if an agent lacks a needed tool, it returns the work product and the PO performs the git/GitHub I/O.

---

## Roles

### Product Owner (PO)

- Primary interface with the human stakeholder; maintains/prioritizes the backlog. Spawns the **Architect** for new features; reviews/publishes specs; plans sprints; monitors progress, handles escalations; writes a session summary after each sprint.
- **T1 delegated fixes** (see Tiered Sprint Model) — brief inline, no plan file needed. **The PO NEVER edits code, at any tier.** Write surface: `docs/plans/`, `PROJECT_STATE.md`, `PROJECT_CONTEXT.md`, `.claude/`, `AGENT_TEAM.md` — enforced by `hooks/enforce-delegation.sh`.
- **Never reviews code inline** — `code-reviewer` is spawned T2+; T1 relies on the coder's gate run.
- **Read discipline**: Read/Grep only for targeted verification (1-2 files) and orchestration files; open-ended exploration goes to **Explore** (see Model & Effort Policy).
- **Never runs builds or tests** — coders gate, tester verifies, `ops` handles env/tool work; PO verifies via `.gate/last-pass.json`.
- Closes tasks after merge; does **NOT** block the merge pipeline.
- **Open Brain context mediation**: search before spawning, include findings, capture insights after (*Open Brain Context for Agents*).
- **Spawn-prompt skill injection**: look up `subagent_type` in the Spawn-Prompt Binding Table and include a `## Required Skills` block verbatim (`hooks/require-skills-block.sh` enforces this). Omit for `code-reviewer`.

## Model & Effort Policy

- Orchestrator = session model via `/model`: `fable` for T3/T4, `opus` for T1/T2. Workers run `sonnet`; `architect`/`code-reviewer` run `opus` at `effort: xhigh`; `Explore` runs `haiku` at `effort: low`.
- Session effort is deliberately **unset**; raise it per role via the agent file's `effort:`, or `/effort` for one session. Never pass `model` in the Agent call — each agent file owns its own.
- **Aliases only** (`sonnet`/`opus`/`haiku`/`fable`/`inherit`), never a pinned `claude-*` id.
- Wrong answer despite full context -> bigger model. Skipped files/steps -> raise `effort`.

## Workstream Model

### What is a workstream?

An **independent pipeline** per task: `Developer -> Code Reviewer -> Tester -> Developer merges PR`. Each operates autonomously — no shared reviewer/tester bottleneck.

## Mode Behavior Table

The `task-source` field in `PROJECT_CONTEXT.md` determines which column applies.

| Action | `github-issues` | `plan-files` |
|--------|-----------------|--------------|
| **Task definition** | GitHub Issue with acceptance criteria | `docs/plans/sprint-N-*.md` with task sections |
| **RE output** | Issue markdown for PO to post | Plan file markdown for PO to save |
| **Architect guidance** | Comment on the GitHub Issue | Inline `## Architect Guidance` section in plan file |
| **Dev discovers task** | Dev reads issue via `mcp__MCP_DOCKER__issue_read` | PO inlines task AC + files in dev prompt |
| **Review findings** | PR review via `mcp__MCP_DOCKER__pull_request_review_write` | same |
| **Test findings** | PR comment via `mcp__MCP_DOCKER__add_issue_comment` | same |
| **Close task** | PO closes GitHub Issue via `mcp__MCP_DOCKER__issue_write` | Task list (TaskUpdate); MEMORY.md after sprint |
| **Branch naming** | `feature/issue-{number}` or `bugfix/issue-{number}` | PO specifies per task (e.g. `feature/calendar-tz-fix`) |
| **Worktree naming** | `{base}/{project}-issue-{number}/` | `{base}/{project}-{branch-name}/` |
| **Commit convention** | `issue-{number}: {description}` | `feat:`/`fix:`/`chore:`/`test:`/`docs:` prefixes |
| **Tech debt tracking** | GitHub Issue with `tech-debt` label | MEMORY.md or next sprint's plan file |
| **Sprint state** | `PROJECT_STATE.md` with issue/PR numbers | MEMORY.md sprint summary |

---

## Tiered Sprint Model

Not all changes need the full sprint ceremony. The PO selects the tier based on complexity:

| Tier | Criteria | Agents | Testing Discipline |
|------|----------|--------|--------------------|
| **T1 Trivial** | < 10 lines, style/config, no logic | 1 coder (solo) | No new tests. Coder runs the gate before merging. |
| **T2 Simple** | 1-2 files, < 50 lines, clear root cause | coder + code-reviewer | Tests if logic changed. Coder runs the gate; reviewer approves. |
| **T3 Standard** | Multi-file, < 200 lines, needs tests | Dev + reviewer + tester | **TDD required.** Failing tests first. Coverage >= 80%. |
| **T4 Complex** | Architectural, > 200 lines, new entities | Architect + dev + reviewer + tester, or **"use a workflow"** if too big for one pass | **Full BDD/TDD.** Scenarios from AC, failing tests first, coverage >= 80%, architect reviews test strategy. |

### Tier Selection Guidelines

- **Lowest defensible tier wins**; justify escalation, not restraint. The tier table is a **cap**, not a menu.
- **Single file/symbol => T1.** Question-shaped turns spawn at most one `Explore`/`ops`, never a team. Never spawn `Explore` on an already-named file — hand the dev the path.
- **Same-file rule**: 2+ fixes touching one file go to a **single dev agent**, regardless of tier.
- **Style/config-only**: T1 unless it affects data binding or behavior. Known-root-cause bug fix: T2 single-file, T3 multi-file/needs tests. New features/refactors: T3 minimum, T4 if architectural.
- **Tester**: T3 runs existing tests + log checks, no new cases; T4 full verification incl. new cases; T1-T2 skip it — the coder's gate run covers it.
- **Visual verification, capture by agent, judgment by PO**: coder/tester captures screenshots; PO reviews and never launches the app itself.

### Tiered Definition of Done

| Checkpoint | T1 | T2 | T3 | T4 |
|-----------|----|----|----|----|
| Acceptance criteria met | PO | PO | Tester | Tester |
| BDD scenarios exist | — | — | — | Yes |
| New tests for changed logic | — | if logic changed | Yes | Yes |
| All existing tests pass | Yes | Yes | Yes | Yes |
| Code reviewer approved | — | PO reviews | Yes | Yes |
| Coverage >= 80% changed files | — | — | Yes | Yes |
| Architect guidance followed | — | — | — | Yes |
| Post-rebase verification | — | Yes | Yes | Yes |
| Build clean + formatted | Yes | Yes | Yes | Yes |
| PR squash-merged | — | Yes | Yes | Yes |
| Worktree cleaned up | — | Yes | Yes | Yes |
| No `TODO`/`FIXME`/`HACK` in changed files | Yes | Yes | Yes | Yes |
| Task closed (see Mode Table) | PO | PO | PO | PO |

### Lean Dev Prompt Templates

Both templates carry the five *Task Brief Upfront* headings — an issue link or a plan path
is a reference, never a substitute for the brief.

**github-issues mode (T2-T3):**

```
Task #{n}: issue #{issue}. Worktree: {path}, branch: feature/issue-{issue}.

## Goal
{1-2 sentences}

## Constraints
{what must not change; platform/style rules}

## Acceptance Criteria
- [ ] {criterion 1}

## Files in scope
- {file} — {what to change}   (out of scope: {paths})

## Definition of done
{tests to pass} + `bash hooks/run-gate.sh` green, then PR.

## Required Skills
- {skill} — {why}

Context: the GitHub issue (reference only). Workflow: implement -> gate -> commit -> push -> create PR -> report the PR URL.
```

**plan-files mode (T2-T3):** same shape as above, except the Task line reads `Task: {title}. Worktree: {path}, branch: {branch}.` (no issue number), plus `Architect guidance: {summary or "none"}` and `Context: {plan_file_path} if one exists (reference only)`.

```
## Required Skills
- {skill} — {why}
```

**PO responsibility (plan-files mode):** inline the acceptance criteria and file list directly in the dev spawn prompt — the dev should not need to read the plan file. The path is additional context only.

---

## Parallel Development via Git Worktrees

### Why worktrees?

Each developer gets its own working directory and branch, backed by one shared `.git` database — no checkout conflicts, no stashing.

### Setup

Prefer `isolation: worktree` in the agent's frontmatter (or on the Agent call) and let Claude Code create/attach the worktree — the PO does not run `git worktree add` by hand; a worktree the PO made is one the harness doesn't know it owns.

Fallback only (a developer agent creating its own):

```
git worktree add {worktree_base}/{project}-issue-{number} -b feature/issue-{number} main   # github-issues
git worktree add {worktree_base}/{project}-{branch-name} -b {branch-name} main             # plan-files
```

See `PROJECT_CONTEXT.md` for the worktree base path; see Mode Behavior Table for naming.

### Rules

- Each worktree is created from `main` at assignment time; each developer works **only** in its assigned worktree.
- Max parallel workstreams as specified in `PROJECT_CONTEXT.md`. Architect **must** flag scope conflicts before parallel work begins.
- Completion cleanup (remove worktree, delete branch) is Merge Protocol step 6.
- `isolation: worktree` cuts from **`origin/main`**, not local `main` or the session branch — a worktree coder lags until an unlanded session PR lands. PO check: `git rev-list --count <base>..<session-branch>` = 0, and `git cat-file -e <base>:<path>` succeeds for every file the brief names. Untracked files are never in the worktree — hand the coder an absolute path.

**Background jobs outlive the agent that spawned them:** A backgrounded job is not scoped to the agent that started it — a `sleep`-and-act cleanup deleted a live lock an hour after its agent handed back (MM-Agent, 2026-09-22), and a stopped gate's children outlived `TaskStop`, kill denied to the noticing agent (this toolkit, same day). Rules: cleanup belongs in the agent's OWN final turn or is handed to a NAMED successor; a successor that finds a resource already released SAYS so instead of claiming it; the noticing agent may be denied the kill, so report the pid and command line — never assume it stopped.

---

## Merge Protocol

After review and testing pass, the developer executes the merge — git/GitHub MCP tools are listed explicitly in its `tools:` frontmatter (`coder`, `<lang>-coder`, `general-purpose`).

### Steps (Developer-executed)

```
1. Pull latest main into the worktree (`git pull --rebase origin main`).
2. Conflicts: resolve (prefer both changes), reformat, rebuild+retest (0 errors,
   0 new failures), commit the resolution, force-push. >10 files or >100 lines:
   message the PO to (a) guide, (b) defer, or (c) re-spawn architect.
3. Verify CI via gh_workflow_list after push (open the PR first — a bare branch
   push produces no run); fix before merging if it fails.
4. Run the gate on the rebased head: `bash hooks/run-gate.sh` writes
   `.gate/last-pass.json` for the current HEAD — the ONLY accepted green;
   commit exactly what was gated. `hooks/gate-before-merge.sh` hard-blocks
   merge tools without a fresh, SHA-matching artifact (< 60 min) unless
   PROJECT_CONTEXT.md's **Gate** field is unset.
5. Squash-merge via GitHub MCP; verify it succeeded.
6. Remove the worktree, delete the local and remote branch, notify the PO.
```

### Merge Ordering

**First-ready, first-merge** — each subsequent merge rebases onto the updated main first.

---

## Workflow

### Task Brief Upfront

Current models need the whole task in the prompt, not a staged planning ritual. Every spawn states: **Goal** (1-2 sentences), **Constraints**, **Acceptance criteria**, **Files in scope** (in and explicitly out), **Definition of done** (tests + `bash hooks/run-gate.sh`).

An agent that has to go looking for any of the five is under-briefed — a prompt defect, not an agent failure.

**Plan files are optional** — write one in `docs/plans/` when work spans sessions or several workstreams need a shared reference; nothing blocks a spawn on one, and no hook parses a literal in it.

**Challenging a plan is optional and on demand** — invoke the `challenge` skill or spawn the architect with the draft.

---

## Rules

1. **No direct pushes to main** — everything through PRs, including T1 (one coder: branch -> fix -> gate -> PR -> self-merge). No exceptions.
2. **One task per developer, one worktree** — see Git Worktrees → Rules.
3. **Max parallel workstreams** — see Git Worktrees → Rules.
4. **Architect reviews BEFORE development** (T4).
5. **Developers own the merge and its sequencing** — wait for the PO's go-ahead. Template syncs are PO-run.
6. **Post-rebase verification required** — rebuild + retest before merge.
7. **Max 3 fix cycles per task** — then Escalation Protocol applies.
8. **Workstream agents are ephemeral** — shut down after their phase.
9. **Permission propagation** — requested once at sprint start; agents spawned with `mode: bypassPermissions`.
10. **Mode consistency** — see Mode Behavior Table; T1/T2 hotfixes may bypass mode formalities but still run the gate.
11. **Brief discipline** — see Task Brief Upfront.

---

## Escalation Protocol

- **Developer stuck** (>3 fix cycles): PO selects (a) scope reduction — simplify and restart, (b) architect re-design — re-spawn with failure context, or (c) human escalation — task, what was tried, failure, recommended next steps.
- **Merge conflicts too complex**: developer reports; PO decides per Merge Protocol.
- **Tester can't verify**: reports why; PO routes to a developer.
- **Scope conflict mid-sprint**: PO pauses workstreams, re-spawns architect.
- **Any agent stuck after escalation**: PO notifies the human.
- **Missing/empty report** (runbook):
  1. A **foreground** Agent call cannot stall — final message or error; an error is a failed dispatch, re-dispatch it.
  2. A **background** agent reports via the completion notification. Check once (or `ListAgents`); past budget with no completion, treat as failed.
  3. Verify actual state via `git log`/`git status`/`list_pull_requests` — never trust the agent's last claim.
  4. Count it as one strike toward the 3-cycle escalation, re-dispatch with a TIGHTER brief. **Never self-perform the failed agent's work.**
  5. **Dead-coder merge handoff**: a pushed branch with an open PR gets a FRESH coder (PR URL, branch, worktree path) to rebase, re-gate, and merge — the PO never finishes merges by hand.
  6. **Report agents**: their report IS the deliverable — none means a failed run, re-dispatch citing the mandate. `hooks/enforce-agent-contract.sh` catches coder/reviewer cases mechanically.
  7. Repeated failures of the same shape are recorded by `hooks/retro-ledger.sh` and replayed at next session start — fix the cause, not the symptom.

---

## Superpowers Skills Integration

[superpowers](https://github.com/anthropics/claude-plugins-official/tree/main/superpowers) handles implementation mechanics; AGENT_TEAM.md owns quality gates (tier, workstream, review, test, merge).

### Spawn-Prompt Binding Table

Include a `## Required Skills` block in every spawn prompt, listing the skills below for the target subagent type — invoked via the Skill tool before task work starts. **Mechanically enforced** by `hooks/require-skills-block.sh` (PreToolUse on `Task`) — a spawn without the block exits 2.

| subagent_type | Required Skills |
|---|---|
| `coder` / any `<lang>-coder` (hook matches the shape, not a list) | `karpathy-guidelines`, `test-driven-development`, `verification-before-completion`, `receiving-code-review` |
| `code-reviewer` | *(none)* |
| `tester` — absorbed `test-writer` | `systematic-debugging`, `verification-before-completion`, `test-driven-development` |
| `architect` — absorbed `requirements-engineer` | `writing-plans`, `brainstorming` |
| `ops` | *(none — pass-through)* |
| `Explore` | *(none — pass-through; custom Explore agent, haiku, effort low)* |

**Reference-only** (not injected): `using-git-worktrees`, `finishing-a-development-branch`, `dispatching-parallel-agents`, `subagent-driven-development`

**v3.0.0 consolidation** — `test-writer`/`requirements-engineer`/`doc-generator` were ABSORBED into `tester`/`architect`/`coder`; survivors keep their name so a stale reference fails loudly at spawn time.

### Copy-paste snippets

Use verbatim; append task-specific instructions below each.

**Report agents (code-reviewer, architect, tester, ops) — always add:**

```markdown
CRITICAL: your final message IS the deliverable — status, files changed, commands run + output summary, open concerns.
If the task grows past its stated scope, stop and report what is done plus the blocker instead of expanding scope.
```

**Coder (and `dotnet-coder`, `rust-coder`, `java-coder`, `python-coder`)** — the Report-agents CRITICAL block above, plus:

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- karpathy-guidelines
- superpowers:test-driven-development
- superpowers:verification-before-completion
- superpowers:receiving-code-review
```

**Tester (absorbed `test-writer`):**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- superpowers:systematic-debugging
- superpowers:verification-before-completion
- superpowers:test-driven-development
```

**Architect (absorbed `requirements-engineer`):**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- superpowers:writing-plans
- superpowers:brainstorming
```

**Code-reviewer:** omit the block (passes through). **Docs** are a `coder` spawn (`doc-generator` absorbed in v3.0.0) — use the coder block.

---

## Appendix: PROJECT_CONTEXT.md Template

The template is `PROJECT_CONTEXT.md` at the project root, shipped by `setup-project.{sh,ps1}` — no second copy here; it drifts from the real file.

---

## Open Brain Context for Agents

Spawned agents cannot access Open Brain directly — the PO searches for relevant context and includes it in spawn prompts, then captures durable insights after the agent returns.

### Before Spawning

| Agent Type | Search Query |
|---|---|
| Architect | architecture/tech-debt/feature/scope for the component |
| Code Reviewer | bug pattern / review history for the component |
| Coder | implementation notes / pitfalls for the component |
| Tester | failure mode / regression / edge case / test pattern for the feature |

Include whatever the search returns: past decisions, rejected alternatives, known weak spots, flaky-test history.

### After Agent Returns

Capture durable insights, not routine results ("no issues found", "all tests pass"): decisions with rationale, rejected alternatives, tech debt found, non-trivial bug patterns, non-obvious implementation calls, bugs with root cause.

---

<!-- PROJECT-CUSTOM:BEGIN — sync-template preserves everything between these markers -->
<!-- Project-specific rules, routing blocks, and extensions go here. -->
<!-- PROJECT-CUSTOM:END -->

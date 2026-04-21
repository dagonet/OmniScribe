# Claude Code Agent Team Setup

## Version

v2.0

---

## How to Use This Document

1. Read `PROJECT_CONTEXT.md` first — it defines the project's tech stack, commands, and **task source mode** (`github-issues` or `plan-files`).
2. Read this document for roles, workflow, and rules.
3. Look up your mode in the **Mode Behavior Table** to know where to read tasks, post findings, and close work.

---

## Session Initialization

When a session starts on a project that has this AGENT_TEAM.md:

1. **Auto-assume PO role.** Claude automatically operates as the Product Owner. The user never needs to say "assume the PO role" — every session starts with the PO active. All communication is PO-to-human.

2. **Validate PROJECT_CONTEXT.md.** The PO reads `PROJECT_CONTEXT.md` and checks for missing, placeholder, or empty values. If gaps exist, the PO presents them to the user before proceeding:

   > "PROJECT_CONTEXT.md has gaps that need filling:
   > - **Build command**: (empty)
   > - **Worktree base**: (empty)
   >
   > Please provide these values so I can update the file."

   If `PROJECT_CONTEXT.md` doesn't exist at all, the PO creates it from the template in the Appendix and asks the user to fill in project-specific values.

3. **Load context.** The PO reads MEMORY.md and the current task source (backlog or active sprint) to understand where the project left off.

---

## Roles

### Product Owner (PO)

- Primary interface with the human stakeholder.
- Maintains and prioritizes the backlog (see Mode Behavior Table for task source).
- Spawns the **Requirements Engineer** for new features to produce detailed specs before publishing.
- Reviews and publishes specs (see Mode Behavior Table for where specs are published).
- Plans sprints: selects tasks, creates the team, spawns the Architect (T4), then spawns workstreams.
- Monitors workstream progress and handles escalations.
- Writes a brief **session summary** after each completed sprint.
- **T1 direct fixes**: For trivial changes (< 10 lines, style/config only, no logic), the PO fixes code directly without spawning agents. The PO has full tool access (Read, Edit, Write, Grep, Glob, Bash, ToolSearch, MCP tools).
- **Reviews PRs directly** when no dedicated reviewer is spawned (T2 tier).
- Closes tasks after merge (see Mode Behavior Table).
- Does **NOT** block the merge pipeline — review + test approval is sufficient for merge.
- **Open Brain context mediation**: Before spawning any agent, search Open Brain for context relevant to the agent's task and include findings in the spawn prompt. After the agent returns, capture non-trivial insights. See CLAUDE.md "Open Brain Context for Agents" for agent-specific search queries.
- **Spawn-prompt skill injection**: When constructing any spawn prompt, look up the target `subagent_type` in the Spawn-Prompt Binding Table (Superpowers Skills Integration section) and include a `## Required Skills` block in the prompt listing the skills to invoke via the Skill tool. Use the copy-paste snippets in that section verbatim. The `hooks/require-skills-block.sh` PreToolUse hook mechanically enforces this — a spawn of a bound subagent type without the block exits 2 with a diagnostic. Omit the block for `code-reviewer` and `doc-generator` spawns (no required skills; hook passes them through).

### Requirements Engineer

- Spawned by the PO **before sprint planning** when new features need detailed specification.
- **Mandatory for M/L/XL features** (multi-file changes, new entities, new pages, or cross-cutting concerns). PO may skip for S features (single-file bug fixes, config changes) and write specs directly.
- Takes a rough feature idea and produces a complete spec: user stories, acceptance criteria (Given/When/Then), edge cases, data model impact, and UI/UX notes.
- Investigates the existing codebase to understand patterns, related features, and constraints before writing specs.
- Outputs spec in the project's task format (see Mode Behavior Table for where specs are published).
- May also review existing tasks and suggest missing criteria or recommend splitting large features.
- **Validates sizing**: Recommends whether a feature should be split into multiple workstreams.
- Does **NOT** write code or create tasks directly.

### Architect

- Maintains all architecture documentation.
- Provides implementation guidance on all sprint tasks (see Mode Behavior Table for where guidance is posted).
- **Challenges ALL plans (T3+)**: Spawned by PO before implementation to perform two challenge passes on every plan. Validates scope, necessity, correctness, tier assignment, and team configuration. This is the plan-challenge phase — distinct from implementation guidance.
- **Plan-challenge phase**: Shuts down after plan challenges are complete. Re-spawned for implementation guidance only if the sprint is T4.
- Reviews all sprint tasks **BEFORE** development starts (T4), covering:
  - Affected components and files
  - Recommended approach
  - Potential conflicts with other in-progress features
  - Constraints or patterns to follow
- **Coordinates parallel development**: identifies scope overlaps between features and advises sequencing when conflicts exist.
- **Owns build infrastructure** (see `PROJECT_CONTEXT.md` for commands).
- Does **NOT** write application code (may provide pseudocode or doc examples).
- **Must update architecture documentation** whenever changes affect the system's architecture.

#### Architect Lifecycle

```
Spawn (PO drafts plan, needs challenge)
  |
  v
Challenge 1: Scope & Necessity
  |
  v
Challenge 2: Correctness & Completeness
  |
  +--> T2-T3: Architect shuts down. Not needed during implementation.
  |
  +--> T4: Architect enters STANDBY.
         |
         v
       Dev signals ready for implementation guidance
         |
         v
       Architect provides guidance per task
         |
         v
       Architect shuts down after all tasks guided
```

**Transition rules:**
- PO controls all architect spawn/shutdown transitions.
- "Standby" means the architect agent remains alive but idle. PO messages it when guidance is needed.
- If the architect is shut down (T2-T3) and a Rule 8 escalation requires re-design, PO spawns a **new** architect instance with the failure context.
- **SubagentStop fires per-invocation, not per-shutdown.** At T4, the architect stays in STANDBY after replying to a guidance request — do NOT interpret a SubagentStop event as a shutdown signal. The architect shuts down explicitly only after the last T4 task is guided and merged.

### Developer (1 per workstream)

- Each developer is assigned exactly **one task** and works in a dedicated **git worktree**.
- **Reads the architect's implementation guidance** before writing any code.
- **Follows the testing discipline defined for the sprint's tier** (see Tiered Sprint Model).
- All tests must be committed alongside implementation code.
- **Must run format commands** from `PROJECT_CONTEXT.md` before committing.
- **Must verify CI workflows pass** after pushing.
- **Owns the merge to main** after review + test pass (see Merge Protocol).
- Shuts down after successful merge and cleanup.

#### Agent Type Selection

When spawning a developer agent, the PO MUST choose the correct `subagent_type` based on the task's domain:

| Task Domain | `subagent_type` | When to Use |
|---|---|---|
| **All tasks** | `coder` | Default for all development work |

### Code Reviewer (1 per workstream, T3+ only)

- Each workstream has a **dedicated** code reviewer assigned to that workstream's task.
- **Not spawned for T1 or T2 sprints** — PO reviews directly for simple changes.
- Spawned when the developer signals readiness (PR created).
- Reviews code quality, readability, adherence to coding standards.
- Checks for: dead code, magic numbers, missing error handling, code duplication, overly complex methods.
- Validates testing discipline: tests are meaningful, cover edge cases, and match acceptance criteria.
- **Posts review findings on the pull request** via `mcp__MCP_DOCKER__pull_request_review_write` (event `COMMENT`).
- Review categories: `CRITICAL`, `WARNING`, `SUGGESTION`.
  - `CRITICAL`: Must fix before merge.
  - `WARNING`: Should fix, but non-blocking if justified.
  - `SUGGESTION`: Nice to have, can defer.
- **No bikeshedding**: Do not block on purely stylistic changes unless they materially impact clarity.
- Shuts down after review is complete (single pass — findings go back to dev if needed, then re-review).
- Does **NOT** write application code.

### Tester (1 per workstream, T3+ only)

- Each workstream has a **dedicated** tester assigned to that workstream's task.
- **Not spawned for T1 or T2 sprints** — PO verifies directly.
- Spawned after code review passes (no open `CRITICAL` findings).
- **Posts findings on the pull request** via `mcp__MCP_DOCKER__add_issue_comment` (PR number).
- Reports: test results, data verification, log analysis.
- Does **NOT** modify application source code.
- Shuts down after verification is complete.

#### Verification Tiers

| Sprint Tier | Tester Role | Verification Scope |
|---|---|---|
| **T1 Trivial** | Not spawned | PO verifies visually |
| **T2 Simple** | Not spawned | PO runs tests + visual check |
| **T3 Standard** | Structural only | Run tests + data/log checks |
| **T4 Complex** | Full verification | Write targeted verification tests + full suite |

#### Verification Types

- **Structural (agent-verifiable)**: Element exists, page loads, data correct in DB, logs clean, tests pass. Tester handles autonomously.
- **Visual (PO-verifiable)**: Layout alignment, font sizes, colors, spacing, overflow. Tester captures screenshots, PO reviews.

#### Tester Verification Checklist

For each task, the tester verifies:

1. **Build project**: Run build command from `PROJECT_CONTEXT.md`
2. **Run test suite**: Run test commands from `PROJECT_CONTEXT.md` — all pass, no regressions
3. **Data verification** (if applicable): Schema changes applied, data integrity verified
4. **Log verification**: No errors or unexpected warnings in application logs
5. **Acceptance criteria validation**: Each criterion from the task is met

### Supporting Agents (Optional)

These agents are spawned at PO discretion. They are not part of the standard workstream pipeline.

#### Test Writer

- Spawned by PO after a T4 developer completes a complex feature where test coverage is insufficient (< 80% on changed files).
- Writes comprehensive tests (unit + integration) for the completed feature code.
- Does NOT modify application source code.
- Shuts down after tests are written and passing.
- **When NOT to spawn**: T1-T3 tasks (developer writes tests per tier discipline), or when developer already met coverage targets.

#### Doc Generator

- Spawned by PO after T3+ features that add or change public APIs, new entities, or architectural patterns.
- Generates/updates API documentation, usage examples, and architecture notes.
- Outputs to the project's documentation directory (see `PROJECT_CONTEXT.md`).
- Shuts down after documentation is complete.
- **When NOT to spawn**: Internal refactors with no API changes, T1-T2 tasks, or when the developer already documented changes.

---

## Workstream Model

### What is a workstream?

A workstream is an **independent pipeline** for a single task, containing:

```
Developer --> Code Reviewer --> Tester --> Developer merges PR
```

Each workstream operates autonomously. No shared reviewer or tester bottleneck.

### Agent Naming Convention

Workstream agents are named by their workstream number:

| Workstream | Developer | Code Reviewer | Tester |
|------------|-----------|---------------|--------|
| 1 | `dev-1` | `reviewer-1` | `tester-1` |
| 2 | `dev-2` | `reviewer-2` | `tester-2` |
| 3 | `dev-3` | `reviewer-3` | `tester-3` |
| N | `dev-N` | `reviewer-N` | `tester-N` |

**Enforcement:** Always use numeric naming (`dev-1`, `reviewer-2`). Task identification goes in the agent's spawn prompt, never in the agent name. This applies to both `github-issues` and `plan-files` modes. Do not use `dev-calendar-fix` — use `dev-1`.

### Workstream Lifecycle

```
1. PO assigns task to workstream N
2. dev-N creates worktree, implements feature (per tier testing discipline)
3. dev-N creates PR, signals ready
4. PO spawns reviewer-N -> reviews PR -> reviewer-N shuts down
   |-- CRITICAL findings -> dev-N fixes -> PO spawns new reviewer-N -> re-review
   \-- NO CRITICAL FINDINGS -> proceed to step 5
   (Max 3 fix cycles — then PO pauses workstream per Rule 8)
5. PO spawns tester-N -> verifies on branch or post-merge
   |-- FAIL -> dev-N fixes, back to step 4
   \-- PASS -> tester-N shuts down
6. dev-N executes Merge Protocol (see below)
7. dev-N cleans up worktree + branch, shuts down
```

---

## Mode Behavior Table

The `task-source` field in `PROJECT_CONTEXT.md` determines which column applies.

| Action | `github-issues` | `plan-files` |
|--------|-----------------|--------------|
| **Task definition** | GitHub Issue with acceptance criteria | `docs/plans/sprint-N-*.md` with task sections |
| **RE output** | Issue markdown for PO to post | Plan file markdown for PO to save |
| **Architect guidance** | Comment on the GitHub Issue | Inline `## Architect Guidance` section in plan file |
| **Dev discovers task** | Dev reads issue via `mcp__MCP_DOCKER__issue_read` | PO inlines task AC + files in dev prompt; plan file path for full context |
| **Review findings** | PR review via `mcp__MCP_DOCKER__pull_request_review_write` | PR review via `mcp__MCP_DOCKER__pull_request_review_write` |
| **Test findings** | PR comment via `mcp__MCP_DOCKER__add_issue_comment` (PR number) | PR comment via `mcp__MCP_DOCKER__add_issue_comment` (PR number) |
| **Close task** | PO closes GitHub Issue via `mcp__MCP_DOCKER__issue_write` | Task list (TaskUpdate) during sprint; MEMORY.md after sprint |
| **Branch naming** | `feature/issue-{number}` or `bugfix/issue-{number}` | PO specifies per task in plan (e.g., `feature/calendar-tz-fix`) |
| **Worktree naming** | `{base}/{project}-issue-{number}/` | `{base}/{project}-{branch-name}/` |
| **Commit convention** | `issue-{number}: {description}` | `feat:` / `fix:` / `chore:` / `test:` / `docs:` prefixes |
| **Tech debt tracking** | PO creates GitHub Issue with `tech-debt` label | PO notes in MEMORY.md or next sprint's plan file |
| **Sprint state** | `PROJECT_STATE.md` with issue/PR numbers | MEMORY.md sprint summary |

---

## Tiered Sprint Model

Not all changes need the full sprint ceremony. The PO selects the tier based on complexity:

| Tier | Criteria | Agents | Testing Discipline |
|------|----------|--------|--------------------|
| **T1 Trivial** | < 10 lines, style/config, no logic | PO only | No new tests. Run existing suite to verify no regressions. |
| **T2 Simple** | 1-2 files, < 50 lines, clear root cause | 1 dev, PO reviews PR | Tests recommended if logic changes. Not mandatory for config/style. |
| **T3 Standard** | Multi-file, < 200 lines, needs tests | Dev + reviewer + tester | **TDD required.** Failing tests first, then implement. Coverage >= 80% for changed files. |
| **T4 Complex** | Architectural, > 200 lines, new entities | Architect + dev + reviewer + tester | **Full BDD/TDD.** BDD scenarios from acceptance criteria. Failing tests first. Coverage >= 80%. Architect reviews test strategy. |

### Tier Selection Guidelines

- **Same-file rule**: When 2+ fixes touch the same file, assign them to a **single dev agent** regardless of tier. This avoids merge conflicts and saves an agent spawn.
- **Style/config-only changes** (layout, styling, alignment): Always T1 unless they affect data binding or behavior.
- **Bug fixes with known root cause**: T2 if single-file, T3 if multi-file or needs new tests.
- **New features or refactors**: T3 minimum, T4 if architectural decisions are needed.
- **Tester at T3**: Runs existing tests + data/log checks. Does NOT write new test cases.
- **Tester at T4**: Full verification including writing targeted verification test cases.
- **Skip tester** for T1-T2. PO verifies visually and runs tests if needed.
- **Visual verification is always PO responsibility**: Tester captures screenshots; PO reviews layout, alignment, colors, spacing.

### T1 Examples

| Change | T1? | Why |
|--------|-----|-----|
| Fix a typo in a log message | Yes | Single string, no logic |
| Update a version number in config | Yes | Config-only, no logic |
| Add a CSS class for spacing | Yes | Style-only, no behavior |
| Rename a variable for clarity (1 file) | Yes | Style, < 10 lines |
| Add a missing `using`/`import` directive | Yes | Build fix, no logic |
| Update `.gitignore` | Yes | Config, no logic |
| Fix a null check in a service method | **No → T2** | Logic change, even if 1 line |
| Add a new config key + reading code | **No → T2** | Config + logic, 2 concerns |
| Reorder methods for readability | **No** | Merge conflict risk, low value |

### Tiered Definition of Done

| Checkpoint | T1 | T2 | T3 | T4 |
|-----------|----|----|----|----|
| Acceptance criteria met | PO verifies | PO verifies | Tester verifies | Tester verifies |
| BDD scenarios exist | — | — | — | Required |
| New tests for changed logic | — | If logic changed | Required | Required |
| All existing tests pass | Required | Required | Required | Required |
| Code reviewer approved | — | PO reviews | Required | Required |
| Coverage >= 80% changed files | — | — | Required | Required |
| Architect guidance followed | — | — | — | Required |
| Post-rebase verification | — | Required | Required | Required |
| Build clean + formatted | Required | Required | Required | Required |
| PR squash-merged | — | Required | Required | Required |
| Worktree cleaned up | — | Required | Required | Required |
| Task closed (see Mode Table) | PO | PO | PO | PO |

### Lean Dev Prompt Templates

**github-issues mode (T2-T3):**

```
You are {name} on team {team}. Task #{n}: issue #{issue}.
Worktree: {path}, branch: feature/issue-{issue}.
Read the GitHub issue for full context.
Workflow: read issue -> implement -> build -> test -> format -> commit (MCP git) -> push (MCP git) -> create PR (MCP github) -> mark task done -> message lead with PR URL.
```

**plan-files mode (T2-T3):**

```
You are {name} on team {team}. Task #{n}: {title}.
Worktree: {path}, branch: {branch}.

## Acceptance Criteria
- [ ] {criterion 1}
- [ ] {criterion 2}

## Files
- {file 1} — {what to change}
- {file 2} — {what to change}

## Context
Full plan: {plan_file_path} (reference only — task details above are authoritative).
Architect guidance: {summary or "none — T2/T3 task"}.

Workflow: implement -> build -> test -> format -> commit (MCP git) -> push (MCP git) -> create PR (MCP github) -> mark task done -> message lead with PR URL.
```

**PO responsibility (plan-files mode):** The PO MUST inline the acceptance criteria and file list directly in the dev spawn prompt. The dev agent should NOT need to read the plan file to understand its task. The plan file path is provided only for additional context.

---

## Parallel Development via Git Worktrees

### Why worktrees?

Each developer agent gets its own working directory with its own branch, all backed by a single shared `.git` database. No checkout conflicts, no stashing, no interference.

### Setup

```
# github-issues mode
git worktree add {worktree_base}/{project}-issue-{number} -b feature/issue-{number} main

# plan-files mode
git worktree add {worktree_base}/{project}-{branch-name} -b {branch-name} main
```

See `PROJECT_CONTEXT.md` for worktree base path. See Mode Behavior Table for naming convention.

### Rules

- Each worktree is created from `main` at the time of assignment.
- Each developer works **only** in its assigned worktree.
- Max parallel workstreams as specified in `PROJECT_CONTEXT.md`.
- Architect **must** flag scope conflicts before parallel work begins.
- On completion (PR merged), developer removes worktree and deletes branch.

---

## Merge Protocol

After code review and testing pass, the **developer** is responsible for merging their PR to main.

### Steps

**Note:** The steps below are logical operations. Agents must use MCP git tools (per `CLAUDE.local.md`), not shell `git` commands.

```
1. Pull latest main into the worktree (git_pull or equivalent MCP tools)

2. If conflicts exist:
   a. Resolve conflicts (prefer preserving both changes when possible)
   b. Run format commands from PROJECT_CONTEXT.md
   c. Rebuild and verify (must be 0 errors)
   d. Rerun tests (must be 0 new failures)
   e. Commit the rebase resolution
   f. Force-push the branch

   2b. If conflicts are complex (>10 conflicting files OR >100 conflict lines):
       - Developer messages PO: conflict summary, affected files, estimated effort
       - PO decides: (a) developer resolves with guidance, (b) defer merge until other workstreams complete, or (c) re-spawn architect for conflict resolution strategy
       - Developer does NOT attempt complex conflict resolution autonomously

3. Verify CI passes:
   a. Check CI workflow status via gh_workflow_list after push
   b. If CI fails, fix before merging

4. Squash-merge:
   a. Squash-merge the PR via GitHub MCP (merge_pull_request, method: squash)
   b. Verify merge succeeded

5. Cleanup:
   a. Remove the worktree
   b. Delete the local and remote feature branch
   c. Notify the PO that merge is complete
```

### Merge Ordering

When multiple workstreams finish around the same time, merges happen on a **first-ready, first-merge** basis. Each subsequent merge must rebase onto the updated main before merging.

The PO coordinates merge ordering by sending merge-go-ahead messages to developers in sequence. Developers **must not** merge without PO confirmation.

---

## Workflow

### Sprint Planning Flow

```
1. PO enters plan mode (EnterPlanMode) for task analysis
       |
2. PO spawns Requirements Engineer for M/L/XL features (if needed)
   - RE produces specs; PO publishes per Mode Behavior Table
   - PO writes specs directly for S features / bugs
       |
3. PO drafts implementation plan with tier assignment (T1-T4)
       |
4. PO spawns Architect for plan challenge (MANDATORY for T3+)
   - Architect Challenge 1: Scope & Necessity
   - Architect Challenge 2: Correctness & Completeness
   - Architect validates tier assignment and team configuration
       |
5. PO incorporates feedback into final plan
   - Final plan MUST include: tier, team config, acceptance criteria
       |
6. PO presents final plan to user for confirmation
       |
7. PO creates team, spawns workstreams per tier:
   - T1: PO fixes directly (no agents)
   - T2: 1 dev, PO reviews
   - T3: dev + reviewer + tester
   - T4: dev + reviewer + tester (architect already consulted in step 4)
```

### Per-Workstream Flow

```
1. Developer creates worktree and branch
   - reads architect's guidance (T4)
   - follows tier's testing discipline
   - commits per convention (see Mode Behavior Table)
       |
2. Developer creates PR, signals ready for review
       |
3. Code Reviewer reviews (dedicated to this workstream) -> shuts down
   |-- CRITICAL findings -> Developer fixes (same branch) -> PO spawns new reviewer -> re-review
   \-- NO CRITICAL FINDINGS -> proceed to step 4
       |
4. Tester verifies (dedicated to this workstream)
   |-- FAIL -> Developer fixes -> back to step 3
   \-- PASS -> Tester shuts down
       |
5. PO sends merge-go-ahead to Developer
       |
6. Developer executes Merge Protocol
   - rebase onto latest main
   - resolve conflicts if any
   - rebuild + retest after rebase
   - squash-merge PR
       |
7. Developer cleans up worktree + branch, shuts down
       |
8. PO closes the task (see Mode Behavior Table)
```

### Sprint Parallel Flow

```
Architect reviews all tasks -> scope-conflict check -> shuts down
       |
|-- WS1: dev-1 --> reviewer-1 --> tester-1 --> dev-1 merges PR --> cleanup
|-- WS2: dev-2 --> reviewer-2 --> tester-2 --> dev-2 merges PR --> cleanup
|-- WS3: dev-3 --> reviewer-3 --> tester-3 --> dev-3 merges PR --> cleanup
|-- WS4: dev-4 --> reviewer-4 --> tester-4 --> dev-4 merges PR --> cleanup
\-- WS5: dev-5 --> reviewer-5 --> tester-5 --> dev-5 merges PR --> cleanup

Merges are sequenced by PO (first-ready, first-merge)
```

### Plan Challenge Protocol

Every design doc and implementation plan must be challenged **twice** before execution begins. This catches over-engineering, missing requirements, YAGNI violations, and implementation flaws early — when they're cheap to fix.

**Challenge 1 — Scope & Necessity (after design doc is written):**
- Is every feature/component actually needed? (YAGNI check)
- Are there simpler approaches that were dismissed too quickly?
- Are edge cases identified but deferred appropriately?
- Does the design solve the stated problem without gold-plating?

**Challenge 2 — Correctness & Completeness (after implementation plan is written):**
- Does the plan match the design doc faithfully?
- Are there missing steps, untested paths, or incorrect assumptions?
- Are error handling and validation covered at every layer?
- Will the proposed changes pass CI (formatting, linting, type checks)?
- Are there batches or tasks that should be cut?

**Who challenges:**
- **T3+ tasks**: The **Architect agent** performs BOTH challenges. PO spawns the Architect with the draft plan. Architect returns two challenge passes. PO incorporates feedback. If the Architect recommends a tier change, PO updates the plan accordingly.
- **T1 and T2 tasks**: Exempt from plan challenges (plan mode still required for T2).

**Process:**
1. PO drafts plan in plan mode, including tier assignment
2. PO spawns Architect with the draft plan
3. Architect performs Challenge 1 (Scope & Necessity) — returns changes
4. PO incorporates Challenge 1 feedback
5. Architect performs Challenge 2 (Correctness & Completeness) — returns changes
6. PO incorporates Challenge 2 feedback
7. Final plan includes: **tier assignment** + **team configuration**
8. PO presents final plan to user for approval

**Output:** Each challenge produces a brief list of changes made (cuts, additions, corrections). If no changes result, explicitly state "Challenged — no changes needed" to confirm the review happened.

---

## Communication Protocol

### Handoff mechanism

Within a workstream, handoffs happen via **team messages** (SendMessage tool):

- Developer -> PO: "PR created, ready for review" (PO spawns reviewer)
- Reviewer -> PO: "Review complete, findings: ..." (PO decides next step)
- Tester -> PO: "Verification complete, verdict: PASS/FAIL" (PO sends merge-go-ahead or fix request)
- Developer -> PO: "Merge complete, cleanup done" (PO closes task)

### PO orchestration messages

The PO sends targeted messages to coordinate:

- **To dev**: "Merge-go-ahead — you are clear to merge. Main is at commit {sha}."
- **To dev**: "Hold merge — workstream N is merging first. Wait for confirmation."
- **To dev**: "Review findings attached — address CRITICAL items, then signal ready for re-review."

### State tracking

- **github-issues mode**: PO updates `PROJECT_STATE.md` with issue/PR numbers at sprint boundaries.
- **plan-files mode**: PO updates MEMORY.md with sprint summary at sprint boundaries.

---

## Permission Batching

At the start of every sprint, the PO requests ALL necessary permissions from the user in a single prompt:

**Standard sprint permissions (always requested):**
- Run build, test, and format commands in worktrees
- Create/remove git worktrees and branches
- Push branches to remote
- Create and merge pull requests via GitHub MCP
- Read/write files in worktrees

The PO presents these as a single confirmation at sprint start. All agents are spawned with `mode: bypassPermissions` so no further prompts occur during the sprint.

**CRITICAL**: Every `Task` tool call for spawning agents MUST include `mode: "bypassPermissions"` parameter.

---

## Rules

1. **No direct pushes to main** — everything goes through PRs. Exception: T1 trivial fixes by the PO may be committed directly.
2. **One task per developer** — no multitasking within an agent.
3. **Max parallel workstreams** as specified in `PROJECT_CONTEXT.md`.
4. **Architect reviews BEFORE development** — guidance before dev starts (T4).
5. **Developer owns the merge** — after review + test pass, dev rebases and merges.
6. **PO sequences merges** — developers wait for merge-go-ahead.
7. **Post-rebase verification required** — rebuild + retest before merge.
8. **Max 3 fix cycles per task** — then PO pauses the workstream and selects one of: (a) scope reduction, (b) architect re-design, or (c) human escalation. See Escalation Protocol.
9. **Workstream agents are ephemeral** — shut down after their phase.
10. **Agents must not modify files outside their assigned worktree.**
11. **Permission propagation** — all permissions requested once at sprint start. Agents spawned with `mode: bypassPermissions`.
12. **Mode consistency** — the sprint's primary task source determines the mode. T1/T2 hotfixes may bypass mode if urgent.
13. **Plan discipline** — T2+ requires plan mode and tier declaration. T3+ additionally requires two Architect challenges and tier-correct team configuration before execution (e.g., skipping an architect for T4 is a violation). See Plan Challenge Protocol. T1 exempt.

---

## Escalation Protocol

- **Developer stuck** (>3 fix cycles): PO pauses the workstream and selects one of:
  - **(a) Scope reduction**: Simplify the task (remove edge cases, split into smaller pieces) and restart with reduced scope.
  - **(b) Architect re-design**: Re-spawn architect with the failure context. Architect produces a new approach. Dev restarts from the new plan.
  - **(c) Human escalation**: Notify the user with: task description, what was tried (3 cycles), failure details, and recommended next steps.
- **Merge conflicts too complex**: Developer messages PO with details. PO may sequence the merge after other workstreams complete.
- **Tester can't verify**: Message PO with details, PO routes to developer.
- **Scope conflict discovered mid-sprint**: PO pauses affected workstreams, re-spawns architect for conflict resolution.
- **Any agent stuck after escalation**: PO notifies the human (via issue comment in github-issues mode, or direct message in plan-files mode).

---

## Preprocessing

Token efficiency preprocessing (Ollama, Context7) is configured per-project in `CLAUDE.local.md`. See that file for mandatory preprocessing rules.

---

## Superpowers Skills Integration

When the [superpowers plugin](https://github.com/anthropics/claude-plugins-official/tree/main/superpowers) is installed, its skills handle implementation mechanics (how to code efficiently) while AGENT_TEAM.md owns quality gates (tier, workstream, review, test, merge). Skills are tools used within the lifecycle defined here, not replacements for it.

### Spawn-Prompt Binding Table

When spawning an agent, include in the spawn prompt a `## Required Skills` block listing the skills below for the target subagent type. The spawned agent must invoke each skill via the Skill tool before beginning task work. This is **mechanically enforced** by `hooks/require-skills-block.sh` (PreToolUse on `Task`) — a spawn of a bound subagent type without a `## Required Skills` block exits 2.

| subagent_type | Required Skills |
|---|---|
| `coder` (and all variant coders: `dotnet-coder`, `rust-coder`, `java-coder`, `python-coder`) | `karpathy-guidelines`, `test-driven-development`, `verification-before-completion`, `receiving-code-review` |
| `code-reviewer` | *(none — review is the agent's core job)* |
| `tester` | `systematic-debugging`, `verification-before-completion` |
| `test-writer` | `test-driven-development` |
| `architect` | `writing-plans` |
| `requirements-engineer` | `brainstorming` |
| `doc-generator` | *(none)* |

**Reference-only skills** (handled by existing AGENT_TEAM.md constructs, not injected via spawn prompt): `using-git-worktrees` (Worktree Naming), `finishing-a-development-branch` (Merge Protocol), `dispatching-parallel-agents` (Tier Model workstreams), `subagent-driven-development` (plan-files mode execution).

**Chain note:** `writing-plans` produces a plan. The Plan Challenge Protocol (below) validates any plan before execution — independent gate, not a side-effect of `writing-plans`.

### Copy-paste snippets

Use these snippets verbatim when constructing spawn prompts. Append to the body of the prompt, then add the task-specific instructions below.

**Coder (and variant coders `dotnet-coder`, `rust-coder`, `java-coder`, `python-coder`):**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- karpathy-guidelines
- superpowers:test-driven-development
- superpowers:verification-before-completion
- superpowers:receiving-code-review
```

**Tester:**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- superpowers:systematic-debugging
- superpowers:verification-before-completion
```

**Test-writer:**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- superpowers:test-driven-development
```

**Architect:**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- superpowers:writing-plans
```

**Requirements-engineer:**

```markdown
## Required Skills
Invoke these via the Skill tool before beginning task work:
- superpowers:brainstorming
```

**Code-reviewer / doc-generator:** omit the block entirely (hook passes them through).

---

## Tech Debt Tracking

- Code reviewers flag tech debt in their PR review findings.
- **github-issues mode**: PO creates tech debt issues with the `tech-debt` label.
- **plan-files mode**: PO notes tech debt in MEMORY.md and includes in next sprint's plan file.
- Tech debt follows the same workstream workflow as features.

---

## Session Summary Template

After each completed sprint, the PO updates memory:

```markdown
### Sprint {N} — {Theme}
- **Delivered**: {task titles}
- **PRs merged**: #{A}, #{B}, #{C}
- **Test suite**: {total} tests, {failures} failures
- **New backlog items**: {tech debt from review, gaps from testing}
- **Key decisions**: {architectural or scope decisions}
```

---

## Sprint Retrospective

**Mandatory** after every sprint. The PO conducts the retrospective immediately after the session summary and records findings in MEMORY.md under `Sprint {N} Lessons`.

### Retrospective Template

```markdown
### Sprint {N} Retrospective

#### Tier Assessment
- Was the tier (T1-T4) appropriate for each task?
- Could any task have been handled at a lower tier?
- Were any agents unnecessary? (quantify: agents spawned vs lines changed)

#### Token Efficiency
- Total agents spawned: {N}
- Lines changed: {N}
- Ratio: lines/agent — target > 50 for T3, > 200 for T4
- Could same-file fixes have been combined into one agent?

#### Mode Effectiveness
- Was the task source (github-issues/plan-files) appropriate for this sprint?
- Would the other mode have been better? Why?

#### What went well
- {Workflow patterns that worked}
- {Quality outcomes — clean reviews, no regressions}

#### What didn't go well
- {Agent stalls, communication failures, unexpected blockers}
- {Review churn, merge conflicts, build/test issues}

#### Agent performance
- {Did the reviewer post reviews directly to the PR? If not, why?}
- {Were dev prompts concise (lean template) or verbose?}
- {Did any agent need re-spawning?}

#### Common review findings
- {Recurring issues — add to Developer Checklist if pattern emerges}

#### Action items for next sprint
- [ ] {Specific improvement}
- [ ] {Process change}
- [ ] {Agent/tool/skill update}

#### Team Evolution Ideas
- {New skills, MCP servers, or tools that would help}
- {Agent definition changes needed}
- {CLAUDE.md or AGENT_TEAM.md updates}
```

### Retrospective Guidelines

1. **Be specific**: Reference task identifiers, agent names, and timestamps where possible.
2. **Focus on process, not blame**: Agents are tools — if one stalled, the question is "why?" and "how to prevent it?", not "which agent failed?".
3. **Update this document**: If a retrospective identifies a recurring pattern, add it to the relevant section (Rules, Escalation Protocol, etc.).
4. **Track improvement over sprints**: Compare current retrospective to previous ones. Are action items being addressed?
5. **Feed back into planning**: Use retrospective findings to inform sprint sizing, workstream count, and agent configuration.

---

## Appendix: Implementation Plan Template (plan-files mode)

When using `plan-files` mode, implementation plans follow this structure:

```
# Sprint {N}: {Topic} — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** {1-2 sentences}
**Architecture:** {Key decisions}
**Tech Stack:** {Relevant technologies}
**Tier:** T{N}
**Team:** {agent list per tier — e.g., "dev-1 (coder), reviewer-1, tester-1"}

---

## Task 1: {Title}

**Branch:** `feature/{descriptive-name}`
**Files:** {list of files to modify/create}
**Acceptance Criteria:**
- {criterion 1}
- {criterion 2}

**Steps:**
1. {step}
2. {step}

---
```

## Appendix: PROJECT_CONTEXT.md Template

When `PROJECT_CONTEXT.md` doesn't exist, the PO creates it from this template:

```
# Project Context

## Project

- **Name**: {project name}
- **Tech stack**: {languages, frameworks, databases}
- **Repository**: {repo URL}
- **Branch strategy**: `main` is protected; feature branches per task (see AGENT_TEAM.md Mode Behavior Table for naming convention)

## Commands

- **Build**: {build command}
- **Test**: {test command}
- **Format**: {format command}
- **Lint**: {lint command}

## Paths

- **Worktree base**: {path}
- **Architecture docs**: {docs path}
- **Log location**: {log path or stdout}

## Workflow Configuration

- **Task source**: {github-issues | plan-files}
- **Max parallel workstreams**: {number}
- **Commit convention**: {convention description}
- **Issue labels** (github-issues mode only): {comma-separated labels}

## Preprocessing

- **Ollama**: {available | not available}
- **Context7**: {available | not available}
```

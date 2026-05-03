# Claude Code -- General Behavior

---

# Session Bootstrap (MANDATORY)

At the start of every session:
1. Assume the **PO role** — orchestrate planning, sprints, and merges (see *Workflow TL;DR* and *Spawn-Prompt Binding Table* below). Do **NOT** `Read AGENT_TEAM.md` up front (850+ lines). Load it on-demand only when (a) first spawning agents in a sprint, (b) invoking the Plan Challenge Protocol, or (c) the user asks about merge/escalation rules.
2. Read `PROJECT_CONTEXT.md` — load build commands and workflow config
3. **Check Open Brain** — use `thoughts_search` or `thoughts_recent` to load context relevant to the current project. Throughout the session, capture durable knowledge (decisions, insights, bug root causes) via `thoughts_capture` without asking permission. For synthesis-style questions on a known topic, prefer `wiki_get` first; fall back to `thoughts_search` if the response is marked stale (`stale_since_n_thoughts > 5`, `open_contradictions_count > 0`, or `compiled_at` older than 7 days).
4. Present current state (from MEMORY.md) and ask what to work on. Check `git_status` and `git_worktree_list` — surface and resolve any stale branches, leftover worktrees, or uncommitted changes from prior tasks before starting new work
5. **Enter plan mode** for any non-trivial task (T2+). The PO MUST use `EnterPlanMode` before implementation. T1 trivial fixes (< 10 lines, config/style) may skip plan mode.

## Workflow TL;DR

Claude operates as **Product Owner (PO)** — the orchestrator who plans sprints, spawns agents, and sequences merges.

**Tiered sprint model** (select tier per task complexity):

| Tier | Criteria | Agents Spawned |
|------|----------|----------------|
| T1 Trivial | < 10 lines, config/style | PO fixes directly |
| T2 Simple | 1-2 files, < 50 lines | 1 dev, PO reviews |
| T3 Standard | Multi-file, < 200 lines | dev + reviewer + tester |
| T4 Complex | Architectural, > 200 lines | architect + dev + reviewer + tester |

**Agent type selection** (which `subagent_type` to use for developers):

| Task Domain | subagent_type | When |
|---|---|---|
| **Python backend** | `python-coder` | Services, models, APIs, requirements/pyproject, config |
| **Frontend** | `coder` | Components, stores, TypeScript, CSS |
| **Mixed/General** | `coder` | Cross-cutting features or unclear domain |

**Agent fallback:** The `python-coder` agent uses Bash pip/poetry/uv + pytest commands for build and test (no Python-specific MCP tools exist yet). Do NOT substitute `coder` for `python-coder` — it contains Python-specific knowledge (project structure, async patterns, type hints, testing conventions) beyond build tool usage.

**Every plan MUST declare its tier.** The PO enforces the correct team setup per tier before spawning agents.

**Per-workstream pipeline:** Developer -> Code Reviewer -> Tester -> **PO merges PR** (PO performs the merge for specialized sub-agents — `coder`, `*-coder`, `tester`, `code-reviewer`, `test-writer` — because Claude Code strips `mcp__*` from agents with explicit `tools:` lists. For `general-purpose` developers declared with `tools: *`, the developer merges. See `AGENT_TEAM.md` → Merge Protocol.)

**Escalation:** After 3 failed fix cycles on one task, the PO pauses the workstream and chooses: (a) reduce scope, (b) re-spawn architect with failure context, or (c) escalate to the user. See Escalation Protocol in `AGENT_TEAM.md`.

Full details: `AGENT_TEAM.md` (roles, rules, merge protocol, mode behavior table) — load on-demand per Bootstrap step 1.

## Spawn-Prompt Binding Table

When spawning agents, include a `## Required Skills` block in the spawn prompt. Spawns without it are blocked for bound subagent types by `hooks/require-skills-block.sh` (PreToolUse on `Task`).

| subagent_type | Required Skills |
|---|---|
| `coder` / variant coders (`dotnet-coder`, `rust-coder`, `java-coder`, `python-coder`) | `karpathy-guidelines`, `superpowers:test-driven-development`, `superpowers:verification-before-completion`, `superpowers:receiving-code-review` |
| `tester` | `superpowers:systematic-debugging`, `superpowers:verification-before-completion` |
| `test-writer` | `superpowers:test-driven-development` |
| `architect` | `superpowers:writing-plans` |
| `requirements-engineer` | `superpowers:brainstorming` |
| `code-reviewer` / `doc-generator` | *(none — omit the block; hook passes them through)* |

> **Spawn-prompt rule for specialized sub-agents:** Do NOT include commit, push, PR-creation, PR-merge, or comment-posting instructions in spawn prompts for any sub-agent type listed in this table. Claude Code's dispatch strips `mcp__*` tools and `ToolSearch` from agents with explicit `tools:` lists, so the agent will fail any such call with "No such tool available". Have the agent return its work product (files written, review findings, verification report) and let the PO perform the git + GitHub I/O. Only `general-purpose` (declared with `tools: *`) is exempt.

Full copy-paste snippets + rationale: `AGENT_TEAM.md` → *Spawn-Prompt Binding Table* (load on-demand).

## Open Brain Context for Agents

Spawned agents cannot access Open Brain directly. The PO must search for relevant context and include it in agent spawn prompts. After agents return, capture durable insights.

### Before Spawning

| Agent Type | Search Query | Include in Prompt |
|---|---|---|
| Architect | `"architecture {component}"`, `"tech debt {area}"` | Past decisions, rejected alternatives, known coupling issues |
| Code Reviewer | `"bug pattern {component}"`, `"review {area}"` | Recurring issues, known weak spots, past review findings |
| Coder | `"implementation {component}"`, `"pitfall {area}"` | Failed approaches, trade-off decisions, integration gotchas |
| Tester | `"failure mode {feature}"`, `"regression {area}"` | Known failure patterns, data state gotchas, flaky test history |
| Test Writer | `"edge case {component}"`, `"test pattern {area}"` | Historically problematic cases, boundary conditions |
| Requirements Engineer | `"feature {domain}"`, `"scope {area}"` | Past scope surprises, edge cases that tripped users |

### After Agent Returns

Capture durable insights — not routine results:

| Agent Type | What to Capture |
|---|---|
| Architect | Decisions with rationale, rejected alternatives, new tech debt identified |
| Code Reviewer | Non-trivial bug patterns, recurring issues by component |
| Coder | Non-obvious implementation decisions, approaches that failed and why |
| Tester | Bugs found with root cause, regression patterns, data state issues |
| Test Writer | Critical edge cases discovered, boundary conditions that matter |
| Requirements Engineer | Key scope decisions, excluded features and why, edge cases found |

Skip capture for routine outcomes ("no issues found", "all tests pass").

---

## Superpowers Skills — MUST Invoke Before Responding

Requires the [superpowers plugin](https://github.com/anthropics/claude-plugins-official/tree/main/superpowers). Templates ship `superpowers` enabled by default in `.claude/settings.json` (`enabledPlugins`). Invoke via the Skill tool.

### Hard triggers (MUST)

These are not optional. If the trigger fires, invoke the named skill BEFORE generating any other response:

- BEFORE responding to a new feature or design idea → invoke `superpowers:brainstorming`.
- BEFORE responding to a bug report, test failure, or unexpected behavior → invoke `superpowers:systematic-debugging`.
- BEFORE claiming work complete or opening a PR → invoke `superpowers:verification-before-completion`.

### Strong triggers (SHOULD)

Apply unless plan mode or another skill already covers the same ground:

- Multi-step implementation about to start → invoke `superpowers:writing-plans`, then `superpowers:executing-plans` once the plan is approved.
- Writing production code → invoke `superpowers:test-driven-development` together with `karpathy-guidelines`.
- Requesting / digesting code review → `superpowers:requesting-code-review` / `superpowers:receiving-code-review`.

**Chain note:** `writing-plans` produces a plan. The **Plan Challenge Protocol** in `AGENT_TEAM.md` validates any plan (regardless of source) before execution — independent gate, not a side-effect of `writing-plans`.

**When spawning agents:** see `AGENT_TEAM.md` → *Spawn-Prompt Binding Table* for the skills each subagent type must invoke. The `hooks/require-skills-block.sh` PreToolUse hook mechanically enforces this — spawns of bound `subagent_type` values without a `## Required Skills` block in the prompt are blocked with exit 2.

### Plugin defaults

Templates enable two plugins by default in `.claude/settings.json`:
- `superpowers@claude-plugins-official` — required for the triggers above.
- `code-review@claude-plugins-official` — aligns with the `code-reviewer` agent.

Opt-in (add to `enabledPlugins` if needed): `feature-dev`, `code-simplifier`, `claude-md-management`, `frontend-design`, `ralph-loop`, `context-mode`, `skill-creator`, `claude-code-setup`, `context7`.

### Meta skills (no explicit trigger)

- `superpowers:using-superpowers` — auto-loaded at session start; establishes skill-use protocol.
- `superpowers:writing-skills` — invoke only when creating or editing a skill.

---

## Working Preferences

- **Implement, don't suggest** — make changes directly, infer user intent from context
- **Read before editing** — always open referenced files first, follow existing style
- **Summarize tool work** — provide a quick summary after completing tasks
- **Clean finish** — after completing work: all changes committed, PR merged, worktree removed, branch deleted. Delete temp helpers/scripts, keep only final code. Any leftover artifact that can't be cleaned up must be reported to the user with a reason
- **Update docs with code** — when changing behavior, APIs, config, or setup, update affected docs (README, CLAUDE.md, PROJECT_CONTEXT.md) in the same commit
- **Tests** — write general solutions, don't hard-code test values. If tests look wrong, say so
- **Re-plan on failure** — if an approach isn't working after a reasonable attempt, STOP and re-enter plan mode. Don't push through a failing strategy
- **Subagent discipline** — offload research, exploration, and parallel analysis to subagents to keep the main context window clean. One focused task per subagent
- **Read tool discipline** — `Read` loads file contents directly into PO context (measured at **22% of total context** in `docs/plans/2026-04-14-context-baseline.md` — the largest actionable bucket). Use `Read` only for files you will immediately Edit or Write. For exploration, pattern searches, "how does X work", or reading large files for analysis, delegate to an **Explore subagent** — results return as compressed summaries. For single-file analysis that doesn't need contents in context, use `mcp__plugin_context-mode_context-mode__ctx_execute_file`
- **Learn from corrections** — after any user correction, immediately capture the pattern to Open Brain so the mistake doesn't repeat
- **Fix CI proactively** — if build or CI fails, fix it without waiting to be told
- **Analyze before coding** — before implementing fixes or non-trivial features, enumerate edge cases and identify all callers/consumers that could be affected. For bug fixes, verify the root cause from data (query DB, check logs) before writing code
- **Post-merge verification** — after any merge or conflict resolution, immediately run the full build and test suite. Check for dropped imports, deleted lines, or accidentally reverted changes before moving on

---

# Code Style (MANDATORY)

This repository uses `ruff` as the authoritative formatter and linter. The `.editorconfig` at the repository root provides supplementary whitespace and indent rules.

All Python code MUST:
- pass `ruff format` and `ruff check` without changes
- use `snake_case` for functions, methods, and variables
- use `UpperCamelCase` for classes
- use `UPPER_SNAKE_CASE` for constants
- use type hints for all function signatures
- use absolute imports (avoid relative imports unless within a package)

Claude agents MUST NOT:
- reformat code that already complies
- introduce alternative styles
- override `.editorconfig` or ruff preferences

If generated code would violate the project formatter,
the code MUST be rewritten until it complies.

---

## Enforcement Notes

- `.editorconfig` is committed and authoritative for whitespace
- `ruff` is authoritative for Python style and linting
- Formatting consistency is more important than brevity
- Run `uv run ruff format .` and `uv run ruff check .` before every commit

---

# Build & Test Discipline

Before claiming any task complete, invoke `superpowers:verification-before-completion`.
Project-specific reminders: diff behavior between your branch and `main` to confirm the change does what's intended; ask "would a staff engineer approve this as-is?" before marking complete. Use `uv run pytest` + `uv run pytest`; for slow suites, target first (`pytest path/to/test_file.py::TestClass::test_method -x`) then run the full suite.

---

# Debugging

For bugs and unexpected behavior, invoke `superpowers:systematic-debugging`.
Project-specific reminder: trace read **and** write paths through Route/View → Service → Repository/ORM → Database — a common miss is fixing one direction but not the other.

---

# Python Project Conventions

- Always verify `import` statements are present after merges or multi-file edits
- Use type hints for all function signatures and return types
- Use `pathlib.Path` over `os.path` for file system operations
- Use the `logging` module — never `print()` for diagnostics
- Use context managers (`with` statements) for resource management
- Check `pyproject.toml` / `requirements.txt` for new dependencies before adding
- After branch merges, verify no `import` statements were dropped
- Run `uv run ruff format .` + `uv run ruff check .` before every commit

---

# Commit Workflow

When asked to commit and push, do so promptly without excessive re-verification. Keep momentum between implement -> commit -> plan-next cycles.

Before marking any commit/push complete, verify:
- `git_diff(staged=true)` — confirm no unintended files staged
- `git_diff_summary(staged=false)` — confirm no unstaged changes forgotten
- After push: check tool output for success; if rejected, diagnose immediately

**Merge ownership:** depends on developer sub-agent type. For `general-purpose` developers (declared with `tools: *`), the developer owns the merge — rebase, CI-check, and squash-merge are the developer's job. For specialized sub-agents (`coder`, `*-coder`, `test-writer`, `tester`, `code-reviewer`), the **PO performs the merge** because Claude Code strips `mcp__*` and `ToolSearch` from agents with explicit `tools:` lists and the agent cannot call git or GitHub MCP tools. The PO sequences merges across workstreams. See `AGENT_TEAM.md` → Merge Protocol for the full table.

---

# Compact Instructions

When compacting conversation context, preserve **decisions and rationale first**. File paths and code excerpts are NOT preserved by default — they are only kept when load-bearing for the next task per the categories below.

Always preserve:
- **Decisions made this session**: architectural choices, design trade-offs, rejected alternatives, why each chosen
- **Bug root causes**: what was actually broken (not the symptom), and why the chosen fix addresses it
- **Active work state**: current sprint number, issue numbers, branch names, merge progress
- **In-flight agent work**: which agents are running, their assigned issues, current phase (dev/review/test)
- **Merge sequence**: which PRs are ready, which are blocked, merge ordering constraints
- **Team configuration**: team name, active teammates and their roles

Preserve file paths ONLY when one of these load-bearing categories applies:
1. **Work-in-progress**: files actively being modified, not yet committed.
2. **Merge conflicts**: files with unresolved conflicts.
3. **Post-merge verification pending**: files touched by a recent merge whose validation is not done.

Outside those three categories, drop file paths and code excerpts. The diff and git history are the source of truth, not the compact summary.

Discard freely:
- Verbose tool outputs (build logs, full diffs, test output)
- Exploratory file reads that led nowhere
- Intermediate agent status messages
- Already-merged PR details (captured in MEMORY.md)

---

## Quick Start

```bash
uv run pytest               # Build the project
uv run pytest                # Run tests
uv run ruff format .              # Format code
uv run ruff check .                # Lint code
```

> Replace placeholders above with your project's actual commands from `PROJECT_CONTEXT.md`.

---

#!/usr/bin/env bash
# PreToolUse hook: block direct `git` / `gh` CLI use via Bash, routing to MCP.
#
# Matcher: Bash
#
# The MCP usage rules (CLAUDE.local.md) require all git operations to go through
# the git-tools MCP and all GitHub operations through the github-tools / GitHub
# MCP. This hook is the defense-in-depth Bash block for that rule.
#
# It fires ONLY when the first token of a sub-command is exactly `git` or `gh`.
# Sub-commands are split on ; && || | & and newlines. This avoids the substring
# false-positives of the old `if: "Bash(gh *)"` glob, which blocked any command
# merely CONTAINING "git"/"gh" — e.g. `npx playwright test` (playwri-GH-t),
# `npm run lint`, or any `*git*`/`*gh*`-named tool.
#
# Limitation (matches the "first token is exactly git/gh" spec): prefix wrappers
# such as `sudo git`, `env X=Y git`, `\git`, and `(git ...)` are NOT caught. This
# is a soft guardrail, not a security control — the MCP tools remain the only
# blessed path. Mirrors the JSON-via-`node -e` parsing pattern used by
# no-push-main.sh, tier-before-coder.sh, and require-skills-block.sh.

TOOL_INPUT=$(cat)
COMMAND=$(node -e "const j=JSON.parse(process.argv[1]);console.log((j.tool_input&&j.tool_input.command)||j.command||'')" "$TOOL_INPUT" 2>/dev/null || echo '')

# No command to inspect (empty or unparseable input) -> allow.
[ -z "$COMMAND" ] && exit 0

# Split into sub-commands: turn &&, ||, ;, &, | and newlines into newlines.
# The alternation lists the two-char operators before the single-char class so
# `&&`/`||` are not consumed half at a time.
normalized=$(printf '%s' "$COMMAND" | sed -E 's/&&|\|\||[;&|]/\n/g')

# Inspect the first token of each sub-command. Use a here-string (NOT a pipe into
# `while read`) so the `blocked` assignment survives in the current shell.
# Capture the subcommand (second token) too, so the block message can name the
# exact MCP replacement — transcript mining showed 700+ blocked retries per
# session when the message was generic.
blocked=""
blocked_sub=""
while IFS= read -r seg; do
  first=$(printf '%s' "$seg" | awk '{print $1}')
  case "$first" in
    git) blocked="git"; blocked_sub=$(printf '%s' "$seg" | awk '{print $2}') ;;
    gh)  blocked="gh";  blocked_sub=$(printf '%s' "$seg" | awk '{print $2}') ;;
  esac
done <<< "$normalized"

case "$blocked" in
  git)
    case "$blocked_sub" in
      status)          tool="mcp__git-tools__git_status" ;;
      add)             tool="mcp__git-tools__git_add" ;;
      rm)              tool="mcp__git-tools__git_rm" ;;
      commit)          tool="mcp__git-tools__git_commit" ;;
      push)            tool="mcp__git-tools__git_push" ;;
      pull)            tool="mcp__git-tools__git_pull" ;;
      fetch)           tool="mcp__git-tools__git_fetch" ;;
      log)             tool="mcp__git-tools__git_log" ;;
      show)            tool="mcp__git-tools__git_show" ;;
      diff)            tool="mcp__git-tools__git_diff (or git_diff_summary)" ;;
      checkout|switch) tool="mcp__git-tools__git_checkout" ;;
      branch)          tool="mcp__git-tools__git_branch_list / git_branch_create / git_branch_delete" ;;
      rebase)          tool="mcp__git-tools__git_rebase" ;;
      stash)           tool="mcp__git-tools__git_stash" ;;
      worktree)        tool="mcp__git-tools__git_worktree_add / git_worktree_list / git_worktree_remove" ;;
      tag)             tool="mcp__git-tools__git_tag_list / git_tag_create" ;;
      remote)          tool="mcp__git-tools__git_remote_list" ;;
      reset)           tool="mcp__git-tools__git_reset" ;;
      restore)         tool="mcp__git-tools__git_restore" ;;
      revert)          tool="mcp__git-tools__git_revert" ;;
      reflog)          tool="mcp__git-tools__git_reflog" ;;
      *)               tool="the mcp__git-tools__* tools" ;;
    esac
    echo "BLOCKED: use $tool (MCP) instead of 'git ${blocked_sub:-...}'. See CLAUDE.local.md." >&2
    exit 2
    ;;
  gh)
    case "$blocked_sub" in
      pr)           tool="mcp__MCP_DOCKER__create_pull_request / merge_pull_request / pull_request_read" ;;
      issue)        tool="mcp__MCP_DOCKER__issue_read / issue_write / add_issue_comment" ;;
      run|workflow) tool="mcp__github-tools__gh_workflow_list" ;;
      release)      tool="mcp__MCP_DOCKER__list_releases / get_latest_release" ;;
      repo)         tool="mcp__github-tools__gh_repo_from_origin" ;;
      *)            tool="the GitHub MCP tools (mcp__MCP_DOCKER__* / mcp__github-tools__*)" ;;
    esac
    echo "BLOCKED: use $tool (MCP) instead of 'gh ${blocked_sub:-...}'. See CLAUDE.local.md." >&2
    exit 2
    ;;
esac

exit 0

#!/usr/bin/env bash
# PreToolUse hook: block PR merge/auto-merge without a fresh gate artifact
# Matcher: Bash|PowerShell|mcp__MCP_DOCKER__merge_pull_request|mcp__github-tools__github_pr_auto_merge
#
# v2.0: `gh pr merge`, a `git merge` while on main/master, and a push that
# targets main/master (fast-forward merge by push) are gated too, because the
# native git/gh CLI is allowed again. The MCP branch is unchanged -- those are
# GitHub tools, not the retired git-tools server. Escape hatch:
# <cwd>/.claude/git-guard-off.
#
# v2.4.0 (A6): a merge initiated while the checkout is on a PROTECTED branch is
# refused outright, before the artifact is even read — on that topology the
# merge lands the other side's content, so no comparison against this
# checkout's HEAD can say anything about it. See the block above that check.
#
# QUALIFIER (v3.0.3): "refused outright" is true FOR REPOS WITH A GATE
# CONFIGURED. The `**Gate**:` lookup below exits 0 when the field is absent,
# BEFORE any protected-branch judgement — so **no Gate command configured means
# this hook exits 0 for every merge, on every branch.** Configure `**Gate**:` in
# PROJECT_CONTEXT.md to get merge protection. That is the contract and not a
# bug (a gate with no command has nothing to prove), but it is the reading
# people get wrong, so it is stated here rather than inferred from the code.
# There is deliberately no stderr notice: the harness drops non-blocking hook
# stderr, so it would be invisible to the person who needs it.
#
# ---------------------------------------------------------------------------
# v3.0.1 — WHAT IS AND IS NOT GATED ON A PROTECTED BRANCH. Read this before
# reaching for the kill switch; it is the contract, and it lives HERE because
# this file syncs to consumers while docs/verification.md does not.
#
#   git merge --abort / --continue / --quit   ALLOWED, always. They land nothing
#       new, and a conflicted merge on a protected branch is exactly the state
#       this gate exists to worry about — you must be able to get out of it.
#   git merge <anything else>                 GATED, unconditionally.
#       v3.0.2 DELETED the catch-up exemption that allowed
#       `git merge --ff-only <this branch's upstream>` when HEAD was already an
#       ancestor of it. It resolved the ref by NAME and trusted its VALUE, and
#       `git update-ref refs/remotes/origin/main <sha>` is ungated — measured
#       landing never-gated content on a protected branch while every
#       configuration read came back honest. Proving the ref FRESH does not
#       work either: a reflog message is forgeable with `update-ref -m`, and a
#       fresh clone has no `.git/logs/refs/remotes/origin/main` at all, so a
#       freshness check fails closed on every new clone.
#       THE CATCH-UP ROUTE IS NOW `git pull --ff-only`, which is safe here
#       BECAUSE IT FETCHES FIRST: the fetch overwrites a poisoned tracking ref
#       before the merge reads it (measured).
#   git pull --ff-only   (no remote, no refspec)   ALLOWED only when this
#       branch's own config names it: `branch.<cur>.remote` is set AND
#       `branch.<cur>.merge` is `refs/heads/<cur>`. v3.0.2: "targets the
#       configured upstream by construction" was false — `branch.<cur>.merge`
#       is re-pointable, re-pointing is ungated, and `git pull --ff-only` then
#       landed a whole foreign branch on a protected branch.
#   git pull --ff-only <remote> <branch>      ALLOWED when <branch> is this
#       branch's name and neither operand carries a ':'. Strictly more provably
#       safe than the refspec-free form — it NAMES what it lands — and gated
#       until v3.0.2 purely because the form looked refspec-bearing.
#   git pull <any other form>                 GATED, including a bare `git pull`
#       on a branch that would fast-forward cleanly. That is deliberate: the
#       remedy is one word, and it is the safer command.
#   gh pr merge / the GitHub merge tools      GATED from a protected branch.
#
#   AND: any of the gated forms above is also refused when an EARLIER clause of
#       the SAME command checks out (or switches to) a protected branch, or a
#       target this hook cannot resolve. `git checkout main && git merge
#       feature/x` used to slip through — this hook runs before the command, so
#       it read the branch you were still on. Order is what matters, not
#       chaining: `gh pr merge …; git checkout main; git pull --ff-only` is fine,
#       the gated clause runs first. `git checkout feature/z && git merge …` is
#       fine too, because the target is not protected. Run the checkout and the
#       operation as separate calls, or name the destination — `git push origin
#       <branch>` keys on its argument and never consults the current branch.
#
#   AND (v3.0.2, REPLACED IN v3.0.3 BY AN ALLOWLIST): the same refusal when an
#       earlier clause of the same command could MOVE WHAT THE GATED CLAUSE
#       RESOLVES TO. v3.0.2 enumerated the ways (`git branch -u`, `git config
#       branch.*/remote.*`, `git update-ref`, …) and the enumeration did not
#       close — `git config include.path /x` and `cp rogue .git/config` both
#       walked past it. v3.0.3 inverts it into three categories:
#         inert   `echo`, `printf`, `ls`, `pwd`, `true`, and `git status |
#                 rev-parse | branch | log | diff | show` WITH read-only flags
#                 only. An inert clause is not scanned at all, so `git pull
#                 --ff-only && echo git pull --ff-only on main OK` is allowed.
#                 NOT inert: a redirection operand, any of the four command
#                 substitution forms (`$(`, backtick, `<(`, `>(`), a flag that
#                 is not on the verb's allowlist (`git diff --output=` CLOBBERS
#                 a file), or ANY clause when the command contains a pipe
#                 (`echo 'git merge x' | bash` EXECUTES it).
#         tracked `cd`, `git -C`, `git checkout|switch` — allowed because this
#                 gate FOLLOWS them and re-decides on where they land.
#         mover   everything else. Not refused on its own; it makes a LATER
#                 gated clause on a protected branch refuse.
#
#   AND (v3.0.2, WIDENED IN v3.0.3): the same refusal when the gated git
#       invocation ITSELF carries a global option that changes what it RESOLVES
#       to — `-c`, `--config-env`, `--git-dir`, `--work-tree`, `--namespace`,
#       `--exec-path`, `--no-replace-objects`, an unknown global, or an
#       environment assignment before `git`. This needs no chaining at all: a
#       single `git -c remote.evil.url=… -c branch.main.remote=evil pull
#       --ff-only` was measured landing foreign content while
#       `branch.main.merge` on disk still read `refs/heads/main`. Allowed
#       globals: `-C <path>`, `--no-pager`, `-P`, `--paginate`,
#       `--no-optional-locks`, `--literal-pathspecs`, `--no-lazy-fetch`.
#
# THE STATED LIMIT: every decision above is taken BEFORE any fetch, so the
# CONTENT of a remote ref is unknown to this hook — it can only read the form of
# the command and refs that already exist locally. That is why `pull` is gated
# by form rather than by content: a remote-tracking ref that is merely STALE
# answers "adds nothing" confidently and wrongly about an object that is not the
# one the pull will land.
# ---------------------------------------------------------------------------
#
# Requires an artifact written by hooks/run-gate.sh under <common git dir>/gate/
# (v4.0.1, item 17 — see gc_gate_dir's header note in hooks/lib/git-cmd.sh),
# resolved from the merging session's cwd — worktree-aware, since developer
# agents self-merge from their worktrees, and this is the ONE location every
# worktree of a repo resolves to, so an artifact written from a linked
# worktree is visible to a merge attempted from the main checkout. The
# filename is sha-keyed (`last-pass.<sha>.json`), not a single fixed name,
# because the directory is shared: two worktrees gating concurrently must not
# clobber each other's artifact. Lookup order:
#   1. exact filename for the sha about to be merged (fast path — resolved via
#      `git rev-parse --verify HEAD^{commit}` ONLY, never from command text,
#      and validated against `^[0-9a-f]{7,40}$` before it is used to build a
#      path);
#   2. a scan of `last-pass.*.json` in that directory, newest mtime first,
#      skipping `*.tmp` (an in-progress atomic write), for one whose "tree"
#      matches — this is a PINNED decision (R20): it deliberately blesses a
#      commit whose own sha never had a gate run when another commit with the
#      identical tree did (a different worktree, a cherry-pick, a
#      message-only rebase), because the tree — not the sha — is what was
#      actually tested (item 15);
#   3. the legacy `<repo toplevel>/.gate/last-pass.json` (pre-4.0.1, one
#      release only), which prints a deprecation NOTE to stderr when used.
# Whichever artifact is selected, blocks (exit 2) unless:
#   - the artifact exists,
#   - its "sha" equals the current HEAD of that checkout OR its "tree" equals
#     that checkout's HEAD^{tree} — as of v2.1.5 "tree" is the WORKING tree at
#     gate time, so a commit of exactly what was gated (chained
#     `git add … && git commit`, or `git commit -a`) matches by tree even
#     though the artifact's sha is the parent commit's, and
#   - the artifact file is younger than GC_GATE_TTL_S (hooks/lib/git-cmd.sh;
#     3600s = 60 minutes) by mtime.
#
# CONSEQUENCE, by construction, on a repo that commits straight to trunk: the
# artifact is STALE most of the time. Any commit moves HEAD and changes the
# tree — a docs-only commit included, because docs are tracked content — so both
# keys miss. That is correct. The artifact blesses one specific tree, that tree
# genuinely changed, and the gate cannot know which changes are harmless. The
# tree key was added in v2.1.5 to fix a different problem (the artifact's `sha`
# being the PARENT commit when an agent chains `git add … && git commit`), not
# to make an artifact outlive later commits. It costs nothing in practice: this
# gate only fires on merge-shaped commands, so staleness is invisible until an
# actual merge — at which point re-running the gate is exactly the requirement.
# Do NOT add a path-filtered or docs-excluding tree key to "fix" it: deciding
# which file changes are safe to skip is the judgement a gate must not make.
#
# No-op (exit 0) when PROJECT_CONTEXT.md has no Gate command or the field is
# still a {{...}} placeholder — same graceful degradation as pre-commit-test.sh.
#
# v2.2.0: "main/master" above means the protected set, which an optional
# PROJECT_CONTEXT.md line configures:
#
#   - **Protected branches**: develop release
#
# Default (field absent) is `main master`; `none` protects nothing — a
# `gh pr merge` is still gated, since that is a merge whatever the branch is.
# The payload is parsed through hooks/lib/json.sh (node, python3 or jq); with
# none of the three on PATH this gate fails CLOSED.

# Fail CLOSED when the sourced lib is missing: without it every gc_* helper is
# undefined, GC_TOOL stays empty, and this gate would exit 0 on every merge.
lib="$(dirname "$0")/lib/git-cmd.sh"
[ -f "$lib" ] || { echo "BLOCKED: $lib missing — run /sync-template step 6b (hooks/lib/git-cmd.sh)" >&2; exit 2; }
. "$lib"
command -v gc_current_branch >/dev/null 2>&1 || { echo "BLOCKED: $lib is present but corrupt (gc_current_branch undefined) — this gate cannot evaluate the command, refusing" >&2; exit 2; }

gc_read_stdin
gc_guard_off && exit 0

CWD="$GC_CWD"

# What the block message reports about. Defaults describe the MCP merge tools,
# which carry no command string at all; the Bash branch overrides them.
A6_KIND=mcp
A6_SEG=""
A6_ARGS=""
A6_MOVE_SEG=""
A6_MOVE_TARGET=""
A6_MUT_SEG=""
moved=0
mutated=0

# --- v3.0.1 (A6 fix) argument parsing, deliberately LOCAL to this hook -------
#
# Not added to hooks/lib/git-cmd.sh: that library is covered by the three-parser
# matrix (~90 minutes), nothing else needs these shapes, and keeping them here
# means the A6 fix costs one suite run rather than three.

# a6_args <segment> <subcommand> -- everything after `<subcommand>` in a segment.
a6_args() {
  printf '%s\n' "$1" | sed -n "s/.*[[:space:]]$2\\([[:space:]]\\|\$\\)/\\1/p" | head -1
}

# a6_strip_redir <args> -- <args> with shell REDIRECTION tokens removed.
#
# v3.0.2 (measured minutes after v3.0.1 shipped): `git pull --ff-only 2>&1` was
# BLOCKED, reporting `refspec/remote named: present (2>&1 )`. `2>&1` starts with
# a digit, so the non-flag filter below counted it as an operand — an ordinary
# scripted form refused by a guard, which is how a layer gets switched off.
#
# THE STRIP MUST NOT BE GREEDY, and that is the whole difficulty. Dropping
# everything after a `>`, or every token that merely CONTAINS one, would turn
# `git pull origin feature/x 2>&1` into a refspec-free pull and hand it the
# safe-form exemption — reopening the hole A6 just closed. So it drops only
# tokens that are THEMSELVES a redirection operator:
#   - operator with its target attached (`2>&1`, `>file`, `>>file`, `<file`,
#     `&>file`): the one token goes;
#   - bare operator (`>`, `>>`, `<`, `2>`, `&>`): the token AND the next one,
#     which is its target. Exactly one follower, never a run.
# A ref name can never match either pattern, because both require a `>` or `<`
# immediately after an optional fd number or `&`.
#
# The empty-token arm comes FIRST on purpose: `tr` emits an empty field for a
# double space, so `git pull >  /dev/null` yields `>`, ``, `/dev/null`. If the
# empty token consumed the skip, `/dev/null` would leak back in as an operand.
a6_strip_redir() {
  printf '%s\n' "$1" | tr ' \t' '\n\n' | awk '
    $0 == "" { next }
    skip == 1 { skip = 0; next }
    /^([0-9]*|&)(>>|>|<).+$/ { next }
    /^([0-9]*|&)(>>|>|<)$/ { skip = 1; next }
    { print }
  ' | tr '\n' ' '
}

# a6_nonflag <args> -- the tokens that are not flags (a target ref, a remote).
a6_nonflag() {
  a6_strip_redir "$1" | tr ' \t' '\n\n' | grep -E '^[^-][^[:space:]]*$'
}

# a6_nonflag_count <args>
a6_nonflag_count() {
  a6_nonflag "$1" | grep -c . || true
}

# a6_has_flag <args> <alternation> -- a long flag from the alternation is present.
a6_has_flag() {
  printf '%s\n' "$1" | grep -qE "(^|[[:space:]])--($2)([[:space:]]|=|\$)"
}

# a6_merge_exempt <merge-args> -- `git merge --abort|--continue|--quit`.
#
# HIGHEST-PRIORITY ARM OF THE v3.0.1 FIX, and it is checked before every other
# question. All three were measured exiting 2 on a protected branch, which left
# a consumer in a CONFLICTED MERGE ON `main` — precisely the state this gate
# exists to worry about — with no way out except `.claude/git-guard-off`. A
# guard whose only exit is its own kill switch teaches the kill switch.
# None of the three lands anything new: abort restores the pre-merge state,
# continue completes work already begun (and already gated when it began), quit
# leaves the index alone.
#
# The exemption requires NO non-flag token to be present. gc_segments strips
# quotes, so `git merge -m "retry after --abort" feature/x` otherwise hands us a
# bare `--abort` token and buys a real merge the exemption. The three real forms
# take no operand, so the requirement costs nothing and closes that door.
a6_merge_exempt() {
  [ "$(a6_nonflag_count "$1")" -eq 0 ] || return 1
  a6_has_flag "$1" 'abort|continue|quit'
}

# a6_upstream_facts <repo> -- populate A6_UP / A6_UPSHA / A6_HEADSHA for both
# the discriminator and the block message. Local reads only, no network.
# a6_branch_move <segment> -- a clause that can move HEAD to another branch.
#
# NOT added to hooks/lib/git-cmd.sh even though no-push-main.sh needs the same
# three lines: the lib is covered by the ~90-minute three-parser matrix, and
# this shape carries no JSON. The duplication is deliberate and small.
#
# `--` means "everything after this is a path", so `git checkout -- file` and
# `git checkout <ref> -- path` restore files without moving HEAD. Those must not
# arm the refusal, or a routine file restore would gate the merge that follows.
a6_branch_move() {
  gc_matches_subcommand "$1" "checkout" || gc_matches_subcommand "$1" "switch" || return 1
  printf '%s\n' "$1" | grep -qE '(^|[[:space:]])--([[:space:]]|$)' && return 1
  return 0
}

# a6_move_target <segment> -- the branch a checkout/switch names, or "".
#
# KEY ON THE TARGET, NOT ON THE PRESENCE OF A CHECKOUT. A branch change only
# matters when it moves the checkout ONTO a protected branch;
# `git checkout feature/z && git merge feature/y` lands nothing near one, and
# refusing it would be a false positive on ordinary work — which is how a layer
# gets switched off. The target is an ARGUMENT, in the payload, not ambient
# state the command can change, so it is the safe thing to key on: the same
# distinction that explains the bug, applied one level in.
a6_move_target() {
  a6mt=$(a6_args "$1" "checkout")
  [ -n "$a6mt" ] || a6mt=$(a6_args "$1" "switch")
  a6_nonflag "$a6mt" | head -1
}

# a6_move_verdict <repo> <segment> -- 1 = moves onto a protected branch,
# 2 = names a target this hook cannot resolve, 0 = moves somewhere harmless.
a6_move_verdict() {
  A6_MOVE_TARGET=$(a6_move_target "$2")
  case "$A6_MOVE_TARGET" in
    # No target at all (`git checkout` alone), `-` / `@{-1}` (the previous
    # branch), a variable, or anything that is not a plain ref name: genuinely
    # cannot be determined here, so refuse THOSE rather than every chain.
    ''|*[!A-Za-z0-9._/-]*) printf 0 >/dev/null; return 2 ;;
  esac
  for a6mp in $(gc_protected_branches "$1"); do
    [ "$A6_MOVE_TARGET" = "$a6mp" ] && return 1
  done
  return 0
}

a6_upstream_facts() {
  A6_UP=$(git -C "$1" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)
  A6_UPSHA=""
  [ -n "$A6_UP" ] && A6_UPSHA=$(git -C "$1" rev-parse --verify --quiet "$A6_UP^{commit}" 2>/dev/null)
  A6_HEADSHA=$(git -C "$1" rev-parse --verify --quiet HEAD 2>/dev/null)
}

# v3.0.2 — A6.2's catch-up exemption is GONE. It lived here, and it allowed
# `git merge --ff-only <this branch's configured upstream>` on a protected
# branch when HEAD was already an ancestor of that ref. Measured end-to-end:
#
#   git update-ref refs/remotes/origin/main <rogue sha>   verdict 0  UNGATED
#   git merge --ff-only origin/main                       verdict 0  ALLOWED
#     -> never-gated content landed on the protected branch, and every
#        configuration read (branch.main.merge, main@{upstream}) stayed honest.
#
# THE EXEMPTION RESOLVED A REF BY NAME AND TRUSTED ITS VALUE. A name is not a
# value. And proving the value FRESH is not available to this hook: a reflog
# entry is forgeable (`git update-ref -m 'fetch origin: fast-forward'`), the log
# is a file that can be removed, and a FRESH CLONE HAS NO
# `.git/logs/refs/remotes/origin/main` at all — so a freshness check would fail
# closed on every new clone. Do not reintroduce either shape.
#
# No capability is lost: `git pull --ff-only` catches a protected branch up and
# SELF-HEALS, because the pull fetches before it merges — measured restoring a
# poisoned `origin/main` before reading it. That is now what the DENY message
# recommends.

# a6_pull_catchup <repo> <pull-args> -- true for the two pull forms that are
# provably safe on a protected branch BEFORE any fetch. Sets A6_PULL_WHY for the
# block message.
#
# v3.0.2, finding 54. The form this replaced allowed any refspec-free
# `--ff-only` pull, on the stated grounds that it "targets the configured
# upstream by construction". It does not: `branch.<cur>.merge` is re-pointable,
# and re-pointing is ungated on both gates.
#
#   git branch -u origin/rogue main    verdict 0   (ungated)
#   git pull --ff-only                 verdict 0   -> HEAD MOVED, rogue landed
#
# So compare NAMES — a local config read, offline, sound before the fetch:
#
#   refspec-free `--ff-only`   allowed only when `branch.<cur>.remote` is
#       non-empty AND `branch.<cur>.merge` is exactly `refs/heads/<cur>`.
#       Missing or mismatched is cannot-determine, so it gates.
#   `--ff-only <remote> <branch>`   allowed when there are exactly two operands,
#       neither carries a ':', and <branch> is the current branch. This form was
#       GATED until v3.0.2 and is strictly more provably safe than the one that
#       was allowed — it NAMES what it lands. A consumer reported the false
#       positive; the report's harmless half turned out to be the sound form.
#
# Operands are counted through a6_nonflag, which strips redirections first, so
# `git pull --ff-only origin main 2>&1` still reads as two operands and not
# three. That is the row where this rule and the v3.0.2 redirect strip meet.
#
# `config --get` is used, not `--get-all`: a multi-valued key makes it exit
# non-zero with no value, which lands in the mismatch arm and gates. That is the
# right answer — a branch with two merge refs is not one this can prove.
a6_pull_catchup() {
  a6pc_repo=$1; a6pc_args=$2
  A6_PULL_WHY=""
  a6_has_flag "$a6pc_args" 'ff-only' || { A6_PULL_WHY="no --ff-only"; return 1; }
  a6pc_cur=$(gc_current_branch "$a6pc_repo")
  [ -n "$a6pc_cur" ] || { A6_PULL_WHY="current branch unreadable"; return 1; }
  a6pc_n=$(a6_nonflag_count "$a6pc_args")
  case "$a6pc_n" in
    0)
      a6pc_rem=$(git -C "$a6pc_repo" config --get "branch.$a6pc_cur.remote" 2>/dev/null)
      a6pc_mrg=$(git -C "$a6pc_repo" config --get "branch.$a6pc_cur.merge" 2>/dev/null)
      A6_PULL_WHY="branch.$a6pc_cur.remote=${a6pc_rem:-<unset>} branch.$a6pc_cur.merge=${a6pc_mrg:-<unset>} (want refs/heads/$a6pc_cur)"
      [ -n "$a6pc_rem" ] || return 1
      [ "$a6pc_mrg" = "refs/heads/$a6pc_cur" ] || return 1
      return 0
      ;;
    2)
      a6pc_dst=$(a6_nonflag "$a6pc_args" | sed -n 2p)
      A6_PULL_WHY="operands: $(a6_nonflag "$a6pc_args" | tr '\n' ' ')(want <remote> $a6pc_cur, no ':')"
      printf '%s\n' "$(a6_nonflag "$a6pc_args")" | grep -q ':' && return 1
      [ "$a6pc_dst" = "$a6pc_cur" ] || return 1
      return 0
      ;;
    *)
      A6_PULL_WHY="$a6pc_n operand(s): $(a6_nonflag "$a6pc_args" | tr '\n' ' ')(want 0, or <remote> $a6pc_cur)"
      return 1
      ;;
  esac
}

# v3.0.3 item 2 — a6_clause_class replaces the a6_mutates_resolution BLOCKLIST.
#
# WHY THE BLOCKLIST HAD TO GO, in its own words: four resolution inputs
# (`branch.*.remote`, `branch.*.merge`, `remote.*.url`, `remote.*.fetch`) times
# at least four mutation channels (a git verb, a direct ref write, a
# `.git/config` file edit, env injection) — and the same channel behaves
# differently per key. The cell matters, not the row or the column, so no
# enumeration of keys and channels is sufficient. It missed
# `git config include.path /x` and `cp rogue .git/config`, both measured.
#
# THREE CATEGORIES, not two:
#   inert   never affects the gated clause. The arms are NOT RUN on it.
#   tracked allowed BECAUSE the scanner follows it: `cd`, `git -C`,
#           `git checkout|switch <target>`. `cd` is NOT inert — declaring it so
#           would switch OFF working machinery: `cd <protected repo> &&
#           git merge feature/x` from an unprotected cwd is 2 today, via
#           gc_cd_target/gc_resolve, and would flip to 0.
#   mover   everything else. The arms still run on it; if none fires, it sets
#           the `mutated` flag the pull/push arms read. This is what keeps the
#           gated clause itself — which is a `mover` by classification — from
#           being skipped, and what keeps a mover on a FEATURE branch from
#           being gated for no reason.
#
# WHY NOT SCANNING INSIDE AN INERT CLAUSE IS THE WHOLE POINT: the arms below
# grep UNANCHORED inside each segment, so `git pull --ff-only && echo git pull
# --ff-only on main OK` was refused — the echo body re-parsed as a second pull
# whose trailing words became a refspec. Running a command and then echoing what
# you ran is reflexive narration; an allowlist alone does not close it.
#
# AND WHY THE CATEGORY IS THIS NARROW. Three measured collisions:
#
#  (1) FOUR substitution forms. `$(`, a backtick, `<(` and `>(` can all execute
#      anything, and the last two carry neither `$` nor a backtick. All four are
#      refused today only because the arms see inside them; declaring the verb
#      inert without excluding all four would trade one false positive for four
#      real bypasses.
#
#  (2) NO CLAUSE IS INERT IN A COMMAND CONTAINING A PIPE (`|`, not `||`).
#      Measured, with paired controls:
#        echo 'git merge feature/x'          prints it   — the false positive
#        echo 'git merge feature/x' | bash   RUNS it     — a real merge
#      Identical leading verb, identical quoted content, opposite correct
#      verdicts, and the difference is what CONSUMES the clause's stdout, which
#      a per-clause classifier cannot see. The cheapest sound rule is the whole
#      command: a pipe anywhere and nothing is inert. `echo hello | bash` still
#      passes, because nothing in it is gated.
#
#  (3) AN INERT GIT VERB IS INERT ONLY WITH A FLAG ALLOWLIST — see
#      a6_inert_flags. `git diff HEAD~1 --output=.git/config` CLOBBERS
#      .git/config with NO shell operator in the clause, so the redirection rule
#      cannot see it: the redirection is an option VALUE. `git show --output=`
#      and `git log -p --output=` write files too, and
#      `git branch --set-upstream-to=` / `--unset-upstream` are the long forms
#      of the `-u` guard. Adding those to a guard list would be the third list
#      that does not close, so the allowlist is inverted the same way the argv
#      globals are: any flag not on the verb's list makes the clause a mover.
#
# THE MOVER RULE REFUSES WHEN THE VERDICT DEPENDS ON THE BRANCH THE MOVER LANDS
# ON — not whenever a mover is present. `git checkout main && git merge <x>` is
# refused: the landing is real and the branch decides. `git checkout main &&
# git pull --ff-only` is ALLOWED, because a bare `--ff-only` pull fetches first
# and can only fast-forward to the upstream, so it is judged the same on every
# branch; refusing it would be a denied legitimate command. It blocked on
# v3.0.2 and is allowed from v3.0.3 for that reason. Both polarities have rows.
#
# SETS TWO GLOBALS, does not print. A `$(a6_clause_class …)` call would run the
# body in a SUBSHELL and the reason string would never reach the DENY text — the
# refusal would then read as an ordinary merge refusal and the fixture asserting
# WHICH arm fired would pass on the wrong discriminator.
# Task 2.6b (panoscribe): a `>`/`<` CHARACTER anywhere in the segment used to
# be enough to classify it a mover -- `2>&1`, `>&2`, `1>&2` are fd
# DUPLICATIONS, not file writes, and cannot touch .git/config. Only a
# redirection with a FILE operand (`>`, `>>`, `&>`, `>|`, `<` -- glued to the
# file or as its own token, the file's identity does not matter either way)
# counts as a mover; a bare fd-dup token is inert. Quotes are already gone by
# the time a segment reaches here (gc_segments strips them), so
# `echo "2>&1" >out.txt` and `echo 2>&1 >out.txt` are indistinguishable here
# on purpose -- both carry a real `>out.txt` token and both must be movers.
a6_redir_mover() { # <segment> -> prints mover|inert
  printf '%s\n' "$1" | tr ' \t' '\n\n' | awk '
    function isdup(t) { return (t ~ /^[0-9]*>&[0-9-]+$/) || (t ~ /^[0-9]*<&[0-9-]+$/) }
    $0 == "" { next }
    isdup($0) { next }
    /[<>]/ { found = 1 }
    END { print (found ? "mover" : "inert") }
  '
}

a6_clause_class() { # <segment> -> sets A6_CLASS=inert|tracked|mover, A6_CLASS_WHY
  a6cc_seg="$1"
  A6_CLASS_WHY=""
  case "$a6cc_seg" in
    *'$('*|*'`'*|*'<('*|*'>('*)
      A6_CLASS_WHY="the clause contains a command substitution, which can execute anything"
      A6_CLASS=mover; return ;;
  esac
  case "$a6cc_seg" in
    *'>'*|*'<'*)
      if [ "$(a6_redir_mover "$a6cc_seg")" = mover ]; then
        A6_CLASS_WHY="the clause carries a redirection operand, which can write .git/config"
        A6_CLASS=mover; return
      fi
      ;;
  esac
  a6cc_first=$(printf '%s' "$a6cc_seg" | awk '{print $1}')
  case "$a6cc_first" in
    echo|printf|ls|pwd|true|:)
      a6cc_inert; return ;;
    cd)
      A6_CLASS=tracked; return ;;
    git)
      if gc_matches_subcommand "$a6cc_seg" "checkout" || gc_matches_subcommand "$a6cc_seg" "switch"; then
        A6_CLASS=tracked; return
      fi
      for a6cc_ro in status rev-parse branch log diff show; do
        if gc_matches_subcommand "$a6cc_seg" "$a6cc_ro"; then
          if a6_inert_flags "$a6cc_ro" "$a6cc_seg"; then
            a6cc_inert; return
          fi
          A6_CLASS=mover; return
        fi
      done
      A6_CLASS_WHY="'git $(printf '%s' "$a6cc_seg" | awk '{print $2}')' is not one of the read-only verbs this gate can prove inert"
      A6_CLASS=mover; return ;;
  esac
  A6_CLASS_WHY="'$a6cc_first' is not on the inert or tracked lists"
  A6_CLASS=mover
}

# The one place `inert` is emitted, so the pipe rule cannot be forgotten at one
# of the three call sites above. Only `inert` is suppressed by a pipe; `tracked`
# is a gating mechanism, not a bypass, and stays on.
a6cc_inert() {
  if [ "${A6_NOINERT:-0}" = 1 ]; then
    # Task 2.6 (panoscribe): name the actual downstream stage that can consume
    # this clause's output, not the generic "a pipe elsewhere" -- the first
    # OTHER segment in the pipe whose leading word is not itself provably
    # harmless. $segments is the whole-command segment list set once before
    # this loop runs; $a6cc_seg is this clause, set by the caller.
    a6cc_stage_seg=$(printf '%s\n' "$segments" | while IFS= read -r a6cc_other; do
      [ -n "$a6cc_other" ] || continue
      [ "$a6cc_other" = "$a6cc_seg" ] && continue
      case "$(printf '%s' "$a6cc_other" | awk '{print $1}')" in
        echo|printf|ls|pwd|true|:) continue ;;
        *) printf '%s\n' "$a6cc_other"; break ;;
      esac
    done)
    a6cc_stage=$(printf '%s' "$a6cc_stage_seg" | awk '{print $1}')
    if [ -n "$a6cc_stage" ]; then
      A6_CLASS_WHY="the pipe's later stage (\`${a6cc_stage}\`) is not inert"
    else
      A6_CLASS_WHY="a pipe elsewhere in this command can consume this clause's output and execute it"
    fi
    A6_CLASS=mover
    return
  fi
  A6_CLASS=inert
}

# a6_inert_flags <verb> <segment> -- true when every FLAG token after <verb> is
# on that verb's read-only allowlist. Non-flag tokens (revs, paths) are free.
# Start narrow: a consumer chain that needs another flag gets a row and an entry,
# which is a decision someone makes on purpose rather than a gap that opens
# itself. Anything carrying `--output`, `-o` or a path-valued option is by
# construction absent from these lists and therefore refuses.
a6_inert_flags() {
  case "$1" in
    status)    a6if_ok='--short|-s|--porcelain|--porcelain=.*|--branch|-b|--untracked-files=.*|-u.*|--no-color' ;;
    rev-parse) a6if_ok='--show-toplevel|--git-dir|--abbrev-ref|--short|--short=.*|--verify|--symbolic-full-name|--is-inside-work-tree' ;;
    branch)    a6if_ok='--show-current' ;;
    log)       a6if_ok='--oneline|-n|-[0-9]+|--format=.*|--pretty=.*|--graph|--decorate|--no-decorate|--no-color' ;;
    diff)      a6if_ok='--stat|--name-only|--name-status|--cached|--staged|--no-color|--' ;;
    show)      a6if_ok='--stat|--name-only|--format=.*|--pretty=.*|--no-patch|-s|--no-color' ;;
    *)         A6_CLASS_WHY="'git $1' has no read-only flag allowlist"; return 1 ;;
  esac
  for a6if_t in $(a6_args "$2" "$1"); do
    case "$a6if_t" in
      -*)
        printf '%s\n' "$a6if_t" | grep -qE "^($a6if_ok)$" && continue
        A6_CLASS_WHY="flag '$a6if_t' is not on the read-only allowlist for 'git $1', so this clause is not provably inert"
        return 1 ;;
    esac
  done
  return 0
}

# a6_inline_config <segment> <subcommand> -- the gated invocation ITSELF carries
# configuration, so no preceding clause is needed to re-point it.
#
# BROAD BY DESIGN: any `-c` or `--config-env` before the subcommand, and any
# `GIT_CONFIG_*` env prefix on the clause — not an enumeration of keys, because
# a new key cannot then silently join the list. Measured, ONE clause:
#
#   git -c remote.evil.url=<other> -c remote.evil.fetch=+refs/heads/*:… \
#       -c branch.main.remote=evil pull --ff-only
#     -> rc=0, fast-forward, foreign content on main, while
#        branch.main.merge on disk still read refs/heads/main
#
# The name comparison in a6_pull_catchup PASSES there — the merge ref is
# honest — and the content came from another remote entirely.
#
# THE HONEST COST: a benign `git -c core.pager=cat merge …` on a protected
# branch is now refused. That is the correct direction for a fail-closed gate,
# and the DENY message says to re-run without the `-c`.
#
# The `-c` search is restricted to the text BETWEEN `git` and the subcommand:
# gc_segments strips quotes, so a segment-wide grep would fire on
# `git merge -m "note -c x" feature/x` and refuse a message body.
# a6_is_git_sub is GONE (v3.0.3, finding 62). It existed because the lib's
# GC_GIT_PRE tolerated only `-C` and `-c`, so a clause carrying any other
# global did not match as a pull/push AT ALL and fell through UNGATED. The
# prefix is widened in the lib now — which is where the other two git gates
# read it from — so the local compensation is dead code and the arms below
# call gc_matches_subcommand directly.


# v3.0.3 item 1's classifier (gc_global_options) MOVED TO hooks/lib/git-cmd.sh
# as gc_global_options when finding 62 showed no-push-main.sh and
# pre-commit-test.sh need the identical classification. Same contract:
# `ok | refuse:<opt> | env:<VAR>`. One definition, three callers.

# gc_repo_for IS GONE (v3.0.3, defect 1 round 2). It held a private copy of the
# repeated-`-C` fold, on the stated grounds that fixing hooks/lib/git-cmd.sh
# would drag the ~90-minute three-parser matrix into this release. That reason
# was void — the lib already carries functional change in v3.0.3, so the matrix
# is already mandatory — and the private copy fixed THIS hook while leaving the
# same bypass live in hooks/no-push-main.sh, MEASURED at 0d7806e:
#
#   git -C <feature repo> -C <protected repo> push   from either cwd -> 0
#
# i.e. git lands on the protected branch and the push gate never sees it. The
# fold now lives in gc_repo_for; all three callers get it. See the comment
# there for the git 2.55.0 measurement of `-C` composition.

# a6_deny_unresolved_c <segment> <base> -- refuse a GATED clause whose repeated
# `-C` fold does not resolve. Never called for a 0- or 1-`-C` segment: a single
# `-C` into a missing directory keeps the documented pre-v3.0.3 behaviour (fall
# back to the cwd, decide, let git fail). A fold that does not resolve is the
# cannot-determine case, and a fail-closed gate refuses it.
a6_deny_unresolved_c() {
  a6du_out=$(gc_dash_c_unresolved "$1" "$2")
  [ -n "$a6du_out" ] || return 0
  a6du_kind=$(printf '%s\n' "$a6du_out" | sed -n 1p)
  a6du=$(printf '%s\n' "$a6du_out" | sed -n 2p)
  if [ "$a6du_kind" = cannot-determine ]; then
    # v3.0.3 defect 2 -- a different fact from "does not resolve": the operand
    # contains an unexpanded shell expression this hook cannot know the value
    # of without executing it (never done here -- see gc_classify_c).
    {
      echo "BLOCKED: A6 — hook cannot DETERMINE the -C target (contains an unexpanded shell expression): $a6du"
      echo "  clause:          $1"
      echo "  This clause names a -C operand this hook cannot resolve by string substitution"
      echo "  alone. Pass a literal or absolute path, or one of \$HOME/\$USERPROFILE/~."
    } >&2
    exit 2
  fi
  {
    echo "BLOCKED: A6 — hook could not resolve \`-C $a6du\`; if git can, pass an absolute path."
    echo "  clause:          $1"
    echo "  This clause carries more than one 'git -C' and NOT ONE of them resolves, so"
    echo "  there is no candidate repository to judge. A fold with at least one resolvable"
    echo "  step is judged on the strictest candidate instead of being refused."
  } >&2
  exit 2
}

# v2.2.6 round 2 -- THE 14th FAIL-OPEN, third instance. Checked BEFORE the tool
# case below, because that case's `*)` arm is exactly the door an unreadable
# tool_name walks through. See gc_cmd_unreadable in hooks/lib/git-cmd.sh.
if gc_cmd_unreadable; then
  echo "BLOCKED: gate-before-merge: the payload carries a command this gate could not read, so it cannot rule out a merge -- refusing. Re-run the command. (If it repeats: create '.claude/git-guard-off' under this cwd, make the one fix, then delete it.)" >&2
  exit 2
fi

# Branch on the TOOL, never on the parsed string. This hook is registered on
# Bash|PowerShell, so if node is missing or the JSON does not parse, GC_CMD is
# empty -- and treating that as "not a Bash call" would fall through to the
# artifact check and block every Bash call in the session. A Bash payload with
# NO COMMAND KEY is a Bash payload with no merge in it; only the GitHub merge
# tools are gated unconditionally.
#
# v2.2.6 round 2 narrows what "we cannot read it" means here: a payload that
# HAS a `command` key we could not read no longer reaches this case at all --
# it was refused above. What still falls open below is the genuinely absent
# key and the genuinely unknown tool, which are not cannot-determine states.
case "$GC_TOOL" in
  Bash|PowerShell) ;;   # gate only merge-shaped commands (scanned below)
  mcp__*)          ;;   # the GitHub merge tools: always gated
  *) exit 0 ;;          # unknown tool, or a payload with no command key at all
esac

# v4.0.3 item 12 -- widen GC_CMD to include the body of any script segment it
# invokes (`bash|sh|source|. <path>`, depth 1), BEFORE the git-token
# pre-filter just below AND the segment walk further down: both must see the
# SAME text, or a script's `git merge`/`git push` would pass the pre-filter's
# "no git token" fast exit before the walk that would have caught it ever
# runs. See gc_script_body / gc_augmented_cmd in hooks/lib/git-cmd.sh for the
# 16 KB cap and the depth-1/TOCTOU residuals. No-op for the mcp__* tools
# (empty GC_CMD there).
# v4.1.2: the continuation join runs once at the origin (gc_read) and on each
# appended body, so this text is already joined.
GC_CMD="$(gc_augmented_cmd "$CWD")"

# v3.0.3 item 25 — EXIT BEFORE DOING ANY WORK ON A PAYLOAD THAT CANNOT BE GATED.
#
# The ~1.5 s this gate spends per call is WORK, not parse: measured, comments
# intact 49 KB -> 1512 ms/call, the same logic with comments stripped 17 KB ->
# 1559 ms, a no-op script 38 ms. Stripping 32 KB changed nothing. The lever is
# not doing the work — the segment walk, gc_repo_for, gc_protected_branches,
# gc_on_main and their git subprocesses — on a command with no git or gh token
# in it at all. Every gated path in this file needs one of the two tokens
# below, so a payload carrying neither cannot reach a refusal.
#
# WHY THIS IS AFTER THE PARSE AND NOT BEFORE IT, measured. The obvious cheaper
# form is to grep the RAW JSON payload before sourcing anything. It is unsound:
# a newline inside the command is JSON-escaped as `\n`, so
# `"echo a\ngit merge feature/x"` puts the letter `n` immediately before `git`
# in the raw bytes, the `(^|[^[:alnum:]_-])` prefix fails, and the grep MISSES a
# genuinely gated merge — a FALSE NEGATIVE, i.e. an ungated exit 0, on exactly
# the newline-separated shape gc_segments exists to split. (Probed: the
# backslash-continuation, leading-tab, `&&` and `gh pr merge` forms are all
# seen; the newline one is not.) A false positive costs one wasted parse; a
# false negative costs the gate. So the token test reads GC_CMD, which is
# unescaped, and sits before the first git subprocess rather than before the
# parse.
#
# The MCP branch is untouched: those payloads carry no command string, and the
# GitHub merge tools are gated unconditionally.
#
# Note the test is for a git TOKEN, not for a leading `git merge`: after
# finding 62 a gated command can be `git -P merge feature/y`, and an early exit
# that pattern-matched on the subcommand would pass every timing test and
# re-open finding 62 in the same change — fast and wrong.
if [ "$GC_TOOL" = "Bash" ] || [ "$GC_TOOL" = "PowerShell" ]; then
  if ! printf '%s\n' "$GC_CMD" | grep -qE '(^|[^[:alnum:]_-])git([[:space:]]|$)' &&
     ! printf '%s\n' "$GC_CMD" | grep -qE '(^|[^[:alnum:]_-])gh[[:space:]]+pr[[:space:]]+merge'; then
    exit 0
  fi
fi

# INVARIANT — RE-CHECK IT BEFORE YOU ADD A GATED PATH (v3.0.2; penumbra).
#
# `git update-ref refs/remotes/origin/main <sha>` is UNGATED and stays so:
# the ways to MOVE a ref are unbounded, the ways to LAND content are few, so
# this gate protects the landing and never the ref. The property this file
# guarantees is therefore NOT "tracking refs are trustworthy". It is:
#
#   every path that acts on a remote-tracking ref on a protected branch is
#   gated UNCONDITIONALLY — its verdict never depends on the ref's VALUE.
#
# That is a property of the CONSUMER LIST, not of any one arm. The list is
# A6_CONSUMER_LIST below; test-hooks.sh's A6.11 census asserts the case arms
# in this file match it, and the A6.11 canary runs every form in FOUR ref
# states. If you add a gated path that RESOLVES a tracking ref and branches on
# what it finds — a new catch-up exemption, an "only if behind" shortcut, an
# "already up to date, nothing to gate" fast path, anything keyed on
# `rev-parse origin/X` — you have reopened ref poisoning, and NO EXISTING
# FIXTURE GOES RED except the canary, and only if you add the form to the
# list. The poisoning question is UNREACHABLE today, not ANSWERED.
# Before merging such a path:
#   (1) add its command form to A6_CONSUMER_LIST;
#   (2) the A6.11 canary then runs it in all four values of the pairwise
#       ancestry relation between HEAD and origin/main — BEHIND, EQUAL,
#       AHEAD, DIVERGED — and asserts the verdict AND the discriminator are
#       unchanged across all four. Four, not two: the v3.0.1 exemption keyed
#       on "HEAD is an ancestor of upstream", which is true for BEHIND and
#       EQUAL alike, and DIVERGED is the one people forget — "upstream is an
#       ancestor of HEAD" is true for AHEAD and false for DIVERGED, exactly
#       the shape a push-side shortcut would key on. WHAT THE FOUR STATES DO
#       AND DO NOT COVER, measured: the canary is asserted for every ancestry
#       predicate AND every value-equality predicate — EQUAL is one of the four
#       and the other three poison away from the honest value, so any
#       single-sha exemption gives a 1-vs-3 split whichever sha it keys on. It
#       is NOT asserted for predicates on commit CONTENT or METADATA that the
#       four fixture states share (author, message text, a file's presence):
#       the forged AHEAD/DIVERGED commits inherit those from the fixture;
#   (3) run delete-the-guard on that row: restore v3.0.1's exemption from git
#       and confirm the row goes red.
#
# ACCEPTED RESIDUAL, precisely: the name comparison in a6_pull_catchup reads
# branch.<cur>.merge ONLY. branch.<cur>.remote, remote.<r>.url and
# remote.<r>.fetch are NOT read, so a config write to one of those in a
# SEPARATE call, followed by a bare `git pull --ff-only`, passes with `merge`
# honest. Those are the reads that would close it, if anyone ever wants to.
#
# ONE LINE, ON PURPOSE: test-hooks.sh reads this variable out of this file
# with grep. Wrapping or splitting it makes the census silently report the
# list as missing.
A6_CONSUMER_LIST='merge:any-target pull:bare pull:named-refspec push:any'

if [ "$GC_TOOL" = "Bash" ] || [ "$GC_TOOL" = "PowerShell" ]; then
  is_merge=0
  base="$CWD"
  segments=$(gc_segments)

  # v3.0.1 (consumer report) — THE PREMISE THIS GATE READS IS ONE THE COMMAND
  # CAN CHANGE. Every ambient check below resolves the branch with
  # `git branch --show-current`, and this is a PreToolUse hook: it runs BEFORE
  # the command. So `git checkout main && git merge feature/x` is evaluated
  # while the checkout is still on the feature branch, the protected-branch test
  # is false, and an entire unreviewed branch lands on `main` — using the exact
  # verb this gate watches.
  #
  # AND IT IS WORSE THAN A SKIPPED CHECK. The gate does not stand aside: it
  # falls through to the artifact comparison and runs it under the
  # feature-branch premise ("artifact.sha == branch tip == merged content"),
  # which that command invalidates. With a fresh artifact the comparison PASSES.
  # A skipped guard leaves no evidence; this one emits a POSITIVE RECEIPT for
  # something it did not check.
  #
  # ORDER IS THE DISCRIMINATOR, not co-presence. `gh pr merge …; git checkout
  # main; git pull --ff-only` is SAFE and is the flow the toolkit recommends:
  # the gated operation runs FIRST, on the branch this hook can see. Refusing
  # every chain that contains a checkout would gate the recommended workflow,
  # which is how guards get switched off. Only a branch change that PRECEDES a
  # gated operation matters, because only that changes where the operation
  # LANDS.
  #
  # And the guard must gate a merge that LANDS ON a protected branch, not one
  # merely INITIATED FROM a non-protected one: an agent merging its own PR from
  # its worktree is on a feature branch, which is the toolkit's own merge
  # protocol. Gating that would block every worktree-isolated merge, and would
  # read as "the fix works" rather than as the regression it is.
  #
  # Forms whose verdict does NOT depend on the branch — the three merge-state
  # subcommands, a refspec-free `--ff-only` pull, a push naming its destination
  # explicitly — stay allowed after a branch change, because for them the moved
  # premise is not load-bearing. Where a decision can key on the payload's
  # arguments it already does; this flag covers what is left.

  moved=0
  mutated=0

  # v3.0.3 item 2: a pipe anywhere in the command disables the `inert` category
  # for EVERY clause — see a6_clause_class's comment (2). `||` is stripped first
  # because it is a control operator, not a pipe, and gc_segments already splits
  # on it.
  A6_NOINERT=0
  case "$(printf '%s' "$GC_CMD" | sed 's/||//g')" in
    *'|'*) A6_NOINERT=1 ;;
  esac

  while IFS= read -r seg; do
    [ -n "$seg" ] || continue

    # v3.0.2 — the same ordering rule, one premise further out. v3.0.1 keyed on
    # a preceding CHECKOUT because that moves the branch a gated clause lands
    # on; a preceding `git branch -u`, `git config branch.*`, `git update-ref`
    # or `git remote set-url` moves what a gated clause RESOLVES TO, which is
    # the same defect with a different mutable premise. Set here, read by the
    # merge/pull/push arms below. Never reset: a mutation cannot be undone by a
    # later clause the way a checkout can be.
    # v3.0.3 item 2: the blocklist is replaced by a6_clause_class. `inert` skips
    # the arms entirely — that is the fix for the echoed-narration false
    # positive. `mover` does NOT skip them and does NOT set `mutated` here: the
    # gated clause is itself a mover by classification, and setting the flag
    # before the arms would make a bare `git pull --ff-only` its own "earlier
    # clause". The flag is set at the END of the loop body, reached only when no
    # arm fired.
    a6_clause_class "$seg"
    a6cls=$A6_CLASS
    A6_SEG_WHY=$A6_CLASS_WHY
    [ "$a6cls" = inert ] && continue

    cdt=$(gc_cd_target "$seg")
    if [ -n "$cdt" ]; then
      base=$(gc_resolve "$base" "$cdt")
      continue
    fi

    if a6_branch_move "$seg"; then
      # Last one wins: a later checkout back onto a feature branch means the
      # gated clause no longer lands on a protected branch.
      a6_move_verdict "$(gc_repo_for "$seg" "$base")" "$seg"
      moved=$?
      # Trim: gc_segments splits on `&&`/`;`/`|`, which leaves a leading or
      # trailing space on the clause either side of the delimiter -- interior
      # spacing (the checkout's own arguments) is untouched.
      A6_MOVE_SEG=$(printf '%s' "$seg" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
      continue
    fi

    # 1. gh pr merge (any flags)
    if printf '%s\n' "$seg" | grep -qE '\bgh[[:space:]]+pr[[:space:]]+merge\b'; then
      is_merge=1
      A6_KIND=ghpr
      A6_MOVED_VERB="gh pr merge"
      [ "$moved" != 0 ] && A6_KIND=moved
      A6_SEG=$seg
      a6_deny_unresolved_c "$seg" "$base"
      CWD=$(gc_repo_for "$seg" "$base")
      break
    fi

    # 2. git merge while the checkout is on a protected branch
    if gc_matches_subcommand "$seg" "merge"; then
      a6_deny_unresolved_c "$seg" "$base"
      repo=$(gc_repo_for "$seg" "$base")
      margs=$(a6_args "$seg" "merge")
      # v3.0.1 item 1: the three merge-state subcommands are exempt on the
      # SUBCOMMAND PARSE, before the branch is even looked at. See
      # a6_merge_exempt.
      if a6_merge_exempt "$margs"; then
        continue
      fi
      # A branch change earlier in the same command: the branch this merge lands
      # on is not the branch this hook can read. Cannot determine, so refuse.
      if [ "$moved" != 0 ]; then
        is_merge=1
        A6_KIND=moved
        A6_MOVED_VERB=merge
        A6_SEG=$seg
        A6_ARGS=$margs
        CWD="$repo"
        break
      fi
      if gc_on_main "$repo"; then
        # v3.0.2: NO exemption left here but the three merge-state subcommands
        # above. The catch-up exemption that used to sit on this line resolved a
        # ref by name and trusted its value; see the comment on a6_pull_catchup's
        # neighbour block. A merge on a protected branch is gated.
        is_merge=1
        # v3.0.3 (finding 62 fixture round): the merge arm never asked the
        # classifier. The VERDICT was right by luck — a merge on a protected
        # branch is gated whatever the argv — but the DISCRIMINATOR named the
        # merge rule when the reason was an inline `-c`, and a 2 from the wrong
        # discriminator passes without testing what it claims. Scoped inside
        # gc_on_main so a resolving global on a FEATURE-branch merge stays
        # allowed, as the A6.9 row asserts.
        # v3.0.3 defect 2 — this `-c` classifier runs AFTER `$repo` has already
        # been resolved by gc_repo_for (it is INSIDE the `gc_on_main "$repo"`
        # scope above), and it is left there BY DESIGN: it is
        # protected-branch-conditioned, not a `-C`-resolution question, so
        # moving it earlier would change what it answers. The unresolvable-`-C`
        # case is refused earlier, unconditionally, by a6_deny_unresolved_c.
        A6_KIND=merge
        a6g=$(gc_global_options "$seg")
        case "$a6g" in
          refuse:*) A6_KIND=global; A6_GLOBAL="${a6g#refuse:}" ;;
          env:*)    A6_KIND=global; A6_GLOBAL="${a6g#env:}=" ;;
        esac
        A6_TARGET=$(a6_nonflag "$margs" | head -1)
        A6_SEG=$seg
        A6_ARGS=$margs
        CWD="$repo"
        break
      elif [ -n "${A6_MOVE_TARGET:-}" ] && gc_branch_is_gate_checked "$repo" "$A6_MOVE_TARGET"; then
        # I1 fix wave: a checkout earlier in this same command landed on a
        # gate-checked branch. The branch this merge lands on is not one this
        # hook can read from ambient state (CWD is still pre-checkout) -- same
        # cannot-determine as the protected `moved` arm above, and it MUST
        # route through A6_KIND=moved, never =gatechecked. Routing it to the
        # gatechecked arm would set is_merge=1 and fall through to the
        # artifact-freshness check with $CWD still on the PRE-checkout branch:
        # a fresh artifact THERE (the common case -- gate the feature branch,
        # then checkout the session branch and merge) would satisfy the
        # comparison and emit a green receipt for a merge that lands somewhere
        # else entirely. The existing moved message already tells the operator
        # to split the call, which is the correct answer here too.
        is_merge=1
        A6_KIND=moved
        A6_MOVED_VERB=merge
        A6_SEG=$seg
        A6_ARGS=$margs
        CWD="$repo"
        break
      elif gc_branch_is_gate_checked "$repo" "$(gc_current_branch "$repo")"; then
        # Task 2.7 (companion spec): checked AFTER gc_on_main, deliberately --
        # a branch declared in BOTH `**Protected branches**:` and
        # `**Gate-checked branches**:` must still get the protected refusal
        # above, never this weaker one. This arm never sets A6_KIND to
        # anything the block-message case statements below recognise, so it
        # falls through to the plain artifact-freshness check untouched by
        # any A6 protected-branch refusal -- and a PUSH to this same branch
        # is never routed through gc_branch_is_gate_checked at all, so it
        # stays ungated (spec req. 2).
        is_merge=1
        A6_KIND=gatechecked
        A6_SEG=$seg
        A6_ARGS=$margs
        CWD="$repo"
        break
      fi
    fi

    # 2b. v3.0.1 item 3: `git pull` while the checkout is on a protected branch.
    #
    # NO DISCRIMINATOR HERE, and that is a measured decision rather than a
    # shortcut. A pull FETCHES FIRST, so anything this hook resolves before the
    # command runs is an answer about a different object:
    #   - a CURRENT remote-tracking ref answers correctly (measured rc=0)
    #   - an ABSENT one is at least distinguishable (rc=128)
    #   - a STALE one answers CONFIDENTLY AND WRONGLY: `--is-ancestor` returns 0
    #     for the stale tip, the check reads "adds nothing", and the pull then
    #     lands every commit gained since. Resolve-after-fetch is too late, and
    #     refuse-when-unresolvable never fires because the ref does resolve.
    #   - `ls-remote` is ~1.4 s per PreToolUse call and FAILS OFFLINE, which for
    #     a fail-closed gate means "no pull on a protected branch without a
    #     network".
    #
    # So: gate every pull except the one form that cannot land foreign content.
    # Refspec-free means the target is the configured upstream by construction,
    # and --ff-only means git itself refuses if it is not a fast-forward. Both
    # are readable BEFORE the fetch, which is the constraint that defeated
    # everything else.
    #
    # NAMED RESIDUAL FALSE POSITIVE: a bare `git pull` on a cleanly
    # fast-forwardable protected branch is gated. That is deliberate — the
    # remedy is "use git pull --ff-only", a one-word fix and the safer command,
    # not a multi-minute gate run. Gating only refspec-bearing pulls would leave
    # bare `git pull` with divergent local history creating a real merge commit
    # on the protected branch, which is the topology A6 exists for.
    if gc_matches_subcommand "$seg" "pull"; then
      a6_deny_unresolved_c "$seg" "$base"
      repo=$(gc_repo_for "$seg" "$base")
      pargs=$(a6_args "$seg" "pull")
      # THE PROTECTED-BRANCH DECISION COMES FIRST (v3.0.2). The name comparison
      # in a6_pull_catchup reads THIS repo's config for THIS branch, so it is
      # only meaningful when the branch this hook can read is the branch the
      # pull lands on — i.e. not after a checkout, and only when protected.
      # Applying it to the branch-independent exemption below would gate
      # `git checkout main && git pull --ff-only` from a worktree whose feature
      # branch has no upstream, which v3.0.1 shipped as allowed on purpose.
      # NAMED RESIDUAL: a checkout onto a protected branch whose upstream config
      # is poisoned is still allowed through that exemption. Queued, not fixed
      # here — closing it needs the inverse (allowlist) shape.
      if [ "$moved" = 0 ] && gc_on_main "$repo"; then
        a6g=$(gc_global_options "$seg")
        if [ "$a6g" != ok ]; then
          is_merge=1
          A6_KIND=global
          case "$a6g" in
            refuse:*) A6_GLOBAL="${a6g#refuse:}" ;;
            env:*)    A6_GLOBAL="${a6g#env:}=" ;;
          esac
        elif [ "$mutated" != 0 ]; then
          is_merge=1
          A6_KIND=mutated
        elif a6_pull_catchup "$repo" "$pargs"; then
          continue
        else
          is_merge=1
          A6_KIND=pull
        fi
        A6_SEG=$seg
        A6_ARGS=$pargs
        CWD="$repo"
        break
      fi
      # Not on a protected branch as this hook reads it. The refspec-free
      # --ff-only form is allowed on ANY branch, so a preceding branch change
      # does not change its verdict — it stays out of the refusal.
      if [ "$(a6_nonflag_count "$pargs")" -eq 0 ] && a6_has_flag "$pargs" 'ff-only' \
         && [ "$mutated" = 0 ] && [ "$(gc_global_options "$seg")" = ok ]; then
        continue
      fi
      if [ "$moved" != 0 ]; then
        is_merge=1
        A6_KIND=moved
        A6_MOVED_VERB=pull
        A6_SEG=$seg
        A6_ARGS=$pargs
        CWD="$repo"
        break
      fi
    fi

    # 3. a push that targets a protected branch -- fast-forward merge by push
    #
    # THE TWO PUSH GATES KEY ON DIFFERENT THINGS, ON PURPOSE (v3.0.3, comment
    # only). This arm keys on the CURRENT branch being protected — the merge
    # gate guards what lands FROM here. hooks/no-push-main.sh keys on the push
    # TARGET — it guards the DESTINATION. Both catch push-main-from-main;
    # only no-push-main.sh catches push-to-main-from-a-feature-branch, verified.
    # That is not a gap in this arm: the target check has an owner.
    if gc_matches_subcommand "$seg" "push"; then
      a6_deny_unresolved_c "$seg" "$base"
      repo=$(gc_repo_for "$seg" "$base")
      # v3.0.2: the push half of the redirection defect is a FAIL-OPEN, not a
      # false positive. `git push origin 2>&1` on a protected branch gave
      # gc_has_refspec two non-flag tokens, so it read "destination named", the
      # current-branch check was skipped, and the push went through ungated.
      # The counting bug is inside gc_has_refspec in hooks/lib/git-cmd.sh;
      # compensating at the call site keeps that lib — and its ~90-minute
      # three-parser matrix — out of this fix.
      args=$(a6_strip_redir "$(gc_push_args "$seg")")
      # v3.0.2: a push FROM a protected branch whose remote was just re-pointed
      # (`git remote set-url`, `-c remote.origin.url=…`) sends this branch's
      # content somewhere this hook cannot see. Scoped to a protected branch on
      # purpose: gating every `git remote set-url && git push` on a feature
      # branch would be an over-correction on ordinary work.
      a6g=$(gc_global_options "$seg")
      if gc_on_main "$repo" && { [ "$a6g" != ok ] || [ "$mutated" != 0 ]; }; then
        is_merge=1
        A6_KIND=global
        case "$a6g" in
          refuse:*) A6_GLOBAL="${a6g#refuse:}" ;;
          env:*)    A6_GLOBAL="${a6g#env:}=" ;;
          ok)       A6_KIND=mutated ;;
        esac
        A6_SEG=$seg
        A6_ARGS=$args
        CWD="$repo"
        break
      fi
      if gc_targets_main_ref "$args" "$repo"; then
        is_merge=1
        A6_KIND=push
        A6_SEG=$seg
        A6_ARGS=$args
        CWD="$repo"
        break
      fi
      # A push with no refspec follows the current branch, which a preceding
      # checkout has changed. A push that NAMES its destination is immune by
      # construction — it never consults ambient state — and is not refused.
      if ! gc_has_refspec "$args" && ! gc_push_skips_branch_check "$args" && [ "$moved" != 0 ]; then
        is_merge=1
        A6_KIND=moved
        A6_MOVED_VERB=push
        A6_SEG=$seg
        A6_ARGS=$args
        CWD="$repo"
        break
      fi
      if ! gc_has_refspec "$args" && ! gc_push_skips_branch_check "$args" && gc_on_main "$repo"; then
        is_merge=1
        A6_KIND=push
        A6_SEG=$seg
        A6_ARGS=$args
        CWD="$repo"
        break
      fi
    fi

    # No arm fired on this segment, and it is neither inert nor tracked. It is a
    # mover: this gate cannot rule out that it changes what a LATER gated clause
    # resolves through. First one wins, so the DENY text names the earliest
    # unexplained clause rather than whichever one happened to be last.
    if [ "$a6cls" = mover ]; then
      mutated=1
      if [ -z "${A6_MUT_SEG:-}" ]; then
        A6_MUT_SEG=$seg
        A6_MUT_WHY=$A6_SEG_WHY
      fi
    fi
  done <<GC_SEGMENTS
$segments
GC_SEGMENTS

  [ "$is_merge" = "1" ] || exit 0
fi

REPO_TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_TOP" ]; then
  # Not a git checkout (nothing to gate against) — allow.
  exit 0
fi

# Read Gate command from PROJECT_CONTEXT.md through GC_KEY_PRE (see the header
# note on that constant in hooks/lib/git-cmd.sh: a leading UTF-8 BOM otherwise
# hides a key that sits on line 1).
# Tolerates: leading "- " / "* " list
# markers, the "**Gate Command**:" label style (java/python variants), and
# surrounding backticks — several variants write commands as `cmd`.
# v3.0.3 defect 3b — anchored at GC_KEY_PRE (same grammar the grep above
# uses), not a greedy `.*`: a value that itself contains the literal text
# `**Gate**:` a second time used to have everything up to THAT occurrence
# stripped too, truncating the extracted command instead of returning the
# whole original value.
GATE_CMD=$(grep -E "${GC_KEY_PRE}\*\*Gate( Command)?\*\*:" "$REPO_TOP/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*Gate( Command)?\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1)

# No-op: no PROJECT_CONTEXT.md or no Gate command configured
if [ -z "$GATE_CMD" ]; then
  exit 0
fi

# No-op: placeholder not yet filled in
case "$GATE_CMD" in
  *\{\{*\}\}*) exit 0 ;;
esac

# v2.4.0 (A6, found live merging PR #75) -- THE ARTIFACT CHECK BELOW COMPARES
# THE ARTIFACT TO **HEAD**, AND HEAD IS THE MERGE CONTENT ONLY ON SOME
# TOPOLOGIES. A controller sitting on `main` merges the PR BRANCH's tip, not
# HEAD; so "re-run the gate on the current head" gates content that is already
# merged, writes a green artifact, and then permits a merge of entirely
# different content -- a correct guard whose own remediation manufactures the
# false receipt. A flow of branch -> commit -> gate -> push -> PR -> merge has
# the controller ON the branch, where `artifact.sha == branch tip == merged
# content` and the same check is sound. Same hook, opposite validity.
#
# The split is purely local -- no API, no network, and both helpers are already
# in this hook: if the merge is initiated while HEAD is on a PROTECTED branch,
# the thing being merged is BY CONSTRUCTION not HEAD, so the comparison below
# verifies nothing. Say so and refuse; on a feature branch, fall through to the
# comparison, which is meaningful there.
#
# DELIBERATELY NOT UNCONDITIONALLY FAIL-CLOSED. Target resolvability varies by
# path: `git merge <ref>` resolves locally, the MCP merge tools carry
# pullNumber/owner/repo (identified, but needing an API call), and a bare
# `gh pr merge` names no target at all. Refusing on every unresolvable path
# hard-blocks every PR merge for anyone offline -- the "unconditional
# fail-closed blocks everything" hazard from the GC_CMD round. The
# protected-branch detector is what makes cannot-determine-must-refuse
# affordable here: it refuses exactly the case where the evidence is known to
# be irrelevant, and stays out of the way otherwise.
# v3.0.1 (consumer report): a branch change PRECEDES the gated operation in this
# same command, so the branch it lands on is not the branch this hook can read.
# Refused before the artifact is compared — running that comparison under a
# premise the command invalidates is what produced a green receipt for an
# unchecked merge.
if [ "$A6_KIND" = "moved" ]; then
  {
    if [ "$moved" = 1 ]; then
      # Task 2.6 (penumbra's sentence, verbatim): name the checkout clause
      # that has not run yet, rather than the vaguer "an earlier clause ...
      # checks out a PROTECTED branch" -- the reader needs the exact clause
      # to split out, not just the fact that one exists.
      echo "refused: ${A6_MOVED_VERB:-operation} evaluated on branch '$(gc_current_branch "$CWD")' — the '${A6_MOVE_SEG}' earlier in this call has not run when this hook fires; split the call: checkout first, then ${A6_MOVED_VERB:-the operation} alone."
    else
      echo "BLOCKED: gate-before-merge cannot determine which branch this operation lands on."
    fi
    echo "  branch now:      $(gc_current_branch "$CWD")  (as this hook sees it, BEFORE the command runs)"
    echo "  protected set:   $(gc_protected_branches "$CWD" 2>/dev/null || true)"
    echo "  branch change:   ${A6_MOVE_SEG}"
    echo "  change target:   ${A6_MOVE_TARGET:-<none named, or not a plain ref name>}$([ "$moved" = 1 ] && echo '  (protected)' || echo '  (unresolvable here)')"
    echo "  matched segment: ${A6_SEG}"
    echo "  discriminator:   the branch change comes FIRST, so every branch-derived input to this gate is the pre-change value. The change TARGET is read from the command instead, because that is an argument rather than state the command can move."
    echo "Could not determine: this hook runs before the command, and the command moves HEAD before the gated clause executes. Letting it through would not merely skip a check — the gate would compare its artifact against THIS checkout's HEAD and pass, emitting a green receipt for content it never examined."
    echo "ALLOWED without a gate run: run the two as SEPARATE calls — do the checkout, then issue the merge/pull/push on its own, where this gate can see the branch it lands on. A gated operation placed BEFORE the branch change is also fine ('gh pr merge …; git checkout main; git pull --ff-only'), and a push that NAMES its destination ('git push origin <branch>') never consults the current branch at all, so it is immune to this by construction."
    echo "(Last resort, and an admission of this guard's limits rather than a remedy: create '.claude/git-guard-off' under this cwd, make the one operation, then delete it.)"
  } >&2
  exit 2
fi

if gc_on_main "$CWD"; then
  # v3.0.1 item 5: DIAGNOSIS AND FIX, NOT ARGUMENT. The rationale for the
  # refusal lives in the comment block above, where the next editor will read
  # it; at block time nobody reads an argument. What a blocked consumer needs is
  # the configuration they are subject to, the segment that fired, the inputs
  # the decision used, the limit of what the hook can see, and — before the
  # escape hatch, never after it — the form that WOULD be allowed. Whichever of
  # those last two is read first is the one that gets used, and the toolkit has
  # plenty of hooks that name only the bypass.
  a6_upstream_facts "$CWD"
  A6_BRANCH=$(gc_current_branch "$CWD")
  A6_PROT=$(gc_protected_branches "$CWD")
  {
    echo "BLOCKED: gate-before-merge refuses this operation on a protected branch."
    echo "  branch:          $A6_BRANCH"
    echo "  protected set:   ${A6_PROT:-<empty>}  (set '- **Protected branches**:' in PROJECT_CONTEXT.md; 'none' protects nothing)"
    echo "  matched segment: ${A6_SEG:-<GitHub merge tool payload — no command string>}"
    echo "  upstream:        ${A6_UP:-<none configured for this branch>}${A6_UPSHA:+ ($A6_UPSHA)}"
    echo "  HEAD:            ${A6_HEADSHA:-<unresolvable>}"
    case "$A6_KIND" in
      merge)
        echo "  discriminator:   target ref: ${A6_TARGET:-<none named, or more than one>}"
        echo "                   --ff-only: $(a6_has_flag "$A6_ARGS" 'ff-only' && echo present || echo absent)   --no-ff/--squash: $(a6_has_flag "$A6_ARGS" 'no-ff|squash' && echo present || echo absent)"
        echo "                   verdict: a merge on a protected branch is gated unconditionally as of v3.0.2. The catch-up exemption was deleted: it resolved the target by NAME and trusted its VALUE, and 'git update-ref refs/remotes/origin/main <sha>' is ungated — measured landing never-gated content here with every config read honest."
        ;;
      pull)
        echo "  discriminator:   refspec/remote named: $([ "$(a6_nonflag_count "$A6_ARGS")" -eq 0 ] && echo absent || echo "present ($(a6_nonflag "$A6_ARGS" | tr '\n' ' '))")"
        echo "                   --ff-only: $(a6_has_flag "$A6_ARGS" 'ff-only' && echo present || echo absent)"
        echo "                   name comparison: ${A6_PULL_WHY:-<not reached>}"
        echo "                   verdict: not one of the two pull forms whose target can be proved by NAME before the fetch."
        ;;
      global)
        echo "  discriminator:   global option '$A6_GLOBAL' before the subcommand changes what this command RESOLVES to (config, repo, ref namespace, or binaries), or is unknown to this gate."
        echo "                   verdict: refused. Allowed globals: -C <path>, --no-pager, -P, --paginate, --no-optional-locks, --literal-pathspecs, --no-lazy-fetch."
        echo "                   inline config in particular can re-point remote.*.url / branch.*.remote for this one command: measured landing foreign content on a protected branch in a SINGLE clause, while branch.<branch>.merge on disk stayed honest."
        ;;
      mutated)
        echo "  discriminator:   earlier clause: ${A6_MUT_SEG}"
        echo "                   clause class: mover (not inert, not tracked) — ${A6_MUT_WHY:-it is not on either allowlist}"
        # v3.0.3: this line used to ASSERT that the clause changes what the
        # gated clause resolves through. Under the three-category rule the arm
        # also fires for clauses the gate has no evidence about at all, so the
        # honest claim is the one the gate can actually make: it could not prove
        # the clause inert. A DENY line claiming more than the gate knows is the
        # same defect as a 2 from the wrong discriminator.
        echo "                   verdict: this gate could not prove that clause harmless, and it runs BEFORE the command, so it cannot observe what the clause does to the configuration or refs this clause resolves through."
        ;;
      *)
        echo "  discriminator:   not applicable — this operation names no local ref that can be resolved before it runs."
        ;;
    esac
    # v3.0.3 item 2: when the MATCHED clause was refused for a reason the arm
    # above cannot name — a substitution, a redirection operand, or a pipe that
    # can consume and execute it — say so. Without this a `echo $(git merge …)`
    # refusal reads as an ordinary merge refusal and the fixture asserting the
    # arm would pass on the wrong discriminator.
    case "${A6_SEG_WHY:-}" in
      *"command substitution"*|*"redirection operand"*|*"pipe elsewhere"*|*"pipe's later stage"*)
        echo "                   clause class: mover (not inert, not tracked) — ${A6_SEG_WHY}" ;;
    esac
    echo "Could not determine: this check runs before any fetch, so the CONTENT of a remote target ref — what a fetch would bring in — is unknown to it. It decides on the FORM of the command and on refs that already exist locally. If your case is one only the content would settle, the hook cannot see it."
    case "$A6_KIND" in
      merge)
        echo "ALLOWED without a gate run: to CATCH THIS BRANCH UP to its upstream (${A6_UP:-none configured}), use 'git pull --ff-only' — not 'git merge --ff-only origin/main'. The pull FETCHES FIRST, so it re-reads the tracking ref from the remote instead of trusting whatever is in .git/refs; that is the whole difference, and it is why the merge form is no longer exempt. Also always allowed: 'git merge --abort', '--continue', '--quit'."
        echo "Otherwise: gate the content that is actually being merged — check out the merge target (the PR branch head), run 'bash hooks/run-gate.sh' there, and merge from that checkout."
        ;;
      pull)
        echo "ALLOWED without a gate run: 'git pull --ff-only' with no remote and no refspec, when this branch's own config names itself (branch.$A6_BRANCH.remote set, branch.$A6_BRANCH.merge = refs/heads/$A6_BRANCH); or 'git pull --ff-only <remote> $A6_BRANCH', which names the same thing explicitly. Either way git itself refuses if the result would not be a fast-forward."
        ;;
      global)
        echo "ALLOWED without a gate run: re-run this command WITHOUT the '$A6_GLOBAL' option — the same command with '$A6_GLOBAL' removed. Set the option in your configuration instead, in a separate call where a reader can see it; this gate compares configuration by NAME, so an inline override is exactly what it cannot see."
        ;;
      mutated)
        echo "ALLOWED without a gate run: run the two as SEPARATE calls — make the configuration or ref change first, then issue the pull/merge/push on its own, where this gate reads the configuration the operation will actually use."
        ;;
      *)
        echo "ALLOWED without a gate run: nothing on this path — a merge run from a protected branch lands the OTHER side's content, not this checkout's HEAD, so comparing the gate artifact to HEAD verifies nothing and re-gating here would only bless content that is already merged."
        echo "Do this instead: check out the merge target (the PR branch head), run 'bash hooks/run-gate.sh' there, and merge from that checkout — or gate on the branch before opening the PR."
        ;;
    esac
    echo "(Last resort, and an admission of this guard's limits rather than a remedy: create '.claude/git-guard-off' under this cwd, make the one operation, then delete it.)"
  } >&2
  exit 2
fi

# HEAD_SHA/HEAD_TREE computed here (moved up from below, v4.0.1 addendum to
# item 17): the exact-filename lookup needs the candidate sha before it can
# build a path.
HEAD_SHA=$(git -C "$CWD" rev-parse --verify HEAD^{commit} 2>/dev/null)
HEAD_TREE=$(git -C "$CWD" rev-parse 'HEAD^{tree}' 2>/dev/null)

# CANDIDATE SHA AS A PATH SEGMENT (v4.0.1 addendum to item 17): HEAD_SHA came
# ONLY from `git rev-parse --verify ...^{commit}` above, never from command
# text, but it is about to be interpolated into a filename — validate its
# SHAPE before that happens. A `rev-parse` failure (empty output) or anything
# that is not bare lowercase hex is treated as "no exact candidate": the
# lookup below falls through to the tree scan rather than building a path
# from a value that did not pass validation.
HEAD_SHA_PATH=""
if printf '%s' "$HEAD_SHA" | grep -qE '^[0-9a-f]{7,40}$'; then
  HEAD_SHA_PATH="$HEAD_SHA"
fi

GATE_DIR=$(gc_gate_dir "$CWD" 2>/dev/null)
ARTIFACT=""

# 1. Exact filename for the candidate sha (fast path — the common case: the
#    gate ran on this exact commit).
if [ -n "$GATE_DIR" ] && [ -n "$HEAD_SHA_PATH" ] && [ -f "$GATE_DIR/last-pass.$HEAD_SHA_PATH.json" ]; then
  ARTIFACT="$GATE_DIR/last-pass.$HEAD_SHA_PATH.json"
fi

# 2. Tree scan, newest mtime first, skipping `*.tmp` (an in-progress atomic
#    write from hooks/run-gate.sh — never a finished artifact). SAME TREE,
#    DIFFERENT SHA is a pinned decision (R20, v4.0.1): this deliberately
#    blesses a commit whose own sha never had a gate run when another commit
#    — a different worktree, a cherry-pick, a message-only rebase — gated the
#    identical tree, because the tree is what was actually tested (item 15).
if [ -z "$ARTIFACT" ] && [ -n "$GATE_DIR" ] && [ -d "$GATE_DIR" ]; then
  GBM_NAMES=$(cd "$GATE_DIR" && ls -1t -- last-pass.*.json 2>/dev/null)
  while IFS= read -r gbm_name; do
    [ -n "$gbm_name" ] || continue
    case "$gbm_name" in *.tmp) continue ;; esac
    gbm_f="$GATE_DIR/$gbm_name"
    gbm_tree=$(grep -o '"tree"[[:space:]]*:[[:space:]]*"[^"]*"' "$gbm_f" 2>/dev/null | head -1 | sed 's/.*"tree"[[:space:]]*:[[:space:]]*"//;s/"$//')
    if [ -n "$gbm_tree" ] && [ "$gbm_tree" = "$HEAD_TREE" ]; then
      ARTIFACT="$gbm_f"
      break
    fi
  done <<GBM_NAMES
$GBM_NAMES
GBM_NAMES
fi

# 3. Legacy pre-4.0.1 location, one release only.
if [ -z "$ARTIFACT" ] && [ -f "$REPO_TOP/.gate/last-pass.json" ]; then
  echo "NOTE: legacy artifact path $REPO_TOP/.gate (pre-4.0.1); re-run hooks/run-gate.sh to migrate" >&2
  ARTIFACT="$REPO_TOP/.gate/last-pass.json"
fi

if [ -z "$ARTIFACT" ]; then
  echo "BLOCKED: No gate artifact found. Run 'bash hooks/run-gate.sh' on the PR branch head (green gate writes <common git dir>/gate/last-pass.<sha>.json), then merge." >&2
  exit 2
fi

# v2.4.0 (A6, consumer report): READ THE ARTIFACT AS JSON, NOT AS ONE SPELLING
# OF IT. Until now both reads were hardcoded to `"sha":"` — exactly the bytes
# our own run-gate.sh printf emits, with no space after the colon. A consumer
# whose project-owned gate writes the same fields pretty-printed
# (`{"sha": "…"}`, entirely valid JSON) got an EMPTY ARTIFACT_SHA and was
# blocked on every merge with `artifact sha: none` — a gate that passed,
# reported as stale, for a reason that has nothing to do with staleness. They
# carried a local widening for releases.
#
# REPLACING run-gate.sh WHOLESALE IS A SUPPORTED CONFIGURATION: the contract
# between the two halves is the `**Gate**` field plus the artifact FORMAT, not
# the script. So the reader must accept any valid JSON spelling of that format,
# not merely the one our writer happens to produce.
#
# THE `sed` IS WIDENED IN LOCKSTEP WITH THE `grep`, deliberately. Accepting
# `"sha": "` in the grep while stripping only `"sha":"` in the sed leaves a
# LEADING SPACE on the value — a sha that matches nothing, i.e. the same silent
# mismatch moved one step later and made harder to see. Both use the same
# tolerant pattern for that reason; do not "simplify" one of them.
ARTIFACT_SHA=$(grep -o '"sha"[[:space:]]*:[[:space:]]*"[^"]*"' "$ARTIFACT" | head -1 | sed 's/.*"sha"[[:space:]]*:[[:space:]]*"//;s/"$//')
ARTIFACT_TREE=$(grep -o '"tree"[[:space:]]*:[[:space:]]*"[^"]*"' "$ARTIFACT" | head -1 | sed 's/.*"tree"[[:space:]]*:[[:space:]]*"//;s/"$//')
# HEAD_SHA/HEAD_TREE: computed above, before the artifact lookup (v4.0.1
# addendum to item 17) — not recomputed here.

# v2.1.3 fix round 1 (Critical 2 / penumbra #2c): accept either a sha match
# (the classic case: gate ran on this exact commit) or a tree match (the
# pre-commit-test.sh -> run-gate.sh chain: the gate ran against the INDEX
# just before `git commit`, so its "sha" is the PARENT commit but its "tree"
# is the tree the new commit just got). Both still gated by the freshness
# window below.
if [ -z "$ARTIFACT_SHA" ] || { [ "$ARTIFACT_SHA" != "$HEAD_SHA" ] && { [ -z "$ARTIFACT_TREE" ] || [ "$ARTIFACT_TREE" != "$HEAD_TREE" ]; }; }; then
  # v2.4.0 (A6, defect 1): the MESSAGE had drifted to the weaker half of the
  # check. The artifact matches by sha OR by tree, and the tree half is the one
  # that survives a squash -- a consumer measured a squash changing the sha and
  # leaving the tree byte-identical -- so a message naming only the sha sends
  # them to re-run a gate whose tree already matched. It also said "the current
  # head" without saying WHICH head, which is exactly the ambiguity that makes
  # gating `main` look like a remedy. The message is what a consumer acts on at
  # 2am; report both keys and name the head.
  {
    if [ "$A6_KIND" = "gatechecked" ]; then
      # Task 2.7 (companion spec req. 4): name the branch as gate-checked, not
      # protected -- "protected branch" on a session branch sends the operator
      # to the wrong PROJECT_CONTEXT.md line. Both keys are still reported
      # (defect 1 precedent): the tree key is the half that survives a squash.
      echo "BLOCKED: $(gc_current_branch "$CWD") is gate-checked (PROJECT_CONTEXT.md **Gate-checked branches**); artifact sha ${ARTIFACT_SHA:-none} does not match HEAD $HEAD_SHA"
    else
      echo "BLOCKED: Gate artifact is stale — it matches this checkout by neither key."
    fi
    echo "  artifact sha:  ${ARTIFACT_SHA:-none}    HEAD:          $HEAD_SHA"
    echo "  artifact tree: ${ARTIFACT_TREE:-none}    HEAD^{tree}:   $HEAD_TREE"
    echo "Run 'bash hooks/run-gate.sh' on the head that is actually being MERGED — the PR branch tip, from a checkout of that branch — then merge from there. Gating some other head produces a fresh artifact that verifies nothing."
  } >&2
  exit 2
fi

# Freshness: artifact file mtime < GC_GATE_TTL_S (hooks/lib/git-cmd.sh; 3600s
# = 60 minutes, mtime avoids date-parsing portability issues). Applies
# uniformly to whichever arm selected $ARTIFACT above -- exact-sha, tree scan,
# or the legacy fallback all flow through this same check (v4.0.1 addendum to
# item 17): the legacy path is a REASSIGNMENT of $ARTIFACT above, not a
# separate early exit, specifically so it cannot skip freshness.
# a13_first_diff_label <old pipe-joined label=value text> <new ...> -- prints
# the label (text before the first `=`) of the first segment that differs
# between the two, comparing positionally (gc_gate_env -v always emits
# pyvenv, dist, py, node in that fixed order, pipe-joined). Empty if none
# differ. v4.0.3 item 13.
a13_first_diff_label() {
  local old="$1" new="$2" oldlines newlines n i oline nline
  oldlines=$(printf '%s' "$old" | tr '|' '\n')
  newlines=$(printf '%s' "$new" | tr '|' '\n')
  n=$(printf '%s\n' "$oldlines" | wc -l)
  i=1
  while [ "$i" -le "$n" ]; do
    oline=$(printf '%s\n' "$oldlines" | sed -n "${i}p")
    nline=$(printf '%s\n' "$newlines" | sed -n "${i}p")
    if [ "$oline" != "$nline" ]; then
      printf '%s\n' "${oline%%=*}"
      return 0
    fi
    i=$((i + 1))
  done
}

# a13_first_absent_label <pipe-joined label=value text> -- prints the label
# (text before the first `=`) of the first segment whose value is literally
# "absent", or nothing when none is. v4.1.1 (#15): checked on BOTH the
# artifact's stored env_detail and the freshly-computed current one, before
# the aggregate-hash comparison below ever runs -- a cannot-determine
# contributor must void the extension even when it happens to sit inside a
# MATCHING aggregate (two "absent" values for the same contributor hash
# equal, and would otherwise read as "unchanged").
a13_first_absent_label() {
  local detail="$1" lines n i line
  lines=$(printf '%s' "$detail" | tr '|' '\n')
  n=$(printf '%s\n' "$lines" | wc -l)
  i=1
  while [ "$i" -le "$n" ]; do
    line=$(printf '%s\n' "$lines" | sed -n "${i}p")
    case "$line" in
      *=absent)
        printf '%s\n' "${line%%=*}"
        return 0
        ;;
    esac
    i=$((i + 1))
  done
}

ARTIFACT_EPOCH=$(stat -c %Y "$ARTIFACT" 2>/dev/null || stat -f %m "$ARTIFACT" 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
AGE=$((NOW_EPOCH - ARTIFACT_EPOCH))
if [ "$ARTIFACT_EPOCH" -eq 0 ] || [ "$AGE" -gt "$GC_GATE_TTL_S" ]; then
  # v4.0.3 item 13 -- an artifact past the ordinary TTL is still accepted up
  # to the prune window (GC_GATE_PRUNE_S, hooks/lib/git-cmd.sh: ONE expression
  # derived from GC_GATE_TTL_S) when its tree equals HEAD^{tree} AND its
  # environment fingerprint ("env", hooks/run-gate.sh) still matches the
  # CURRENT one (gc_gate_env "$REPO_TOP"). A tree that has not moved is the
  # stronger binding for the code; the tree is NOT the environment (reviewer:
  # server/.venv can be reinstalled between two runs against an
  # tree-identical checkout), hence the additional fingerprint requirement.
  # FAILS CLOSED (advisor): if gc_gate_env cannot compute (no sha256 backend,
  # or the artifact carries no "env" at all -- an older writer, or a sha-only
  # artifact with no "tree") the extension does NOT apply; the ordinary
  # 3600s-TTL block below fires as before.
  A13_ALLOW=0
  A13_LABEL=""
  if [ "$ARTIFACT_EPOCH" -ne 0 ] && [ -n "$ARTIFACT_TREE" ] && [ "$ARTIFACT_TREE" = "$HEAD_TREE" ] && [ "$AGE" -le "$GC_GATE_PRUNE_S" ]; then
    ARTIFACT_ENV=$(grep -o '"env"[[:space:]]*:[[:space:]]*"[^"]*"' "$ARTIFACT" | head -1 | sed 's/.*"env"[[:space:]]*:[[:space:]]*"//;s/"$//')
    if [ -n "$ARTIFACT_ENV" ]; then
      CURRENT_ENV=$(gc_gate_env "$REPO_TOP" 2>/dev/null) || CURRENT_ENV=""
      if [ -n "$CURRENT_ENV" ]; then
        ARTIFACT_ENV_DETAIL=$(grep -o '"env_detail"[[:space:]]*:[[:space:]]*"[^"]*"' "$ARTIFACT" | head -1 | sed 's/.*"env_detail"[[:space:]]*:[[:space:]]*"//;s/"$//')
        CURRENT_DETAIL=$(gc_gate_env "$REPO_TOP" -v 2>/dev/null | tr '\n' '|')
        # v4.1.1 (#15): ANY contributor reading "absent" on EITHER side voids
        # the extension outright -- checked BEFORE the aggregate comparison,
        # so a matching aggregate built from two "absent" values on the same
        # contributor never masks it. Polarity from the spec (§4.3): names no
        # language, a node-only consumer earns the extension back by adding
        # contributors that do not read absent.
        # v4.1.2: a no-venv repo now reads pyvenv=sys:<hash> and becomes
        # ELIGIBLE; the void rule itself is unchanged.
        A13_ABSENT=""
        [ -n "$ARTIFACT_ENV_DETAIL" ] && A13_ABSENT=$(a13_first_absent_label "$ARTIFACT_ENV_DETAIL")
        [ -n "$A13_ABSENT" ] || A13_ABSENT=$(a13_first_absent_label "$CURRENT_DETAIL")
        if [ -n "$A13_ABSENT" ]; then
          A13_LABEL="${A13_ABSENT}=absent (cannot-determine voids the tree+env extension)"
        elif [ "$ARTIFACT_ENV" = "$CURRENT_ENV" ]; then
          A13_ALLOW=1
        else
          # Tree matched but the aggregate differs -- name WHICH contributor,
          # using env_detail (the per-contributor breakdown; see run-gate.sh's
          # header note on why this is an addition beyond the brief's "env"-
          # only wording: a single aggregate hash cannot name a part alone).
          [ -n "$ARTIFACT_ENV_DETAIL" ] && A13_LABEL=$(a13_first_diff_label "$ARTIFACT_ENV_DETAIL" "$CURRENT_DETAIL")
        fi
      fi
    fi
  fi
  if [ "$A13_ALLOW" -eq 1 ]; then
    echo "NOTE: gate artifact accepted on tree identity: ${AGE}s exceeds the ${GC_GATE_TTL_S}s TTL, tree and environment unchanged" >&2
  else
    if [ -n "$A13_LABEL" ]; then
      echo "BLOCKED: Gate artifact expired (${AGE}s old, max ${GC_GATE_TTL_S}s); tree unchanged, environment changed: $A13_LABEL" >&2
    else
      echo "BLOCKED: Gate artifact expired (${AGE}s old, max ${GC_GATE_TTL_S}s). Re-run 'bash hooks/run-gate.sh', then merge." >&2
    fi
    exit 2
  fi
fi

# v4.0.1 (item 15): name which arm of the sha-or-tree check above actually
# matched. sha is the classic case (the gate ran on this exact commit); tree
# is EITHER the pre-commit-test.sh -> run-gate.sh chain (the gate ran against
# the INDEX just before `git commit`, so the artifact's sha is the commit's
# PARENT but its tree already equals HEAD^{tree}) OR the same-tree-different-
# sha case above (R20, v4.0.1 addendum) -- sha is checked first, so a sha
# match takes the label even when tree also happens to match. Names the
# matched FILE too (v4.0.1 addendum): the directory can hold several
# artifacts now, so "matched: tree" alone no longer says which one.
if [ -n "$ARTIFACT_SHA" ] && [ "$ARTIFACT_SHA" = "$HEAD_SHA" ]; then
  echo "matched: sha ($ARTIFACT)"
else
  echo "matched: tree ($ARTIFACT)"
fi

exit 0

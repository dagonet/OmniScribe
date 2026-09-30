#!/usr/bin/env bash
# deny-claude-md-writes.sh — PreToolUse hook: under a manifest v4 consumer,
# the repo-root CLAUDE.md is template-owned and the next sync overwrites it.
#
# Matcher: Edit|Write|MultiEdit|NotebookEdit
#
# v4.1 (spec docs/plans/2026-09-18-v4.1-design.md §6). Manifest v4 moves the
# project's own instructions out of CLAUDE.md's PROJECT-CUSTOM region and into
# `.claude/project-instructions.md` (a once-class file the sync server never
# overwrites). CLAUDE.md itself becomes template-owned: every sync replaces it
# wholesale. This hook is the mechanical half of that contract — a PO or agent
# who edits the root CLAUDE.md anyway loses the edit silently at the next
# sync, and this hook turns that into an immediate, explained refusal instead.
#
# DECISION RULE, AND ITS POLARITY (fail-CLOSED on cannot-determine):
#   tool_name not Edit/Write/MultiEdit/NotebookEdit  -> exit 0 (nothing to
#     read — the matcher already excludes other tools; this hook must never
#     deny on a call shape it cannot inspect)
#   path field absent/empty                          -> exit 0
#   normalised path != <root>/CLAUDE.md               -> exit 0 (a nested
#     CLAUDE.md, e.g. docs/CLAUDE.md, is not this hook's business)
#   no <root>/.claude/template-manifest.json          -> exit 0 (pre-v4
#     consumer, or the toolkit's own checkout — no manifest, no rule)
#   manifest present but its JSON does not parse      -> exit 2 (CANNOT
#     DETERMINE the version, so this hook refuses rather than guess allow —
#     the gate-design rule: a check that cannot answer must not clear the
#     call it cannot read)
#   manifest_version == 4                             -> exit 2 (DENY, the
#     message below)
#   manifest_version == 3, or anything else parseable -> exit 0 (allow —
#     manifest v3 has no `.claude/project-instructions.md` yet; the region in
#     CLAUDE.md is still the PO's legitimate surface)
#   no JSON parser on PATH / unparsable stdin          -> exit 2 (same
#     fail-closed posture as hooks/deny-secret-reads.sh: a call this hook
#     cannot inspect is not one it can clear)
#
# TWO HOOKS, ONE PATH (plan ruling R-L). hooks/enforce-delegation.sh keeps
# CLAUDE.md on the PO's main-thread write surface — under a v3 manifest that
# is still correct, the PROJECT-CUSTOM region lives there. Under a v4
# manifest the two project-level PreToolUse hooks disagree on the same path:
# enforce-delegation.sh's allow-side message still names CLAUDE.md; THIS hook
# denies. Both are registered; Claude Code runs every matching PreToolUse
# hook and a single "deny" wins over any "allow", so the deny always fires
# first in effect regardless of hook order. enforce-delegation.sh's comment,
# --help text and deny message were updated (R-L) to say CLAUDE.md is on the
# surface "pre-v4 manifests only", so its allow-side text stops teaching
# something false once a consumer migrates.
#
# SCOPE (spec §6, reviewer D5): this is a guardrail on the editing TOOLS, not
# a filesystem boundary. A Bash write (`sed -i CLAUDE.md`, `>> CLAUDE.md`, a
# heredoc) is not seen by this hook at all — deny-secret-reads.sh's Bash arm
# has a verb model for exactly this reason, and this hook does not reuse it,
# because unlike a secret a rewritten CLAUDE.md is not exfiltration; it is
# simply undone by the next sync. Never described as "CLAUDE.md cannot be
# written" — only that the editing tools refuse it and say where to write
# instead.
#
# REGISTRATION SCOPE: project-level only. Registered in every template's
# `.claude/settings.json`; NOT mirrored into `user-level-reference/hooks/`
# and NEVER registered in `~/.claude/settings.json` (HOOKS_NO_MIRROR in
# scripts/verify-template-consistency.sh carries the reason) — a user-level
# copy would refuse CLAUDE.md writes in every non-consumer repo on the
# machine, including this toolkit's own checkout and every project that has
# never run `/sync-template` at all.
#
# PATH NORMALISATION. hooks/lib/git-cmd.sh has no public repo-root helper (its
# gccc_norm is an internal two-letter-prefixed helper local to the -C
# resolver, and it only maps backslash -> forward slash — no drive-letter
# case fold), so this hook resolves the root directly with `git -C`, the same
# call hooks/enforce-delegation.sh already makes at its own line 292. dcm_norm
# below is the ONE normaliser for the payload path, the payload cwd AND the
# root (v4.1.1 #23) — relatives resolve against the normalised CWD, not the
# root (the pre-existing bug: cwd=<root>/docs + file_path=CLAUDE.md used to
# join against the ROOT and deny docs/CLAUDE.md, out of this hook's own
# stated scope). It folds backslash -> forward slash, the MSYS single-letter
# form (`/g/...`) -> drive form (`g:/...` — a real payload and a `git
# rev-parse --show-toplevel` result are known to disagree exactly here),
# collapses `.`/`..` segments (pure sh, no `realpath`: the path need not
# exist), and on MSYS/MinGW/Cygwin lower-cases the WHOLE path (Windows
# filesystem semantics — `claude.md` and `CLAUDE.md` name the same file, not
# only the drive letter).
#
# BASENAME PRE-FILTER (v4.1.1 #24). Measured ~634 ms per editing call before
# this: json_have/json_valid/json_get each probe/spawn node, python3 or jq —
# four interpreter spawns on EVERY Edit/Write/MultiEdit/NotebookEdit, whether
# or not the path is anywhere near CLAUDE.md. dcm_pre_path below reads the
# raw file_path/notebook_path value with grep ALONE (the same "has to work
# with no parser at all" reasoning as json_session, hooks/lib/json.sh) and a
# loose case-insensitive "ends in claude.md" check — deliberately not the
# brief's four-shape enumeration (slash/backslash x case), which has no
# case-insensitive-backslash member and so misses `C:\proj\claude.md`; a
# plain suffix check cannot have that gap, because it does not enumerate
# separators at all. False positives here (a real file that merely ends in
# "claude.md") just fall through to the precise check below, at the cost of
# one wasted parse — never a correctness issue. A payload whose path does
# not end in "claude.md" exits 0 here, before lib/json.sh's json_have ever
# runs, with no node/python3/jq spawned.

lib="$(dirname "$0")/lib/json.sh"
[ -f "$lib" ] || { echo "BLOCKED: $lib missing — run /sync-template step 6b (hooks/lib/json.sh)" >&2; exit 2; }
# shellcheck source=lib/json.sh
. "$lib"

# Slurp stdin with a bash builtin, not an external `cat` — the #24 fixture
# runs this hook with a PATH that carries nothing but the pre-filter's own
# tools (sh, git, sed, grep, head, tr), and a leak into `cat` would fail
# there for a reason that has nothing to do with json.sh.
IFS= read -r -d '' DCM_JSON <&0 || true

# dcm_pre_path <json> -- the raw file_path/notebook_path VALUE, extracted by
# grep alone (no JSON parser). Approximate on purpose: it exists only to
# decide whether the expensive, precise path below is worth running at all.
dcm_pre_path() {
  printf '%s' "$1" \
    | grep -Eo '"(file_path|notebook_path)"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | head -1 \
    | sed 's/^.*:[[:space:]]*"//;s/"$//'
}

# Fix round 1 (Class A): exit 0 here must be a POSITIVE determination -- a
# file_path/notebook_path value was actually read from stdin AND it does not
# end in "claude.md" (case-insensitive) -- never a default for "the cheap
# read found nothing". Two pre-existing fixtures caught the earlier version
# of this getting it backwards: with no JSON parser on PATH, and with
# unparseable stdin (`{"tool_name":"Edit",` -- no tool_input at all), grep
# finds no file_path/notebook_path KEY, DCM_PRE came back empty, and the old
# `case "" in *claude.md) ;; *) exit 0` fell into the `*)` arm and ALLOWED --
# exactly the cannot-determine case this hook must refuse, not clear. An
# EMPTY DCM_PRE now falls through to the full path below unconditionally,
# which fails closed exactly as it did before the pre-filter existed (parser
# check, json_valid, exit 2).
DCM_PRE=$(dcm_pre_path "$DCM_JSON")
if [ -n "$DCM_PRE" ]; then
  case "$(printf '%s' "$DCM_PRE" | tr 'A-Z' 'a-z')" in
    *claude.md) ;;                                  # might be CLAUDE.md -- fall through
    *) exit 0 ;;                                     # positive determination: not CLAUDE.md
  esac
fi

# dcm_norm <path> -- see the PATH NORMALISATION note above.
#
# Fix round 1 (Class B): the single-drive-letter MSYS fold (`/c/...` ->
# `c:/...`) only covers ONE MSYS mount shape. Git Bash's own `/tmp` is a
# DIFFERENT, named mount (`/tmp/xyz` -> `C:/Users/<user>/AppData/Local/
# Temp/xyz`, measured on this box) that the single-letter regex cannot
# express -- test-hooks.sh's own fixtures build their throwaway repos under
# system `mktemp -d`, which resolves under exactly this mount, so EVERY #23
# fixture's cwd/file_path silently failed to normalise to the same string
# `git rev-parse --show-toplevel` returns (always drive-letter form) and the
# comparison always missed. `cygpath -m` (already this file's second choice
# behind nothing -- test-hooks.sh's own `natpath` helper uses it for the same
# reason) understands every MSYS mount, not only the drive-letter ones, and
# is a pure lexical/mount-table rewrite -- confirmed on a non-existent path,
# so it does not violate "the normaliser must not require the path to
# exist". Applied ONLY to a string that already starts with `/`: a RELATIVE
# input (`./CLAUDE.md`, `docs/../CLAUDE.md`) must not be handed to `cygpath`
# before it is joined with the payload's own cwd, or it resolves against
# the HOOK PROCESS's cwd instead -- wrong base entirely. The single-letter
# regex stays as the fallback for a host with no `cygpath` (plain WSL/Linux).
dcm_norm() {
  dn=$(printf '%s' "$1" | tr '\\' '/')
  case "$dn" in
    /*)
      if command -v cygpath >/dev/null 2>&1; then
        dnc=$(cygpath -m -- "$dn" 2>/dev/null) && [ -n "$dnc" ] && dn="$dnc"
      fi
      ;;
  esac
  case "$dn" in
    /[A-Za-z]/*)
      dnl=$(printf '%s' "${dn#/}" | cut -c1 | tr 'A-Z' 'a-z')
      dn="$dnl:${dn#/?}"
      ;;
  esac
  case "$dn" in
    [A-Za-z]:/*)
      dnl=$(printf '%s' "${dn%%:*}" | tr 'A-Z' 'a-z')
      dn="$dnl:${dn#*:}"
      ;;
  esac
  while :; do
    dn2=$(printf '%s' "$dn" | sed 's#/\./#/#')
    if [ "$dn2" != "$dn" ]; then dn="$dn2"; continue; fi
    dn2=$(printf '%s' "$dn" | sed 's#/[^/]*/\.\./#/#')
    if [ "$dn2" != "$dn" ]; then dn="$dn2"; continue; fi
    break
  done
  # v4.1.2 #1: fold case when the FILESYSTEM is case-insensitive, probed once
  # against `.git` at the working-tree root -- it always exists there (a
  # directory in the main checkout, a file in a worktree), so `.GIT` resolving
  # answers the question with no missing-target case, on Windows, macOS
  # (APFS default) and a case-insensitive Linux mount alike. Replaces the
  # MINGW/MSYS/CYGWIN uname enumeration, which gave macOS no fold at all.
  # DCM_ROOT is computed once, above, from the payload cwd (falls back to the
  # cwd itself outside a git repo, matching the old branch's residual there).
  if [ -n "$DCM_ROOT" ] && [ -e "$DCM_ROOT/.GIT" ] && [ -e "$DCM_ROOT/.git" ]; then
    dn=$(printf '%s' "$dn" | tr 'A-Z' 'a-z')
  fi
  printf '%s' "$dn"
}

json_have || {
  echo "BLOCKED: deny-claude-md-writes: no JSON parser (node, python3 or jq) on PATH — this hook cannot inspect the call, and a call it cannot inspect is not one it can clear. Install one of the three." >&2
  exit 2
}
json_valid "$DCM_JSON" || {
  echo "BLOCKED: deny-claude-md-writes: hook payload did not parse — this hook cannot inspect the call. Report the payload; do not work around it." >&2
  exit 2
}

DCM_TOOL=$(json_get "$DCM_JSON" tool_name)

case "$DCM_TOOL" in
  Edit|Write|MultiEdit) DCM_FIELD=file_path ;;
  NotebookEdit)         DCM_FIELD=notebook_path ;;
  *) exit 0 ;;
esac

DCM_RAW=$(json_get "$DCM_JSON" "tool_input.$DCM_FIELD")
[ -n "$DCM_RAW" ] || exit 0

DCM_CWD=$(json_get "$DCM_JSON" cwd)
[ -n "$DCM_CWD" ] || DCM_CWD="."

DCM_ROOT=$(git -C "$DCM_CWD" rev-parse --show-toplevel 2>/dev/null)
[ -n "$DCM_ROOT" ] || DCM_ROOT="$DCM_CWD"

DCM_NCWD=$(dcm_norm "$DCM_CWD")
DCM_NCWD="${DCM_NCWD%/}"

DCM_NPATH=$(dcm_norm "$DCM_RAW")
case "$DCM_NPATH" in
  /*|[a-z]:/*) ;;                                   # already absolute
  *) DCM_NPATH=$(dcm_norm "$DCM_NCWD/$DCM_NPATH") ;; # relative -> under the CWD
esac

DCM_NROOT=$(dcm_norm "$DCM_ROOT")
DCM_NROOT="${DCM_NROOT%/}"
DCM_TARGET=$(dcm_norm "$DCM_NROOT/CLAUDE.md")

[ "$DCM_NPATH" = "$DCM_TARGET" ] || exit 0

DCM_MANIFEST="$DCM_NROOT/.claude/template-manifest.json"
[ -f "$DCM_MANIFEST" ] || exit 0

DCM_MCONTENT=$(cat "$DCM_MANIFEST" 2>/dev/null)
json_valid "$DCM_MCONTENT" || {
  echo "BLOCKED: deny-claude-md-writes: cannot determine manifest version: $DCM_MANIFEST" >&2
  exit 2
}

DCM_MVER=$(json_get "$DCM_MCONTENT" manifest_version)
[ "$DCM_MVER" = "4" ] || exit 0

echo "BLOCKED: deny-claude-md-writes: CLAUDE.md is template-owned under manifest v4 — write to .claude/project-instructions.md instead; the next sync overwrites CLAUDE.md" >&2
exit 2

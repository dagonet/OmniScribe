#!/usr/bin/env bash
# Native git pre-push hook (v4.3.2, design P2). Git runs this for EVERY push --
# typed in a terminal, run by a script, an alias, an IDE or a GUI client -- and
# hands it the refs that push will update, already resolved:
#   argv:  <remote name> <remote url>
#   stdin: <local ref> <local sha> <remote ref> <remote sha>   (one line per ref)
# A remote ref refs/heads/<b> with <b> protected is refused: create, update,
# force and delete (local sha all zeros) alike. Tags and every other ref pass.
# One refused line fails the hook, and git then lands NOTHING of that push.
#
# The protected set is the one hooks/no-push-main.sh enforces:
# gc_protected_branches (hooks/lib/git-cmd.sh) over the '- **Protected
# branches**:' line of the top-level PROJECT_CONTEXT.md -- CALLED, never
# re-implemented, so the two cannot drift (consistency check 69). No line, an
# empty value or a placeholder: main master (+ the remote's trunk). `none`:
# nothing.
#
# FAIL CLOSED: a missing or corrupt lib, no top-level, or a PROJECT_CONTEXT.md
# that exists but cannot be read refuses every push.
#
# The ONE deliberate escape is `git push --no-verify` (git skips this hook);
# .claude/git-guard-off is NOT honoured here. The server-side layer is a GitHub
# ruleset that requires a pull request (docs/templates.md).
#
# Not registered in settings.json: installed per clone as a shim in
# <git common dir>/hooks/pre-push by `bash hooks/git-pre-push.sh --install`.

# gpp_shim -- the file --install writes to <git common dir>/hooks/pre-push. It
# never changes between releases: the logic lives in the tracked
# hooks/git-pre-push.sh, which every sync updates. A checkout without that file
# (a branch older than v4.3.2) is REFUSED -- fail closed -- with the way out.
gpp_shim() {
  cat <<'GPP_SHIM'
#!/bin/sh
# claude-code-toolkit pre-push shim -- written by `bash hooks/git-pre-push.sh --install`; rewritten on every install, do not edit.
h="$(git rev-parse --show-toplevel 2>/dev/null)/hooks/git-pre-push.sh"
if [ ! -f "$h" ]; then
  echo "BLOCKED: pre-push: $h is missing (this checkout predates v4.3.2, or hooks/ was removed) -- push refused. Merge the trunk into this branch, or push deliberately with the --no-verify flag (git then skips this hook)." >&2
  exit 1
fi
exec bash "$h" "$@"
GPP_SHIM
}

# gpp_chain -- the line a user adds to a pre-push hook this installer must not
# overwrite. It saves stdin first: the user's own hook may read the refs too.
gpp_chain() {
  cat <<'GPP_CHAIN'
refs=$(cat); printf '%s\n' "$refs" | bash "$(git rev-parse --show-toplevel)/hooks/git-pre-push.sh" "$@" || exit 1
GPP_CHAIN
}

# gpp_real <path> <base> -- the physical (pwd -P) path of <path>, a relative one
# resolved against <base>; backslashes become '/', trailing slashes go. A last
# component that does not exist yet is the physical parent plus its name. Prints
# nothing on any failure (the caller then refuses -- never fails open).
gpp_real() {
  gr_p=$(printf '%s' "$1" | tr '\\' '/')
  while :; do case "$gr_p" in ?*/) gr_p=${gr_p%/} ;; *) break ;; esac; done
  case "$gr_p" in /*|[A-Za-z]:*) ;; *) gr_p="$2/$gr_p" ;; esac
  if [ -d "$gr_p" ]; then ( cd -P "$gr_p" 2>/dev/null && pwd -P ); return 0; fi
  gr_d=${gr_p%/*}; gr_n=${gr_p##*/}
  [ -n "$gr_d" ] && [ -n "$gr_n" ] && gr_d=$( cd -P "$gr_d" 2>/dev/null && pwd -P ) && [ -n "$gr_d" ] && printf '%s/%s\n' "${gr_d%/}" "$gr_n"
  return 0
}

# gpp_install [<dir>] -- 0 installed, 1 not installed (reason on stderr). Never
# overwrites a foreign hook, never writes where core.hooksPath makes git look
# elsewhere, and never points a shim at a top-level without this file (R-2).
gpp_install() {
  gi_top=$(git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null)
  if [ -z "$gi_top" ]; then
    echo "pre-push: not installed: ${1:-.} is not a git repository -- run 'bash hooks/git-pre-push.sh --install' after 'git init'." >&2
    return 1
  fi
  if [ ! -f "$gi_top/hooks/git-pre-push.sh" ]; then
    echo "pre-push: not installed: $gi_top/hooks/git-pre-push.sh is missing -- the hooks must live at the repository top-level." >&2
    return 1
  fi
  gi_common=$(git -C "$gi_top" rev-parse --git-common-dir 2>/dev/null)
  case "$gi_common" in /*|[A-Za-z]:*) ;; *) gi_common="$gi_top/$gi_common" ;; esac
  gi_hp=$(git -C "$gi_top" config --get core.hooksPath 2>/dev/null)
  # A hooksPath that resolves to the default <common dir>/hooks is the same as unset.
  if [ -n "$gi_hp" ]; then
    gi_a=$(gpp_real "$gi_hp" "$gi_top"); gi_b=$(gpp_real "$gi_common/hooks" "$gi_top")
    if [ -n "$gi_a" ] && [ "$gi_a" = "$gi_b" ]; then gi_hp=""; fi
  fi
  if [ -n "$gi_hp" ]; then
    { echo "pre-push: not installed: core.hooksPath is set ($gi_hp), so git ignores .git/hooks. Add this line as the FIRST line after the shebang of $gi_hp/pre-push; anything below it that reads the refs must read \"\$refs\" instead, e.g. printf '%s\\n' \"\$refs\" | git lfs pre-push \"\$@\":"; gpp_chain; } >&2
    return 1
  fi
  gi_dst="$gi_common/hooks/pre-push"
  if { [ -e "$gi_dst" ] || [ -L "$gi_dst" ]; } && [ "$(sed -n 2p "$gi_dst" 2>/dev/null)" != "$(gpp_shim | sed -n 2p)" ]; then
    { echo "pre-push: not installed: $gi_dst already exists and is not this toolkit's shim -- left untouched. Add this line as the FIRST line after the shebang; anything below it that reads the refs must read \"\$refs\" instead, e.g. printf '%s\\n' \"\$refs\" | git lfs pre-push \"\$@\":"; gpp_chain; } >&2
    return 1
  fi
  if mkdir -p "$gi_common/hooks" 2>/dev/null && gpp_shim > "$gi_dst.tmp.$$" 2>/dev/null &&
     chmod +x "$gi_dst.tmp.$$" 2>/dev/null && mv -f "$gi_dst.tmp.$$" "$gi_dst" 2>/dev/null; then
    echo "pre-push: installed $gi_dst (runs hooks/git-pre-push.sh on every push)"
    return 0
  fi
  rm -f "$gi_dst.tmp.$$" 2>/dev/null
  echo "pre-push: not installed: cannot write $gi_dst" >&2
  return 1
}

if [ "${1:-}" = --install ]; then gpp_install "${2:-.}"; exit $?; fi

gpp_lib="$(dirname "$0")/lib/git-cmd.sh"
if [ ! -f "$gpp_lib" ]; then
  echo "BLOCKED: pre-push: $gpp_lib missing -- the protected branches cannot be read, push refused. Run /sync-template (hooks/lib/git-cmd.sh), or push deliberately with the --no-verify flag (git then skips this hook)." >&2
  exit 1
fi
. "$gpp_lib"
command -v gc_protected_branches >/dev/null 2>&1 || {
  echo "BLOCKED: pre-push: $gpp_lib is present but corrupt (gc_protected_branches undefined) -- push refused." >&2
  exit 1
}

gpp_top=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$gpp_top" ]; then
  echo "BLOCKED: pre-push: cannot find the repository top-level -- push refused." >&2
  exit 1
fi
if [ -e "$gpp_top/PROJECT_CONTEXT.md" ] && [ ! -r "$gpp_top/PROJECT_CONTEXT.md" ]; then
  echo "BLOCKED: pre-push: $gpp_top/PROJECT_CONTEXT.md exists but cannot be read, so the protected branches are unknown -- push refused." >&2
  exit 1
fi
gpp_prot=$(gc_protected_branches "$gpp_top")

gpp_rc=0
while read -r gpp_lref gpp_lsha gpp_rref gpp_rsha; do
  case "$gpp_rref" in refs/heads/?*) gpp_b=${gpp_rref#refs/heads/} ;; *) continue ;; esac
  case " $gpp_prot " in *" $gpp_b "*) ;; *) continue ;; esac
  case "$gpp_lsha" in *[!0]*) gpp_what=push ;; *) gpp_what=delete ;; esac
  echo "BLOCKED: pre-push: $gpp_what of protected branch '$gpp_b' on remote '${1:-?}' refused (protected: $gpp_prot). Push a feature branch and open a PR; to push it deliberately, use the --no-verify flag (git then skips this hook)." >&2
  gpp_rc=1
done
exit "$gpp_rc"

#!/usr/bin/env bash
# deny-hang-shapes.sh -- PreToolUse(Bash): refuse three command shapes that
# hung unattended agents (v4.3.0, spec Part B): a heredoc written into a file,
# a sleep wait loop, and a leading `cd` followed by two or more commands (R-B).
# ADVISORY: a missing lib, no JSON parser, an unreadable payload or any doubt
# lets the call through (exit 0). Honours .claude/git-guard-off.
lib="$(dirname "$0")/lib/json.sh"
[ -f "$lib" ] || exit 0
# shellcheck source=lib/json.sh
. "$lib"
DH_JSON=$(cat)
json_have || exit 0
json_valid "$DH_JSON" || exit 0
DH_CWD=$(json_get "$DH_JSON" cwd)
[ -n "$DH_CWD" ] && [ -f "$DH_CWD/.claude/git-guard-off" ] && exit 0
DH_CMD=$(json_get "$DH_JSON" tool_input.command)
[ -n "$DH_CMD" ] || exit 0
_j=$(printf '%s' "$DH_CMD" | cmd_join_continuations) && [ -n "$_j" ] && DH_CMD="$_j"
dh_refuse() { echo "BLOCKED: deny-hang-shapes: $1" >&2; exit 2; }

# DH_HERE matches a heredoc operator (not a `<<<` here-string) that opens a
# delimiter word. Shapes 2 and 3 read only the text BEFORE the first heredoc
# operator: what follows is data (a commit message, a script body), not commands.
DH_Q="'"
DH_HERE="(^|[^<])<<-?[[:space:]]*[\"$DH_Q]?[A-Za-z_]"
DH_NL2=$(printf '\002')
DH_SEP=$(printf '\001')

# dh_norm [expose] -- stdin -> stdout. Quoted text is data, not commands: every
# quoted string collapses to the one word Q, so a command that only MENTIONS a
# shape (a grep pattern, a commit message, an issue body) is left alone. With the
# argument `expose` ONE exception applies (shapes 1 and 2 only, ruling S-18): the
# body of `bash -c '...'` / `sh -lc "..."` -- a SHELL (bash sh zsh dash ksh,
# optionally path-prefixed) given -c or a short-flag cluster ending in c -- is
# exposed first, because a loop inside one still hangs. Shape 3 never exposes: a
# quoted body is one word for it, even for a shell (`cd d && bash -c 'a; b'` is
# ONE command; so is `python -c "a; b"`). Double quotes honour backslash escapes;
# single quotes have none. Newlines survive (mapped out and back) so a string may
# span lines.
dh_norm() {
  local _shell='((^|[;&|[:space:](])([^[:space:]]*\/)?(bash|sh|zsh|dash|ksh)([[:space:]]+-[a-zA-Z]+)*[[:space:]]+-[a-zA-Z]*c[[:space:]]+)'
  tr '\n' "$DH_NL2" \
    | if [ "${1:-}" = expose ]; then
        sed -E "s/${_shell}'([^']*)'/\\1 \\6 /g; s/${_shell}\"(([^\"\\\\]|\\\\.)*)\"/\\1 \\6 /g"
      else cat; fi \
    | sed -E 's/"([^"\\]|\\.)*"|'\''[^'\'']*'\''/Q/g' \
    | tr "$DH_NL2" '\n'
}

# 1. A heredoc into a file: the FIRST line holds both the << and a file target.
case "$DH_CMD" in
  *'<<'*)
    # The delimiter's own quotes (<<'EOF') come off first, so they do not read as a string.
    DH_T=$(printf '%s\n' "$DH_CMD" | head -1 \
      | sed -E 's/<<(-?)[[:space:]]*'\''([A-Za-z_][A-Za-z_0-9]*)'\''/<<\1\2/g; s/<<(-?)[[:space:]]*"([A-Za-z_][A-Za-z_0-9]*)"/<<\1\2/g' \
      | dh_norm expose \
      | sed -E 's#[0-9]*>&[0-9]+##g; s#>+[[:space:]]*/dev/(null|stdout|stderr)##g')
    if printf '%s' "$DH_T" | grep -Eq "$DH_HERE"; then
      if printf '%s' "$DH_T" | grep -Eq '(^|[;&|[:space:]])cat[[:space:]][^|;&]*>{1,2}[[:space:]]*[^&[:space:]]' \
         || printf '%s' "$DH_T" | grep -Eq '(^|[;&|[:space:]])tee([[:space:]]+-[a-z]+)*[[:space:]]+[^-|;&[:space:]]'; then
        dh_refuse "a heredoc written into a file can hang an unattended agent -- write files with the Write tool."
      fi
    fi
    ;;
esac

# The command text up to (not including) the first heredoc operator.
DH_HEAD="$DH_CMD"
case "$DH_CMD" in
  *'<<'*)
    DH_HEAD=$(printf '%s\n' "$DH_CMD" | awk -v re="$DH_HERE" '
      match($0, re) { p = RSTART + (substr($0, RSTART, 1) == "<" ? 0 : 1); print substr($0, 1, p - 1); exit }
      { print }')
    ;;
esac

# 2. A wait loop (quoted text is data; a bash -c body is not).
case "$DH_HEAD" in
  *sleep*)
    DH_W=$(printf '%s' "$DH_HEAD" | dh_norm expose)
    DH_B='(^|[;&|({[:space:]])'
    if printf '%s' "$DH_W" | grep -Eq "${DH_B}(while|until)[[:space:]]" \
       && printf '%s' "$DH_W" | grep -Eq "${DH_B}sleep[[:space:]]" \
       && printf '%s' "$DH_W" | grep -Eq "${DH_B}done([;&|)}[:space:]]|\$)"; then
      dh_refuse "don't poll: run the command you are waiting on in the foreground (Bash timeout up to 600000 ms), or start it with run_in_background and wait for its completion notice; to watch a condition use the Monitor tool."
    fi
    ;;
esac

# 3. A leading cd followed by two or more commands (R-B, ruling S-16): `cd <dir>
# && <one command>` stays allowed. Commands are counted, not lines or operators:
# quoted strings collapse to one word, a redirect (`> log 2>&1`) is part of its
# command, a trailing separator or blank line is not a command, and a pipeline or
# ONE compound command (for/while/until..done, if..fi, case..esac, { }, ( )) is a
# single command however many `;` it holds. The awk tracks compound nesting: a
# segment that starts while a compound is still open belongs to it.
DH_TRIM="${DH_HEAD#"${DH_HEAD%%[![:space:]]*}"}"
case "$DH_TRIM" in
  cd[[:space:]]*)
    DH_N=$(printf '%s' "$DH_TRIM" | dh_norm | tr '\n' "$DH_NL2" \
      | sed -E "s/\\\\;/ /g; s/&&|\\|\\||;|${DH_NL2}/${DH_SEP}/g" \
      | tr "$DH_SEP" '\n' \
      | awk '
        { s = $0; gsub(/^[ \t]+|[ \t]+$/, "", s); if (s == "") next
          if (sp == 0) n++
          nw = split(s, w, /[ \t]+/)
          for (i = 1; i <= nw; i++) {
            t = w[i]
            c = (i == 1 || w[i-1] ~ /^(then|do|else|elif|\||!|time|\(|\{)$/)
            while (c && t ~ /^\(/) { st[++sp] = "("; t = substr(t, 2) }
            if (t == "") continue
            if (c && t ~ /^(for|while|until|if|case)$/) st[++sp] = t
            else if (c && t == "{") st[++sp] = "{"
            # The closing side is peeled like the opening side: a close keyword (or a
            # bare group close) may carry trailing ) and } -- `done)` `esac)}` `}` --
            # so pop the keyword first, then each glued group close in order.
            k = t; tl = ""
            if (match(k, /[)}]+$/)) {
              stem = substr(k, 1, RSTART - 1)
              if (stem == "" || stem ~ /^(done|fi|esac)$/) { tl = substr(k, RSTART); k = stem }
            }
            if (c && k ~ /^(done|fi|esac)$/ && sp > 0) sp--
            if (tl != "") {
              for (j = 1; j <= length(tl); j++) {
                ch = substr(tl, j, 1)
                if (sp > 0 && ((ch == ")" && st[sp] == "(") || (ch == "}" && st[sp] == "{"))) sp--
              }
            } else {
              tmp = t
              while (sp > 0 && st[sp] == "(" && sub(/\)$/, "", tmp)) sp--
            }
          }
        }
        END { print n + 0 }')
    if [ "${DH_N:-0}" -ge 3 ]; then
      dh_refuse "use absolute paths or git -C <dir>, or put the steps in a script file and run bash <path>, instead of a leading cd before several commands."
    fi
    ;;
esac
exit 0

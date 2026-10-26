#!/usr/bin/env bash
# Project Scout PreToolUse guard (plan §5.4, layer 2).
#
# Claude Code pipes each tool call to this script as JSON on stdin.
# Exit 0 = no objection (normal permission rules apply).
# Exit 2 = block; the message on stderr is shown to the agent.
#
# This is a second line of defence that matches command TEXT. The real
# boundary is the container (read-only root, no caps, no Docker socket,
# no secrets except the DeepSeek key). Keep the rules simple and readable.

set -u

input=$(cat)
tool=$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)
cmd=$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)
path=$(jq -r '.tool_input.file_path // .tool_input.path // ""' <<<"$input" 2>/dev/null)
url=$(jq -r '.tool_input.url // ""' <<<"$input" 2>/dev/null)

log_file=${SCOUT_GUARD_LOG:-}

block() {
  local reason=$1
  if [[ -n "$log_file" ]]; then
    mkdir -p "$(dirname "$log_file")" 2>/dev/null
    jq -cn --arg t "$(date -u +%FT%TZ)" --arg tool "$tool" --arg reason "$reason" \
      --arg detail "${cmd:-${path:-$url}}" \
      '{time:$t, tool:$tool, reason:$reason, detail:$detail}' >>"$log_file" 2>/dev/null
  fi
  echo "Blocked by Scout guard: $reason. This rule is fixed; do not try to work around it. If the task needs it, write it under 'Needs your attention' in your notes." >&2
  exit 2
}

# Text matcher, case-insensitive, on one line.
has() { grep -Eiq -- "$1" <<<"$2"; }

# Start of a command word: line start or after a shell operator / subshell.
W='(^|[;&|(`]|\$\(|[[:space:]])'
E='([[:space:]]|$|[;&|)])'

# Home LAN and the host. localhost inside the container is the container itself, so it's allowed.
LAN_NET='(10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|169\.254\.[0-9]+\.[0-9]+|host\.docker\.internal|gateway\.docker\.internal)'
SECRETS='(/srv/scout/secrets|/proc/[^[:space:]]*/environ|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY)'

case "$tool" in
  Bash)
    c=$(tr '\n' ' ' <<<"$cmd")

    # Downloads piped or substituted into an interpreter.
    has '\|[[:space:]]*(sudo[[:space:]]+)?(env[[:space:]]+)?(/[^[:space:]]*/)?(ba|z|da|k|c)?sh'"$E" "$c" \
      && block "piping into a shell"
    has '(curl|wget)[^;&]*\|[[:space:]]*(/[^[:space:]]*/)?(python3?|perl|ruby|node)'"$E" "$c" \
      && block "piping a download into an interpreter"
    has '(ba|z|da)?sh[[:space:]]+(-c[[:space:]]+)?["'"'"']?(<\(|\$\()[[:space:]]*(curl|wget)' "$c" \
      && block "running a downloaded script"

    # Privilege, host and container control.
    has "${W}(sudo|su|doas|pkexec|docker|podman|nsenter|chroot|mount)${E}" "$c" \
      && block "privilege or container control"

    # Remote shells, file transfer and mail: the box only talks to DeepSeek and the web.
    has "${W}(ssh|scp|sftp|rsync|telnet|nc|ncat|netcat|socat|sendmail|msmtp|swaks)${E}" "$c" \
      && block "remote shell, file transfer or mail"
    has 'smtps?://' "$c" && block "sending mail"

    # Git: local commits only, never a remote.
    has "${W}git(([[:space:]]+-[cC])[[:space:]]+[^[:space:]]+|[[:space:]]+--?[^[:space:]]+)*[[:space:]]+(push|remote|send-email|credential|request-pull)${E}" "$c" \
      && block "git remotes and pushing"
    has 'github\.com|gitlab\.com|bitbucket\.org' "$c" && has "${W}git${E}" "$c" \
      && block "git against a hosted remote"

    # Packages only from PyPI and npm defaults.
    has "${W}(pip3?|python3?[[:space:]]+-m[[:space:]]+pip|uv[[:space:]]+pip)[[:space:]]+install" "$c" \
      && has '(git\+|https?://|--index-url|--extra-index-url|--trusted-host|(^|[[:space:]])-i[[:space:]]|--find-links|(^|[[:space:]])-f[[:space:]])' "$c" \
      && block "installing Python packages from outside PyPI"
    has "${W}(npm|npx|pnpm|yarn)[[:space:]]+(install|i|add)${E}" "$c" \
      && has '(git\+|https?://|github:|--registry|file:)' "$c" \
      && block "installing npm packages from outside the npm registry"
    has "${W}(apt|apt-get|dpkg|snap)${E}" "$c" \
      && block "system package managers"

    # Secrets and environment dumps.
    has "$SECRETS" "$c" && block "reading secrets"
    has "${W}(printenv|env|export[[:space:]]+-p|set)[[:space:]]*($|[;&|>])" "$c" \
      && block "dumping the environment"

    # Home network: the agent has no business on the LAN or the host.
    has "$LAN_NET" "$c" && block "contacting the local network or host"

    # The locked rules themselves.
    has '/etc/claude-code' "$c" \
      && has '(>|tee|cp|mv|rm|chmod|chown|ln|sed[[:space:]]+-i|install|truncate)' "$c" \
      && block "changing the locked rules"
    ;;

  Read|Grep|Glob)
    has "$SECRETS" "$path" && block "reading secrets"
    ;;

  Edit|Write)
    has "$SECRETS" "$path" && block "touching secrets"
    has '^/etc/claude-code' "$path" && block "changing the locked rules"
    ;;

  WebFetch)
    has '^(file|ftp|smtp|ssh)://' "$url" && block "non-web URL schemes"
    has "^[a-z]+://([^/@]*@)?${LAN_NET}([:/]|$)" "$url" \
      && block "contacting the local network or host"
    ;;
esac

exit 0

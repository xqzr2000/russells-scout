#!/usr/bin/env bash
# Box: lets the WSL2 sync key in, locked to ops/sync/sync-gate.sh (box-setup §18).
#
#   ops/sync/add-sync-key.sh 'ssh-ed25519 AAAA… scout-sync'   add it (the public key)
#   ops/sync/add-sync-key.sh --remove                         take the sync key out again
#
# The line it adds to ~/.ssh/authorized_keys says: this key may only run sync-gate.sh, with
# no terminal and no forwarding of any kind ("restrict"). Your normal key is not touched.

set -euo pipefail

GATE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sync-gate.sh
AK=${SCOUT_AUTHORIZED_KEYS:-$HOME/.ssh/authorized_keys}
STAMP_DIR=${SCOUT_HOME:-/srv/scout}/logs/sync
MARK=scout-sync

remove() {
  [[ -f $AK ]] || return 0
  local tmp
  tmp=$(mktemp "$AK.XXXXXX")
  grep -v " $MARK\$" "$AK" > "$tmp" || true
  chmod 600 "$tmp"
  mv -f "$tmp" "$AK"
}

if [[ ${1:-} == --remove ]]; then
  remove
  echo "removed the $MARK key from $AK"
  exit 0
fi

key=${1:-}
if [[ ! $key =~ ^(ssh-ed25519)\ ([A-Za-z0-9+/]+={0,2})(\ .*)?$ ]]; then
  echo "Give the sync key's public key, in quotes: $0 'ssh-ed25519 AAAA… scout-sync'" >&2
  echo "(the contents of ~/.ssh/scout_sync.pub on WSL2; an ed25519 key)" >&2
  exit 1
fi
type=${BASH_REMATCH[1]}
blob=${BASH_REMATCH[2]}

mkdir -p "$(dirname "$AK")"
chmod 700 "$(dirname "$AK")"
touch "$AK"
if grep -v " $MARK\$" "$AK" | grep -qF "$blob"; then
  echo "That key is already in $AK without the sync restrictions (is it your normal key?)." >&2
  echo "Make a separate key for syncing: box-setup §18." >&2
  exit 1
fi
remove   # replace any earlier sync key
echo "command=\"$GATE\",restrict $type $blob $MARK" >> "$AK"
chmod 600 "$AK"
mkdir -p "$STAMP_DIR"
echo "added the $MARK key to $AK; it can only run $GATE"

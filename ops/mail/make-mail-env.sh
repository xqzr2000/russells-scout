#!/usr/bin/env bash
# Creates $SCOUT_HOME/secrets/mail.env for the daily digest, asking for the Gmail address,
# the recipient and the app password. The password isn't shown as you type or paste it,
# and doesn't end up in your shell history. Only the digest container reads this file.
#
#   cd /srv/scout/code
#   ops/mail/make-mail-env.sh             create it
#   ops/mail/make-mail-env.sh --replace   overwrite it (e.g. with a new app password)

set -euo pipefail

FILE=${SCOUT_HOME:-/srv/scout}/secrets/mail.env

if [[ -e $FILE && ${1:-} != --replace ]]; then
  echo "$FILE already exists. To overwrite it: $0 --replace" >&2
  exit 1
fi
if [[ ! -d $(dirname "$FILE") ]]; then
  echo "$(dirname "$FILE") doesn't exist (box-setup §2)." >&2
  exit 1
fi

read -rp "Gmail address that sends the digest: " user
user=${user//[[:space:]]/}
if [[ $user != *@*.* ]]; then
  echo "That doesn't look like an email address." >&2
  exit 1
fi
read -rp "Send the digest to [$user]: " to
to=${to//[[:space:]]/}
to=${to:-$user}
if [[ $to != *@*.* ]]; then
  echo "That doesn't look like an email address." >&2
  exit 1
fi
read -rsp "App password (paste it; spaces are fine; nothing shows): " pw
echo
pw=${pw//[[:space:]]/}
if [[ ! $pw =~ ^[A-Za-z]{16}$ ]]; then
  echo "A Gmail app password is 16 letters; that was ${#pw} characters. Nothing saved." >&2
  exit 1
fi

umask 077
tmp=$(mktemp "$FILE.XXXXXX")
{
  echo "SCOUT_SMTP_HOST=smtp.gmail.com"
  echo "SCOUT_SMTP_PORT=587"
  echo "SCOUT_SMTP_USER=$user"
  echo "SCOUT_SMTP_PASSWORD=$pw"
  echo "SCOUT_MAIL_TO=$to"
} > "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$FILE"
echo "Saved $FILE (only you can read it)."
echo "Test it: docker compose --profile scout run --rm digest digest --test-email"

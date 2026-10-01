#!/usr/bin/env sh
set -eu
if git grep -nE '(sk-or-v1-[A-Za-z0-9_-]{20,}|OPENROUTER_API_KEY=[^[:space:]]+)' -- ':!scripts/check-secrets.sh' ':!.env.example'; then
  echo 'Potential secret found.' >&2
  exit 1
fi
echo 'No obvious OpenRouter secret found.'

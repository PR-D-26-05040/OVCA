#!/usr/bin/env bash
# Reliably resume and push pre-created CC3M batch commits to GitHub.

set -uo pipefail

REMOTE="${1:-origin}"
BRANCH="${2:-main}"
MAX_RETRIES="${MAX_RETRIES:-10}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-20}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/github_key_ovca}"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if ! [[ "$MAX_RETRIES" =~ ^[1-9][0-9]*$ ]]; then
  printf 'MAX_RETRIES must be a positive integer\n' >&2
  exit 2
fi
if ! [[ "$RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
  printf 'RETRY_DELAY_SECONDS must be a non-negative integer\n' >&2
  exit 2
fi
if [ ! -f "$SSH_KEY" ]; then
  printf 'SSH key not found: %s\n' "$SSH_KEY" >&2
  exit 2
fi
if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  printf 'Git remote does not exist: %s\n' "$REMOTE" >&2
  exit 2
fi
if ! git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  printf 'Local branch does not exist: %s\n' "$BRANCH" >&2
  exit 2
fi

export GIT_SSH_COMMAND="ssh -p 443 -o HostName=ssh.github.com -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o TCPKeepAlive=yes"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

retry() {
  local description=$1
  shift
  local attempt
  for ((attempt = 1; attempt <= MAX_RETRIES; attempt++)); do
    printf '[%s] %s (attempt %d/%d)\n' "$(timestamp)" "$description" "$attempt" "$MAX_RETRIES"
    if "$@"; then
      return 0
    fi
    if [ "$attempt" -lt "$MAX_RETRIES" ]; then
      printf '[%s] retrying in %d seconds\n' "$(timestamp)" "$RETRY_DELAY_SECONDS"
      sleep "$RETRY_DELAY_SECONDS"
    fi
  done
  return 1
}

retry "fetching $REMOTE/$BRANCH" git fetch "$REMOTE" "$BRANCH" || {
  printf 'Could not fetch the remote branch after %d attempts\n' "$MAX_RETRIES" >&2
  exit 1
}

REMOTE_REF="refs/remotes/$REMOTE/$BRANCH"
mapfile -t COMMITS < <(git rev-list --reverse "$REMOTE_REF..refs/heads/$BRANCH")

if [ "${#COMMITS[@]}" -eq 0 ]; then
  printf '[%s] %s is already fully uploaded\n' "$(timestamp)" "$BRANCH"
  exit 0
fi

TOTAL=${#COMMITS[@]}
for index in "${!COMMITS[@]}"; do
  commit=${COMMITS[$index]}
  number=$((index + 1))
  subject=$(git log -1 --format=%s "$commit")
  description="pushing $number/$TOTAL ${commit:0:7} $subject"
  retry "$description" git push "$REMOTE" "$commit:refs/heads/$BRANCH" || {
    printf 'Stopped at commit %s after %d attempts\n' "$commit" "$MAX_RETRIES" >&2
    printf 'Run this script again to resume from the last successful commit.\n' >&2
    exit 1
  }
done

printf '[%s] upload complete: %s/%s\n' "$(timestamp)" "$REMOTE" "$BRANCH"

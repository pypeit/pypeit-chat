#!/bin/bash
# Refresh the corpus and index (DESIGN Section 14).
#
# Fetches the official PypeIt repository into cache/PypeIt.  If the latest
# release tag (newest tag on origin/release) has moved, checks it out and
# rebuilds index/.  The running bot notices the new meta.json and reloads,
# so the version line changes without a restart.
#
# Run weekly by deploy/org.pypeit.chat.refresh.plist, or by hand.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') refresh"
pypeit_chat_ingest --if-changed

#!/bin/zsh
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "${SCRIPT_DIR}/Stop Read My Zotero.app/Contents/MacOS/Stop Read My Zotero"

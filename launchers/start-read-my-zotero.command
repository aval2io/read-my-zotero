#!/bin/zsh
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "${SCRIPT_DIR}/Read My Zotero.app/Contents/MacOS/Read My Zotero"

#!/bin/zsh
set -u
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="${HOME}/Applications/Read My Zotero.app"
STOP_TARGET_DIR="${HOME}/Applications/Stop Read My Zotero.app"

/bin/mkdir -p "${HOME}/Applications"
/bin/mkdir -p "${TARGET_DIR}"
/bin/mkdir -p "${STOP_TARGET_DIR}"
/usr/bin/ditto "${PROJECT_DIR}/launchers/Read My Zotero.app" "${TARGET_DIR}"
/usr/bin/ditto "${PROJECT_DIR}/launchers/Stop Read My Zotero.app" "${STOP_TARGET_DIR}"
/bin/chmod +x "${TARGET_DIR}/Contents/MacOS/Read My Zotero" "${STOP_TARGET_DIR}/Contents/MacOS/Stop Read My Zotero" "${PROJECT_DIR}/launchers/start-read-my-zotero.command" "${PROJECT_DIR}/launchers/stop-read-my-zotero.command"
/usr/bin/touch "${TARGET_DIR}"
/usr/bin/touch "${STOP_TARGET_DIR}"

echo "Installed: ${TARGET_DIR}"
echo "Installed: ${STOP_TARGET_DIR}"
echo "Use Spotlight or Raycast to launch Read My Zotero or Stop Read My Zotero."

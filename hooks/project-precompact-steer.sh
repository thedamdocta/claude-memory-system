#!/bin/bash
# PreCompact hook — MyProject Compaction Steering
# Fires before compaction for MyProject project only.
# Emits stdout with custom compact instructions (PreCompact exit 0 stdout IS read by compactor).

CWD="$(pwd)"

# Project detection — only fire for MyProject
case "$CWD" in
  __VAULT_PATH__*)
    cat <<'EOF'
Preserve in compaction summary:
- Vault entry pointer ([[_INDEX]]) and three-layer architecture terminology
- Current session focus and active episode scope (Ryse arc)
- MyProject Compass rule: "Was it all worth dying for?"
- Open question IDs and unresolved threads
- Last 2 wikilinks discussed in conversation
EOF
    exit 0
    ;;
  *)
    # Non-MyProject project — silent pass
    exit 0
    ;;
esac

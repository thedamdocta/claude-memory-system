#!/bin/bash
# UserPromptSubmit hook — MyProject Compass Reminder
# Fires on every user prompt for MyProject project only.
# Injects brief creative compass reminder via plain stdout (UserPromptSubmit allows it).

CWD="$(pwd)"

# Project detection — only fire for MyProject
case "$CWD" in
  __VAULT_PATH__*)
    echo "Compass: does this serve the theme? Search before Read. Write lore to docs immediately."
    exit 0
    ;;
  *)
    # Non-MyProject project — silent pass
    exit 0
    ;;
esac

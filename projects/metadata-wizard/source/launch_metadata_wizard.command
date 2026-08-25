#!/bin/zsh

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "Python 3 is required to open Metadata Wizard." buttons {"OK"} default button "OK" with icon stop'
  exit 1
fi

python3 metadata_wizard.py


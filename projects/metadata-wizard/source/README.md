# Metadata Wizard: public portfolio edition

This is a sanitized copy of a desktop metadata workflow. It uses generic templates, synthetic configuration, and no organizational records.

## What it does

- Parses labeled intake fields into a structured form.
- Applies XMP/IPTC metadata with ExifTool without recompressing image pixels.
- Optionally drafts accessibility alt text with an OpenAI vision model.
- Optionally suggests image-specific keywords and separates approved-list matches from review exceptions.
- Stores an optional API key in macOS Keychain instead of the project folder.
- Processes work in a background thread and records per-file errors without stopping an entire batch.

## Quick start on macOS

1. Install Python 3 and ExifTool.
2. Install the Python dependencies with `python3 -m pip install -r requirements.txt`.
3. Double-click `launch_metadata_wizard.command`, or run `python3 metadata_wizard.py`.

The application can apply metadata without an OpenAI API key. An API key is required only for the optional AI-assisted alt-text and keyword features.

## Public-safe sample files

- `templates/` contains generic XMP templates.
- `approved_keywords.txt` contains a small synthetic controlled vocabulary.
- No production templates, internal keyword lists, credentials, or operational records are included.


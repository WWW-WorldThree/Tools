# DualSaver

Windows-native file watcher that automatically duplicates files to two destinations.

Designed as a lightweight, zero-effort backup layer: just drop files into a folder.

---

## Overview

DualSaver monitors a specified folder and automatically copies new or updated files to two separate locations.

It is intended to reduce human error in file handling by ensuring:
- files are duplicated
- versions are preserved
- integrity can be verified

---

## Features

- Real-time folder monitoring (FileSystemWatcher)
- Automatic duplication to two destinations
- Versioned file saving (no overwrite)
- Retry mechanism for failed copies
- Debounce control to avoid partial writes
- Optional SHA256 hash verification
- CSV logging of all operations

---

## Use Cases

- Simple backup system without external tools
- Redundant storage of important documents
- Preventing file loss from manual handling errors
- "Drop-and-forget" workflow for non-engineers

---

## Basic Flow

1. Place a file into the watched folder
2. DualSaver detects the change
3. File is copied to Destination A and B
4. (Optional) Hash verification is performed
5. Operation is logged

---

## Configuration

Key parameters (editable in script):

- WatchRoot: source folder
- DestA / DestB: destination paths
- EnableHash: enable/disable SHA256 verification
- RetryMax: retry attempts
- DebounceMs: delay before processing
- PreserveTree: maintain folder structure

---

## Tech Stack

- PowerShell (Windows standard)
- FileSystemWatcher
- SHA256 (optional verification)

---

## Notes

This tool focuses on **preservation and reliability**, not synchronization or version control.

It assumes a simple principle:

> "Put it somewhere safe first."

---

## Related

Can be combined with search/retrieval tools:

- DualSaver → intake / preservation
- AIE Cognitive Salvage Engine → search / salvage

---

## Status

Working script / practical utility

---

## License

MIT

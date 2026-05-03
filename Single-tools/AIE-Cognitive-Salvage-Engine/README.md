# AIE Cognitive Salvage Engine

Local search engine for large conversational logs (e.g. ChatGPT exports).

Reconstructs the active branch of conversations and enables fast search with contextual navigation.

---

## Overview

This tool is designed to handle large JSON exports that are difficult to browse directly.

Instead of reading logs linearly, it allows:
- Searching by keyword
- Navigating surrounding context
- Reconstructing actual conversation flow (active branch only)

---

## Features

- Active branch reconstruction (ignores alternative assistant branches)
- SQLite-based storage for large datasets
- Fast search using:
  - direct string match (instr)
  - FTS5 full-text search
- Context view (before / after messages)
- Lightweight local UI (Flet)

---

## Use Cases

- Recover past ideas and reasoning processes
- Navigate large ChatGPT logs efficiently
- Reuse previous research or discussions
- Find blind spots by revisiting context

---

## Basic Flow

1. Load OpenAI conversation export (JSON)
2. Extract active conversation branches
3. Store in SQLite database
4. Search and explore via UI

---

## Tech Stack

- Python
- SQLite (FTS5)
- Flet

---

## Notes

This tool focuses on **retrieval and reuse of information**, not organization.

Instead of perfect categorization, it assumes:
> "Store first, salvage later."

---

## Related

This tool can be combined with file preservation tools such as DualSaver:

- DualSaver → data intake / preservation
- AIE → search / salvage

---

## Status

Prototype / working implementation

---

## License

MIT

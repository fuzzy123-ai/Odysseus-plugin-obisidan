from __future__ import annotations

import os
from typing import Any, Dict, Optional


MAX_MARKDOWN_LINES = 600
RULES_NOTE_PATH = "AI Memory/Canonical/Vault Writing Rules.md"


RULES_NOTE_CONTENT = f"""---
type: rules
scope: vault
status: active
tags:
  - odysseus
  - vault-rules
---

# Vault Writing Rules

These rules define how Odysseus agents should write information into this vault.

## Findability

- Put durable rules, decisions, architecture notes, and implementation plans under `AI Memory/Canonical/`.
- Put raw captures, temporary notes, and unprocessed material under `AI Memory/Inbox/`.
- Prefer specific filenames over generic names like `Notes.md` or `Update.md`.
- Use frontmatter for stable metadata: `type`, `status`, `scope`, `source`, `created`, and `tags`.
- Link related notes with Obsidian links when the relationship is important.

## Size

- Keep individual Markdown files at or below {MAX_MARKDOWN_LINES} lines.
- This keeps every file small enough to fit into a manageable AI context window during retrieval, review, and follow-up edits.
- If a note would exceed {MAX_MARKDOWN_LINES} lines, split it by topic, phase, date, or subcomponent.
- Use index notes to connect split files instead of creating one giant document.

## External AI Agents

- External AI clients that write through Odysseus, MCP, or scoped HTTP APIs must follow the same {MAX_MARKDOWN_LINES}-line Markdown softcap.
- When a write response includes a line-count warning, treat it as an instruction to split the content before continuing.
- Prefer several linked files with clear titles over one oversized note, so later agents can retrieve only the relevant context.

## Write Safety

- Do not overwrite existing notes without explicit confirmation.
- Keep generated content concise enough to review.
- Prefer append/update operations that preserve existing context over full rewrites.
"""


def markdown_line_count(content: str) -> int:
    if not content:
        return 0
    return len(str(content).splitlines())


def validate_markdown_note(path: str, content: str) -> Dict[str, Any]:
    """Return non-blocking rule metadata for a Markdown note write."""
    if not str(path or "").lower().endswith(".md"):
        return {"line_count": None, "soft_cap": MAX_MARKDOWN_LINES, "warning": None}
    line_count = markdown_line_count(content)
    warning: Optional[str] = None
    if line_count > MAX_MARKDOWN_LINES:
        warning = (
            f"Vault rules softcap exceeded: {line_count} lines in {path}; "
            f"target is <= {MAX_MARKDOWN_LINES}. Split this note into smaller, findable files."
        )
    return {
        "line_count": line_count,
        "soft_cap": MAX_MARKDOWN_LINES,
        "warning": warning,
    }


def apply_write_rule_metadata(result: Dict[str, Any], path: str, content: str) -> Dict[str, Any]:
    metadata = validate_markdown_note(path, content)
    if metadata["line_count"] is not None:
        result["line_count"] = metadata["line_count"]
        result["line_soft_cap"] = metadata["soft_cap"]
    if metadata["warning"]:
        result["warning"] = metadata["warning"]
    return result


def ensure_rules_note(vault_dir: str) -> str:
    """Ensure the visible vault writing rules note exists, without history noise."""
    abs_path = os.path.abspath(os.path.join(vault_dir, RULES_NOTE_PATH))
    abs_vault = os.path.abspath(vault_dir)
    if os.path.commonpath([abs_vault, abs_path]) != abs_vault:
        raise ValueError("Rules note path escaped vault")
    if not os.path.exists(abs_path):
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(RULES_NOTE_CONTENT)
    return RULES_NOTE_PATH

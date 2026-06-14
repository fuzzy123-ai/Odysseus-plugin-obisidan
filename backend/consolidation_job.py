import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import vault_service
from .context_provider import parse_frontmatter
from .vault_model import build_vault_index
from .vault_security import VaultSecurityError


JOB_ID = "obsidian.vault_consolidation"
REPORT_PATH = ".obsidian/consolidation_report.json"


def run_vault_consolidation(
    owner: Optional[str] = None,
    trigger: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
    except VaultSecurityError:
        return {"skipped": True, "reason": "vault_locked"}

    notes = vault_service.markdown_notes(vault_dir)
    index = build_vault_index(vault_dir) if notes else {"graph": {"edges": []}, "tags": [], "notes": []}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "context": _safe_context(context or {}),
        "owner": owner or "default",
        "summary": {
            "note_count": len(notes),
            "tag_count": len(index.get("tags", [])),
            "edge_count": len(index.get("graph", {}).get("edges", [])),
        },
        "duplicate_title_candidates": _duplicate_title_candidates(notes),
        "orphan_note_candidates": _orphan_note_candidates(index),
        "frontmatter_suggestions": _frontmatter_suggestions(vault_dir, notes),
        "safety": {
            "destructive_changes": False,
            "note_files_modified": False,
            "report_only": True,
        },
    }
    _write_report(vault_dir, report)
    return {
        "skipped": False,
        "report_path": REPORT_PATH,
        "summary": report["summary"],
        "duplicate_title_candidates": len(report["duplicate_title_candidates"]),
        "orphan_note_candidates": len(report["orphan_note_candidates"]),
        "frontmatter_suggestions": len(report["frontmatter_suggestions"]),
    }


def job_spec() -> Dict[str, Any]:
    return {
        "id": JOB_ID,
        "label": "Obsidian Vault Consolidation",
        "priority": 50,
        "capabilities": ["chat_completed", "periodic", "vault", "obsidian"],
        "run": run_vault_consolidation,
    }


def _write_report(vault_dir: str, report: Dict[str, Any]) -> None:
    path = vault_service.secure_path(vault_dir, REPORT_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)


def _safe_context(context: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {}
    for key in ("session_id", "model", "user_message"):
        value = context.get(key)
        if value is not None:
            allowed[key] = str(value)[:500]
    return allowed


def _duplicate_title_candidates(notes: List[str]) -> List[Dict[str, Any]]:
    by_title: Dict[str, List[str]] = {}
    for path in notes:
        title = os.path.splitext(os.path.basename(path))[0].strip().lower()
        by_title.setdefault(title, []).append(path)
    return [
        {"title": title, "paths": sorted(paths, key=str.lower)}
        for title, paths in sorted(by_title.items())
        if len(paths) > 1
    ]


def _orphan_note_candidates(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    notes = {note["path"] for note in index.get("notes", [])}
    connected = set()
    for edge in index.get("graph", {}).get("edges", []):
        connected.add(edge.get("source"))
        connected.add(edge.get("target"))
    orphaned = sorted(path for path in notes if path not in connected)
    return [{"path": path, "suggestion": "Review whether this note should link to a hub or be archived."} for path in orphaned]


def _frontmatter_suggestions(vault_dir: str, notes: List[str]) -> List[Dict[str, Any]]:
    suggestions = []
    for path in notes:
        frontmatter, _ = parse_frontmatter(vault_service.read_file(vault_dir, path))
        missing = [key for key in ("status", "active_context") if key not in frontmatter]
        if missing:
            suggestions.append({
                "path": path,
                "missing": missing,
                "suggestion": "Add explicit frontmatter before using this as durable context.",
            })
    return suggestions

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import vault_service
from .context_provider import parse_frontmatter


STATE_DOC_PATH = "_state/active_run.md"
VALID_STATUSES = {"active", "blocked", "done"}


@dataclass(frozen=True)
class StateDoc:
    path: str
    frontmatter: Dict[str, Any]
    body: str
    content: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_state_doc(vault_dir: str) -> Optional[StateDoc]:
    content = vault_service.read_text_if_exists(vault_service.secure_path(vault_dir, STATE_DOC_PATH))
    if content is None:
        return None
    frontmatter, body = parse_frontmatter(content)
    return StateDoc(path=STATE_DOC_PATH, frontmatter=frontmatter, body=body, content=content)


def initialize_state_doc(
    vault_dir: str,
    *,
    owner: Optional[str],
    session_id: Optional[str],
    goal: str,
    checklist: Optional[List[str]] = None,
    open_questions: Optional[List[str]] = None,
) -> StateDoc:
    now = utc_now_iso()
    frontmatter = {
        "status": "active",
        "owner": owner or "default",
        "session_id": session_id or "",
        "updated": now,
    }
    body = "\n\n".join([
        "# Active Run",
        "## Goal\n" + _clean_block(goal or "Unspecified goal."),
        "## Checklist\n" + _format_checklist(checklist or []),
        "## Step Log\n",
        "## Delegations\n",
        "## Reflections\n",
        "## Open Questions\n" + _format_bullets(open_questions or []),
    ]).rstrip() + "\n"
    _write_state_doc(vault_dir, frontmatter, body, owner=owner)
    doc = read_state_doc(vault_dir)
    assert doc is not None
    return doc


def update_state_doc_status(
    vault_dir: str,
    *,
    owner: Optional[str],
    status: str,
) -> StateDoc:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid state-doc status: {status}")
    doc = _require_state_doc(vault_dir)
    frontmatter = dict(doc.frontmatter)
    frontmatter["status"] = status
    frontmatter["updated"] = utc_now_iso()
    _write_state_doc(vault_dir, frontmatter, doc.body, owner=owner)
    updated = read_state_doc(vault_dir)
    assert updated is not None
    return updated


def append_step_entry(
    vault_dir: str,
    *,
    owner: Optional[str],
    entry: str,
    status: str = "done",
) -> StateDoc:
    doc = _require_state_doc(vault_dir)
    frontmatter = _touch(doc.frontmatter)
    line = f"- {utc_now_iso()} [{_clean_inline(status)}] {_clean_inline(entry)}"
    body = _append_to_section(doc.body, "Step Log", line)
    _write_state_doc(vault_dir, frontmatter, body, owner=owner)
    updated = read_state_doc(vault_dir)
    assert updated is not None
    return updated


def append_delegation_entry(
    vault_dir: str,
    *,
    owner: Optional[str],
    task: str,
    status: str,
    summary: str = "",
) -> StateDoc:
    doc = _require_state_doc(vault_dir)
    frontmatter = _touch(doc.frontmatter)
    parts = [
        f"- {utc_now_iso()} [{_clean_inline(status)}] {_clean_inline(task)}",
    ]
    if summary:
        parts.append(f"  Summary: {_clean_inline(summary)}")
    body = _append_to_section(doc.body, "Delegations", "\n".join(parts))
    _write_state_doc(vault_dir, frontmatter, body, owner=owner)
    updated = read_state_doc(vault_dir)
    assert updated is not None
    return updated


def append_reflection_entry(
    vault_dir: str,
    *,
    owner: Optional[str],
    trigger: str,
    status: str,
    assessment: str = "",
    risks: Optional[List[str]] = None,
    next_step: str = "",
    note: str = "",
    teacher_model: str = "",
) -> StateDoc:
    doc = _require_state_doc(vault_dir)
    reflected_at = utc_now_iso()
    frontmatter = _touch(doc.frontmatter)
    frontmatter["last_reflection_at"] = reflected_at
    lines = [
        f"- {reflected_at} [{_clean_inline(status)}] {_clean_inline(trigger)}",
    ]
    if teacher_model:
        lines.append(f"  Teacher: {_clean_inline(teacher_model)}")
    if assessment:
        lines.append(f"  Assessment: {_clean_inline(assessment)}")
    for risk in risks or []:
        if risk:
            lines.append(f"  Risk: {_clean_inline(risk)}")
    if next_step:
        lines.append(f"  Next: {_clean_inline(next_step)}")
    if note:
        lines.append(f"  Note: {_clean_inline(note)}")
    body = _append_to_section(doc.body, "Reflections", "\n".join(lines))
    _write_state_doc(vault_dir, frontmatter, body, owner=owner)
    updated = read_state_doc(vault_dir)
    assert updated is not None
    return updated


def _require_state_doc(vault_dir: str) -> StateDoc:
    doc = read_state_doc(vault_dir)
    if doc is None:
        raise FileNotFoundError(f"State doc not found: {STATE_DOC_PATH}")
    return doc


def _touch(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(frontmatter)
    out["updated"] = utc_now_iso()
    return out


def _write_state_doc(vault_dir: str, frontmatter: Dict[str, Any], body: str, *, owner: Optional[str]) -> None:
    content = _dump_frontmatter(frontmatter) + "\n" + body.rstrip() + "\n"
    vault_service.write_file(
        vault_dir,
        STATE_DOC_PATH,
        content,
        owner=owner,
        tool="obsidian_state_doc",
    )


def _dump_frontmatter(frontmatter: Dict[str, Any]) -> str:
    lines = ["---"]
    for key in ("status", "owner", "session_id", "updated"):
        value = frontmatter.get(key, "")
        lines.append(f"{key}: {_yaml_scalar(value)}")
    if "last_reflection_at" in frontmatter:
        lines.append(f"last_reflection_at: {_yaml_scalar(frontmatter.get('last_reflection_at', ''))}")
    for key in sorted(k for k in frontmatter if k not in {"status", "owner", "session_id", "updated", "last_reflection_at"}):
        lines.append(f"{key}: {_yaml_scalar(frontmatter[key])}")
    lines.append("---")
    return "\n".join(lines)


def _yaml_scalar(value: Any) -> str:
    text = str(value or "")
    if not text or any(ch in text for ch in ":\n#[]{}"):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _append_to_section(body: str, heading: str, text: str) -> str:
    pattern = re.compile(rf"(^## {re.escape(heading)}\s*$)", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return body.rstrip() + f"\n\n## {heading}\n{text}\n"
    next_heading = re.search(r"^##\s+", body[match.end():], re.MULTILINE)
    insert_at = match.end() + (next_heading.start() if next_heading else len(body[match.end():]))
    before = body[:insert_at].rstrip()
    after = body[insert_at:].lstrip("\n")
    joined = before + "\n" + text.rstrip() + "\n"
    if after:
        joined += "\n" + after
    return joined.rstrip() + "\n"


def _format_checklist(items: List[str]) -> str:
    if not items:
        return "- [ ] Define next concrete step."
    return "\n".join(f"- [ ] {_clean_inline(item)}" for item in items)


def _format_bullets(items: List[str]) -> str:
    if not items:
        return ""
    return "\n".join(f"- {_clean_inline(item)}" for item in items)


def _clean_block(text: str) -> str:
    return str(text or "").strip()


def _clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())

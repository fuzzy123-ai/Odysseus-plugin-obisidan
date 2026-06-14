import hashlib
import os
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from . import vault_service
from .project_planning import normalize_hash_tag, normalize_relative_path
from .vault_model import build_vault_index, normalize_tag_name


KINDS = {
    "decision",
    "rule",
    "preference",
    "open_question",
    "project_note",
    "session_log",
    "reference",
    "raw",
}
SOURCES = {"agent", "chat", "manual", "file", "mail", "document"}
SCOPES = {"global", "project", "personal", "technical", "session"}
CONFIDENCE = {"low", "medium", "high"}
TARGETS = {"auto", "inbox", "canonical", "append"}
BROAD_TAGS = {"ai", "ki", "llm", "project", "note", "notes", "memory", "obsidian"}

CANONICAL_NOTES = {
    "preference": "AI Memory/Canonical/User Preferences.md",
    "rule": "AI Memory/02 Entscheidungen.md",
    "decision": "AI Memory/02 Entscheidungen.md",
    "open_question": "AI Memory/03 Offene Fragen.md",
}


class MemoryCaptureRequest(BaseModel):
    content: str
    title: str = ""
    kind: str = ""
    source: str = "agent"
    source_ref: str = ""
    project: str = ""
    scope: str = "global"
    confidence: str = "medium"
    tags: List[str] = Field(default_factory=list)
    link_paths: List[str] = Field(default_factory=list)
    target: str = "auto"
    confirm: bool = False


class CaptureCandidate(BaseModel):
    path: str
    title: str = ""
    reason: str = ""
    score: int = 0


class MemoryCapturePlan(BaseModel):
    action: str
    risk: str
    kind: str
    normalized_title: str
    target_path: str
    markdown: str
    frontmatter: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    suggested_links: List[CaptureCandidate] = Field(default_factory=list)
    duplicate_candidates: List[CaptureCandidate] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)
    operations: List[Dict[str, Any]] = Field(default_factory=list)
    confirm_required: bool = True


class MemoryCaptureApplyRequest(BaseModel):
    plan: MemoryCapturePlan
    confirm: bool = False


class MemoryCaptureValidationError(ValueError):
    pass


def build_memory_capture_plan(
    vault_dir: str,
    request: MemoryCaptureRequest,
    *,
    today: Optional[date] = None,
) -> MemoryCapturePlan:
    content = (request.content or "").strip()
    if not content:
        raise MemoryCaptureValidationError("Memory capture content is required")

    created = (today or date.today()).isoformat()
    kind = _normalize_kind(request.kind, content)
    source = _normalize_choice(request.source, SOURCES, "agent")
    scope = _normalize_choice(request.scope, SCOPES, "global")
    confidence = _normalize_choice(request.confidence, CONFIDENCE, "medium")
    target = _normalize_choice(request.target, TARGETS, "auto")
    title = _title_for_capture(request.title, content, kind)
    requested_tags = _normalized_requested_tags(request.tags)
    tags = _schema_tags(kind, source, scope, confidence, request.project, requested_tags)
    frontmatter = _frontmatter(kind, source, scope, confidence, created, request)

    index = build_vault_index(vault_dir)
    notes = index.get("notes", [])
    suggested_links = _suggest_links(notes, request.link_paths, content, title)
    duplicates = _duplicate_candidates(vault_dir, notes, content, title, tags)
    strong_duplicate = duplicates and duplicates[0].score >= 12
    medium_duplicate = duplicates and duplicates[0].score >= 6

    action, risk, target_path = _route_capture(kind, target, created, title, content, medium_duplicate)
    warnings: List[str] = []
    questions: List[str] = []
    if strong_duplicate:
        action = "discard_duplicate"
        risk = "low"
        target_path = duplicates[0].path
        warnings.append("Strong duplicate candidate found; no vault write is planned.")
    elif medium_duplicate:
        warnings.append("Similar existing notes found; routing this capture to the review queue.")
    if not suggested_links and action not in {"discard_duplicate"}:
        warnings.append("No strong existing links found; review this before treating it as connected context.")
    if _has_broad_only_tags(tags):
        warnings.append("Only broad tags were detected; add a more specific project or topic tag when possible.")
    if kind == "raw" or len(content) > 3500:
        warnings.append("Long or raw memory is stored away from canonical notes until reviewed.")

    markdown = _render_capture_markdown(
        title=title,
        content=content,
        kind=kind,
        created=created,
        source=source,
        source_ref=request.source_ref,
        scope=scope,
        confidence=confidence,
        tags=tags,
        links=[item.path for item in suggested_links[:5]],
        frontmatter=frontmatter,
        append_mode=action in {"append", "update_canonical"},
    )
    operations = _operations_for_plan(vault_dir, action, target_path, markdown, frontmatter)

    return MemoryCapturePlan(
        action=action,
        risk=risk,
        kind=kind,
        normalized_title=title,
        target_path=target_path,
        markdown=markdown,
        frontmatter=frontmatter,
        tags=tags,
        suggested_links=suggested_links,
        duplicate_candidates=duplicates,
        warnings=warnings,
        questions=questions,
        operations=operations,
        confirm_required=bool(operations),
    )


def validate_memory_capture_plan(vault_dir: str, plan: MemoryCapturePlan) -> MemoryCapturePlan:
    if plan.action not in {"create", "append", "update_canonical", "review_queue", "discard_duplicate"}:
        raise MemoryCaptureValidationError(f"Unsupported memory capture action: {plan.action}")
    if plan.action == "discard_duplicate":
        if plan.operations:
            raise MemoryCaptureValidationError("Discard duplicate plans must not contain operations")
        return plan
    if not plan.target_path:
        raise MemoryCaptureValidationError("target_path is required")
    normalize_relative_path(plan.target_path)
    vault_service.secure_path(vault_dir, plan.target_path)
    for op in plan.operations:
        action = op.get("action")
        path = op.get("path")
        if action not in {"create_file", "update_file", "merge_frontmatter"}:
            raise MemoryCaptureValidationError(f"Unsupported capture operation: {action}")
        if not path:
            raise MemoryCaptureValidationError("Capture operation path is required")
        vault_service.secure_path(vault_dir, path)
        if action in {"create_file", "update_file"} and "content" not in op:
            raise MemoryCaptureValidationError(f"Capture operation content is required for {path}")
    return plan


def apply_memory_capture_plan(
    vault_dir: str,
    plan: MemoryCapturePlan,
    *,
    owner: Optional[str],
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = validate_memory_capture_plan(vault_dir, plan)
    if plan.action == "discard_duplicate":
        return {
            "success": True,
            "action": plan.action,
            "target_path": plan.target_path,
            "result": {"success": True, "dry_run": False, "results": [], "count": 0},
        }
    result = vault_service.batch_operations(
        vault_dir,
        plan.operations,
        owner=owner,
        tool="obsidian_memory_capture_apply",
        dry_run=False,
        actor=actor,
    )
    if not result.get("success"):
        raise MemoryCaptureValidationError(str(result.get("errors") or "Memory capture apply failed"))
    return {
        "success": True,
        "action": plan.action,
        "target_path": plan.target_path,
        "result": result,
    }


def _route_capture(
    kind: str,
    target: str,
    created: str,
    title: str,
    content: str,
    medium_duplicate: bool,
) -> Tuple[str, str, str]:
    if target == "inbox" or medium_duplicate:
        return "review_queue", "medium", f"AI Memory/Review Queue/{created}-{_slug(title)}.md"
    if kind in {"decision", "rule", "preference", "open_question"} and target in {"auto", "canonical", "append"}:
        return "update_canonical", "medium", CANONICAL_NOTES[kind]
    if kind == "session_log":
        return "create", "low", f"AI Memory/Session Log/{created}-{_slug(title)}.md"
    if kind == "raw" or len(content) > 3500:
        year, month = created.split("-")[:2]
        return "create", "medium", f"AI Memory/Raw/{year}/{month}/{created}-{_slug(title)}.md"
    if kind == "reference":
        return "create", "low", f"AI Memory/References/{created}-{_slug(title)}.md"
    return "review_queue", "medium", f"AI Memory/Review Queue/{created}-{_slug(title)}.md"


def _operations_for_plan(vault_dir: str, action: str, target_path: str, markdown: str, frontmatter: Dict[str, Any]) -> List[Dict[str, Any]]:
    if action == "discard_duplicate":
        return []
    abs_path = vault_service.secure_path(vault_dir, target_path)
    if action in {"append", "update_canonical"}:
        if os.path.exists(abs_path):
            before = vault_service.read_file(vault_dir, target_path)
            content = before.rstrip() + "\n\n" + markdown.strip() + "\n"
            return [{"action": "update_file", "path": target_path, "content": content}]
        content = _render_canonical_header(target_path, frontmatter) + "\n\n" + markdown.strip() + "\n"
        return [{"action": "create_file", "path": target_path, "content": content}]
    return [{"action": "create_file", "path": target_path, "content": markdown}]


def _render_canonical_header(path: str, frontmatter: Dict[str, Any]) -> str:
    fm = dict(frontmatter)
    fm["type"] = "canonical"
    fm["status"] = "active"
    title = os.path.splitext(os.path.basename(path))[0]
    return _yaml_frontmatter(fm) + f"\n# {title}"


def _render_capture_markdown(
    *,
    title: str,
    content: str,
    kind: str,
    created: str,
    source: str,
    source_ref: str,
    scope: str,
    confidence: str,
    tags: List[str],
    links: List[str],
    frontmatter: Dict[str, Any],
    append_mode: bool,
) -> str:
    if append_mode:
        prefix = [
            f"## {created} - {title}",
            "",
            f"Type: `{kind}` | Scope: `{scope}` | Source: `{source}` | Confidence: `{confidence}`",
        ]
        if source_ref.strip():
            prefix.append(f"Source ref: `{source_ref.strip()}`")
        prefix.extend(["", *_body_sections(kind, content), "", _links_section(links), "", "Tags: " + " ".join(tags)])
        return "\n".join(prefix).strip() + "\n"
    return _yaml_frontmatter(frontmatter) + "\n" + "\n".join([
        f"# {title}",
        "",
        *_body_sections(kind, content),
        "",
        _links_section(links),
        "",
        "Tags: " + " ".join(tags),
        "",
    ])


def _body_sections(kind: str, content: str) -> List[str]:
    if kind == "decision":
        return ["## Aussage", "", content, "", "## Kontext", "", "", "## Konsequenz", ""]
    if kind == "rule":
        return ["## Regel", "", content, "", "## Gilt fuer", "", "", "## Ausnahmen", "", "", "## Beispiele", ""]
    if kind == "open_question":
        return ["## Frage", "", content, "", "## Warum offen", "", "", "## Naechster Schritt", ""]
    if kind == "session_log":
        return ["## Kurzfassung", "", content, "", "## Aenderungen", "", "", "## Entscheidungen", "", "", "## Offen", ""]
    return ["## Erkenntnis", "", content]


def _links_section(paths: List[str]) -> str:
    lines = ["## Quellen/Links", ""]
    if not paths:
        lines.append("- Noch keine belastbare Verknuepfung gefunden.")
    else:
        lines.extend(f"- [[{os.path.splitext(path)[0]}]]" for path in paths)
    return "\n".join(lines)


def _frontmatter(kind: str, source: str, scope: str, confidence: str, created: str, request: MemoryCaptureRequest) -> Dict[str, Any]:
    fm: Dict[str, Any] = {
        "type": kind,
        "status": "review" if request.target in {"inbox"} else "active",
        "scope": scope,
        "source": source,
        "created": created,
        "updated": created,
        "confidence": confidence,
    }
    if request.project.strip():
        fm["project"] = normalize_tag_name(request.project)
    if request.source_ref.strip():
        fm["source_ref"] = request.source_ref.strip()
    return fm


def _yaml_frontmatter(frontmatter: Dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _normalize_kind(kind: str, content: str) -> str:
    normalized = normalize_tag_name(kind or "").replace("-", "_")
    if normalized in KINDS:
        return normalized
    text = content.lower()
    if any(term in text for term in ["entscheidung", "decision", "beschlossen", "konsequenz"]):
        return "decision"
    if any(term in text for term in ["regel", "rule", "immer", "nie ", "muss ", "soll "]):
        return "rule"
    if "?" in content or any(term in text for term in ["offene frage", "unklar", "todo"]):
        return "open_question"
    if any(term in text for term in ["preference", "praeferenz", "bevorzugt", "ich mag", "ich will"]):
        return "preference"
    if any(term in text for term in ["meeting", "session", "heute", "zusammenfassung"]):
        return "session_log"
    return "memory" if "memory" in KINDS else "project_note"


def _normalize_choice(value: str, allowed: Set[str], default: str) -> str:
    normalized = normalize_tag_name(value or "").replace("-", "_")
    return normalized if normalized in allowed else default


def _title_for_capture(title: str, content: str, kind: str) -> str:
    if title.strip():
        return title.strip()[:90]
    first_line = next((line.strip("# ").strip() for line in content.splitlines() if line.strip()), "")
    if first_line:
        return first_line[:90]
    return kind.replace("_", " ").title()


def _normalized_requested_tags(tags: Iterable[str]) -> List[str]:
    result: List[str] = []
    for tag in tags or []:
        try:
            value = normalize_hash_tag(tag)
        except Exception:
            continue
        if value not in result:
            result.append(value)
    return result


def _schema_tags(kind: str, source: str, scope: str, confidence: str, project: str, requested: List[str]) -> List[str]:
    tags = ["#memory", f"#type/{kind}", "#status/active", f"#source/{source}", f"#scope/{scope}", f"#confidence/{confidence}"]
    if project.strip():
        tags.append(f"#project/{normalize_tag_name(project)}")
    tags.extend(requested)
    result: List[str] = []
    for tag in tags:
        value = normalize_hash_tag(tag)
        if value not in result:
            result.append(value)
    return result


def _suggest_links(notes: List[Dict[str, Any]], requested_paths: Iterable[str], content: str, title: str) -> List[CaptureCandidate]:
    requested = {normalize_relative_path(path) for path in requested_paths or [] if str(path or "").strip()}
    tokens = _tokens(title + " " + content)
    candidates: List[CaptureCandidate] = []
    for note in notes:
        path = note.get("path", "")
        note_tags = [tag for tag in note.get("tags", []) if tag not in BROAD_TAGS]
        note_text = " ".join([path, note.get("title", ""), " ".join(note_tags)])
        overlap = tokens & _tokens(note_text)
        score = len(overlap) + (8 if path in requested else 0)
        if score > 0:
            reason = "Requested link" if path in requested else "Shared terms: " + ", ".join(sorted(overlap)[:5])
            candidates.append(CaptureCandidate(path=path, title=note.get("title", path), reason=reason, score=score))
    candidates.sort(key=lambda item: (-item.score, item.path.lower()))
    return candidates[:8]


def _duplicate_candidates(vault_dir: str, notes: List[Dict[str, Any]], content: str, title: str, tags: List[str]) -> List[CaptureCandidate]:
    content_tokens = _tokens(title + " " + content)
    content_hash = hashlib.sha256(_normalize_content(content).encode("utf-8")).hexdigest()
    tag_names = {tag.lstrip("#") for tag in tags if tag.lstrip("#") not in BROAD_TAGS}
    candidates: List[CaptureCandidate] = []
    for note in notes:
        path = note.get("path", "")
        try:
            existing = vault_service.read_file(vault_dir, path)
        except OSError:
            existing = ""
        existing_hash = hashlib.sha256(_normalize_content(existing).encode("utf-8")).hexdigest()
        existing_tokens = _tokens(path + " " + note.get("title", "") + " " + existing[:2000])
        overlap = content_tokens & existing_tokens
        shared_tags = tag_names & {tag for tag in note.get("tags", []) if tag not in BROAD_TAGS}
        score = len(overlap) + (len(shared_tags) * 2)
        if existing_hash == content_hash:
            score += 20
        if _slug(title) and _slug(title) in _slug(path):
            score += 4
        if score >= 4:
            reason = "Possible duplicate: " + ", ".join(sorted(overlap)[:5])
            candidates.append(CaptureCandidate(path=path, title=note.get("title", path), reason=reason, score=score))
    candidates.sort(key=lambda item: (-item.score, item.path.lower()))
    return candidates[:8]


def _has_broad_only_tags(tags: List[str]) -> bool:
    meaningful = []
    for tag in tags:
        clean = tag.lstrip("#")
        if clean.startswith(("type/", "status/", "source/", "scope/", "confidence/")):
            continue
        if clean not in BROAD_TAGS:
            meaningful.append(clean)
    return not meaningful


def _normalize_content(content: str) -> str:
    return re.sub(r"\s+", " ", content or "").strip().lower()


def _tokens(value: str) -> Set[str]:
    stop = {"and", "the", "for", "mit", "der", "die", "das", "und", "ein", "eine", "ist", "soll", "this", "that"}
    return {token for token in re.findall(r"[a-z0-9_/-]{3,}", value.lower()) if token not in stop}


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "memory"

import os
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field

from .project_planning import ProjectPlanValidationError, normalize_hash_tag, normalize_relative_path, resolve_inside
from .vault_model import add_manual_relationship, build_vault_index, normalize_tag_name


MEMORY_ACTIONS = {"memory_only", "save_to_obsidian", "append_to_note", "discard"}
NOTE_TYPES = {"decision", "idea", "memory", "meeting", "project", "reference", "resource"}
SOURCES = {"agent", "chat", "document", "file", "mail", "manual", "calendar"}
RELATIONSHIP_TYPES = {"manual", "relates_to", "depends_on", "blocks", "supports"}


class MemoryCandidate(BaseModel):
    title: str = ""
    content: str
    source: str = "manual"
    source_ref: str = ""
    risk: str = "normal"


class SuggestedNote(BaseModel):
    path: str
    title: str
    reason: str = ""
    score: int = 0


class MemoryReviewFile(BaseModel):
    path: str
    mode: str = "create"
    title: str
    type: str = "memory"
    status: str = "review"
    tags: List[str] = Field(default_factory=list)
    frontmatter: Dict[str, Any] = Field(default_factory=dict)
    links: List[str] = Field(default_factory=list)
    content_preview: str = ""
    content: Optional[str] = None


class MemoryReviewRelationship(BaseModel):
    source: str
    target: str
    type: str = "relates_to"
    reason: str = ""


class MemoryReviewNewTag(BaseModel):
    tag: str
    reason: str = ""


class MemoryReviewPlan(BaseModel):
    action: str
    candidate: MemoryCandidate
    target_folder: str = ""
    target_note: str = ""
    files: List[MemoryReviewFile] = Field(default_factory=list)
    relationships: List[MemoryReviewRelationship] = Field(default_factory=list)
    suggested_notes: List[SuggestedNote] = Field(default_factory=list)
    suggested_tags: List[str] = Field(default_factory=list)
    new_tags: List[MemoryReviewNewTag] = Field(default_factory=list)
    conflicts: List[Dict[str, str]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)


class MemoryReviewRequest(BaseModel):
    candidate: MemoryCandidate
    action: str = "save_to_obsidian"
    target_folder: str = "Memory Review"
    target_note: str = ""
    note_type: str = "memory"
    status: str = "review"
    project: str = ""
    tags: List[str] = Field(default_factory=list)
    link_paths: List[str] = Field(default_factory=list)


class MemoryReviewApplyRequest(BaseModel):
    plan: MemoryReviewPlan
    confirm: bool = False


class MemoryReviewValidationError(ValueError):
    pass


def build_memory_review_plan(
    vault_dir: str,
    request: MemoryReviewRequest,
    *,
    today: Optional[date] = None,
) -> MemoryReviewPlan:
    action = _normalize_action(request.action)
    candidate = request.candidate
    if not candidate.content.strip():
        raise MemoryReviewValidationError("Memory candidate content is required")

    target_folder = _normalize_path(request.target_folder)
    target_note = _normalize_path(request.target_note)
    note_type = _normalize_note_type(request.note_type)
    status = normalize_tag_name(request.status or "review")
    source = _normalize_source(candidate.source)
    created = (today or date.today()).isoformat()
    index = build_vault_index(vault_dir)
    existing_notes = {note["path"] for note in index.get("notes", [])}
    existing_tags = {tag["name"] for tag in index.get("tags", [])}
    suggested_notes = _suggest_notes(vault_dir, candidate, request.link_paths)
    chosen_links = _chosen_links(request.link_paths, suggested_notes, existing_notes)
    suggested_tags = _suggest_tags(candidate, request.tags, existing_tags)
    tags = _schema_tags(note_type, status, request.project, source, request.tags, suggested_tags)

    plan = MemoryReviewPlan(
        action=action,
        candidate=candidate,
        target_folder=target_folder,
        target_note=target_note,
        suggested_notes=suggested_notes,
        suggested_tags=suggested_tags,
        warnings=[],
        questions=[],
    )

    if action == "discard":
        plan.warnings.append("Candidate will be discarded; no Obsidian files will be changed.")
        return validate_memory_review_plan(vault_dir, plan, collect_conflicts=True)
    if action == "memory_only":
        plan.warnings.append("Candidate stays in Odysseus memory only; no Obsidian note will be written.")
        return validate_memory_review_plan(vault_dir, plan, collect_conflicts=True)

    if action == "append_to_note":
        if not target_note:
            if suggested_notes:
                target_note = suggested_notes[0].path
            else:
                raise MemoryReviewValidationError("target_note is required when appending to an existing note")
        content = render_memory_append(candidate=candidate, source=source, created=created, tags=tags, links=chosen_links)
        plan.target_note = target_note
        plan.files = [MemoryReviewFile(
            path=target_note,
            mode="append",
            title=_title_for_candidate(candidate),
            type=note_type,
            status=status,
            tags=tags,
            frontmatter={},
            links=chosen_links,
            content_preview=_content_preview(content),
            content=content,
        )]
        return validate_memory_review_plan(vault_dir, plan, collect_conflicts=True)

    title = _title_for_candidate(candidate)
    path = _memory_note_path(target_folder, title, created)
    links = chosen_links
    if not links and suggested_notes:
        links = [_wiki_link(suggested_notes[0].path)]
    if not links:
        plan.warnings.append("No strong existing note match found; review this as an inbox note before treating it as connected context.")
    frontmatter = {
        "type": note_type,
        "status": status,
        "source": source,
        "created": created,
        "updated": created,
    }
    if request.project.strip():
        frontmatter["project"] = normalize_tag_name(request.project)
    if candidate.source_ref.strip():
        frontmatter["source_ref"] = candidate.source_ref.strip()
    content = render_memory_markdown(
        title=title,
        candidate=candidate,
        note_type=note_type,
        status=status,
        source=source,
        created=created,
        tags=tags,
        links=links,
        project=request.project,
    )
    plan.files = [MemoryReviewFile(
        path=path,
        mode="create",
        title=title,
        type=note_type,
        status=status,
        tags=tags,
        frontmatter=frontmatter,
        links=links,
        content_preview=_content_preview(content),
        content=content,
    )]
    plan.relationships = [
        MemoryReviewRelationship(source=path, target=_link_to_path(link), type="relates_to", reason="Memory review linked this note to existing vault context")
        for link in links
        if _link_to_path(link)
    ]
    plan.new_tags = _new_tags_for(tags, existing_tags)
    return validate_memory_review_plan(vault_dir, plan, collect_conflicts=True)


def validate_memory_review_plan(
    vault_dir: str,
    plan: MemoryReviewPlan,
    *,
    collect_conflicts: bool = False,
) -> MemoryReviewPlan:
    plan.action = _normalize_action(plan.action)
    plan.target_folder = _normalize_path(plan.target_folder)
    if not plan.candidate.content.strip():
        raise MemoryReviewValidationError("Memory candidate content is required")

    existing_notes = set(_markdown_notes(vault_dir))
    planned_paths: Set[str] = set()
    conflicts: List[Dict[str, str]] = []
    warnings = list(plan.warnings)

    if plan.action in {"memory_only", "discard"}:
        if plan.files:
            raise MemoryReviewValidationError(f"{plan.action} must not contain file changes")
        plan.conflicts = []
        return plan

    for planned in plan.files:
        planned.path = _normalize_path(planned.path)
        if not planned.path.lower().endswith(".md"):
            raise MemoryReviewValidationError(f"Review file must be markdown: {planned.path}")
        if planned.mode not in {"create", "append"}:
            raise MemoryReviewValidationError(f"Unsupported review file mode: {planned.mode}")
        _resolve_inside(vault_dir, planned.path)
        if planned.mode == "create":
            if planned.path.lower() in {p.lower() for p in planned_paths}:
                raise MemoryReviewValidationError(f"Duplicate planned file path: {planned.path}")
            planned_paths.add(planned.path)
            if os.path.exists(os.path.join(vault_dir, planned.path)):
                conflicts.append({"path": planned.path, "reason": "file_exists"})
            _validate_review_tags(planned.tags)
            if planned.frontmatter.get("source") not in SOURCES:
                raise MemoryReviewValidationError(f"Invalid source for {planned.path}")
        if planned.mode == "append" and planned.path not in existing_notes:
            raise MemoryReviewValidationError(f"Append target does not exist: {planned.path}")

    allowed_relationship_sources = planned_paths | existing_notes
    allowed_relationship_targets = planned_paths | existing_notes
    for relationship in plan.relationships:
        relationship.source = _normalize_path(relationship.source)
        relationship.target = _normalize_path(relationship.target)
        if relationship.source not in allowed_relationship_sources:
            raise MemoryReviewValidationError(f"Relationship source does not exist: {relationship.source}")
        if relationship.target not in allowed_relationship_targets:
            raise MemoryReviewValidationError(f"Relationship target does not exist: {relationship.target}")
        if relationship.source == relationship.target:
            raise MemoryReviewValidationError("Relationship source and target must differ")
        if relationship.type not in RELATIONSHIP_TYPES:
            raise MemoryReviewValidationError(f"Unsupported relationship type: {relationship.type}")

    for new_tag in plan.new_tags:
        normalized = normalize_hash_tag(new_tag.tag)
        if new_tag.tag != normalized:
            new_tag.tag = normalized
        if not new_tag.reason:
            raise MemoryReviewValidationError(f"New tag needs a reason: {new_tag.tag}")

    if plan.action == "save_to_obsidian" and not any(file.links for file in plan.files):
        warnings.append("Saved note has no confirmed links; keep it in review/inbox until context is added.")

    plan.conflicts = conflicts if collect_conflicts else plan.conflicts
    plan.warnings = sorted(set(warnings))
    return plan


def apply_memory_review_plan(vault_dir: str, plan: MemoryReviewPlan) -> Dict[str, Any]:
    plan = validate_memory_review_plan(vault_dir, plan, collect_conflicts=True)
    if plan.conflicts:
        raise MemoryReviewValidationError("Plan has file conflicts")
    if plan.action in {"memory_only", "discard"}:
        return {"success": True, "action": plan.action, "created_files": [], "updated_files": [], "relationships": [], "graph": build_vault_index(vault_dir)["graph"]}

    created: List[str] = []
    updated: List[Dict[str, str]] = []
    relationships: List[Dict[str, Any]] = []
    for planned in plan.files:
        abs_path = _resolve_inside(vault_dir, planned.path)
        if planned.mode == "create":
            if os.path.exists(abs_path):
                raise MemoryReviewValidationError(f"File already exists: {planned.path}")
            parent = os.path.dirname(abs_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(planned.content or planned.content_preview)
            created.append(planned.path)
        elif planned.mode == "append":
            with open(abs_path, "r", encoding="utf-8") as fh:
                before = fh.read()
            addition = planned.content or planned.content_preview
            separator = "\n\n" if before.strip() else ""
            after = before.rstrip() + separator + addition.strip() + "\n"
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(after)
            updated.append({"path": planned.path, "before": before, "after": after})

    for relationship in plan.relationships:
        payload = relationship.model_dump() if hasattr(relationship, "model_dump") else relationship.dict()
        relationships.append(add_manual_relationship(vault_dir, payload))

    graph = build_vault_index(vault_dir)["graph"]
    return {
        "success": True,
        "action": plan.action,
        "created_files": created,
        "updated_files": [item["path"] for item in updated],
        "updated_file_details": updated,
        "relationships": relationships,
        "graph": {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
        },
    }


def render_memory_markdown(
    *,
    title: str,
    candidate: MemoryCandidate,
    note_type: str,
    status: str,
    source: str,
    created: str,
    tags: Iterable[str],
    links: Iterable[str],
    project: str = "",
) -> str:
    frontmatter = [
        "---",
        f"type: {note_type}",
        f"status: {status}",
        f"source: {source}",
        f"created: {created}",
        f"updated: {created}",
    ]
    if project.strip():
        frontmatter.append(f"project: {normalize_tag_name(project)}")
    if candidate.source_ref.strip():
        frontmatter.append(f"source_ref: {candidate.source_ref.strip()}")
    frontmatter.extend(["---", ""])
    body = [
        f"# {title}",
        "",
        "## Erkenntnis",
        "",
        candidate.content.strip(),
        "",
        "## Verknuepfte Notizen",
        "",
    ]
    link_lines = [f"- {link}" for link in links] or ["- Noch keine belastbare Verknuepfung gefunden."]
    source_lines = [
        "",
        "## Quelle",
        "",
        f"- Typ: {source}",
        f"- Referenz: {candidate.source_ref.strip() or 'nicht angegeben'}",
        f"- Review-Risiko: {candidate.risk.strip() or 'normal'}",
        "",
        "Tags: " + " ".join(tags),
        "",
    ]
    return "\n".join(frontmatter + body + link_lines + source_lines)


def render_memory_append(
    *,
    candidate: MemoryCandidate,
    source: str,
    created: str,
    tags: Iterable[str],
    links: Iterable[str],
) -> str:
    lines = [
        f"## Memory Review {created}",
        "",
        candidate.content.strip(),
        "",
        f"Quelle: {source}" + (f" ({candidate.source_ref.strip()})" if candidate.source_ref.strip() else ""),
        "",
    ]
    if links:
        lines.extend(["Verknuepfte Notizen:", "", *[f"- {link}" for link in links], ""])
    lines.append("Tags: " + " ".join(tags))
    return "\n".join(lines)


def _normalize_action(action: str) -> str:
    normalized = str(action or "save_to_obsidian").strip().lower()
    if normalized not in MEMORY_ACTIONS:
        raise MemoryReviewValidationError(f"Unsupported memory review action: {action}")
    return normalized


def _normalize_path(path: str) -> str:
    try:
        return normalize_relative_path(path)
    except ProjectPlanValidationError as exc:
        raise MemoryReviewValidationError(str(exc))


def _resolve_inside(vault_dir: str, path: str) -> str:
    try:
        return resolve_inside(vault_dir, path)
    except ProjectPlanValidationError as exc:
        raise MemoryReviewValidationError(str(exc))


def _normalize_note_type(note_type: str) -> str:
    normalized = normalize_tag_name(note_type or "memory").replace("-", "_")
    return normalized if normalized in NOTE_TYPES else "memory"


def _normalize_source(source: str) -> str:
    normalized = normalize_tag_name(source or "manual").replace("-", "_")
    return normalized if normalized in SOURCES else "manual"


def _title_for_candidate(candidate: MemoryCandidate) -> str:
    if candidate.title.strip():
        return candidate.title.strip()[:80]
    words = re.findall(r"[A-Za-z0-9]+", candidate.content.strip())
    return " ".join(words[:8])[:80] or "Reviewed Memory"


def _memory_note_path(target_folder: str, title: str, created: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title.lower()).strip("-") or "reviewed-memory"
    filename = f"{created}-{slug}.md"
    return "/".join(part for part in [target_folder, filename] if part)


def _schema_tags(note_type: str, status: str, project: str, source: str, requested: Iterable[str], suggested: Iterable[str]) -> List[str]:
    tags = ["#memory", f"#type/{note_type}", f"#status/{status}", f"#source/{source}"]
    if project.strip():
        tags.append(f"#project/{normalize_tag_name(project)}")
    tags.extend(requested or [])
    tags.extend(f"#{tag}" for tag in suggested or [])
    normalized = []
    for tag in tags:
        value = normalize_hash_tag(tag)
        if value not in normalized:
            normalized.append(value)
    return normalized


def _validate_review_tags(tags: List[str]) -> None:
    normalized = {normalize_hash_tag(tag) for tag in tags}
    required = {"#memory"}
    if not required.issubset(normalized):
        raise MemoryReviewValidationError("Memory tag is required")
    if not any(tag.startswith("#type/") for tag in normalized):
        raise MemoryReviewValidationError("A type tag is required")
    if not any(tag.startswith("#status/") for tag in normalized):
        raise MemoryReviewValidationError("A status tag is required")


def _suggest_notes(vault_dir: str, candidate: MemoryCandidate, requested_paths: Iterable[str]) -> List[SuggestedNote]:
    requested = {normalize_relative_path(path) for path in requested_paths or [] if str(path or "").strip()}
    index = build_vault_index(vault_dir)
    candidate_tokens = _tokens(" ".join([candidate.title, candidate.content, candidate.source_ref]))
    suggestions: List[SuggestedNote] = []
    for note in index.get("notes", []):
        path = note["path"]
        content = _read_note(vault_dir, path)
        note_tokens = _tokens(" ".join([path, note.get("title", ""), " ".join(note.get("tags", [])), content]))
        overlap = candidate_tokens & note_tokens
        score = len(overlap) + (5 if path in requested else 0)
        if score > 0:
            reason = "Requested by review" if path in requested else "Shared terms: " + ", ".join(sorted(overlap)[:5])
            suggestions.append(SuggestedNote(path=path, title=note.get("title", path), reason=reason, score=score))
    suggestions.sort(key=lambda item: (-item.score, item.path.lower()))
    return suggestions[:5]


def _suggest_tags(candidate: MemoryCandidate, requested_tags: Iterable[str], existing_tags: Set[str]) -> List[str]:
    requested = {normalize_tag_name(tag) for tag in requested_tags or []}
    tokens = _tokens(" ".join([candidate.title, candidate.content, candidate.source_ref]))
    matches = sorted(tag for tag in existing_tags if tag in requested or tag.split("/")[-1] in tokens)
    return matches[:8]


def _chosen_links(link_paths: Iterable[str], suggestions: List[SuggestedNote], existing_notes: Set[str]) -> List[str]:
    paths = [normalize_relative_path(path) for path in link_paths or [] if str(path or "").strip()]
    for suggestion in suggestions[:3]:
        if suggestion.path not in paths:
            paths.append(suggestion.path)
    valid = []
    for path in paths:
        if path in existing_notes and _wiki_link(path) not in valid:
            valid.append(_wiki_link(path))
    return valid


def _new_tags_for(tags: Iterable[str], existing_tags: Set[str]) -> List[MemoryReviewNewTag]:
    result = []
    for tag in sorted({normalize_hash_tag(tag) for tag in tags}):
        name = tag[1:]
        if name not in existing_tags:
            result.append(MemoryReviewNewTag(tag=tag, reason="Required by Phase 5 memory review note schema or explicit review input"))
    return result


def _markdown_notes(vault_dir: str) -> List[str]:
    notes: List[str] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [d for d in dirs if d != ".obsidian"]
        for file in files:
            if file.lower().endswith(".md"):
                notes.append(os.path.relpath(os.path.join(root, file), vault_dir).replace("\\", "/"))
    notes.sort(key=str.lower)
    return notes


def _read_note(vault_dir: str, path: str) -> str:
    try:
        with open(os.path.join(vault_dir, path), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _wiki_link(path: str) -> str:
    stem = os.path.splitext(path)[0].replace("\\", "/")
    return f"[[{stem}]]"


def _link_to_path(link: str) -> Optional[str]:
    match = re.match(r"\[\[([^\]|#]+)", str(link or "").strip())
    if not match:
        return None
    target = match.group(1).strip()
    if not target.lower().endswith(".md"):
        target = f"{target}.md"
    return normalize_relative_path(target)


def _tokens(value: str) -> Set[str]:
    stop = {"and", "the", "for", "mit", "der", "die", "das", "und", "ein", "eine", "ist", "soll"}
    return {token for token in re.findall(r"[a-z0-9]{3,}", value.lower()) if token not in stop}


def _content_preview(content: str) -> str:
    lines = [line for line in content.splitlines() if line.strip()]
    return "\n".join(lines[:10])

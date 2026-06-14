import difflib
import os
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from pydantic import BaseModel, Field

from . import vault_service
from .vault_model import build_vault_index, build_vault_embedding_index
from .vault_rules import RULES_NOTE_PATH


CANONICAL_PATHS = [
    "AI Memory/Canonical/User Preferences.md",
    "AI Memory/Canonical/Odysseus Architecture.md",
    "AI Memory/Canonical/Obsidian MCP Rules.md",
    "AI Memory/Canonical/Open Decisions.md",
]
IGNORED_DIRS = {".obsidian", ".trash", "__pycache__"}
BROAD_TAG_THRESHOLD = 100


class SparkAnalyzeRequest(BaseModel):
    scope: str = "vault"
    path: str = ""
    tag: str = ""
    limit: int = 5000


class SparkHealth(BaseModel):
    total_notes: int = 0
    total_tags: int = 0
    largest_tags: List[Dict[str, Any]] = Field(default_factory=list)
    broad_tags: List[Dict[str, Any]] = Field(default_factory=list)
    orphan_notes: List[str] = Field(default_factory=list)
    review_queue_count: int = 0
    canonical_coverage: Dict[str, bool] = Field(default_factory=dict)
    stale_open_questions: List[str] = Field(default_factory=list)
    missing_frontmatter: List[str] = Field(default_factory=list)
    notes_without_links: List[str] = Field(default_factory=list)
    duplicate_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    folder_distribution: List[Dict[str, Any]] = Field(default_factory=list)
    analyzed_notes: int = 0
    truncated: bool = False


class SparkAction(BaseModel):
    id: str
    type: str
    risk: str
    paths: List[str] = Field(default_factory=list)
    target_path: str = ""
    preview_markdown: str = ""
    operations: List[Dict[str, Any]] = Field(default_factory=list)
    reason: str = ""
    requires_confirmation: bool = True


class SparkPlan(BaseModel):
    id: str
    created: str
    scope: str = "vault"
    summary: str
    health: SparkHealth
    actions: List[SparkAction] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SparkApplyRequest(BaseModel):
    plan: SparkPlan
    confirm: bool = False
    selected_action_ids: List[str] = Field(default_factory=list)


class SparkValidationError(ValueError):
    pass


def analyze_memory_health(vault_dir: str, request: Optional[SparkAnalyzeRequest] = None) -> SparkHealth:
    request = request or SparkAnalyzeRequest()
    index = build_vault_index(vault_dir)
    notes = _filtered_notes(index.get("notes", []), request)
    limit = max(1, int(request.limit or 5000))
    truncated = len(notes) > limit
    notes = notes[:limit]

    graph = index.get("graph", {})
    incoming, outgoing = _link_counts(graph)
    tags = Counter()
    folders = Counter()
    missing_frontmatter: List[str] = []
    notes_without_links: List[str] = []
    stale_open_questions: List[str] = []
    duplicate_buckets: Dict[str, List[str]] = defaultdict(list)

    for note in notes:
        path = note.get("path", "")
        folders[_folder(path)] += 1
        for tag in note.get("tags", []):
            tags[tag] += 1
        if not _has_frontmatter(vault_dir, path):
            missing_frontmatter.append(path)
        if incoming.get(path, 0) == 0 and outgoing.get(path, 0) == 0:
            notes_without_links.append(path)
        if _is_stale_open_question(vault_dir, path):
            stale_open_questions.append(path)
        bucket = _duplicate_bucket(vault_dir, path, note.get("title", ""))
        if bucket:
            duplicate_buckets[bucket].append(path)

    orphan_notes = sorted(set(notes_without_links), key=str.lower)[:50]
    duplicate_candidates = [
        {"paths": sorted(paths, key=str.lower), "reason": "Similar title or content fingerprint"}
        for paths in duplicate_buckets.values()
        if len(paths) > 1
    ][:20]
    largest_tags = [{"name": tag, "count": count} for tag, count in tags.most_common(15)]
    broad_tags = [
        {"name": tag, "count": count, "quality": "broad"}
        for tag, count in tags.most_common()
        if count >= BROAD_TAG_THRESHOLD or tag in {"memory", "ai", "ki", "project", "note"}
    ][:20]

    return SparkHealth(
        total_notes=len(notes),
        total_tags=len(tags),
        largest_tags=largest_tags,
        broad_tags=broad_tags,
        orphan_notes=orphan_notes,
        review_queue_count=sum(1 for note in notes if note.get("path", "").startswith("AI Memory/Review Queue/")),
        canonical_coverage={path: os.path.exists(vault_service.secure_path(vault_dir, path)) for path in CANONICAL_PATHS},
        stale_open_questions=sorted(stale_open_questions, key=str.lower)[:50],
        missing_frontmatter=sorted(missing_frontmatter, key=str.lower)[:50],
        notes_without_links=sorted(notes_without_links, key=str.lower)[:50],
        duplicate_candidates=duplicate_candidates,
        folder_distribution=[{"folder": folder, "count": count} for folder, count in folders.most_common(12)],
        analyzed_notes=len(notes),
        truncated=truncated,
    )


def build_spark_plan(vault_dir: str, request: Optional[SparkAnalyzeRequest] = None) -> SparkPlan:
    request = request or SparkAnalyzeRequest()
    health = analyze_memory_health(vault_dir, request)
    created = datetime.now(timezone.utc).isoformat()
    actions: List[SparkAction] = []
    warnings: List[str] = []

    missing_canonicals = [path for path, exists in health.canonical_coverage.items() if not exists]
    for path in missing_canonicals:
        markdown = _canonical_stub(path)
        actions.append(SparkAction(
            id=uuid.uuid4().hex,
            type="update_canonical",
            risk="medium",
            paths=[],
            target_path=path,
            preview_markdown=markdown,
            operations=[{"action": "create_file", "path": path, "content": markdown}],
            reason="Canonical memory note is missing.",
        ))

    if health.broad_tags:
        target = f"AI Memory/Summaries/{date.today().isoformat()} Broad Tag Review.md"
        markdown = _broad_tag_summary(health)
        actions.append(SparkAction(
            id=uuid.uuid4().hex,
            type="create_summary",
            risk="low",
            target_path=target,
            preview_markdown=markdown,
            operations=[{"action": "create_file", "path": target, "content": markdown}],
            reason="Broad tags reduce retrieval quality; create a review summary before retagging.",
        ))

    if health.orphan_notes:
        target = f"AI Memory/Summaries/{date.today().isoformat()} Orphan Notes.md"
        markdown = _orphan_summary(health)
        actions.append(SparkAction(
            id=uuid.uuid4().hex,
            type="create_summary",
            risk="low",
            paths=health.orphan_notes[:50],
            target_path=target,
            preview_markdown=markdown,
            operations=[{"action": "create_file", "path": target, "content": markdown}],
            reason="Orphan notes have no graph context and should be reviewed for links or archive.",
        ))

    if health.missing_frontmatter:
        retag_paths = health.missing_frontmatter[:50]
        retag_ops, retag_preview = _build_retag_operations(vault_dir, retag_paths)
        actions.append(SparkAction(
            id=uuid.uuid4().hex,
            type="retag",
            risk="medium",
            paths=retag_paths,
            reason="Notes without frontmatter are harder to rank and classify.",
            preview_markdown=retag_preview,
            operations=retag_ops,
        ))

    for group in health.duplicate_candidates[:5]:
        paths = group.get("paths", [])
        if len(paths) < 2:
            continue

        # Find similar pairs within the group
        similar_pairs = _find_similar_pairs(vault_dir, [paths], similarity_threshold=0.65)

        if not similar_pairs:
            # No high-confidence similarity found — still show for manual review
            actions.append(SparkAction(
                id=uuid.uuid4().hex,
                type="merge_candidate",
                risk="high",
                paths=paths,
                reason=group.get("reason", "Potential duplicate notes — verify manually."),
                preview_markdown=_generate_merge_preview(vault_dir, paths, paths[0], paths[1:], []),
                operations=[],
            ))
            continue

        # Deduplicate to connected components
        merged_groups = _group_similar_pairs(paths, similar_pairs)
        for mg in merged_groups:
            if len(mg) < 2:
                continue
            primary = _select_primary(vault_dir, mg)
            secondaries = [p for p in mg if p != primary]
            ops, preview = _build_merge_operations(vault_dir, mg, primary, secondaries)

            actions.append(SparkAction(
                id=uuid.uuid4().hex,
                type="merge_candidate",
                risk="high",
                paths=mg,
                reason=f"Smart merge: {len(secondaries)} duplicate(s) into `{os.path.basename(primary)}` "
                       f"(similarity: {', '.join(f'{s:.0%}' for _, _, s, _ in similar_pairs if _ in mg and s >= 0.65)})",
                preview_markdown=preview,
                operations=ops if ops else [],
            ))

    if health.truncated:
        warnings.append("Spark analysis reached the note limit; rerun scoped to a folder or tag for deeper cleanup.")

    summary = (
        f"Analyzed {health.analyzed_notes} notes, found {len(health.broad_tags)} broad tags, "
        f"{len(health.orphan_notes)} orphan candidates, {len(health.missing_frontmatter)} notes without frontmatter, "
        f"and {len(health.duplicate_candidates)} duplicate groups."
    )
    return SparkPlan(
        id=uuid.uuid4().hex,
        created=created,
        scope=request.scope,
        summary=summary,
        health=health,
        actions=actions,
        warnings=warnings,
    )


def apply_spark_plan(
    vault_dir: str,
    request: SparkApplyRequest,
    *,
    owner: Optional[str],
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not request.confirm:
        raise SparkValidationError("Confirmation required before applying Spark actions")
    selected = set(request.selected_action_ids or [])
    if not selected:
        raise SparkValidationError("Select at least one Spark action to apply")

    operations: List[Dict[str, Any]] = []
    applied_actions: List[str] = []
    skipped_actions: List[Dict[str, str]] = []
    warnings: List[str] = []
    for action in request.plan.actions:
        if action.id not in selected:
            continue
        if action.risk == "high" and not action.operations:
            skipped_actions.append({"id": action.id, "reason": "High-risk action has no automatic operations — review manually"})
            continue
        if not action.operations:
            skipped_actions.append({"id": action.id, "reason": "Action has no automatic operations"})
            continue
        if action.risk == "high":
            warnings.append(f"Applying high-risk action '{action.type}' on {len(action.paths)} path(s)")
        operations.extend(action.operations)
        applied_actions.append(action.id)

    if not operations:
        return {"success": True, "applied_actions": [], "skipped_actions": skipped_actions, "warnings": warnings, "result": {"success": True, "count": 0}}

    result = vault_service.batch_operations(
        vault_dir,
        operations,
        owner=owner,
        tool="obsidian_spark_apply",
        dry_run=False,
        actor=actor,
    )
    if not result.get("success"):
        raise SparkValidationError(str(result.get("errors") or "Spark apply failed"))
    return {
        "success": True,
        "applied_actions": applied_actions,
        "skipped_actions": skipped_actions,
        "warnings": warnings,
        "result": result,
    }


def _filtered_notes(notes: List[Dict[str, Any]], request: SparkAnalyzeRequest) -> List[Dict[str, Any]]:
    scope = (request.scope or "vault").strip().lower()
    path_prefix = request.path.strip("/").replace("\\", "/")
    tag = request.tag.strip().lower().lstrip("#")
    result = []
    for note in notes:
        path = note.get("path", "")
        if path == RULES_NOTE_PATH:
            continue
        if any(part in IGNORED_DIRS for part in path.split("/")):
            continue
        if scope == "folder" and path_prefix and not path.startswith(path_prefix.rstrip("/") + "/"):
            continue
        if scope == "tag" and tag and tag not in [str(t).lower().lstrip("#") for t in note.get("tags", [])]:
            continue
        if scope == "current_note" and path_prefix and path != path_prefix:
            continue
        result.append(note)
    result.sort(key=lambda item: item.get("path", "").lower())
    return result


def _link_counts(graph: Dict[str, Any]) -> tuple[Counter, Counter]:
    incoming: Counter = Counter()
    outgoing: Counter = Counter()
    for edge in graph.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        if source:
            outgoing[source] += 1
        if target:
            incoming[target] += 1
    return incoming, outgoing


def _has_frontmatter(vault_dir: str, path: str) -> bool:
    try:
        content = vault_service.read_file(vault_dir, path)
    except OSError:
        return True
    return content.lstrip().startswith("---")


def _is_stale_open_question(vault_dir: str, path: str) -> bool:
    if "Offene Fragen" not in path and "Open Questions" not in path:
        return False
    try:
        stat = os.stat(vault_service.secure_path(vault_dir, path))
    except OSError:
        return False
    age_days = (datetime.now(timezone.utc).timestamp() - stat.st_mtime) / 86400
    return age_days > 30


def _duplicate_bucket(vault_dir: str, path: str, title: str) -> str:
    """Legacy coarse bucket — used as initial grouping before fine-grained similarity."""
    try:
        content = vault_service.read_file(vault_dir, path)
    except OSError:
        content = ""
    normalized_title = re.sub(r"[^a-z0-9]+", " ", (title or os.path.basename(path)).lower()).strip()
    first_words = " ".join(re.findall(r"[a-z0-9]{4,}", content.lower())[:12])
    bucket = normalized_title or first_words
    return bucket[:80]


def _find_similar_pairs(
    vault_dir: str,
    duplicate_groups: List[List[str]],
    similarity_threshold: float = 0.72,
) -> List[Tuple[str, str, float, str]]:
    """Refine coarse duplicate groups into high-confidence similar pairs.

    Returns list of (path_a, path_b, similarity_score, method) tuples
    where method is 'embedding' or 'textdiff'.
    """
    pairs: List[Tuple[str, str, float, str]] = []

    # Try embedding-based similarity first
    emb_matrix, emb_paths, _ = build_vault_embedding_index(vault_dir)
    emb_lookup: Dict[str, int] = {}
    if emb_matrix.size > 0:
        emb_lookup = {p: i for i, p in enumerate(emb_paths)}

    for group in duplicate_groups:
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                path_a, path_b = group[i], group[j]
                score, method = _similarity(vault_dir, path_a, path_b, emb_matrix, emb_lookup)
                if score >= similarity_threshold:
                    pairs.append((path_a, path_b, round(score, 3), method))
    return pairs


def _similarity(
    vault_dir: str,
    path_a: str,
    path_b: str,
    emb_matrix: np.ndarray,
    emb_lookup: Dict[str, int],
) -> Tuple[float, str]:
    """Compute similarity between two notes. Returns (score, method)."""
    # 1. Try embedding cosine similarity
    if emb_matrix.size > 0 and path_a in emb_lookup and path_b in emb_lookup:
        a_vec = emb_matrix[emb_lookup[path_a]]
        b_vec = emb_matrix[emb_lookup[path_b]]
        cos_sim = float(np.dot(a_vec, b_vec))
        if cos_sim >= 0.72:
            return cos_sim, "embedding"

    # 2. Fallback: text-based difflib
    try:
        content_a = vault_service.read_file(vault_dir, path_a)
        content_b = vault_service.read_file(vault_dir, path_b)
    except OSError:
        return 0.0, "error"

    # Strip frontmatter for fair comparison
    body_a = _strip_frontmatter(content_a)
    body_b = _strip_frontmatter(content_b)

    if not body_a.strip() or not body_b.strip():
        return 0.0, "empty"

    ratio = difflib.SequenceMatcher(None, body_a, body_b).ratio()
    return ratio, "textdiff"


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter delimited by ---."""
    if not content.lstrip().startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) >= 3:
        return parts[2]
    return content


def _build_merge_operations(
    vault_dir: str,
    group: List[str],
    primary: str,
    secondaries: List[str],
) -> Tuple[List[Dict[str, Any]], str]:
    """Generate batch ops to merge secondaries into the primary note.

    Operations:
      1. Append unique sections from each secondary into primary
      2. Delete secondaries (or move to .trash/)
      3. Note: Wikilink rewriting must be done separately after all merges

    Returns (operations, preview_markdown).
    """
    operations: List[Dict[str, Any]] = []
    preview_lines = [
        f"# Merge Preview: {os.path.basename(primary)}",
        "",
        f"**Primary (kept):** `{primary}`",
        "",
    ]

    try:
        primary_content = vault_service.read_file(vault_dir, primary)
    except OSError:
        return [], f"Error: cannot read primary `{primary}`"

    merged_body = _strip_frontmatter(primary_content)
    frontmatter = _extract_frontmatter(primary_content)

    for sec in secondaries:
        preview_lines.append(f"### Merging: `{sec}`")
        try:
            sec_content = vault_service.read_file(vault_dir, sec)
        except OSError:
            preview_lines.append(f"  ⚠ Cannot read — skipped")
            continue

        sec_body = _strip_frontmatter(sec_content)
        sec_fm = _extract_frontmatter(sec_content)

        # Merge frontmatter: take newest 'updated' date
        if sec_fm.get("updated", "") > frontmatter.get("updated", ""):
            frontmatter["updated"] = sec_fm["updated"]
        # Merge tags
        existing_tags = set(frontmatter.get("tags", []))
        for tag in sec_fm.get("tags", []):
            if tag not in existing_tags:
                existing_tags.add(tag)
                frontmatter.setdefault("tags", []).append(tag)

        # Find sections in secondary that aren't in primary
        new_blocks = _extract_new_blocks(merged_body, sec_body)
        if new_blocks:
            preview_lines.append(f"  + {len(new_blocks)} unique block(s) appended")
            merged_body += "\n\n" + "\n\n".join(new_blocks)
        else:
            preview_lines.append(f"  (no new content — fully redundant)")

        # Schedule deletion of secondary
        operations.append({
            "action": "delete_file",
            "path": sec,
        })
        preview_lines.append("")

    # Rebuild primary with merged frontmatter
    new_content = _rebuild_note(frontmatter, merged_body)
    operations.insert(0, {
        "action": "update_file",
        "path": primary,
        "content": new_content,
    })

    preview = "\n".join(preview_lines)
    return operations, preview


def _extract_frontmatter(content: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from markdown content."""
    if not content.lstrip().startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 2:
        return {}
    fm_text = parts[1].strip()
    result: Dict[str, Any] = {}
    list_key: Optional[str] = None
    for line in fm_text.split("\n"):
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and list_key:
            result.setdefault(list_key, []).append(line.strip()[2:].strip())
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val == "":
                list_key = key
                result[key] = []
            else:
                list_key = None
                result[key] = val
    return result


def _rebuild_note(frontmatter: Dict[str, Any], body: str) -> str:
    """Reassemble a note from frontmatter dict + body."""
    if not frontmatter:
        return body
    fm_lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            if not value:
                fm_lines.append(f"{key}:")
            else:
                fm_lines.append(f"{key}:")
                for item in value:
                    fm_lines.append(f"  - {item}")
        else:
            fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")
    return "\n".join(fm_lines) + "\n\n" + body.lstrip()


def _extract_new_blocks(existing_body: str, new_body: str) -> List[str]:
    """Find sections/paragraphs in new_body not already in existing_body."""
    # Split both into #-heading-delimited blocks (or paragraphs)
    existing_blocks = _split_blocks(existing_body)
    new_blocks = _split_blocks(new_body)
    existing_norm = {_normalize_block(b): b for b in existing_blocks if _normalize_block(b)}

    unique: List[str] = []
    for block in new_blocks:
        norm = _normalize_block(block)
        if norm and norm not in existing_norm:
            unique.append(block)
    return unique


def _split_blocks(text: str) -> List[str]:
    """Split text into heading-delimited blocks."""
    blocks: List[str] = []
    current: List[str] = []
    for line in text.split("\n"):
        if line.startswith("#"):
            if current:
                blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def _normalize_block(block: str) -> str:
    """Normalize a block for comparison: lowercase, strip whitespace, remove links."""
    text = block.lower().strip()
    # Remove wikilinks [[...]] and markdown links [...](...)
    text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text[:200]  # first 200 chars as fingerprint


def _generate_merge_preview(
    vault_dir: str,
    group: List[str],
    primary: str,
    secondaries: List[str],
    similarities: List[Tuple[str, str, float, str]],
) -> str:
    """Human-readable merge preview for the Spark plan UI."""
    lines = [
        f"## Merge Group: {os.path.basename(primary)}",
        "",
        f"**Primary (kept):** `{primary}`",
        "",
        "| Note | Similarity | Method |",
        "|------|-----------|--------|",
    ]
    for path_a, path_b, score, method in similarities:
        if path_a in group and path_b in group:
            name = os.path.basename(path_b)
            lines.append(f"| `{name}` | {score:.1%} | {method} |")
    lines.append("")
    lines.append(f"**Secondaries will be deleted** after their unique content is merged into the primary.")
    lines.append("")
    lines.append("### After merge:")
    lines.append(f"- All unique sections from {len(secondaries)} note(s) appended to `{primary}`")
    lines.append(f"- Tags and frontmatter merged (newest `updated` date kept)")
    lines.append(f"- Secondaries moved to `.trash/`")
    lines.append("")
    lines.append("⚠ **Review carefully** — this is a destructive operation.")
    return "\n".join(lines)


def _group_similar_pairs(
    paths: List[str],
    pairs: List[Tuple[str, str, float, str]],
) -> List[List[str]]:
    """Cluster paths into connected components based on similar pairs."""
    # Union-find
    parent = {p: p for p in paths}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b, _, _ in pairs:
        union(a, b)

    groups: Dict[str, List[str]] = defaultdict(list)
    for p in paths:
        groups[find(p)].append(p)

    return [sorted(g, key=str.lower) for g in groups.values() if len(g) >= 2]


def _select_primary(vault_dir: str, group: List[str]) -> str:
    """Choose the best note to keep as primary when merging duplicates.

    Heuristic: longest content > most incoming links > newest mtime.
    """
    best = group[0]
    best_score = -1.0

    for path in group:
        score = 0.0
        try:
            content = vault_service.read_file(vault_dir, path)
            score += len(content) * 0.001  # ~1 point per 1000 chars
        except OSError:
            pass
        try:
            mtime = os.path.getmtime(vault_service.secure_path(vault_dir, path))
            score += mtime * 0.0000001  # tiny boost for newer
        except (OSError, ValueError):
            pass

        if score > best_score:
            best_score = score
            best = path

    return best


def _folder(path: str) -> str:
    folder = "/".join(path.replace("\\", "/").split("/")[:-1])
    return folder or "/"


def _canonical_stub(path: str) -> str:
    title = os.path.splitext(os.path.basename(path))[0]
    today = date.today().isoformat()
    return "\n".join([
        "---",
        "type: canonical",
        "status: active",
        "scope: global",
        "source: spark",
        f"created: {today}",
        f"updated: {today}",
        "tags: [memory, canonical]",
        "---",
        "",
        f"# {title}",
        "",
        "## Zweck",
        "",
        "Diese kanonische Notiz buendelt stabile Langzeitgedaechtnis-Inhalte zu diesem Thema.",
        "",
        "## Aktueller Stand",
        "",
        "- Noch nicht kuratiert.",
        "",
    ])


def _build_retag_operations(vault_dir: str, paths: List[str]) -> Tuple[List[Dict[str, Any]], str]:
    """Generate frontmatter-insert operations for notes missing frontmatter.

    Reads each note, tries to guess its kind from content, and prepends
    schema-compliant YAML frontmatter with #memory, #type/, #status/, #source tags.
    """
    today = date.today().isoformat()
    ops: List[Dict[str, Any]] = []
    preview_lines: List[str] = []

    # Keywords from memory_capture._normalize_kind
    kind_hints = [
        (["entscheidung", "decision", "beschlossen", "konsequenz"], "decision"),
        (["regel", "rule", "immer ", "nie ", "muss ", "soll "], "rule"),
        (["offene frage", "unklar", "todo", "open question"], "open_question"),
        (["praeferenz", "preference", "bevorzugt", "ich mag", "ich will"], "preference"),
        (["meeting", "session", "heute", "zusammenfassung", "session log"], "session_log"),
        (["referenz", "reference", "quelle", "source"], "reference"),
        (["projekt", "project", "roadmap", "meilenstein"], "project_note"),
    ]

    for path in paths:
        try:
            content = vault_service.read_file(vault_dir, path)
        except OSError:
            preview_lines.append(f"- ⚠ `{path}`: unreadable — skipped")
            continue

        # Guess kind from content
        content_lower = content.lower()
        guessed_kind = "memory"
        for keywords, kind in kind_hints:
            if any(kw in content_lower for kw in keywords):
                guessed_kind = kind
                break

        # Build frontmatter
        title = os.path.splitext(os.path.basename(path))[0]
        fm = [
            "---",
            f"type: {guessed_kind}",
            "status: review",
            "source: spark",
            f"created: {today}",
            "tags: [memory]",
            "---",
            "",
        ]

        if not content.lstrip().startswith("---"):
            # No existing frontmatter — prepend
            new_content = "\n".join(fm) + content.lstrip()
            ops.append({
                "action": "update_file",
                "path": path,
                "content": new_content,
            })
            preview_lines.append(
                f"- `{os.path.basename(path)}`: +frontmatter `type={guessed_kind}`, `#memory`, `#status/review`"
            )
        else:
            # Has frontmatter but was flagged (possibly incomplete/broken)
            # For now, note it but don't rewrite — manual review needed
            preview_lines.append(
                f"- `{os.path.basename(path)}`: has frontmatter but flagged — manual review recommended"
            )

    if not ops:
        preview_lines.insert(0, "### Retag Preview\n")
        preview_lines.append("\n*No automatic fixes applied — all flagged notes already have frontmatter or are unreadable.*")
    else:
        preview_lines.insert(0, f"### Retag Preview ({len(ops)} notes)\n")
        preview_lines.append(f"\n*{len(ops)} note(s) will get schema frontmatter; {len(paths) - len(ops)} skipped.*")

    return ops, "\n".join(preview_lines)


def _broad_tag_summary(health: SparkHealth) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        "type: spark_summary",
        "status: review",
        "source: spark",
        f"created: {today}",
        "tags: [memory, spark, tag-review]",
        "---",
        "",
        f"# Broad Tag Review {today}",
        "",
        "## Tags",
        "",
    ]
    lines.extend(f"- #{item['name']}: {item['count']} notes" for item in health.broad_tags)
    lines.extend(["", "## Vorschlag", "", "Diese Tags sollten in spezifischere Projekt-, Themen- oder Status-Tags aufgeteilt werden.", ""])
    return "\n".join(lines)


def _orphan_summary(health: SparkHealth) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        "type: spark_summary",
        "status: review",
        "source: spark",
        f"created: {today}",
        "tags: [memory, spark, orphan-review]",
        "---",
        "",
        f"# Orphan Notes {today}",
        "",
        "## Kandidaten",
        "",
    ]
    lines.extend(f"- [[{os.path.splitext(path)[0]}]]" for path in health.orphan_notes[:50])
    lines.extend(["", "## Vorschlag", "", "Diese Notizen brauchen Links, Zusammenfassung, Archivierung oder eine Review-Entscheidung.", ""])
    return "\n".join(lines)

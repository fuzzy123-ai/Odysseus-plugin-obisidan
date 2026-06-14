import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from . import vault_service
from .vault_model import extract_tags, search_semantic
from .vault_security import VaultSecurityError


PROVIDER_ID = "obsidian.vault_context"
SNIPPET_CHARS = 700
MAX_ENRICHED_BACKLINKS = 3
MAX_ENRICHED_SHARED_TAGS = 3
MAX_ENRICHED_FOLDER = 2
SHARED_TAG_EXCLUDED_PREFIXES = ("project/", "status/", "type/")
BROAD_TAGS = {"ai", "ki", "llm", "project", "note", "notes", "memory", "obsidian"}
CANONICAL_PREFIXES = ("AI Memory/Canonical/",)
SUMMARY_PREFIXES = ("AI Memory/Summaries/", "AI Memory/Clusters/")


def _estimate_text_tokens(text: str) -> int:
    """Use the same token estimator as the Odysseus context pipeline."""
    from src.model_context import estimate_tokens

    return estimate_tokens([{"role": "system", "content": text or ""}])


def _trim_text_to_token_budget(text: str, budget_tokens: int) -> Tuple[str, int]:
    """Trim text until it fits the caller's token budget."""
    budget_tokens = max(0, int(budget_tokens or 0))
    if budget_tokens <= 0:
        return "", 0
    text = text or ""
    tokens = _estimate_text_tokens(text)
    if tokens <= budget_tokens:
        return text, tokens
    # Start with the estimator's reciprocal, then tighten with exact checks.
    candidate = text[:max(50, int(budget_tokens / 0.3))].rstrip()
    tokens = _estimate_text_tokens(candidate)
    while candidate and tokens > budget_tokens:
        candidate = candidate[: max(0, int(len(candidate) * 0.85))].rstrip()
        tokens = _estimate_text_tokens(candidate)
    return candidate, tokens


def retrieve_vault_context(owner: Optional[str], query: str, budget: int, mode: str = "chat") -> Dict[str, Any]:
    warnings: List[str] = []
    try:
        vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
    except VaultSecurityError:
        payload = {
            "structured_state": {},
            "snippets": [],
            "sources": [],
            "warnings": ["Obsidian vault is locked; no vault context was loaded."],
            "cache_key": "",
        }
        payload["cache_key"] = _cache_key(payload)
        return payload

    query_terms = _query_terms(query)
    budget = max(0, int(budget or 0))

    notes = []
    all_paths = set()
    for path in vault_service.markdown_notes(vault_dir):
        all_paths.add(path)
        try:
            content = vault_service.read_file(vault_dir, path)
        except OSError as exc:
            warnings.append(f"Could not read {path}: {exc}")
            continue
        frontmatter, body = parse_frontmatter(content)
        tags = extract_tags(content, path)["tags"]
        title = str(frontmatter.get("title") or _title_from_body(body) or _stem(path))
        score, reasons = _score_note(path, title, tags, frontmatter, body, query_terms)
        if score <= 0 and query_terms:
            continue
        notes.append({
            "path": path,
            "title": title,
            "tags": tags,
            "frontmatter": frontmatter,
            "body": body,
            "score": score,
            "reason": ", ".join(reasons) if reasons else "stable vault note",
        })

    notes.sort(key=lambda item: (-item["score"], item["path"].lower()))

    # Semantic fallback: if keyword search yields < 5 results, merge embedding-based matches
    keyword_scored_paths = {n["path"] for n in notes}
    if query_terms and len(notes) < 5:
        try:
            semantic_results = search_semantic(vault_dir, query, top_k=5)
            for sr in semantic_results:
                if sr["path"] in keyword_scored_paths:
                    continue
                try:
                    content = vault_service.read_file(vault_dir, sr["path"])
                except OSError:
                    continue
                frontmatter, body = parse_frontmatter(content)
                tags = extract_tags(content, sr["path"])["tags"]
                title = str(frontmatter.get("title") or _title_from_body(body) or _stem(sr["path"]))
                notes.append({
                    "path": sr["path"],
                    "title": title,
                    "tags": tags,
                    "frontmatter": frontmatter,
                    "body": body,
                    "score": int(sr.get("score", 0.5) * 5),
                    "reason": f"semantic match (cosine={sr.get('score', 0):.2f})",
                })
        except Exception:
            pass

    # Enrichment: backlinks, shared tags, folder context
    all_scored = {n["path"]: n for n in notes}
    enriched_paths: set = set()

    for note in list(notes):
        enriched = []
        enriched.extend(_enrich_with_backlinks(note, vault_dir, all_scored, enriched_paths)[:MAX_ENRICHED_BACKLINKS])
        enriched.extend(_enrich_with_shared_tags(note, notes, all_scored, enriched_paths)[:MAX_ENRICHED_SHARED_TAGS])
        enriched.extend(_enrich_with_folder_context(note, notes, all_scored, enriched_paths)[:MAX_ENRICHED_FOLDER])
        for e in enriched:
            if e["path"] not in all_scored:
                all_scored[e["path"]] = e
                notes.append(e)
                enriched_paths.add(e["path"])

    # Re-sort after enrichments
    notes.sort(key=lambda item: (-item["score"], item["path"].lower()))

    structured_state: Dict[str, Any] = {}
    snippets: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    used_tokens = 0

    for note in notes:
        if note["frontmatter"]:
            structured_state[note["path"]] = note["frontmatter"]
        snippet = _best_snippet(note["body"], query_terms)
        if snippet and budget > 0:
            remaining = budget - used_tokens
            if remaining <= 0:
                break
            snippet, snippet_tokens = _trim_text_to_token_budget(snippet, remaining)
            if not snippet:
                break
            used_tokens += snippet_tokens
            snippets.append({
                "path": note["path"],
                "title": note["title"],
                "text": snippet,
                "untrusted": True,
            })
        sources.append({
            "path": note["path"],
            "title": note["title"],
            "tags": note["tags"],
            "score": note["score"],
            "reason": note["reason"],
        })

    # Summary when many results: total_hits, top_tags, folder_distribution
    summary = None
    if len(notes) > len(snippets):
        all_tags: Counter = Counter()
        folder_counts: Counter = Counter()
        for n in notes:
            for tag in n.get("tags", []):
                all_tags[tag] += 1
            folder = "/".join(n["path"].replace("\\", "/").split("/")[:-1]) or "/"
            folder_counts[folder] += 1
        summary = {
            "total_hits": len(notes),
            "shown": len(snippets),
            "top_tags": [{"name": t, "count": c} for t, c in all_tags.most_common(10)],
            "folder_distribution": [{"folder": f, "count": c} for f, c in folder_counts.most_common(8)],
        }

    payload = {
        "structured_state": structured_state,
        "snippets": snippets,
        "sources": sources,
        "warnings": warnings,
        "cache_key": "",
    }
    if summary:
        payload["summary"] = summary
    payload["cache_key"] = _cache_key(payload)
    return payload


def _enrich_with_backlinks(
    note: Dict[str, Any],
    vault_dir: str,
    all_scored: Dict[str, Any],
    enriched_paths: set,
) -> List[Dict[str, Any]]:
    """Find notes that link to this note via Wiki-Links or Markdown links."""
    results = []
    note_path = note["path"]
    note_stem = _stem(note_path).lower()
    # Build patterns that match this note
    import re as _re
    wiki_pat = _re.compile(r"\[\[" + _re.escape(note_stem) + r"(?:\#[^\]]+)?(?:\|[^\]]+)?\]\]", _re.IGNORECASE)
    md_pat = _re.compile(r"\[[^\]]*\]\(" + _re.escape(note_path) + r"(?:#[^)]+)?\)", _re.IGNORECASE)

    for path in vault_service.markdown_notes(vault_dir):
        if path == note_path or path in all_scored or path in enriched_paths:
            continue
        try:
            content = vault_service.read_file(vault_dir, path)
        except OSError:
            continue
        if wiki_pat.search(content) or md_pat.search(content):
            frontmatter, body = parse_frontmatter(content)
            tags = extract_tags(content, path)["tags"]
            title = str(frontmatter.get("title") or _title_from_body(body) or _stem(path))
            results.append({
                "path": path,
                "title": title,
                "tags": tags,
                "frontmatter": frontmatter,
                "body": body,
                "score": max(0, note["score"] - 1),
                "reason": f"backlink → {note['title']}",
            })
    return results


def _enrich_with_shared_tags(
    note: Dict[str, Any],
    all_notes: List[Dict[str, Any]],
    all_scored: Dict[str, Any],
    enriched_paths: set,
) -> List[Dict[str, Any]]:
    """Notes with ≥2 shared tags, excluding project/status/type prefixes."""
    results = []
    note_tags = {
        t for t in note.get("tags", [])
        if not any(t.startswith(p) for p in SHARED_TAG_EXCLUDED_PREFIXES)
        and t.lower().lstrip("#") not in BROAD_TAGS
    }
    if len(note_tags) < 2:
        return results

    for other in all_notes:
        if other["path"] == note["path"] or other["path"] in all_scored or other["path"] in enriched_paths:
            continue
        other_tags = {
            t for t in other.get("tags", [])
            if not any(t.startswith(p) for p in SHARED_TAG_EXCLUDED_PREFIXES)
            and t.lower().lstrip("#") not in BROAD_TAGS
        }
        shared = note_tags & other_tags
        if len(shared) >= 2:
            results.append({
                "path": other["path"],
                "title": other["title"],
                "tags": other["tags"],
                "frontmatter": other["frontmatter"],
                "body": other["body"],
                "score": max(0, note["score"] - 2) + len(shared),
                "reason": f"shared tags: {', '.join(sorted(shared)[:3])}",
            })
    results.sort(key=lambda x: -x["score"])
    return results


def _enrich_with_folder_context(
    note: Dict[str, Any],
    all_notes: List[Dict[str, Any]],
    all_scored: Dict[str, Any],
    enriched_paths: set,
) -> List[Dict[str, Any]]:
    """Notes in the same folder that aren't already included."""
    results = []
    note_folder = "/".join(note["path"].replace("\\", "/").split("/")[:-1])
    if not note_folder:
        return results

    for other in all_notes:
        if other["path"] == note["path"] or other["path"] in all_scored or other["path"] in enriched_paths:
            continue
        other_folder = "/".join(other["path"].replace("\\", "/").split("/")[:-1])
        if other_folder == note_folder:
            results.append({
                "path": other["path"],
                "title": other["title"],
                "tags": other["tags"],
                "frontmatter": other["frontmatter"],
                "body": other["body"],
                "score": max(0, note["score"] - 3),
                "reason": f"same folder: {note_folder}",
            })
    results.sort(key=lambda x: -x["score"])
    return results


def provider_spec() -> Dict[str, Any]:
    return {
        "id": PROVIDER_ID,
        "label": "Obsidian Vault Context",
        "priority": 50,
        "capabilities": ["chat", "agent", "vault", "markdown"],
        "retrieve": retrieve_vault_context,
    }


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    text = str(content or "")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[text.find("\n", end + 1) + 1:]
    return _parse_simple_yaml(raw), body


def _parse_simple_yaml(raw: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    current_key = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_key:
            result.setdefault(current_key, [])
            if isinstance(result[current_key], list):
                result[current_key].append(_clean_scalar(stripped[1:].strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        current_key = key
        value = value.strip()
        if value == "":
            result[key] = []
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [_clean_scalar(part.strip()) for part in value[1:-1].split(",") if part.strip()]
        else:
            result[key] = _clean_scalar(value)
    return result


def _clean_scalar(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _query_terms(query: str) -> List[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{1,}", str(query or ""))]


def _score_note(
    path: str,
    title: str,
    tags: List[str],
    frontmatter: Dict[str, Any],
    body: str,
    query_terms: List[str],
) -> Tuple[int, List[str]]:
    base_bonus = _memory_tier_bonus(path, frontmatter)
    if not query_terms:
        reasons = ["no query filter"]
        if base_bonus:
            reasons.append("preferred memory tier")
        return 1 + base_bonus, reasons
    weighted_tags = " ".join(tag for tag in tags if tag.lower().lstrip("#") not in BROAD_TAGS).lower()
    haystacks = {
        "path": path.lower(),
        "title": title.lower(),
        "tags": weighted_tags,
        "frontmatter": json.dumps(frontmatter, sort_keys=True).lower(),
        "body": body.lower(),
    }
    score = base_bonus
    reasons: List[str] = []
    if base_bonus:
        reasons.append("preferred memory tier")
    weights = {"title": 8, "path": 6, "tags": 5, "frontmatter": 4, "body": 1}
    for term in query_terms:
        for name, haystack in haystacks.items():
            if term in haystack:
                score += weights[name]
                reasons.append(f"{term} in {name}")
                break
    return score, sorted(set(reasons))


def _memory_tier_bonus(path: str, frontmatter: Dict[str, Any]) -> int:
    normalized = path.replace("\\", "/")
    if normalized.startswith(CANONICAL_PREFIXES) or frontmatter.get("type") == "canonical":
        return 12
    if normalized.startswith(SUMMARY_PREFIXES) or frontmatter.get("type") in {"spark_summary", "cluster_summary"}:
        return 7
    if normalized.startswith("AI Memory/02 Entscheidungen"):
        return 8
    return 0


def _best_snippet(body: str, query_terms: List[str]) -> str:
    clean = str(body or "").strip()
    if not clean:
        return ""
    lower = clean.lower()
    positions = [lower.find(term) for term in query_terms if term and lower.find(term) >= 0]
    if not positions:
        return clean[:SNIPPET_CHARS]
    start = max(0, min(positions) - 180)
    return clean[start:start + SNIPPET_CHARS]


def _title_from_body(body: str) -> str:
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _cache_key(payload: Dict[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key != "cache_key"}
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

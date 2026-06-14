import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import vault_service
from .vault_history import list_history, undo_action
from .vault_model import (
    build_vault_index,
    graph_payload,
    search_semantic,
    suggest_links,
    suggest_tags,
)
from .memory_capture import (
    MemoryCaptureApplyRequest,
    MemoryCaptureRequest,
    apply_memory_capture_plan,
    build_memory_capture_plan,
    validate_memory_capture_plan,
)
from .memory_spark import (
    SparkAnalyzeRequest,
    SparkApplyRequest,
    analyze_memory_health,
    apply_spark_plan,
    build_spark_plan,
)


@dataclass(frozen=True)
class VaultToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    access: str
    handler: Callable[[str, Dict[str, Any], str, Dict[str, Any]], Any]


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": dict(properties)}
    if required:
        schema["required"] = required
    return schema


def _actor(owner: str, source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "owner": owner or "default",
        "source": source.get("source") or "mcp",
        "token_id": source.get("token_id") or "",
        "token_prefix": source.get("token_prefix") or "",
    }


def _tree(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    prefix = str(args.get("prefix") or "").strip("/")
    depth = args.get("depth")
    dir_path = vault_service.secure_path(vault_dir, prefix) if prefix else vault_dir
    if not os.path.isdir(dir_path):
        dir_path = vault_dir
    tree = vault_service.file_tree(vault_dir, dir_path=dir_path)
    if isinstance(depth, int) and depth >= 0:
        return _limit_tree_depth(tree, depth)
    return tree


def _limit_tree_depth(nodes: List[Dict[str, Any]], depth: int) -> List[Dict[str, Any]]:
    if depth <= 0:
        return [{k: v for k, v in node.items() if k != "children"} for node in nodes]
    limited = []
    for node in nodes:
        cloned = dict(node)
        if node.get("children"):
            cloned["children"] = _limit_tree_depth(node["children"], depth - 1)
        limited.append(cloned)
    return limited


def _read(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    path = args["path"]
    content = vault_service.read_file(vault_dir, path)
    if args.get("frontmatter_only"):
        frontmatter, _ = vault_service.parse_frontmatter(content)
        return {"path": path, "frontmatter": frontmatter}
    return content


def _search(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    tag_filter = str(args.get("tag_filter") or "").strip().lower().lstrip("#")
    max_results = max(1, min(int(args.get("max_results") or 20), 100))
    results = []
    for result in vault_service.search_markdown(vault_dir, args["query"]):
        if tag_filter:
            try:
                from .vault_model import extract_tags

                content = vault_service.read_file(vault_dir, result.path)
                tags = extract_tags(content, result.path)["tags"]
                if not any(tag.lower() == tag_filter for tag in tags):
                    continue
            except OSError:
                continue
        results.append({
            "path": result.path,
            "matches": [{"line": m.line, "text": m.text} for m in result.matches],
        })
        if len(results) >= max_results:
            break
    return results


def _semantic(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    return search_semantic(vault_dir, args["query"], top_k=max(1, min(int(args.get("top_k") or 10), 50)))


def _tags(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    prefix = str(args.get("prefix") or "")
    if prefix:
        return suggest_tags(vault_dir, prefix=prefix)
    return build_vault_index(vault_dir).get("tags", [])


def _graph(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    return graph_payload(vault_dir, focus=args.get("focus"), tag=args.get("tag"))


def _related(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    return suggest_links(vault_dir, path=args["path"], top_k=max(1, min(int(args.get("top_k") or 5), 25)))


def _recent(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    return vault_service.files_recent(vault_dir, since=args.get("since"), until=args.get("until"))[:50]


def _history(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    path = args.get("path")
    limit = max(1, min(int(args.get("limit") or 20), 200))
    history = list_history(vault_dir, limit=limit)
    if path:
        history = [entry for entry in history if path in entry.get("paths", [])]
    return history[:limit]


def _status(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    idx = build_vault_index(vault_dir)
    return {
        "total_notes": len(idx.get("paths", [])),
        "total_tags": len(idx.get("tags", [])),
        "owner": owner or "default",
    }


def _write(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    path = args["path"]
    actor = _actor(owner, source)
    if args.get("content") is not None:
        vault_service.write_file(
            vault_dir,
            path,
            args.get("content") or "",
            owner=owner,
            tool="mcp_vault_write",
            actor=actor,
        )
    if args.get("frontmatter"):
        vault_service.merge_frontmatter(
            vault_dir,
            path,
            args["frontmatter"],
            owner=owner,
            tool="mcp_vault_write",
            actor=actor,
        )
    return {"success": True, "path": path}


def _batch(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    return vault_service.batch_operations(
        vault_dir,
        args.get("operations", []),
        owner=owner,
        tool="mcp_vault_batch",
        dry_run=bool(args.get("dry_run", False)),
        actor=_actor(owner, source),
    )


def _delete(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    return vault_service.delete_file(
        vault_dir,
        args["path"],
        owner=owner,
        tool="mcp_vault_delete",
        actor=_actor(owner, source),
    )


def _undo(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    result = undo_action(vault_dir, action_id=args.get("action_id"), owner=owner)
    return result or {"success": False, "message": "Nothing to undo"}


def _capture_memory(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    req = MemoryCaptureRequest(**args)
    plan = build_memory_capture_plan(vault_dir, req)
    if not req.confirm:
        return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    plan = validate_memory_capture_plan(vault_dir, plan)
    result = apply_memory_capture_plan(vault_dir, plan, owner=owner, actor=_actor(owner, source))
    payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    payload["apply_result"] = result
    return payload


def _spark_analyze(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    health = analyze_memory_health(vault_dir, SparkAnalyzeRequest(**args))
    return health.model_dump() if hasattr(health, "model_dump") else health.dict()


def _spark_plan(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    plan = build_spark_plan(vault_dir, SparkAnalyzeRequest(**args))
    return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()


def _spark_apply(vault_dir: str, args: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    result = apply_spark_plan(vault_dir, SparkApplyRequest(**args), owner=owner, actor=_actor(owner, source))
    return result


VAULT_TOOL_SPECS: List[VaultToolSpec] = [
    VaultToolSpec("obsidian_tree", "List the folder tree of the Obsidian vault.", _schema({
        "prefix": {"type": "string", "description": "Path prefix to filter, e.g. 'projects/'"},
        "depth": {"type": "integer", "description": "Max folder depth."},
    }), "read", _tree),
    VaultToolSpec("obsidian_read_note", "Read a markdown note from the vault.", _schema({
        "path": {"type": "string", "description": "Path relative to the vault root."},
        "frontmatter_only": {"type": "boolean", "description": "Return only YAML frontmatter."},
    }, ["path"]), "read", _read),
    VaultToolSpec("obsidian_search_notes", "Full-text search across markdown notes.", _schema({
        "query": {"type": "string", "description": "Search query string."},
        "max_results": {"type": "integer", "description": "Max files to return."},
        "tag_filter": {"type": "string", "description": "Only include notes with this tag."},
    }, ["query"]), "read", _search),
    VaultToolSpec("obsidian_search_semantic", "Semantic search across vault notes.", _schema({
        "query": {"type": "string", "description": "Natural language query."},
        "top_k": {"type": "integer", "description": "Number of results."},
    }, ["query"]), "read", _semantic),
    VaultToolSpec("obsidian_list_tags", "List or suggest tags in the vault.", _schema({
        "prefix": {"type": "string", "description": "Optional prefix filter."},
    }), "read", _tags),
    VaultToolSpec("obsidian_graph", "Return the vault graph.", _schema({
        "focus": {"type": "string", "description": "Focus note path."},
        "tag": {"type": "string", "description": "Filter graph to notes with this tag."},
    }), "read", _graph),
    VaultToolSpec("obsidian_suggest_links", "Suggest notes related to a given note.", _schema({
        "path": {"type": "string", "description": "Path to the note."},
        "top_k": {"type": "integer", "description": "Max suggestions."},
    }, ["path"]), "read", _related),
    VaultToolSpec("obsidian_recent_notes", "List recently modified notes.", _schema({
        "since": {"type": "string", "description": "ISO datetime lower bound."},
        "until": {"type": "string", "description": "ISO datetime upper bound."},
    }), "read", _recent),
    VaultToolSpec("obsidian_history", "Show recent vault action history.", _schema({
        "path": {"type": "string", "description": "Optional path filter."},
        "limit": {"type": "integer", "description": "Max entries."},
    }), "read", _history),
    VaultToolSpec("obsidian_vault_stats", "Return vault status.", _schema({}), "read", _status),
    VaultToolSpec("obsidian_write_note", "Create or update a markdown note.", _schema({
        "path": {"type": "string", "description": "Path relative to the vault root."},
        "content": {"type": "string", "description": "Full markdown content."},
        "frontmatter": {"type": "object", "description": "Frontmatter keys to merge."},
    }, ["path"]), "write", _write),
    VaultToolSpec("vault_batch", "Perform multiple vault operations atomically.", _schema({
        "operations": {"type": "array", "items": {"type": "object"}},
        "dry_run": {"type": "boolean", "description": "Preview without applying changes."},
    }, ["operations"]), "write", _batch),
    VaultToolSpec("obsidian_delete_note", "Soft-delete a markdown note.", _schema({
        "path": {"type": "string", "description": "Path to delete."},
    }, ["path"]), "delete", _delete),
    VaultToolSpec("obsidian_undo", "Undo a previous destructive vault action.", _schema({
        "action_id": {"type": "string", "description": "Specific action ID, or omit for latest."},
    }), "write", _undo),
    VaultToolSpec("obsidian_memory_capture", "Normalize and route a long-term memory capture. Defaults to preview; confirm=true applies the generated plan.", _schema({
        "content": {"type": "string", "description": "Memory text to capture."},
        "title": {"type": "string", "description": "Optional title."},
        "kind": {"type": "string", "description": "decision, rule, preference, open_question, project_note, session_log, reference, or raw."},
        "source": {"type": "string", "description": "agent, chat, manual, file, mail, or document."},
        "source_ref": {"type": "string", "description": "Optional source reference."},
        "project": {"type": "string", "description": "Optional project slug or name."},
        "scope": {"type": "string", "description": "global, project, personal, technical, or session."},
        "confidence": {"type": "string", "description": "low, medium, or high."},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "Requested tags."},
        "link_paths": {"type": "array", "items": {"type": "string"}, "description": "Existing notes to link."},
        "target": {"type": "string", "description": "auto, inbox, canonical, or append."},
        "confirm": {"type": "boolean", "description": "When true, apply the generated capture plan."},
    }, ["content"]), "write", _capture_memory),
    VaultToolSpec("obsidian_spark_analyze", "Analyze long-term memory health without changing the vault.", _schema({
        "scope": {"type": "string", "description": "vault, folder, tag, or current_note."},
        "path": {"type": "string", "description": "Folder prefix or current note path for scoped analysis."},
        "tag": {"type": "string", "description": "Tag for scoped analysis."},
        "limit": {"type": "integer", "description": "Maximum notes to analyze."},
    }), "read", _spark_analyze),
    VaultToolSpec("obsidian_spark_plan", "Create a non-destructive Spark cleanup and canonicalization plan.", _schema({
        "scope": {"type": "string", "description": "vault, folder, tag, or current_note."},
        "path": {"type": "string", "description": "Folder prefix or current note path for scoped planning."},
        "tag": {"type": "string", "description": "Tag for scoped planning."},
        "limit": {"type": "integer", "description": "Maximum notes to analyze."},
    }), "read", _spark_plan),
    VaultToolSpec("obsidian_spark_apply", "Apply selected low/medium-risk Spark actions with confirmation.", _schema({
        "plan": {"type": "object", "description": "Spark plan returned by vault_spark_plan."},
        "confirm": {"type": "boolean", "description": "Must be true before changing the vault."},
        "selected_action_ids": {"type": "array", "items": {"type": "string"}, "description": "Action IDs to apply."},
    }, ["plan", "confirm", "selected_action_ids"]), "write", _spark_apply),
]

VAULT_TOOL_BY_NAME = {spec.name: spec for spec in VAULT_TOOL_SPECS}
DESTRUCTIVE_TOOL_NAMES = {
    spec.name for spec in VAULT_TOOL_SPECS if spec.access in {"write", "delete"}
}


def execute_vault_tool(name: str, vault_dir: str, arguments: Dict[str, Any], owner: str, source: Dict[str, Any]) -> Any:
    spec = VAULT_TOOL_BY_NAME.get(name)
    if not spec:
        raise KeyError(f"Unknown tool: {name}")
    safe_arguments = dict(arguments or {})
    safe_arguments.pop("owner", None)
    return spec.handler(vault_dir, safe_arguments, owner, source)


def format_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)

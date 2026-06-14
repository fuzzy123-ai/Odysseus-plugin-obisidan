import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.constants import DATA_DIR

from .vault_history import record_action
from .vault_rules import apply_write_rule_metadata, ensure_rules_note
from .vault_security import require_unlocked


TEXT_EXTENSIONS = (".md", ".txt", ".json", ".html", ".js", ".css")


@dataclass(frozen=True)
class SearchMatch:
    line: int
    text: str


@dataclass(frozen=True)
class SearchResult:
    path: str
    matches: List[SearchMatch]


def owner_folder(owner: Optional[str]) -> str:
    return owner if owner else "default"


def vault_path_for_owner(owner: Optional[str]) -> str:
    folder_name = owner_folder(owner)
    configured_vault = os.getenv("OBSIDIAN_VAULT_DIR", "").strip()
    if configured_vault:
        vault_template = configured_vault.format(owner=folder_name)
        return os.path.abspath(os.path.expanduser(vault_template))
    vault_dir = os.path.abspath(os.path.join(DATA_DIR, "obsidian_vaults", folder_name))
    os.makedirs(vault_dir, exist_ok=True)
    return vault_dir


def unlocked_vault_path_for_owner(owner: Optional[str]) -> str:
    vault_dir = vault_path_for_owner(owner)
    require_unlocked(vault_dir)
    return vault_dir


def secure_path(vault_dir: str, relative_path: str) -> str:
    cleaned_rel = str(relative_path or "").replace("\\", "/").strip("/")
    abs_vault = os.path.abspath(vault_dir)
    abs_target = os.path.abspath(os.path.join(abs_vault, cleaned_rel))
    if os.path.commonpath([abs_vault, abs_target]) != abs_vault:
        raise ValueError("Path traversal attempt detected")
    return abs_target


def read_text_if_exists(path: str) -> Optional[str]:
    if not os.path.exists(path) or os.path.isdir(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def is_text_path(path: str) -> bool:
    return path.lower().endswith(TEXT_EXTENSIONS)


def is_self_or_descendant_move(abs_old: str, abs_new: str) -> bool:
    old_path = os.path.abspath(abs_old)
    new_path = os.path.abspath(abs_new)
    return new_path == old_path or os.path.commonpath([old_path, new_path]) == old_path


def file_tree(vault_dir: str, dir_path: Optional[str] = None) -> List[Dict[str, Any]]:
    base_path = vault_dir
    current_dir = dir_path or vault_dir
    tree: List[Dict[str, Any]] = []
    for entry in os.scandir(current_dir):
        if entry.name == ".obsidian":
            continue
        rel_path = os.path.relpath(entry.path, base_path).replace("\\", "/")
        if entry.is_dir():
            tree.append({
                "name": entry.name,
                "path": rel_path,
                "is_dir": True,
                "children": file_tree(vault_dir, entry.path),
            })
        else:
            tree.append({
                "name": entry.name,
                "path": rel_path,
                "is_dir": False,
            })
    tree.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return tree


def markdown_notes(vault_dir: str) -> List[str]:
    notes: List[str] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [directory for directory in dirs if directory != ".obsidian"]
        for filename in files:
            if filename.lower().endswith(".md"):
                abs_path = os.path.join(root, filename)
                notes.append(os.path.relpath(abs_path, vault_dir).replace("\\", "/"))
    return sorted(notes, key=str.lower)


def read_file(vault_dir: str, path: str) -> str:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    if os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a directory: {path}")
    with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_file(
    vault_dir: str,
    path: str,
    content: str,
    *,
    owner: Optional[str],
    tool: str,
    actor: Optional[Dict[str, Any]] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_rules_note(vault_dir)
    abs_path = secure_path(vault_dir, path)
    exists = os.path.exists(abs_path)
    if exists and os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a directory: {path}")
    before_content = read_text_if_exists(abs_path)
    # Auto-snapshot before overwriting
    if exists and before_content:
        _create_snapshot(vault_dir, path, before_content, batch_id=batch_id)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    record_action(
        vault_dir,
        action="update_file" if exists else "create_file",
        owner=owner,
        tool=tool,
        paths=[path],
        before={"content": before_content} if exists else {},
        after={"content": content},
        batch_id=batch_id,
        actor=actor,
    )
    return apply_write_rule_metadata(
        {"success": True, "path": path, "created": not exists},
        path,
        content,
    )


def create_file(
    vault_dir: str,
    path: str,
    content: str,
    *,
    owner: Optional[str],
    tool: str,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if os.path.exists(abs_path):
        raise FileExistsError(f"File already exists: {path}")
    return write_file(vault_dir, path, content, owner=owner, tool=tool, actor=actor)


def update_file(
    vault_dir: str,
    path: str,
    content: str,
    *,
    owner: Optional[str],
    tool: str,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    return write_file(vault_dir, path, content, owner=owner, tool=tool, actor=actor)


def delete_file(
    vault_dir: str,
    path: str,
    *,
    owner: Optional[str],
    tool: str,
    reversible: bool = False,
    actor: Optional[Dict[str, Any]] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    if os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a folder, not a file: {path}")
    before_content = read_text_if_exists(abs_path)

    # Soft-delete: move to .trash/ instead of os.remove()
    _soft_delete_file(vault_dir, path, abs_path)

    record_action(
        vault_dir,
        action="delete_file",
        owner=owner,
        tool=tool,
        paths=[path],
        before={"content": before_content},
        reversible=True,
        batch_id=batch_id,
        actor=actor,
    )
    return {"success": True, "path": path}


# ── Soft-delete / Snapshots ──────────────────────────────────────────────────

SNAPSHOTS_DIR = ".obsidian/snapshots"
TRASH_DIR = ".trash"
MAX_SNAPSHOTS_PER_FILE = 50
SNAPSHOT_MIN_INTERVAL_SECONDS = int(os.getenv("OBSIDIAN_SNAPSHOT_MIN_INTERVAL_SECONDS", "300"))


def _create_snapshot(vault_dir: str, path: str, content: str, *, batch_id: Optional[str] = None) -> bool:
    """Save a timestamped snapshot before overwriting a file."""
    import hashlib as _hashlib

    file_hash = _hashlib.sha256(path.encode()).hexdigest()[:12]
    snap_dir = os.path.join(vault_dir, SNAPSHOTS_DIR, file_hash)
    os.makedirs(snap_dir, exist_ok=True)

    existing = sorted([f for f in os.listdir(snap_dir) if f.endswith(".md")], reverse=True)
    now = datetime.now(timezone.utc)
    if not batch_id and existing and SNAPSHOT_MIN_INTERVAL_SECONDS > 0:
        latest = _snapshot_time(existing[0])
        if latest and (now - latest).total_seconds() < SNAPSHOT_MIN_INTERVAL_SECONDS:
            return False

    ts = now.strftime("%Y%m%dT%H%M%S%fZ")
    suffix = f"-{batch_id[:12]}" if batch_id else ""
    snap_path = os.path.join(snap_dir, f"{ts}{suffix}.md")
    with open(snap_path, "w", encoding="utf-8") as handle:
        handle.write(content)

    # Rotate: keep only the most recent MAX_SNAPSHOTS_PER_FILE
    existing = sorted([f for f in os.listdir(snap_dir) if f.endswith(".md")], reverse=True)
    for old in existing[MAX_SNAPSHOTS_PER_FILE:]:
        try:
            os.remove(os.path.join(snap_dir, old))
        except OSError:
            pass
    return True


def _snapshot_time(filename: str) -> Optional[datetime]:
    stamp = filename.split("-", 1)[0].removesuffix(".md")
    for fmt in ("%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _soft_delete_file(vault_dir: str, path: str, abs_path: str) -> None:
    """Move a file to .trash/{iso_date}/{rel_path} instead of permanent deletion."""
    from datetime import datetime, timezone as _timezone

    iso_date = datetime.now(_timezone.utc).strftime("%Y-%m-%d")
    trash_root = os.path.join(vault_dir, TRASH_DIR, iso_date)
    trash_path = os.path.join(trash_root, path)
    os.makedirs(os.path.dirname(trash_path), exist_ok=True)
    shutil.move(abs_path, trash_path)


TRASH_RETENTION_DAYS = int(os.getenv("OBSIDIAN_TRASH_RETENTION_DAYS", "30"))


def purge_trash(vault_dir: str, retention_days: int = TRASH_RETENTION_DAYS) -> Dict[str, Any]:
    """Permanently delete .trash/ entries older than retention_days.

    Called by the scheduled background job every 24 hours.
    """
    trash_root = os.path.join(vault_dir, TRASH_DIR)
    if not os.path.isdir(trash_root):
        return {"purged": 0, "errors": 0}

    from datetime import datetime, timezone, timedelta as _timedelta

    cutoff = datetime.now(timezone.utc) - _timedelta(days=retention_days)
    purged = 0
    errors = 0

    for date_dir in sorted(os.listdir(trash_root)):
        dir_path = os.path.join(trash_root, date_dir)
        if not os.path.isdir(dir_path):
            continue
        # Parse date from directory name (YYYY-MM-DD)
        try:
            dir_date = datetime.strptime(date_dir, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dir_date >= cutoff:
            continue
        # This date directory is expired — remove it entirely
        try:
            shutil.rmtree(dir_path)
            purged += 1
        except OSError:
            errors += 1

    # Clean up empty parent dirs and the trash root if empty
    try:
        if os.path.isdir(trash_root) and not os.listdir(trash_root):
            os.rmdir(trash_root)
    except OSError:
        pass

    return {"purged": purged, "errors": errors}


def purge_all_vault_trash(retention_days: int = TRASH_RETENTION_DAYS) -> Dict[str, Any]:
    """Run trash purge across all vaults."""
    vaults_root = os.path.join(DATA_DIR, "obsidian_vaults")
    if not os.path.isdir(vaults_root):
        return {"vaults": 0, "purged": 0, "errors": 0}

    total_purged = 0
    total_errors = 0
    vault_count = 0

    for entry in os.listdir(vaults_root):
        vault_dir = os.path.join(vaults_root, entry)
        if not os.path.isdir(vault_dir):
            continue
        vault_count += 1
        result = purge_trash(vault_dir, retention_days=retention_days)
        total_purged += result["purged"]
        total_errors += result["errors"]

    return {"vaults": vault_count, "purged": total_purged, "errors": total_errors}


def create_folder(vault_dir: str, path: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if os.path.exists(abs_path):
        raise FileExistsError(f"Path already exists: {path}")
    os.makedirs(abs_path, exist_ok=False)
    record_action(vault_dir, action="create_folder", owner=owner, tool=tool, paths=[path], reversible=False)
    return {"success": True, "path": path}


def delete_folder(vault_dir: str, path: str, *, owner: Optional[str], tool: str, recursive: bool = False) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Folder not found: {path}")
    if not os.path.isdir(abs_path):
        raise NotADirectoryError(f"Path is not a folder: {path}")
    if recursive:
        shutil.rmtree(abs_path)
    else:
        os.rmdir(abs_path)
    record_action(vault_dir, action="delete_folder", owner=owner, tool=tool, paths=[path], reversible=False)
    return {"success": True, "path": path}


def rename_item(vault_dir: str, old_path: str, new_path: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_old = secure_path(vault_dir, old_path)
    abs_new = secure_path(vault_dir, new_path)
    if not os.path.exists(abs_old):
        raise FileNotFoundError(f"Source not found: {old_path}")
    if os.path.exists(abs_new):
        raise FileExistsError(f"Destination already exists: {new_path}")
    if os.path.isdir(abs_old) and is_self_or_descendant_move(abs_old, abs_new):
        raise ValueError("Cannot move a folder into itself.")
    os.makedirs(os.path.dirname(abs_new), exist_ok=True)
    os.replace(abs_old, abs_new)
    record_action(
        vault_dir,
        action="rename_item",
        owner=owner,
        tool=tool,
        paths=[old_path, new_path],
        before={"path": old_path},
        after={"path": new_path},
    )
    return {"success": True, "old_path": old_path, "new_path": new_path}


def search_markdown(vault_dir: str, query: str) -> List[SearchResult]:
    query_re = re.compile(re.escape(query), re.IGNORECASE)
    results: List[SearchResult] = []
    for path in markdown_notes(vault_dir):
        abs_path = secure_path(vault_dir, path)
        matches: List[SearchMatch] = []
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            for line_num, line in enumerate(handle, 1):
                if query_re.search(line):
                    matches.append(SearchMatch(line=line_num, text=line.strip()))
        if matches:
            results.append(SearchResult(path=path, matches=matches))
    return results


# ── Frontmatter helpers ──────────────────────────────────────────────────────

def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Extract YAML frontmatter and body from markdown content."""
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


def read_frontmatter(vault_dir: str, path: str) -> Dict[str, Any]:
    """Read only the YAML frontmatter of a markdown file."""
    content = read_file(vault_dir, path)
    frontmatter, _ = parse_frontmatter(content)
    return frontmatter


def merge_frontmatter(
    vault_dir: str,
    path: str,
    frontmatter: Dict[str, Any],
    *,
    owner: Optional[str],
    tool: str,
    actor: Optional[Dict[str, Any]] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge new frontmatter keys into an existing markdown file (body unchanged)."""
    ensure_rules_note(vault_dir)
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    content = read_file(vault_dir, path)
    existing_fm, body = parse_frontmatter(content)

    # Deep merge: new keys overwrite existing, list values append
    merged = dict(existing_fm)
    for key, value in frontmatter.items():
        if key in merged and isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = merged[key] + value
        else:
            merged[key] = value

    # Rebuild the file
    lines = ["---"]
    for key, value in merged.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, str) and ("#" in value or ":" in value):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    new_content = "\n".join(lines) + "\n" + body

    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)

    record_action(
        vault_dir,
        action="update_file",
        owner=owner,
        tool=tool,
        paths=[path],
        before={"content": content},
        after={"content": new_content},
        batch_id=batch_id,
        actor=actor,
    )
    return apply_write_rule_metadata(
        {"success": True, "path": path, "frontmatter": merged},
        path,
        new_content,
    )


# ── Batch operations ─────────────────────────────────────────────────────────

def batch_operations(
    vault_dir: str,
    operations: List[Dict[str, Any]],
    *,
    owner: Optional[str],
    tool: str,
    dry_run: bool = False,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute multiple vault operations atomically.

    Each operation: {action, path, content?, frontmatter?}
    Actions: create_file, update_file, delete_file, merge_frontmatter

    If dry_run=True, returns a diff report without making any changes.
    """
    would_create: List[str] = []
    would_modify: List[str] = []
    would_delete: List[str] = []
    errors: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []

    # Validate all operations first
    for i, op in enumerate(operations):
        action = str(op.get("action", "")).strip()
        path = str(op.get("path", "")).strip()
        if action not in ("create_file", "update_file", "delete_file", "merge_frontmatter"):
            errors.append({"index": i, "error": f"Unknown action: {action}"})
            continue
        if not path:
            errors.append({"index": i, "error": "path is required"})
            continue
        try:
            secure_path(vault_dir, path)
        except ValueError:
            errors.append({"index": i, "error": f"Path traversal: {path}"})
            continue

    if errors:
        return {"success": False, "errors": errors, "dry_run": dry_run}

    # Dry-run: classify and return diff
    if dry_run:
        for i, op in enumerate(operations):
            action = op["action"]
            path = op["path"]
            abs_path = secure_path(vault_dir, path)
            if action == "create_file":
                if os.path.exists(abs_path):
                    errors.append({"index": i, "error": f"File already exists: {path}"})
                else:
                    would_create.append(path)
            elif action == "update_file":
                if not os.path.exists(abs_path):
                    errors.append({"index": i, "error": f"File not found: {path}"})
                else:
                    would_modify.append(path)
            elif action == "delete_file":
                if not os.path.exists(abs_path):
                    errors.append({"index": i, "error": f"File not found: {path}"})
                else:
                    would_delete.append(path)
            elif action == "merge_frontmatter":
                if not os.path.exists(abs_path):
                    errors.append({"index": i, "error": f"File not found: {path}"})
                else:
                    would_modify.append(path)
        return {
            "success": len(errors) == 0,
            "dry_run": True,
            "would_create": would_create,
            "would_modify": would_modify,
            "would_delete": would_delete,
            "errors": errors,
        }

    ensure_rules_note(vault_dir)
    batch_id = uuid.uuid4().hex
    before_after: Dict[str, Dict[str, Any]] = {}

    # Execute: use tempfiles + os.replace() for atomicity
    tempfiles: Dict[str, str] = {}
    try:
        for i, op in enumerate(operations):
            action = op["action"]
            path = op["path"]
            abs_path = secure_path(vault_dir, path)

            if action == "create_file":
                content = op.get("content", "")
                if os.path.exists(abs_path):
                    raise FileExistsError(f"File already exists: {path}")
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                tmp = abs_path + ".batch.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(content)
                tempfiles[tmp] = abs_path
                before_after[path] = {"before": {}, "after": {"content": content}}
                results.append(apply_write_rule_metadata(
                    {"action": "create_file", "path": path, "success": True},
                    path,
                    content,
                ))

            elif action == "update_file":
                content = op.get("content", "")
                if not os.path.exists(abs_path):
                    raise FileNotFoundError(f"File not found: {path}")
                before_content = read_text_if_exists(abs_path)
                if before_content:
                    _create_snapshot(vault_dir, path, before_content, batch_id=batch_id)
                tmp = abs_path + ".batch.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(content)
                tempfiles[tmp] = abs_path
                before_after[path] = {"before": {"content": before_content}, "after": {"content": content}}
                results.append(apply_write_rule_metadata(
                    {"action": "update_file", "path": path, "success": True},
                    path,
                    content,
                ))

            elif action == "delete_file":
                if not os.path.exists(abs_path):
                    raise FileNotFoundError(f"File not found: {path}")
                before_content = read_text_if_exists(abs_path)
                # Move to temp first, then commit
                tmp = abs_path + ".batch.del.tmp"
                os.rename(abs_path, tmp)
                tempfiles[tmp] = None  # marker: commit means os.remove(tmp)
                before_after[path] = {"before": {"content": before_content}, "after": {}}
                results.append({"action": "delete_file", "path": path, "success": True})

            elif action == "merge_frontmatter":
                fm = op.get("frontmatter", {})
                if not os.path.exists(abs_path):
                    raise FileNotFoundError(f"File not found: {path}")
                content = read_file(vault_dir, path)
                if content:
                    _create_snapshot(vault_dir, path, content, batch_id=batch_id)
                existing_fm, body = parse_frontmatter(content)
                merged = dict(existing_fm)
                for key, value in fm.items():
                    if key in merged and isinstance(merged[key], list) and isinstance(value, list):
                        merged[key] = merged[key] + value
                    else:
                        merged[key] = value
                lines = ["---"]
                for key, value in merged.items():
                    if isinstance(value, list):
                        lines.append(f"{key}:")
                        for item in value:
                            lines.append(f"  - {item}")
                    elif isinstance(value, bool):
                        lines.append(f"{key}: {'true' if value else 'false'}")
                    else:
                        lines.append(f"{key}: {value}")
                lines.append("---")
                new_content = "\n".join(lines) + "\n" + body
                tmp = abs_path + ".batch.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(new_content)
                tempfiles[tmp] = abs_path
                before_after[path] = {"before": {"content": content}, "after": {"content": new_content}}
                results.append(apply_write_rule_metadata(
                    {"action": "merge_frontmatter", "path": path, "success": True},
                    path,
                    new_content,
                ))

        # All operations prepared — commit via os.replace()
        for tmp_path, final_path in tempfiles.items():
            if final_path is None:
                # Delete marker
                os.remove(tmp_path)
            else:
                os.replace(tmp_path, final_path)

        # Record all actions
        for result in results:
            record_action(
                vault_dir,
                action=result["action"],
                owner=owner,
                tool=tool,
                paths=[result["path"]],
                before=before_after.get(result["path"], {}).get("before", {}),
                after=before_after.get(result["path"], {}).get("after", {}),
                batch_id=batch_id,
                actor=actor,
            )

        warnings = [result["warning"] for result in results if result.get("warning")]
        response = {"success": True, "dry_run": False, "results": results, "count": len(results), "batch_id": batch_id}
        if warnings:
            response["warnings"] = warnings
        return response

    except (FileExistsError, FileNotFoundError, IsADirectoryError) as e:
        # Clean up tempfiles on failure
        for tmp_path in tempfiles:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        return {"success": False, "errors": [{"error": str(e)}], "dry_run": False}


# ── Zeitraum queries ─────────────────────────────────────────────────────────

def files_recent(
    vault_dir: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return files filtered by modification time range.

    Dates can be ISO format (2026-06-01) or ISO with time (2026-06-01T12:00:00).
    """
    from datetime import datetime, timezone

    since_dt: Optional[datetime] = None
    until_dt: Optional[datetime] = None

    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise ValueError(f"Invalid since date: {since}")
    if until:
        try:
            until_dt = datetime.fromisoformat(until)
        except ValueError:
            raise ValueError(f"Invalid until date: {until}")

    results: List[Dict[str, Any]] = []
    for path in markdown_notes(vault_dir):
        abs_path = os.path.join(vault_dir, path)
        try:
            stat = os.stat(abs_path)
        except OSError:
            continue
        mtime = stat.st_mtime
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)

        if since_dt and mtime_dt < since_dt:
            continue
        if until_dt and mtime_dt > until_dt:
            continue

        results.append({
            "path": path,
            "mtime": mtime_dt.isoformat(),
            "size": stat.st_size,
        })

    results.sort(key=lambda item: item["mtime"], reverse=True)
    return results


def files_changed(
    vault_dir: str,
    since: str,
) -> List[Dict[str, Any]]:
    """Return files modified since a given date (only changed, not newly created).

    Uses vault history to distinguish created vs. changed files.
    """
    from datetime import datetime, timezone

    try:
        since_dt = datetime.fromisoformat(since)
    except ValueError:
        raise ValueError(f"Invalid since date: {since}")

    # Get created files from history
    created_paths: set[str] = set()
    for entry in vault_history.list_history(vault_dir, limit=200):
        entry_ts = entry.get("timestamp", "")
        try:
            entry_dt = datetime.strptime(entry_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if entry_dt >= since_dt:
            if entry.get("action") == "create_file":
                for p in entry.get("paths", []):
                    created_paths.add(p)

    results: List[Dict[str, Any]] = []
    for path in markdown_notes(vault_dir):
        if path in created_paths:
            continue
        abs_path = os.path.join(vault_dir, path)
        try:
            stat = os.stat(abs_path)
        except OSError:
            continue
        mtime = stat.st_mtime
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        if mtime_dt >= since_dt:
            results.append({
                "path": path,
                "mtime": mtime_dt.isoformat(),
                "size": stat.st_size,
            })

    results.sort(key=lambda item: item["mtime"], reverse=True)
    return results

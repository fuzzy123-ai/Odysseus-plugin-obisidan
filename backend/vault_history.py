import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional


def _obsidian_dir(vault_dir: str) -> str:
    path = os.path.join(vault_dir, ".obsidian")
    os.makedirs(path, exist_ok=True)
    return path


def history_path(vault_dir: str) -> str:
    return os.path.join(_obsidian_dir(vault_dir), "history.json")


def _load_raw(vault_dir: str) -> List[Dict[str, Any]]:
    path = history_path(vault_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_raw(vault_dir: str, entries: List[Dict[str, Any]]) -> None:
    path = history_path(vault_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries[-200:], fh, ensure_ascii=False, indent=2)


def list_history(vault_dir: str, limit: int = 50) -> List[Dict[str, Any]]:
    entries = _load_raw(vault_dir)
    return list(reversed(entries[-max(1, min(limit, 200)):]))


def record_action(
    vault_dir: str,
    *,
    action: str,
    owner: Optional[str],
    tool: str,
    paths: Optional[List[str]] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    reversible: bool = True,
    batch_id: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner": owner or "default",
        "tool": tool,
        "action": action,
        "paths": paths or [],
        "before": before or {},
        "after": after or {},
        "reversible": bool(reversible),
        "undone": False,
    }
    if batch_id:
        entry["batch_id"] = batch_id
    if actor:
        entry["actor"] = {
            "source": str(actor.get("source") or tool),
            "token_id": str(actor.get("token_id") or ""),
            "token_prefix": str(actor.get("token_prefix") or ""),
        }
    entries = _load_raw(vault_dir)
    entries.append(entry)
    _save_raw(vault_dir, entries)
    return entry


def mark_undone(vault_dir: str, entry_id: str) -> None:
    entries = _load_raw(vault_dir)
    for entry in entries:
        if entry.get("id") == entry_id:
            entry["undone"] = True
            break
    _save_raw(vault_dir, entries)


def latest_reversible(vault_dir: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    owner_name = owner or "default"
    for entry in reversed(_load_raw(vault_dir)):
        if entry.get("undone"):
            continue
        if not entry.get("reversible"):
            continue
        if owner is not None and entry.get("owner") != owner_name:
            continue
        return entry
    return None


def undo_action(vault_dir: str, action_id: Optional[str] = None, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Undo a specific action by ID, or the most recent reversible action."""
    import os as _os
    import shutil as _shutil

    from .vault_model import add_manual_relationship, remove_manual_relationship
    from . import vault_service

    entries = _load_raw(vault_dir)
    target = None

    if action_id:
        for entry in entries:
            if entry.get("id") == action_id and not entry.get("undone") and entry.get("reversible"):
                target = entry
                break
        if not target:
            return None
    else:
        owner_name = owner or "default"
        for entry in reversed(entries):
            if entry.get("undone") or not entry.get("reversible"):
                continue
            if owner is not None and entry.get("owner") != owner_name:
                continue
            target = entry
            break
        if not target:
            return None

    action = target.get("action")
    before = target.get("before") or {}
    after = target.get("after") or {}
    paths = target.get("paths") or []

    if action == "create_file":
        path = paths[0]
        abs_path = vault_service.secure_path(vault_dir, path)
        if vault_service.read_text_if_exists(abs_path) != after.get("content"):
            raise ValueError("File changed after creation; refusing unsafe undo")
        _os.remove(abs_path)

    elif action == "update_file":
        path = paths[0]
        abs_path = vault_service.secure_path(vault_dir, path)
        if vault_service.read_text_if_exists(abs_path) != after.get("content"):
            raise ValueError("File changed after update; refusing unsafe undo")
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(before.get("content") or "")

    elif action == "rename_item":
        old_path = before.get("path")
        new_path = after.get("path")
        abs_old = vault_service.secure_path(vault_dir, old_path)
        abs_new = vault_service.secure_path(vault_dir, new_path)
        if not _os.path.exists(abs_new) or _os.path.exists(abs_old):
            raise ValueError("Rename can no longer be safely undone")
        _os.makedirs(_os.path.dirname(abs_old), exist_ok=True)
        _shutil.move(abs_new, abs_old)

    elif action == "relationship_add":
        remove_manual_relationship(vault_dir, after.get("relationship") or {})

    elif action == "relationship_delete":
        add_manual_relationship(vault_dir, before.get("relationship") or {})

    elif action == "delete_file":
        # Undo soft-delete: move file back from .trash/
        from datetime import datetime, timezone as _timezone
        path = paths[0]
        abs_original = vault_service.secure_path(vault_dir, path)
        # Find the file in .trash/
        trash_root = vault_service.TRASH_DIR if hasattr(vault_service, 'TRASH_DIR') else ".trash"
        # Search all date folders in .trash
        trash_base = _os.path.join(vault_dir, trash_root)
        found = None
        if _os.path.isdir(trash_base):
            for date_dir in sorted(_os.listdir(trash_base), reverse=True):
                candidate = _os.path.join(trash_base, date_dir, path)
                if _os.path.isfile(candidate):
                    found = candidate
                    break
        if not found:
            raise ValueError("Trash entry no longer available for undo")
        _os.makedirs(_os.path.dirname(abs_original), exist_ok=True)
        _shutil.move(found, abs_original)

    else:
        raise ValueError(f"Action is not undoable: {action}")

    mark_undone(vault_dir, target["id"])
    return {"success": True, "undone": target}

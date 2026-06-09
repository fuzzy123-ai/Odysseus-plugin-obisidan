import os
import json
import re
from typing import Optional

try:
    from obsidian.backend.routes import router
except ModuleNotFoundError:
    from backend.routes import router

# Metadata manifest required by plugin loader
PLUGIN = {
    "name": "obsidian",
    "version": "1.0.0",
    "description": "Obsidian vault integration for direct editing and AI tool search/updates.",
    "category": "productivity",
    "permissions": ["filesystem"]
}

# --- Vault Path Helpers for Agent Tools ---
def get_vault_path_by_owner(owner: Optional[str]) -> str:
    """Resolve vault path by owner username."""
    from src.constants import DATA_DIR
    folder_name = owner if owner else "default"
    configured_vault = os.getenv("OBSIDIAN_VAULT_DIR", "").strip()
    if configured_vault:
        vault_template = configured_vault.format(owner=folder_name)
        vault_dir = os.path.abspath(os.path.expanduser(vault_template))
    else:
        vault_dir = os.path.abspath(os.path.join(DATA_DIR, "obsidian_vaults", folder_name))
        os.makedirs(vault_dir, exist_ok=True)
    return vault_dir

def secure_path(vault_dir: str, relative_path: str) -> str:
    """Ensure relative path is securely located within vault_dir."""
    cleaned_rel = relative_path.replace("\\", "/").strip("/")
    abs_vault = os.path.abspath(vault_dir)
    abs_target = os.path.abspath(os.path.join(abs_vault, cleaned_rel))
    if os.path.commonpath([abs_vault, abs_target]) != abs_vault:
        raise ValueError("Path traversal attempt detected")
    return abs_target

# --- Tool Handlers ---

async def handle_list_notes(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Lists all notes in the user's Obsidian vault."""
    try:
        vault_dir = get_vault_path_by_owner(owner)
        notes = []
        for root, dirs, files in os.walk(vault_dir):
            dirs[:] = [d for d in dirs if d != ".obsidian"]
            for file in files:
                if file.lower().endswith(".md"):
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, vault_dir).replace("\\", "/")
                    notes.append(rel_path)
        notes.sort()
        if not notes:
            return {"output": "No notes found in the Obsidian vault.", "exit_code": 0}
        return {"output": "\n".join(notes), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to list notes: {e}", "exit_code": 1}

async def handle_read_note(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Reads the content of a specific note from the vault."""
    try:
        params = {}
        if content.strip().startswith("{"):
            params = json.loads(content)
        else:
            params = {"path": content.strip()}
        
        path = params.get("path", "").strip()
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
            
        vault_dir = get_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        
        if not os.path.exists(abs_path):
            return {"error": f"Note not found: {path}", "exit_code": 1}
        if os.path.isdir(abs_path):
            return {"error": f"Path is a directory: {path}", "exit_code": 1}
            
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            note_content = f.read()
        return {"output": note_content, "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to read note: {e}", "exit_code": 1}

async def handle_write_note(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Creates a new note or updates an existing one in the vault."""
    try:
        params = {}
        if content.strip().startswith("{"):
            params = json.loads(content)
        else:
            lines = content.strip().split("\n", 1)
            params = {
                "path": lines[0].strip(),
                "content": lines[1] if len(lines) > 1 else ""
            }
        
        path = params.get("path", "").strip()
        note_content = params.get("content", "")
        
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
            
        vault_dir = get_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(note_content)
        return {"output": f"Successfully wrote note to {path}.", "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to write note: {e}", "exit_code": 1}

async def handle_search_notes(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Performs full-text search inside markdown notes."""
    try:
        params = {}
        if content.strip().startswith("{"):
            params = json.loads(content)
        else:
            params = {"query": content.strip()}
            
        query = params.get("query", "").strip()
        if not query:
            return {"error": "Query parameter is required.", "exit_code": 1}
            
        vault_dir = get_vault_path_by_owner(owner)
        query_re = re.compile(re.escape(query), re.IGNORECASE)
        results = []
        
        for root, dirs, files in os.walk(vault_dir):
            dirs[:] = [d for d in dirs if d != ".obsidian"]
            for file in files:
                if not file.lower().endswith(".md"):
                    continue
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, vault_dir).replace("\\", "/")
                
                try:
                    matches = []
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if query_re.search(line):
                                matches.append(f"Line {line_num}: {line.strip()}")
                    if matches:
                        results.append(f"--- {rel_path} ---\n" + "\n".join(matches))
                except Exception:
                    continue
                    
        if not results:
            return {"output": f"No matches found for query: {query}", "exit_code": 0}
        return {"output": "\n\n".join(results), "exit_code": 0}
    except Exception as e:
        return {"error": f"Search failed: {e}", "exit_code": 1}


def setup(ctx):
    """Setup hook to register endpoints and agent tools."""
    
    # 1. Register routes in FastAPI app
    ctx.add_router(router)

    # Register the browser-side panel when Odysseus exposes a plugin frontend loader.
    if hasattr(ctx, "register_frontend_script"):
        ctx.register_frontend_script("/api/plugins/obsidian/web/main.js")
    
    # 2. Register obsidian_list_notes tool
    ctx.register_tool(
        tool_tag="obsidian_list_notes",
        tool_schema={
            "type": "function",
            "function": {
                "name": "obsidian_list_notes",
                "description": "List all markdown notes in the user's Obsidian vault.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        tool_handler=handle_list_notes
    )
    
    # 3. Register obsidian_read_note tool
    ctx.register_tool(
        tool_tag="obsidian_read_note",
        tool_schema={
            "type": "function",
            "function": {
                "name": "obsidian_read_note",
                "description": "Read the contents of a markdown note from the user's Obsidian vault.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "The relative path of the note to read (e.g. 'Work/Project.md')"}
                    },
                    "required": ["path"]
                }
            }
        },
        tool_handler=handle_read_note
    )
    
    # 4. Register obsidian_write_note tool
    ctx.register_tool(
        tool_tag="obsidian_write_note",
        tool_schema={
            "type": "function",
            "function": {
                "name": "obsidian_write_note",
                "description": "Create a new note or update an existing one in the user's Obsidian vault.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "The relative path of the note (e.g. 'Work/Project.md')"},
                        "content": {"type": "string", "description": "The markdown content to write to the note"}
                    },
                    "required": ["path", "content"]
                }
            }
        },
        tool_handler=handle_write_note
    )
    
    # 5. Register obsidian_search_notes tool
    ctx.register_tool(
        tool_tag="obsidian_search_notes",
        tool_schema={
            "type": "function",
            "function": {
                "name": "obsidian_search_notes",
                "description": "Search for notes containing a specific text query in the user's Obsidian vault.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword or text query"}
                    },
                    "required": ["query"]
                }
            }
        },
        tool_handler=handle_search_notes
    )

import os
import re
import shutil
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from src.constants import DATA_DIR
from src.auth_helpers import require_user

router = APIRouter(prefix="/api/plugins/obsidian")

# --- Request Models ---
class FileWriteRequest(BaseModel):
    path: str
    content: str

class FolderCreateRequest(BaseModel):
    path: str

class RenameRequest(BaseModel):
    old_path: str
    new_path: str

# --- Helper Functions ---
def get_vault_path(request: Request) -> str:
    """Get the user-specific vault directory.
    
    Uses multi-user isolation or 'default' if auth is disabled.
    """
    username = require_user(request)
    folder_name = username if username else "default"
    configured_vault = os.getenv("OBSIDIAN_VAULT_DIR", "").strip()
    if configured_vault:
        vault_template = configured_vault.format(owner=folder_name)
        vault_dir = os.path.abspath(os.path.expanduser(vault_template))
    else:
        vault_dir = os.path.abspath(os.path.join(DATA_DIR, "obsidian_vaults", folder_name))
        os.makedirs(vault_dir, exist_ok=True)
    return vault_dir

def secure_path(vault_dir: str, relative_path: str) -> str:
    """Resolve and validate a relative path within the user's vault.
    
    Prevents path traversal attacks. Raises HTTPException 400 if invalid.
    """
    cleaned_rel = relative_path.replace("\\", "/").strip("/")
    abs_vault = os.path.abspath(vault_dir)
    abs_target = os.path.abspath(os.path.join(abs_vault, cleaned_rel))
    
    # Ensure target is strictly inside vault_dir using commonpath
    if os.path.commonpath([abs_vault, abs_target]) != abs_vault:
        raise HTTPException(status_code=400, detail="Path traversal attempt detected")
        
    return abs_target

def get_file_tree(dir_path: str, base_path: str) -> List[Dict[str, Any]]:
    """Recursively build a sorted tree of directories and files."""
    tree = []
    try:
        for entry in os.scandir(dir_path):
            if entry.name == ".obsidian":
                continue
            rel_path = os.path.relpath(entry.path, base_path).replace("\\", "/")
            if entry.is_dir():
                tree.append({
                    "name": entry.name,
                    "path": rel_path,
                    "is_dir": True,
                    "children": get_file_tree(entry.path, base_path)
                })
            else:
                tree.append({
                    "name": entry.name,
                    "path": rel_path,
                    "is_dir": False
                })
    except Exception:
        pass
    # Sort: folders first, then files alphabetically
    tree.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return tree

# --- Endpoints ---

@router.get("/files")
async def list_files(request: Request):
    """Get the complete tree structure of the vault."""
    try:
        vault_dir = get_vault_path(request)
        tree = get_file_tree(vault_dir, vault_dir)
        return tree
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/file")
async def read_file(path: str, request: Request):
    """Read a specific file's content or serve binary assets."""
    vault_dir = get_vault_path(request)
    abs_path = secure_path(vault_dir, path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is a directory")
        
    # Check if the file is markdown or text to return as JSON
    lower_name = abs_path.lower()
    if lower_name.endswith((".md", ".txt", ".json", ".html", ".js", ".css")):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"content": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
    else:
        # Serve binary files (images, PDFs) directly
        return FileResponse(abs_path)

@router.post("/file")
async def create_file(req: FileWriteRequest, request: Request):
    """Create a new file in the vault."""
    vault_dir = get_vault_path(request)
    abs_path = secure_path(vault_dir, req.path)
    
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail="File already exists")
        
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"success": True, "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create file: {e}")

@router.put("/file")
async def update_file(req: FileWriteRequest, request: Request):
    """Update (autosave) an existing file in the vault."""
    vault_dir = get_vault_path(request)
    abs_path = secure_path(vault_dir, req.path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is a directory")
        
    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"success": True, "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update file: {e}")

@router.delete("/file")
async def delete_file(path: str, request: Request):
    """Delete a file from the vault."""
    vault_dir = get_vault_path(request)
    abs_path = secure_path(vault_dir, path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is a directory")
        
    try:
        os.remove(abs_path)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

@router.post("/folder")
async def create_folder(req: FolderCreateRequest, request: Request):
    """Create a new folder in the vault."""
    vault_dir = get_vault_path(request)
    abs_path = secure_path(vault_dir, req.path)
    
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail="Path already exists")
        
    try:
        os.makedirs(abs_path, exist_ok=True)
        return {"success": True, "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create folder: {e}")

@router.delete("/folder")
async def delete_folder(path: str, request: Request):
    """Recursively delete a folder from the vault."""
    vault_dir = get_vault_path(request)
    abs_path = secure_path(vault_dir, path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Folder not found")
        
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is not a directory")
        
    try:
        shutil.rmtree(abs_path)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete folder: {e}")

@router.post("/rename")
async def rename_item(req: RenameRequest, request: Request):
    """Rename or move a file/folder in the vault."""
    vault_dir = get_vault_path(request)
    abs_old = secure_path(vault_dir, req.old_path)
    abs_new = secure_path(vault_dir, req.new_path)
    
    if not os.path.exists(abs_old):
        raise HTTPException(status_code=404, detail="Source not found")
        
    if os.path.exists(abs_new):
        raise HTTPException(status_code=400, detail="Destination already exists")
        
    try:
        os.makedirs(os.path.dirname(abs_new), exist_ok=True)
        shutil.move(abs_old, abs_new)
        return {"success": True, "path": req.new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename: {e}")

@router.get("/search")
async def search_vault(q: str, request: Request):
    """Perform full-text search inside all markdown notes in the vault."""
    vault_dir = get_vault_path(request)
    results = []
    
    if not q.strip():
        return results
        
    query_re = re.compile(re.escape(q), re.IGNORECASE)
    
    try:
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
                                matches.append({
                                    "line": line_num,
                                    "text": line.strip()
                                })
                    if matches:
                        results.append({
                            "path": rel_path,
                            "matches": matches
                        })
                except Exception:
                    continue  # skip unreadable files
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return results

@router.get("/web/{filename:path}")
async def serve_web_assets(filename: str):
    """Serve static web assets for the plugin's frontend."""
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.abspath(os.path.join(current_dir, "frontend"))
    target_path = os.path.abspath(os.path.join(static_dir, filename))
    
    # Path traversal protection
    if os.path.commonpath([static_dir, target_path]) != static_dir:
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    media_type = None
    if target_path.endswith(".css"):
        media_type = "text/css"
    elif target_path.endswith(".js"):
        media_type = "application/javascript"
    elif target_path.endswith(".svg"):
        media_type = "image/svg+xml"
        
    return FileResponse(target_path, media_type=media_type)

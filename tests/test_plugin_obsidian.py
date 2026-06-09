import os
import sys
import tempfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.routes import secure_path, get_file_tree
from plugin import (
    get_vault_path_by_owner,
    handle_list_notes,
    handle_read_note,
    handle_write_note,
    handle_search_notes,
    setup,
)


def test_secure_path_prevents_traversal():
    """Verify that secure_path blocks relative path traversal attacks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_dir = os.path.abspath(tmpdir)

        safe = secure_path(vault_dir, "notes/my_note.md")
        assert safe.replace("\\", "/") == f"{vault_dir}/notes/my_note.md".replace("\\", "/")

        dangerous_paths = [
            "../traversal.md",
            "notes/../../secret.txt",
            "..\\escape",
        ]

        for path in dangerous_paths:
            with pytest.raises(HTTPException) as exc:
                secure_path(vault_dir, path)
            assert exc.value.status_code == 400
            assert "Path traversal attempt detected" in exc.value.detail


def test_get_vault_path_by_owner(monkeypatch):
    """Verify vault isolation by username."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("src.constants.DATA_DIR", tmpdir)

        vault_user1 = get_vault_path_by_owner("user1")
        vault_user2 = get_vault_path_by_owner("user2")
        vault_default = get_vault_path_by_owner(None)

        assert "user1" in vault_user1
        assert "user2" in vault_user2
        assert "default" in vault_default

        assert os.path.isdir(vault_user1)
        assert os.path.isdir(vault_user2)
        assert os.path.isdir(vault_default)


@pytest.mark.asyncio
async def test_tool_handlers_crud(monkeypatch):
    """Test tool handlers for listing, reading, writing, and searching notes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        res = await handle_list_notes("")
        assert res["exit_code"] == 0
        assert "No notes found" in res["output"]

        write_content = '{"path": "Project.md", "content": "# Odysseus Obsidian Integration\\n\\nThis is a test note."}'
        res = await handle_write_note(write_content)
        assert res["exit_code"] == 0
        assert "Successfully wrote note" in res["output"]
        assert os.path.exists(os.path.join(tmpdir, "Project.md"))

        read_content = '{"path": "Project.md"}'
        res = await handle_read_note(read_content)
        assert res["exit_code"] == 0
        assert "Odysseus Obsidian Integration" in res["output"]

        search_query = '{"query": "Integration"}'
        res = await handle_search_notes(search_query)
        assert res["exit_code"] == 0
        assert "Project.md" in res["output"]
        assert "Line 1:" in res["output"]


def test_plugin_setup_registration():
    """Verify that setup registers routes, frontend assets, and agent tools."""
    registered_routers = []
    registered_scripts = []
    registered_tools = {}

    class MockContext:
        def add_router(self, router):
            registered_routers.append(router)

        def register_frontend_script(self, script_url):
            registered_scripts.append(script_url)

        def register_tool(self, tool_tag, tool_schema, tool_handler):
            registered_tools[tool_tag] = (tool_schema, tool_handler)

    ctx = MockContext()
    setup(ctx)

    assert len(registered_routers) == 1
    assert registered_scripts == ["/api/plugins/obsidian/web/main.js"]
    assert "obsidian_list_notes" in registered_tools
    assert "obsidian_read_note" in registered_tools
    assert "obsidian_write_note" in registered_tools
    assert "obsidian_search_notes" in registered_tools

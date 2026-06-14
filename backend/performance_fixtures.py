import os
import time
from typing import Any, Dict

from .vault_model import graph_payload


def create_large_vault_fixture(vault_dir: str, note_count: int = 120) -> Dict[str, Any]:
    """Create deterministic markdown notes for graph/index performance checks."""
    os.makedirs(vault_dir, exist_ok=True)
    folders = ["Projects", "Architecture", "Tests", "Risks"]
    for folder in folders:
        os.makedirs(os.path.join(vault_dir, folder), exist_ok=True)

    paths = []
    for index in range(note_count):
        folder = folders[index % len(folders)]
        path = f"{folder}/Note-{index:03d}.md"
        previous_link = f"[[Note-{index - 1:03d}]]" if index else ""
        next_link = f"[[Note-{(index + 1) % note_count:03d}]]"
        tag = ["#planning", "#architecture", "#test", "#risk"][index % 4]
        body = (
            f"# Note {index:03d}\n\n"
            f"{tag} #large-vault\n\n"
            f"{previous_link}\n{next_link}\n\n"
            f"This note mentions Note {(index + 2) % note_count:03d} for filename relationship coverage.\n"
        )
        with open(os.path.join(vault_dir, path), "w", encoding="utf-8") as fh:
            fh.write(body)
        paths.append(path)

    return {"note_count": note_count, "paths": paths}


def profile_graph_build(vault_dir: str) -> Dict[str, Any]:
    started = time.perf_counter()
    payload = graph_payload(vault_dir)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    graph = payload["graph"]
    return {
        "elapsed_ms": elapsed_ms,
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
    }

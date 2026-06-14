# Odysseus Plugin for Obsidian

Obsidian vault integration for the [Odysseus](https://github.com/fuzzy123-ai/odysseus-fuzzy) local AI workspace.

This is a **standalone plugin** designed to be dynamically loaded into the Odysseus workspace via the new Odysseus Plugin System API. It adds a complete vault workspace to Odysseus, offering a dockable editor UI, secure per-user vault isolation, graph and tag intelligence, AI-assisted project planning, memory review workflows, and a full suite of agent-callable tools for reading, writing, searching, organizing, exporting, and importing Markdown notes.

It also registers a read-only context provider for the Odysseus context-orchestrator. This allows Odysseus to preload relevant vault context through a generic plugin API while keeping all Obsidian-specific rules self-contained inside this plugin.

## Feature Overview

### Vault Workspace UI

- Right-docked Obsidian panel, overlay window, fullscreen mode, and standalone app page at `/api/plugins/obsidian/app`.
- Resizable panel and sidebar widths with persisted local preferences.
- File tree browsing with folder expansion, folder-first sorting, inline selection, drag-and-drop Markdown import, and virtual project-planning session nodes.
- Note creation, editing, rename/move, delete, folder creation, and empty-folder deletion.
- Split Markdown editor with live rendered preview.
- Markdown toolbar actions for headings, bold, italic, quote, lists, code, links, wiki links, and tags.
- Autosave for open notes with toast feedback.
- Search panel with full-text matches, per-result open, rename, and delete actions.
- Standalone serving of frontend assets through `/api/plugins/obsidian/web/{filename}`.

### Markdown Preview and Navigation

- Wiki-link preprocessing for `[[Note]]` and `[[Note|Label]]` links.
- Clickable wiki links that open existing notes or create missing notes after confirmation.
- Markdown file links resolve relative to the current note when possible.
- Mermaid rendering through the shared Odysseus Markdown renderer.
- Hashtag normalization and clickable tag badges in preview.
- Tag detail popovers showing linked notes and one-click creation/opening of tag meta notes under `Tags/<tag>.md`.

### Tags, Graph, and Relationships

- Vault indexer extracts explicit hashtags while ignoring headings, code blocks, inline code, and URL fragments.
- Implicit tags are generated from note filenames.
- Graph payload includes Markdown links, filename mentions, shared-tag relationships, folders, and manually curated relationships.
- Graph view supports Cytoscape rendering with SVG fallback.
- Focused graph mode centers the current note or selected project folder.
- Edge filtering by relationship type.
- Manual relationship storage in `.obsidian/relationships.json`.
- Supported manual relationship types include `manual`, `relates_to`, `depends_on`, `blocks`, and `supports`.

### Vault Security and Portability

- Per-user vault isolation when Odysseus authentication is enabled.
- Default vault location: `data/obsidian_vaults/<owner>`.
- Optional custom vault root through `OBSIDIAN_VAULT_DIR`, including `{owner}` interpolation.
- Path traversal protection for every file, folder, asset, archive, and tool path.
- Optional vault password protection with lock, unlock, and password removal flows.
- Password-derived vault metadata is stored under `.obsidian`; plaintext password values are not rendered back into the UI.
- ZIP export of the whole vault or a relative subtree.
- Optional encrypted ZIP exports.
- ZIP import with archive member validation to block escape paths and reserved internal files.

### History and Undo

- Vault changes are recorded in `.obsidian/history.json`.
- History entries include action, owner, tool/source, paths, timestamps, reversible state, and before/after snapshots where useful.
- Safe undo is available for reversible create, update, rename, relationship-add, and relationship-delete actions.
- Undo refuses to overwrite newer user edits when file contents no longer match the recorded after-state.

### AI Project Planning

- Non-destructive project-plan previews for new or existing vault folders.
- Project templates for `software`, `research`, `writing`, `sec_ops`, `generic`, `teaching`, and `game_dev`.
- Aliases such as `ops`, `Unterricht`, `Education`, `GameDev`, and `game-dev` normalize to supported project kinds.
- Optional AI prompt improvement before preview generation.
- Optional sequential AI content generation for every planned Markdown file.
- Streaming preview endpoint with Server-Sent Events for file-by-file progress.
- Recoverable planning sessions stored under `.obsidian/project_planning_sessions.json`.
- Session list, load, delete/cancel, preview-stream, and apply endpoints.
- GameDev concept draft gate: content generation for game projects requires an editable approved concept first.
- Generated plans include frontmatter, schema tags, warnings, conflicts, project files, and suggested graph relationships.
- Apply flow requires confirmation and records created files and relationships in vault history.

### Memory Review

- Save-to-Obsidian preview workflow for reviewed memories, decisions, ideas, references, resources, meetings, and project notes.
- Actions: `save_to_obsidian`, `append_to_note`, `memory_only`, and `discard`.
- Destination picker for folders or existing notes.
- Tag picker with autocomplete from existing vault tags and support for new tags.
- Link suggestions based on selected notes, requested links, and vault content.
- Conflict detection before writing new or appended note content.
- Apply flow requires confirmation for vault writes and records file/relationship changes in history.

### Agent Tools

The plugin registers Odysseus agent tools through `ctx.register_tool(...)` so AI workflows can work with the vault directly:

- Core notes: `obsidian_list_notes`, `obsidian_tree`, `obsidian_read_note`, `obsidian_write_note`, `obsidian_search_notes`.
- Organization: `obsidian_create_folder`, `obsidian_rename_item`, `obsidian_delete_note`, `obsidian_delete_folder`.
- Tags and graph: `obsidian_list_tags`, `obsidian_graph`, `obsidian_list_relationships`, `obsidian_add_relationship`, `obsidian_delete_relationship`.
- History: `obsidian_history`, `obsidian_undo`.
- Vault security and portability: `obsidian_vault_status`, `obsidian_vault_set_password`, `obsidian_vault_lock`, `obsidian_vault_unlock`, `obsidian_vault_remove_password`, `obsidian_vault_export`, `obsidian_vault_import`.
- Project planning: `obsidian_project_plan_templates`, `obsidian_project_plan_improve_description`, `obsidian_project_plan_gamedev_draft`, `obsidian_project_plan_preview`, `obsidian_project_plan_apply`.
- Memory review: `obsidian_memory_review_preview`, `obsidian_memory_review_apply`.

Destructive or overwriting tool operations require explicit `confirm: true`.

### Vault Writing Rules

Odysseus maintains a visible rules note at `AI Memory/Canonical/Vault Writing Rules.md`.

- Markdown files have a softcap of 600 lines per file.
- The 600-line limit keeps each note small enough for manageable AI context during retrieval, review, and follow-up edits.
- If content would exceed 600 lines, external AI clients should split it by topic, phase, date, or subcomponent and connect the parts with links or an index note.
- Write responses may include `line_count`, `line_soft_cap`, and `warning`; agents should treat that warning as a request to split or reorganize future writes.

### Context Provider

The plugin registers `obsidian.vault_context` through `ctx.register_context_provider(...)` when the host Odysseus fork exposes that API.

Provider contract:

- Input: `owner`, `query`, `budget`, and `mode`.
- Output: `structured_state`, `snippets`, `sources`, `warnings`, and `cache_key`.
- Frontmatter/properties are returned as structured state for machine-readable facts.
- Markdown body excerpts are returned as untrusted snippets.
- Sources include note path, title, tags, score, and match reason.
- Identical vault/query/budget output produces a stable cache key.
- Locked vaults return no note content and include a warning.

The provider does not add a public HTTP route. It uses plugin-internal vault services directly, so the Odysseus core can remain generic.

## API Surface

All routes are registered under `/api/plugins/obsidian`.

### UI and Status

- `GET /app` - standalone plugin app page.
- `GET /ai-status` - resolved Odysseus AI endpoint role/model for project planning.
- `GET /status` - vault password-protection and lock status.
- `GET /web/{filename}` - frontend assets.

### Vault Files

- `GET /files` - file tree.
- `GET /file?path=<path>` - read text files as JSON or serve binary assets.
- `POST /file` - create a file.
- `PUT /file` - update a file.
- `DELETE /file?path=<path>` - delete a file.
- `POST /folder` - create a folder.
- `DELETE /folder?path=<path>` - delete an empty folder.
- `POST /rename` - rename or move a file or folder.
- `GET /search?q=<query>` - full-text Markdown search.

### Vault Model

- `GET /tags` - explicit and implicit tag index.
- `GET /graph` - note graph with optional `focus` or `tag` query filters.
- `GET /relationships` - manual graph relationships.
- `POST /relationships` - add a manual relationship.
- `DELETE /relationships` - remove a manual relationship.

### Vault Security

- `POST /vault/password` - set or replace vault password protection.
- `POST /vault/lock` - lock the vault.
- `POST /vault/unlock` - unlock the vault.
- `DELETE /vault/password` - remove password protection.
- `POST /vault/export` - export base64 ZIP archive data.
- `POST /vault/import` - import base64 ZIP archive data.

### Project Planning

- `GET /project-plan/templates` - available planning templates.
- `GET /project-plan/sessions` - list visible planning sessions.
- `POST /project-plan/sessions` - create a planning session.
- `GET /project-plan/sessions/{session_id}` - load one session.
- `DELETE /project-plan/sessions/{session_id}` - cancel/delete one session.
- `POST /project-plan/sessions/{session_id}/preview-stream` - stream session preview progress.
- `POST /project-plan/sessions/{session_id}/apply` - apply a confirmed session.
- `POST /project-plan/improve-description` - AI-improve a project description.
- `POST /project-plan/gamedev-draft` - create an editable GameDev concept draft.
- `POST /project-plan/preview` - create a non-destructive plan preview.
- `POST /project-plan/preview-stream` - stream a non-destructive plan preview.
- `POST /project-plan/apply` - apply a confirmed plan.

### Memory Review

- `POST /memory-review/preview` - create a non-destructive memory review plan.
- `POST /memory-review/apply` - apply a confirmed memory review plan.

### History

- `GET /history` - recent vault actions.
- `POST /history/undo` - undo the latest safe reversible action for the current user.

## Repository Split

This repository contains only the Obsidian plugin.

Core Odysseus changes belong in [`fuzzy123-ai/odysseus-fuzzy`](https://github.com/fuzzy123-ai/odysseus-fuzzy), not in this plugin repository and not in upstream repositories owned by other projects.

The plugin expects the Odysseus core plugin manager to support:

- Dynamic plugin discovery from `plugins/<plugin-name>/plugin.py`.
- `ctx.add_router(...)`.
- `ctx.register_tool(...)` for agent-controllable vault actions.
- `ctx.register_context_provider(...)` for read-only vault context.
- `ctx.register_consolidation_job(...)` for planned background consolidation jobs.
- Manifest UI entries such as `PLUGIN["ui"]["open"]`.

Core Odysseus must not import this plugin directly. This plugin owns vault path resolution, lock checks, owner isolation, Frontmatter parsing, tags, graph relationships, and snippet selection.

## Current Implementation Status

Implemented in the active Fuzzy/Odysseus branch:

- Phase 0: current Obsidian UI/graph stabilization and release-candidate preparation.
- Phase 1: generic Core plugin API for context providers and consolidation-job specs.
- Phase 2: plugin-internal vault service layer reused by routes and agent tools.
- Phase 3: `obsidian.vault_context` read-only provider with Frontmatter-first structured state, untrusted snippets, sources, warnings, stable cache key, and locked-vault safety.
- Phase 4: Core Context-Orchestrator for chat and agent mode with provider preloading, stable prompt prefix, token budgeting, and final overflow guard.
- Phase 5: preventive history compaction and persistent task-state blocks.
- Phase 6: background consolidation jobs. The Obsidian job writes `.obsidian/consolidation_report.json` with duplicate-title candidates, orphan-note candidates, and frontmatter suggestions. It never deletes or rewrites notes.
- Phase 7: rollout docs, feature flags, and regression tests. Core feature flags are `context_provider_preload` and `consolidation_jobs`.

## Install

From the root of an Odysseus checkout:

```powershell
git clone -b dev https://github.com/fuzzy123-ai/Odysseus-plugin-obsidian.git plugins/obsidian
```

Restart Odysseus after cloning. The plugin manager imports `plugins/obsidian/plugin.py`, registers the API routes, and exposes the UI entry at `/api/plugins/obsidian/app`.

The panel can be opened from the Odysseus plugin settings UI when the plugin is enabled.

## Configuration

By default, vaults are stored per user under Odysseus' data directory:

```text
data/obsidian_vaults/<owner>
```

To point to an existing vault path, set:

```text
OBSIDIAN_VAULT_DIR=C:\path\to\vaults\{owner}
```

`{owner}` is replaced with the authenticated username, or `default` when auth is disabled.

The plugin resolves AI calls through Odysseus' endpoint resolver. Project planning first tries the `utility` role and falls back to `default`.

## Development

Use the `dev` branch for active work and open pull requests against `dev`.

Run the plugin tests from an Odysseus checkout after cloning this repository into `plugins/obsidian`:

```powershell
python -m pytest plugins/obsidian/tests/test_plugin_obsidian.py
```

When testing against the Fuzzy/Odysseus fork, also run the plugin-manager and context-provider integration tests:

```powershell
python -m pytest tests/test_plugin_obsidian_load.py tests/test_plugin_system.py
```

The host Odysseus checkout also contains static sidebar contract tests:

```powershell
python -m pytest tests/test_obsidian_sidebar_static.py
```

For a quick frontend syntax check:

```powershell
node --check plugins/obsidian/frontend/main.js
```

## Files

- `plugin.py` - Odysseus plugin manifest, setup hook, and agent tool handlers.
- `backend/context_provider.py` - read-only vault context provider for the Odysseus context-orchestrator API.
- `backend/consolidation_job.py` - non-destructive vault consolidation report job.
- `backend/vault_service.py` - shared vault path, file, search, tree, and mutation helpers used by routes, tools, and the context provider.
- `backend/routes.py` - FastAPI routes and request models.
- `backend/vault_model.py` - tag extraction, vault indexing, graph construction, and manual relationships.
- `backend/vault_security.py` - password status, lock/unlock, ZIP export/import, and archive validation.
- `backend/vault_history.py` - history storage and undo metadata.
- `backend/project_planning.py` - project templates, preview/apply validation, AI content generation, and GameDev concept flow.
- `backend/memory_review.py` - reviewed-memory preview/apply planning.
- `frontend/main.js` - dockable UI, editor, search, graph, project planner, memory review, import/export, and settings interactions.
- `frontend/style.css` - plugin panel, editor, graph, project planning, memory review, responsive, and standalone styles.
- `frontend/cytoscape.min.js` - bundled graph renderer dependency.

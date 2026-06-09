# Odysseus Plugin for Obsidian

Obsidian vault integration for the Odysseus local AI workspace.

This plugin adds:

- a right-docked Obsidian panel in the Odysseus UI
- file tree browsing, markdown editing, autosave, rename/delete, and search
- agent tools for listing, reading, writing, and searching markdown notes
- per-user vault isolation when Odysseus authentication is enabled

## Repository Split

This repository contains only the Obsidian plugin.

Core Odysseus changes belong in [`fuzzy123-ai/odysseus-fuzzy`](https://github.com/fuzzy123-ai/odysseus-fuzzy), not in this plugin repository and not in upstream repositories owned by other projects. The plugin expects the Odysseus core plugin loader to support:

- dynamic plugin discovery from `plugins/<plugin-name>/plugin.py`
- `ctx.add_router(...)`
- `ctx.register_tool(...)`
- `ctx.register_frontend_script(...)`

## Install

From the root of an Odysseus checkout:

```powershell
git clone -b dev https://github.com/fuzzy123-ai/Odysseus-plugin-obisidan.git plugins/obsidian
```

Restart Odysseus after cloning. The plugin loader imports `plugins/obsidian/plugin.py`, registers the API routes, and loads the browser panel through `/api/plugins/loader.js`.

Use this with the plugin-loader branch from `fuzzy123-ai/odysseus-fuzzy` until the loader is available in your chosen Odysseus base.

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

## Development

Use the `dev` branch for active work and open pull requests against `dev`.

Run the plugin tests from an Odysseus checkout after cloning this repository into `plugins/obsidian`:

```powershell
python -m pytest tests/test_plugin_obsidian.py
```

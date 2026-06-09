/**
 * Obsidian Plugin for Odysseus — Panel-based UI (Option B)
 * 
 * Injects a right-docked panel (like Notes) instead of a centered modal.
 * Features: File tree, Split Editor with Live Preview, Wiki-Links, Autosave, Fulltext Search
 * 
 * Architecture:
 *   - Panel is a fixed-position div on the right side
 *   - Toggle via body class "obsidian-open" (adds CSS transition)
 *   - Chat content shrinks when panel opens (via CSS)
 */

import { styledConfirm, styledPrompt, showToast } from '/static/js/ui.js';

// Dynamic stylesheet insertion
const link = document.createElement('link');
link.rel = 'stylesheet';
link.href = '/api/plugins/obsidian/web/style.css';
document.head.appendChild(link);

// ─── State ───────────────────────────────────────────────────────────────────
let currentNotePath = null;
let vaultFiles = [];
const expandedFolders = new Set();
let autosaveTimeout = null;
let searchTimeout = null;
let isPanelOpen = false;
let currentViewMode = 'document';

// ─── Helpers ─────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function preprocessWikiLinks(text) {
  if (!text) return '';
  return text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (match, notePath, label) => {
    const cleanPath = notePath.trim();
    const displayLabel = (label || notePath).trim();
    const encodedPath = encodeURIComponent(cleanPath);
    return `[${displayLabel}](#obsidian-link-${encodedPath})`;
  });
}

function flattenNotes(nodes, out = []) {
  nodes.forEach(node => {
    if (node.is_dir && node.children) {
      flattenNotes(node.children, out);
    } else if (!node.is_dir && node.path.toLowerCase().endsWith('.md')) {
      out.push(node.path);
    }
  });
  return out;
}

function normalizeNotePath(path) {
  const clean = (path || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
  return clean.toLowerCase().endsWith('.md') ? clean : `${clean}.md`;
}

// ─── Panel UI Injection ──────────────────────────────────────────────────────

function injectUIElements() {
  // 1. Sidebar tool section
  const toolsSection = document.getElementById('tools-section');
  if (toolsSection && !document.getElementById('tool-obsidian-btn')) {
    const btn = document.createElement('div');
    btn.className = 'list-item';
    btn.id = 'tool-obsidian-btn';
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;">
        <polygon points="6 2 18 2 18 6 6 6" />
        <rect x="3" y="6" width="18" height="16" rx="2" />
        <path d="M8 11h8M8 15h5" />
      </svg>
      <span class="grow">Obsidian</span>
    `;
    toolsSection.appendChild(btn);
  }

  // 2. Icon rail
  const iconRail = document.getElementById('icon-rail');
  if (iconRail && !document.getElementById('rail-obsidian')) {
    const settingsBtn = document.getElementById('rail-settings');
    const btn = document.createElement('button');
    btn.className = 'icon-rail-btn';
    btn.id = 'rail-obsidian';
    btn.title = 'Obsidian Vault';
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="6 2 18 2 18 6 6 6" />
        <rect x="3" y="6" width="18" height="16" rx="2" />
        <path d="M8 11h8M8 15h5" />
      </svg>
    `;
    if (settingsBtn) {
      iconRail.insertBefore(btn, settingsBtn);
    } else {
      iconRail.appendChild(btn);
    }
  }

  // 3. Panel skeleton (right-docked, full-height, like Notes)
  if (!document.getElementById('obsidian-panel')) {
    const panelHtml = `
      <div id="obsidian-panel" class="obsidian-panel">
        <div class="obsidian-panel-backdrop" id="obsidian-panel-backdrop"></div>
        <div class="obsidian-panel-content">
          <!-- Header -->
          <div class="obsidian-panel-header">
            <div class="obsidian-panel-title">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px;opacity:0.8;">
                <polygon points="6 2 18 2 18 6 6 6" />
                <rect x="3" y="6" width="18" height="16" rx="2" />
                <path d="M8 11h8M8 15h5" />
              </svg>
              <span>Obsidian Vault</span>
            </div>
            <div class="obsidian-panel-actions">
              <button class="obsidian-panel-btn" id="obsidian-panel-minimize" title="Minimize">─</button>
              <button class="obsidian-panel-btn" id="obsidian-panel-close" title="Close">✕</button>
            </div>
          </div>

          <!-- Body -->
          <div class="obsidian-panel-body">
            <!-- Sidebar: Tree + Search -->
            <div class="obsidian-sidebar">
              <div class="obsidian-actions">
                <button id="obsidian-new-note" title="New Note">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                </button>
                <button id="obsidian-new-folder" title="New Folder">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                </button>
                <button id="obsidian-refresh" title="Refresh Vault">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                </button>
              </div>
              <div class="obsidian-search-box">
                <input type="text" id="obsidian-search-input" placeholder="Search notes..." autocomplete="off">
              </div>
              <div class="obsidian-file-tree" id="obsidian-file-tree"></div>
            </div>

            <!-- Workspace: Editor / Graph -->
            <div class="obsidian-workspace">
              <div class="obsidian-empty-state" id="obsidian-empty-state">
                <span>Select a note to start editing or create a new one</span>
              </div>
              <div class="obsidian-editor-container hidden" id="obsidian-editor-container">
                <div class="obsidian-editor-header">
                  <div class="obsidian-current-note-title" id="obsidian-current-note-title">Untitled.md</div>
                  <div class="obsidian-editor-actions">
                    <label class="obsidian-view-toggle" title="Switch document or graph view">
                      <span>Editor</span>
                      <input type="checkbox" id="obsidian-view-toggle">
                      <span class="obsidian-toggle-track" aria-hidden="true"></span>
                      <span>Graph</span>
                    </label>
                    <button id="obsidian-rename-note" class="btn btn-secondary">Rename</button>
                    <button id="obsidian-delete-note" class="btn btn-danger">Delete</button>
                  </div>
                </div>
                <div class="obsidian-editor-panes">
                  <div class="obsidian-pane obsidian-editor-pane">
                    <textarea id="obsidian-textarea" placeholder="Start writing markdown..."></textarea>
                  </div>
                </div>
                <div class="obsidian-graph-view hidden" id="obsidian-graph-view"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
    const div = document.createElement('div');
    div.innerHTML = panelHtml;
    document.body.appendChild(div.firstElementChild);
  }
}

// ─── Panel Toggle ────────────────────────────────────────────────────────────

function togglePanel() {
  isPanelOpen = !isPanelOpen;
  document.body.classList.toggle('obsidian-open', isPanelOpen);
  
  if (isPanelOpen) {
    loadVaultFiles();
  }
}

function openPanel() {
  if (isPanelOpen) return;
  isPanelOpen = true;
  document.body.classList.add('obsidian-open');
  loadVaultFiles();
}

function closePanel() {
  if (!isPanelOpen) return;
  isPanelOpen = false;
  document.body.classList.remove('obsidian-open');
}

// ─── File Tree ───────────────────────────────────────────────────────────────

async function loadVaultFiles() {
  try {
    const res = await fetch('/api/plugins/obsidian/files');
    if (res.ok) {
      vaultFiles = await res.json();
      renderFileTree();
      if (currentViewMode === 'graph') {
        renderGraphView();
      }
    }
  } catch (e) {
    console.error('Failed to load vault files:', e);
  }
}

function renderFileTree() {
  const container = document.getElementById('obsidian-file-tree');
  if (!container) return;
  buildTreeHTML(vaultFiles, container, 0);
}

function buildTreeHTML(nodes, container, level) {
  if (level === 0) container.innerHTML = '';

  nodes.forEach(node => {
    const item = document.createElement('div');
    item.className = `tree-item ${node.is_dir ? 'tree-folder' : 'tree-file'}`;
    item.dataset.path = node.path;
    if (currentNotePath === node.path) {
      item.classList.add('active');
    }

    const header = document.createElement('div');
    header.className = 'tree-item-header';
    header.style.paddingLeft = `${level * 12 + 6}px`;

    const icon = document.createElement('span');
    icon.className = 'tree-item-icon';
    if (node.is_dir) {
      const isExpanded = expandedFolders.has(node.path);
      icon.innerHTML = isExpanded
        ? `<svg class="chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
           <svg class="folder" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`
        : `<svg class="chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(-90deg)"><polyline points="6 9 12 15 18 9"/></svg>
           <svg class="folder" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
    } else {
      icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
    }

    const name = document.createElement('span');
    name.className = 'tree-item-name';
    name.textContent = node.name;

    header.appendChild(icon);
    header.appendChild(name);
    item.appendChild(header);

    if (node.is_dir && node.children && node.children.length > 0) {
      const childrenContainer = document.createElement('div');
      childrenContainer.className = 'tree-item-children';
      if (!expandedFolders.has(node.path)) {
        childrenContainer.style.display = 'none';
      }
      buildTreeHTML(node.children, childrenContainer, level + 1);
      item.appendChild(childrenContainer);
    }

    header.addEventListener('click', (e) => {
      e.stopPropagation();
      if (node.is_dir) {
        if (expandedFolders.has(node.path)) {
          expandedFolders.delete(node.path);
        } else {
          expandedFolders.add(node.path);
        }
        renderFileTree();
      } else {
        openNote(node.path);
      }
    });

    container.appendChild(item);
  });
}

// ─── Note Operations ─────────────────────────────────────────────────────────

async function openNote(path) {
  try {
    const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(path)}`);
    if (res.ok) {
      const data = await res.json();
      currentNotePath = path;

      // Update UI panels visibility
      document.getElementById('obsidian-empty-state').classList.add('hidden');
      document.getElementById('obsidian-editor-container').classList.remove('hidden');

      // Update active selection class in tree
      document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
      const activeEl = document.querySelector(`.tree-item[data-path="${CSS.escape(path)}"]`);
      if (activeEl) activeEl.classList.add('active');

      // Update header title and textarea value
      document.getElementById('obsidian-current-note-title').textContent = path;
      const textarea = document.getElementById('obsidian-textarea');
      textarea.value = data.content || '';
      if (currentViewMode === 'graph') {
        renderGraphView();
      }
    }
  } catch (e) {
    console.error('Failed to open note:', e);
    showToast('Failed to open note');
  }
}

function setViewMode(mode) {
  currentViewMode = mode === 'graph' ? 'graph' : 'document';
  const panes = document.querySelector('.obsidian-editor-panes');
  const graph = document.getElementById('obsidian-graph-view');
  const toggle = document.getElementById('obsidian-view-toggle');
  if (toggle) toggle.checked = currentViewMode === 'graph';
  panes?.classList.toggle('hidden', currentViewMode === 'graph');
  graph?.classList.toggle('hidden', currentViewMode !== 'graph');
  if (currentViewMode === 'graph') {
    renderGraphView();
  }
}

async function renderGraphView() {
  const graph = document.getElementById('obsidian-graph-view');
  if (!graph) return;

  const notePaths = flattenNotes(vaultFiles);
  if (!notePaths.length) {
    graph.innerHTML = '<div class="obsidian-graph-empty">No markdown notes to graph yet.</div>';
    return;
  }

  graph.innerHTML = '<div class="obsidian-graph-empty">Building graph...</div>';
  const existing = new Map(notePaths.map(path => [path.toLowerCase(), path]));
  const nodeSet = new Set(notePaths);
  const edgeSet = new Set();
  const edges = [];

  await Promise.all(notePaths.map(async path => {
    try {
      const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(path)}`);
      if (!res.ok) return;
      const data = await res.json();
      const content = data.content || '';
      const dir = path.includes('/') ? path.substring(0, path.lastIndexOf('/') + 1) : '';
      const links = [...content.matchAll(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g)];
      links.forEach(match => {
        const rawTarget = normalizeNotePath(match[1]);
        const localTarget = normalizeNotePath(dir + rawTarget);
        const target = existing.get(rawTarget.toLowerCase()) || existing.get(localTarget.toLowerCase()) || rawTarget;
        nodeSet.add(target);
        const key = `${path}->${target}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          edges.push({ from: path, to: target });
        }
      });
    } catch (e) {
      console.error('Failed to read graph note:', path, e);
    }
  }));

  const nodes = [...nodeSet].sort((a, b) => a.localeCompare(b));
  const width = 900;
  const height = 560;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.max(90, Math.min(width, height) * 0.34);
  const positions = new Map();

  nodes.forEach((path, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    const linkedCount = edges.filter(edge => edge.from === path || edge.to === path).length;
    const r = path === currentNotePath ? radius * 0.55 : radius + (linkedCount % 3) * 22;
    positions.set(path, {
      x: cx + Math.cos(angle) * r,
      y: cy + Math.sin(angle) * r
    });
  });

  const edgeSvg = edges.map(edge => {
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) return '';
    return `<line class="obsidian-graph-edge" x1="${from.x.toFixed(1)}" y1="${from.y.toFixed(1)}" x2="${to.x.toFixed(1)}" y2="${to.y.toFixed(1)}"></line>`;
  }).join('');

  const nodeSvg = nodes.map(path => {
    const pos = positions.get(path);
    const isCurrent = path === currentNotePath;
    const isMissing = !existing.has(path.toLowerCase());
    const label = escapeHtml(path.replace(/\.md$/i, '').split('/').pop());
    const safePath = escapeHtml(path);
    const classes = [
      'obsidian-graph-node',
      isCurrent ? 'current' : '',
      isMissing ? 'missing' : ''
    ].filter(Boolean).join(' ');
    return `
      <g class="${classes}" data-path="${safePath}" tabindex="0" role="button">
        <circle cx="${pos.x.toFixed(1)}" cy="${pos.y.toFixed(1)}" r="${isCurrent ? 18 : 13}"></circle>
        <text x="${pos.x.toFixed(1)}" y="${(pos.y + 30).toFixed(1)}">${label}</text>
      </g>
    `;
  }).join('');

  graph.innerHTML = `
    <svg class="obsidian-graph-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Obsidian note graph">
      <g>${edgeSvg}</g>
      <g>${nodeSvg}</g>
    </svg>
  `;

  graph.querySelectorAll('.obsidian-graph-node:not(.missing)').forEach(node => {
    node.addEventListener('click', () => openNote(node.dataset.path));
    node.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openNote(node.dataset.path);
      }
    });
  });
}

function findFileInTree(nodes, relativePath) {
  const cleanSearch = relativePath.toLowerCase();
  for (const node of nodes) {
    const nodePathLower = node.path.toLowerCase();
    if (!node.is_dir) {
      if (nodePathLower === cleanSearch || nodePathLower === cleanSearch + '.md') {
        return node.path;
      }
    } else if (node.children) {
      const found = findFileInTree(node.children, relativePath);
      if (found) return found;
    }
  }
  return null;
}

async function handleWikiLinkClick(targetPath) {
  let notePath = targetPath;
  if (!notePath.toLowerCase().endsWith('.md')) {
    notePath += '.md';
  }

  const existingPath = findFileInTree(vaultFiles, notePath);
  if (existingPath) {
    await openNote(existingPath);
    return;
  }

  let dir = '';
  if (currentNotePath && currentNotePath.includes('/')) {
    dir = currentNotePath.substring(0, currentNotePath.lastIndexOf('/') + 1);
  }
  const fullPath = dir + notePath;

  try {
    const res = await fetch('/api/plugins/obsidian/file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: fullPath,
        content: `# ${targetPath}\n\n`
      })
    });
    if (res.ok) {
      showToast(`Created note: ${targetPath}`);
      await loadVaultFiles();
      await openNote(fullPath);
    }
  } catch (e) {
    console.error('Failed to create wiki note:', e);
  }
}

function renderSearchResults(results) {
  const container = document.getElementById('obsidian-file-tree');
  if (!container) return;

  container.innerHTML = '';
  if (results.length === 0) {
    container.innerHTML = '<div class="obsidian-no-results">No matches found</div>';
    return;
  }

  results.forEach(result => {
    const item = document.createElement('div');
    item.className = 'search-result-item';

    const pathHeader = document.createElement('div');
    pathHeader.className = 'search-result-path';
    pathHeader.textContent = result.path;
    item.appendChild(pathHeader);

    const matchesDiv = document.createElement('div');
    matchesDiv.className = 'search-result-matches';
    result.matches.slice(0, 3).forEach(match => {
      const matchLine = document.createElement('div');
      matchLine.className = 'search-result-match';
      matchLine.innerHTML = `<strong>L${match.line}:</strong> ${escapeHtml(match.text)}`;
      matchesDiv.appendChild(matchLine);
    });
    item.appendChild(matchesDiv);

    item.addEventListener('click', () => {
      openNote(result.path);
    });
    container.appendChild(item);
  });
}

// ─── Event Listeners ─────────────────────────────────────────────────────────

function setupEventListeners() {
  // Toggle via sidebar or rail button. Delegation keeps this working if the
  // Odysseus shell rebuilds either launcher after this module initializes.
  if (!window.__obsidianPanelClickBound) {
    window.__obsidianPanelClickBound = true;
    document.addEventListener('click', (e) => {
      const launcher = e.target.closest('#tool-obsidian-btn, #rail-obsidian');
      if (!launcher) return;
      e.preventDefault();
      togglePanel();
    });
  }

  // Close / Minimize
  document.getElementById('obsidian-panel-close')?.addEventListener('click', closePanel);
  document.getElementById('obsidian-panel-minimize')?.addEventListener('click', () => {
    closePanel();
    showToast('Obsidian panel minimized');
  });

  // Backdrop click closes panel
  document.getElementById('obsidian-panel-backdrop')?.addEventListener('click', closePanel);

  // Keyboard: Escape closes panel
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isPanelOpen) {
      closePanel();
    }
  });

  // New Note
  document.getElementById('obsidian-new-note')?.addEventListener('click', async () => {
    const name = await styledPrompt('Enter note title:', { defaultValue: 'Untitled', confirmText: 'Create' });
    if (!name) return;

    let path = name;
    if (!path.toLowerCase().endsWith('.md')) {
      path += '.md';
    }

    let dir = '';
    if (currentNotePath && currentNotePath.includes('/')) {
      dir = currentNotePath.substring(0, currentNotePath.lastIndexOf('/') + 1);
    }
    const fullPath = dir + path;

    try {
      const res = await fetch('/api/plugins/obsidian/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: fullPath,
          content: `# ${name}\n\n`
        })
      });
      if (res.ok) {
        showToast('Note created');
        await loadVaultFiles();
        await openNote(fullPath);
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create note');
      }
    } catch (e) {
      console.error(e);
      showToast('Error creating note');
    }
  });

  // New Folder
  document.getElementById('obsidian-new-folder')?.addEventListener('click', async () => {
    const name = await styledPrompt('Enter folder name:', { defaultValue: 'New Folder', confirmText: 'Create' });
    if (!name) return;

    let dir = '';
    if (currentNotePath && currentNotePath.includes('/')) {
      dir = currentNotePath.substring(0, currentNotePath.lastIndexOf('/') + 1);
    }
    const fullPath = dir + name;

    try {
      const res = await fetch('/api/plugins/obsidian/folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: fullPath })
      });
      if (res.ok) {
        showToast('Folder created');
        await loadVaultFiles();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create folder');
      }
    } catch (e) {
      console.error(e);
      showToast('Error creating folder');
    }
  });

  // Refresh
  document.getElementById('obsidian-refresh')?.addEventListener('click', async () => {
    await loadVaultFiles();
    if (currentViewMode === 'graph') {
      renderGraphView();
    }
    showToast('Vault refreshed');
  });

  document.getElementById('obsidian-view-toggle')?.addEventListener('change', (e) => {
    setViewMode(e.target.checked ? 'graph' : 'document');
  });

  // Rename
  document.getElementById('obsidian-rename-note')?.addEventListener('click', async () => {
    if (!currentNotePath) return;
    const newPath = await styledPrompt('Rename to:', { defaultValue: currentNotePath, confirmText: 'Rename' });
    if (!newPath || newPath === currentNotePath) return;

    try {
      const res = await fetch('/api/plugins/obsidian/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_path: currentNotePath, new_path: newPath })
      });
      if (res.ok) {
        showToast('Renamed note');
        currentNotePath = newPath;
        await loadVaultFiles();
        await openNote(newPath);
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to rename');
      }
    } catch (e) {
      console.error(e);
      showToast('Error renaming note');
    }
  });

  // Delete
  document.getElementById('obsidian-delete-note')?.addEventListener('click', async () => {
    if (!currentNotePath) return;
    const confirm = await styledConfirm('Are you sure you want to delete this note?', { confirmText: 'Delete', danger: true });
    if (!confirm) return;

    try {
      const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(currentNotePath)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        showToast('Note deleted');
        currentNotePath = null;
        document.getElementById('obsidian-editor-container').classList.add('hidden');
        document.getElementById('obsidian-empty-state').classList.remove('hidden');
        await loadVaultFiles();
      } else {
        showToast('Failed to delete note');
      }
    } catch (e) {
      console.error(e);
      showToast('Error deleting note');
    }
  });

  // Autosave + Preview
  const textarea = document.getElementById('obsidian-textarea');
  textarea?.addEventListener('input', () => {
    clearTimeout(autosaveTimeout);
    const content = textarea.value;

    autosaveTimeout = setTimeout(async () => {
      if (!currentNotePath) return;
      try {
        await fetch('/api/plugins/obsidian/file', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: currentNotePath, content })
        });
      } catch (e) {
        console.error('Autosave failed:', e);
      }
    }, 800);
  });

  // Search with debounce
  const searchInput = document.getElementById('obsidian-search-input');
  searchInput?.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const q = searchInput.value.trim();
    if (q.length === 0) {
      renderFileTree();
      return;
    }
    searchTimeout = setTimeout(async () => {
      try {
        const res = await fetch(`/api/plugins/obsidian/search?q=${encodeURIComponent(q)}`);
        if (res.ok) {
          const results = await res.json();
          renderSearchResults(results);
        }
      } catch (e) {
        console.error('Search failed:', e);
      }
    }, 300);
  });
}

// ─── Init ────────────────────────────────────────────────────────────────────

function init() {
  injectUIElements();
  setupEventListeners();
  window.OdysseusObsidian = { openPanel, closePanel, togglePanel };
  console.log('[Obsidian Plugin] Panel-based UI initialized (Option B)');
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

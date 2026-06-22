    const canvas = document.getElementById('graphCanvas');
    const ctx = canvas.getContext('2d');
    const USER_ARCH_KEY = 'codeatlas.userArchitectures';
    const USER_ARCH_CURRENT_KEY = 'codeatlas.currentArchitectureOverlay';
    const USER_NODE_OVERRIDES_KEY = 'codeatlas.nodeOverrides';
    const LAYOUT_KEY = 'codeatlas.stableLayouts';
    const DETAIL_WIDTH_KEY = 'codeatlas.detailPanelWidth';
    const VIEW_PRESETS_KEY = 'codeatlas.viewPresets';
    const AUTO_RELOAD_STALE_KEY = 'codeatlas.autoReloadStaleUi';
    const EXPECTED_UI_VERSION = '{{ UI_VERSION }}';
    const COMMON_NODE_IDS = new Set([
      '.gitignore', '__init__.py', 'abc', 'argparse', 'ast', 'asyncio', 'collections',
      'configparser', 'copy', 'dataclasses', 'datetime', 'dateutil', 'docutils',
      'docs', 'enum', 'functools', 'hashlib', 'http', 'importlib', 'inspect', 'io',
      'itertools', 'json', 'logging', 'math', 'mcp', 'mock', 'networkx', 'numpy',
      'object', 'operator', 'os', 'pandas', 'pathlib', 'pbr', 'pkgutil',
      'pyproject.toml', 'pytest', 're', 'readme.md', 'requests', 'rich', 'rst',
      'setuptools', 'shutil', 'socket', 'sphinx', 'sqlalchemy', 'sqlite3', 'subprocess',
      'sys', 'tempfile', 'textwrap', 'threading', 'time', 'tree_sitter',
      'tree_sitter_language_pack', 'typer', 'typing', 'unittest', 'urllib',
      'uuid', 'watchdog', 'webbrowser', 'yaml'
    ]);
    const COMMON_NODE_PATTERNS = [
      /(^|[._-])docs?($|[._-])/,
      /(^|[._-])api[-_]?guide($|[._-])/,
      /(^|[._-])api[-_]?ref($|[._-])/,
      /(^|[._-])release[-_]?notes?($|[._-])/,
      /(^|[._-])requirements?($|[._-])/,
      /(^|[._-])test[-_]?requirements?($|[._-])/,
      /(^|[._-])setup($|[._-])/,
      /(^|[._-])tox($|[._-])/,
      /(^|[._-])bindep($|[._-])/,
      /(^|[._-])mypy($|[._-])/,
      /(^|[._-])hacking($|[._-])/,
      /(^|[._-])readme($|[._-])/,
      /(^|[._-])agents($|[._-])/,
      /\.(cfg|conf|coveragerc|ini|md|rst|toml|txt|ya?ml)$/
    ];
    const DEFAULT_TEAM_PREFIXES = [
      'oslo_', 'oslo.', 'openstack', 'keystone', 'nova', 'neutron', 'glance',
      'cinder', 'swift', 'tempest', 'ironic', 'manila', 'horizon', 'ceilometer'
    ];
    const COMPONENT_ROW_HEIGHT = 34;
    const COMPONENT_OVERSCAN = 8;
    const LOW_DETAIL_EDGE_ALPHA_FLOOR = 0.18;
    const DETAIL_TABS = ['evidence', 'flow', 'files', 'history'];
    const CATEGORY_FILTERS = [
      { id: 'owned', label: 'Owned', color: '#77a7ff' },
      { id: 'team', label: 'Team deps', color: '#22d3ee' },
      { id: 'third_party', label: 'Third-party', color: '#b69cff' },
      { id: 'docs_config', label: 'Docs/config', color: '#94a3b8' },
      { id: 'tests', label: 'Tests', color: '#f472b6' },
      { id: 'generated', label: 'Generated', color: '#64748b' }
    ];
    const CONNECTION_FILTERS = [
      { id: 'api', label: 'API calls', color: '#22d3ee' },
      { id: 'functions', label: 'Function calls', color: '#38bdf8' },
      { id: 'graphql', label: 'GraphQL', color: '#f472b6' },
      { id: 'database', label: 'Databases', color: '#71d49b' },
      { id: 'component', label: 'Components', color: '#b69cff' },
      { id: 'projects', label: 'Projects/repos', color: '#60a5fa' },
      { id: 'tests', label: 'Tests', color: '#f472b6' },
      { id: 'git', label: 'Git history', color: '#e4b363' },
      { id: 'custom', label: 'Custom overlay', color: '#facc15' }
    ];
    const REPO_QUESTIONS = [
      { label: 'Where start?', lens: 'subway', question: 'Where should I start understanding this repository?' },
      { label: 'Recent changes', lens: 'git', question: 'What changed recently and why does it matter?' },
      { label: 'Risk areas', lens: 'overview', question: 'Which parts of this repository look risky or high churn?' },
      { label: 'API/data flow', lens: 'apis', question: 'Show the important API and data flow paths.' },
      { label: 'Routes', lens: 'apis', question: 'route:' },
      { label: 'Dead code', lens: 'overview', question: 'dead:functions' },
      { label: 'Docs/config', lens: 'overview', action: 'source-outline', question: 'docs config requirements readme setup' },
      { label: 'Source outline', lens: 'overview', action: 'source-outline', question: 'Show a source outline for the current focus.' },
      { label: 'Rule checks', lens: 'overview', action: 'rules', question: 'Run built-in static rule checks.' },
      { label: 'Verify plan', lens: 'tests', action: 'verify-plan', question: 'Build a verification plan for my local changes.' },
      { label: 'Context pack', lens: 'overview', action: 'context-pack', question: 'Generate a redacted context pack for an AI coding task.' },
      { label: 'Owners', lens: 'git', question: 'Who likely owns the main components?' },
      { label: 'Agent pack', lens: 'overview', question: 'Build an agent context pack for the current task.' }
    ];
    const LENS_LABELS = {
      overview: 'Overview',
      subway: 'Subway',
      apis: 'APIs',
      data: 'Data',
      tests: 'Tests',
      external: 'External',
      git: 'Git',
      full: 'Full'
    };
    const CANVAS_ZOOM_MIN = 0.12;
    const CANVAS_ZOOM_MAX = 18;
    const URL_STATE_KEYS = [
      'state', 'view', 'lens', 'cat', 'conn', 'hidden', 'selected', 'side', 'detailTab', 'trace', 'pin', 'focus',
      'hops', 'budget', 'min', 'connected', 'contrast', 'bundles', 'z', 'pan',
      'base', 'head', 'baseVp', 'headVp', 'changes', 'sync'
    ];
    const state = {
      raw: null,
      view: 'architecture',
      compare: null,
      commitOptions: [],
      zoom: 1,
      panX: 0,
      panY: 0,
      compareViewports: {
        base: { zoom: 1, panX: 0, panY: 0 },
        head: { zoom: 1, panX: 0, panY: 0 }
      },
      activePanSide: null,
      isPanning: false,
      panStartX: 0,
      panStartY: 0,
      panBaseX: 0,
      panBaseY: 0,
      detailWidth: loadDetailPanelWidth(),
      isResizingDetail: false,
      detailResizeStartX: 0,
      detailResizeStartWidth: 0,
      suppressClick: false,
      allNodes: [],
      allEdges: [],
      nodeIndex: new Map(),
      nodes: [],
      edges: [],
      visibleNodeIds: new Set(),
      selected: null,
      activePath: null,
      savedPaths: [],
      customNodes: [],
      customEdges: [],
      nodeOverrides: loadNodeOverrides(),
      contextNode: null,
      savedArchitectures: [],
      activeArchitectureId: 'working',
      layoutStore: loadLayoutStore(),
      search: '',
      componentFilter: '',
      componentFilterNodes: [],
      componentFilterScrollPending: false,
      inventoryLimits: {},
      compareWarmKey: '',
      compareInFlight: false,
      compareChangesOnly: true,
      compareSyncViewports: true,
      configApplied: false,
      buildInfo: null,
      classificationUndo: null,
      toastTimer: null,
      classification: {
        owned_prefixes: [],
        team_prefixes: [],
        company_prefixes: [],
        third_party_packages: [],
        hide_packages: [],
        show_packages: []
      },
      diffHighlight: true,
      filtersCollapsed: false,
      visibilityStatus: null,
      activeLens: 'overview',
      minEdgeWeight: 1,
      nodeBudget: 180,
      connectedOnly: true,
      edgeContrast: 64,
      edgeBundling: true,
      focusSelection: false,
      focusHops: 1,
      traceMode: null,
      pinnedTrace: null,
      performanceGuardActive: false,
      pendingUrlState: null,
      urlStateApplied: false,
      isRestoringUrlState: false,
      urlSyncTimer: null,
      commandPaletteOpen: false,
      commandPaletteQuery: '',
      commandPaletteIndex: 0,
      viewPresets: loadViewPresets(),
      detailSearch: '',
      activeDetailTab: 'evidence',
      detailTabSelectionKind: '',
      autoReloadStaleUi: loadAutoReloadPreference(),
      staleReloadTimer: null,
      staleReloadSeconds: 0,
      graphWorker: null,
      graphWorkerRequestId: 0,
      graphWorkerInFlight: false,
      graphWorkerSupported: false,
      graphWorkerLastMs: null,
      lastFilterWorkerUsed: false,
      lastFilterMs: null,
      lastDrawMs: null,
      lastFrameMs: null,
      lastPerfRenderAt: 0,
      frontendErrors: [],
      minimapRects: new Map(),
      legendCollapsed: false,
      lastLegendLayoutAt: 0,
      lastLegendLayoutKey: '',
      hoveredEdge: null,
      hoveredEdgePoint: null,
      lastHoverHitAt: 0,
      lastFocusBreadcrumbSignature: '',
      graphLoadError: '',
      lastEmptyMapSignature: '',
      detailCacheVersion: 0,
      nodeDetailCache: new Map(),
      isMinimapPanning: false,
      activeMinimapNav: null,
      hiddenNodeIds: new Set(),
      categoryVisibility: {
        owned: true,
        team: true,
        third_party: false,
        docs_config: false,
        tests: true,
        generated: false
      },
      connectionVisibility: {
        api: true,
        functions: true,
        graphql: true,
        database: true,
        component: true,
        projects: true,
        tests: true,
        git: false,
        custom: true
      },
      graphCache: {
        categoryByNodeId: new Map(),
        categoryCounts: {},
        visibleNodesById: new Map(),
        pinnedEdgeKeys: new Set()
      },
      indexStatus: null,
      cameraAnimation: null,
      helpTooltipTarget: null
    };

    function setActiveViewButton() {
      document.getElementById('architectureBtn').classList.toggle('active', state.view === 'architecture');
      document.getElementById('commitsBtn').classList.toggle('active', state.view === 'commits');
      document.getElementById('compareViewBtn').classList.toggle('active', state.view === 'compare');
      document.getElementById('compareMapControls').classList.toggle('active', state.view === 'compare');
      document.getElementById('app').classList.toggle('compare-mode', state.view === 'compare');
      updateDiffToggle();
      updateCompareModeControls();
    }

    loadSavedPaths();
    loadUserArchitectures();
    applyStaticHelp();
    applyDetailPanelWidth();
    renderRepoQuestions();
    renderSavedPaths();
    renderSavedArchitectures();
    renderViewPresets();
    renderClassificationWizard();
    renderPerfPanel();
    loadGraph();

    document.getElementById('refreshBtn').onclick = () => refreshGraph();
    document.getElementById('filterPanelToggle').onclick = () => toggleFilterPanel();
    document.getElementById('legendToggleBtn').onclick = () => toggleLegendCollapsed();
    document.getElementById('architectureBtn').onclick = () => setGraph('architecture');
    document.getElementById('addConnectionBtn').onclick = () => openAddConnectionForm();
    document.getElementById('saveArchitectureBtn').onclick = () => saveCurrentArchitecture();
    document.getElementById('commitsBtn').onclick = () => setGraph('commits');
    document.getElementById('compareViewBtn').onclick = () => setGraph('compare');
    document.getElementById('runCompareBtn').onclick = () => runCompare();
    document.getElementById('diffToggleBtn').onclick = () => toggleDiffHighlight();
    document.getElementById('compareChangesOnlyBtn').onclick = () => toggleCompareChangesOnly();
    document.getElementById('compareSyncBtn').onclick = () => toggleCompareViewportSync();
    document.getElementById('compareExplainBtn').onclick = () => explainCompareDiff();
    document.getElementById('baseCommitSelect').onchange = event => {
      if (state.view === 'compare') scheduleCompareWarmup();
      scheduleUrlStateUpdate();
    };
    document.getElementById('headCommitSelect').onchange = event => {
      if (state.view === 'compare') scheduleCompareWarmup();
      scheduleUrlStateUpdate();
    };
    document.getElementById('askBtn').onclick = () => askQuestion();
    document.getElementById('agentContextBtn').onclick = () => createAgentContext();
    document.getElementById('applyViewPresetBtn').onclick = () => applySelectedViewPreset();
    document.getElementById('saveViewPresetBtn').onclick = () => saveCurrentViewPreset();
    document.getElementById('exportViewPresetsBtn').onclick = () => exportViewPresets();
    document.getElementById('importViewPresetsBtn').onclick = () => document.getElementById('viewPresetImportInput').click();
    document.getElementById('viewPresetImportInput').addEventListener('change', importViewPresetsFromFile);
    document.getElementById('uiErrorDismissBtn').onclick = () => {
      document.getElementById('uiErrorPanel').hidden = true;
    };
    document.getElementById('focusBreadcrumbClearBtn').onclick = () => clearFocusBreadcrumb();
    document.getElementById('emptyMapResetBtn').onclick = () => resetMapView();
    document.getElementById('emptyMapShowAllBtn').onclick = () => showEveryNode();
    document.getElementById('emptyMapRefreshBtn').onclick = () => refreshGraph();
    window.addEventListener('error', event => reportUiError(event.message, event.error));
    window.addEventListener('unhandledrejection', event => reportUiError('Unhandled promise rejection', event.reason));
    document.getElementById('detailSearchInput').addEventListener('input', event => {
      state.detailSearch = event.target.value.toLowerCase().trim();
      applyDetailSearchFilter();
    });
    document.querySelectorAll('#detailTabs [data-detail-tab]').forEach(button => {
      button.addEventListener('click', () => setDetailTab(button.dataset.detailTab));
    });
    document.getElementById('commandPaletteBackdrop').onclick = () => closeCommandPalette();
    document.getElementById('commandPaletteInput').addEventListener('input', event => {
      state.commandPaletteQuery = event.target.value;
      state.commandPaletteIndex = 0;
      renderCommandPaletteResults();
    });
    document.getElementById('commandPaletteInput').addEventListener('keydown', handleCommandPaletteKeydown);
    document.getElementById('chatQuestion').addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') askQuestion();
    });
    canvas.addEventListener('wheel', handleGraphWheel, { passive: false });
    canvas.addEventListener('gesturestart', handleGraphGestureStart, { passive: false });
    canvas.addEventListener('gesturechange', handleGraphGestureChange, { passive: false });
    canvas.addEventListener('pointerdown', handleGraphPointerDown);
    canvas.addEventListener('pointerleave', hideEdgeHoverTooltip);
    window.addEventListener('pointermove', handleGraphPointerMove);
    window.addEventListener('pointerup', handleGraphPointerEnd);
    window.addEventListener('pointercancel', handleGraphPointerEnd);
    document.getElementById('detailPanelResizer').addEventListener('pointerdown', handleDetailResizeStart);
    window.addEventListener('pointermove', handleDetailResizeMove);
    window.addEventListener('pointerup', handleDetailResizeEnd);
    window.addEventListener('pointercancel', handleDetailResizeEnd);
    window.addEventListener('resize', applyDetailPanelWidth);
    document.addEventListener('pointerover', handleHelpTooltipOver);
    document.addEventListener('pointermove', handleHelpTooltipMove);
    document.addEventListener('pointerout', handleHelpTooltipOut);
    document.addEventListener('focusin', handleHelpTooltipFocus);
    document.addEventListener('focusout', handleHelpTooltipBlur);
    document.getElementById('searchInput').oninput = event => {
      state.search = event.target.value.toLowerCase();
    };
    document.getElementById('componentFilterInput').oninput = event => {
      state.componentFilter = event.target.value.toLowerCase();
      renderFilterControls();
    };
    document.getElementById('showAllBtn').onclick = () => {
      state.activeLens = 'full';
      state.minEdgeWeight = 1;
      state.nodeBudget = 0;
      state.connectedOnly = false;
      state.focusSelection = false;
      state.focusHops = 1;
      state.traceMode = null;
      setAllCategoryVisibility(true);
      setAllConnectionVisibility(true);
      state.hiddenNodeIds.clear();
      updateScaleControls();
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('hideAllBtn').onclick = () => {
      setAllCategoryVisibility(false);
      setAllConnectionVisibility(false);
      state.hiddenNodeIds.clear();
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('showAllConnectionsBtn').onclick = () => {
      setAllConnectionVisibility(true);
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('onlyApiConnectionsBtn').onclick = () => {
      setAllConnectionVisibility(false);
      for (const id of ['api', 'functions', 'graphql', 'projects']) state.connectionVisibility[id] = true;
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('applyLensBtn').onclick = () => {
      applyMapLens(document.getElementById('mapLensSelect').value);
    };
    document.getElementById('smartSimplifyBtn').onclick = () => applySmartSimplify();
    document.getElementById('resetViewBtn').onclick = () => resetMapView();
    document.getElementById('fitSelectionBtn').onclick = () => focusCameraOnSelection(state.selected);
    document.getElementById('showEveryNodeBtn').onclick = () => showEveryNode();
    document.getElementById('minWeightInput').oninput = event => {
      state.minEdgeWeight = Number(event.target.value || 1);
      updateScaleControls();
      applyFilters();
    };
    document.getElementById('edgeContrastInput').oninput = event => {
      state.edgeContrast = Number(event.target.value || 64);
      updateScaleControls();
      scheduleUrlStateUpdate();
    };
    document.getElementById('connectedOnlyInput').onchange = event => {
      state.connectedOnly = event.target.checked;
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('edgeBundlingInput').onchange = event => {
      state.edgeBundling = event.target.checked;
      updateScaleControls();
      scheduleUrlStateUpdate();
    };
    document.getElementById('nodeBudgetSelect').onchange = event => {
      state.nodeBudget = Number(event.target.value || 0);
      applyFilters();
      renderFilterSummary();
    };
    document.getElementById('focusSelectionInput').onchange = event => {
      state.focusSelection = event.target.checked;
      applyFilters();
      renderFilterSummary();
    };
    document.getElementById('focusHopsSelect').onchange = event => {
      state.focusHops = Number(event.target.value || 1);
      applyFilters();
      renderFilterSummary();
    };
    canvas.addEventListener('contextmenu', handleGraphContextMenu);
    document.getElementById('contextEditNodeBtn').onclick = () => {
      if (!state.contextNode) return;
      openNodeEditor(state.contextNode.node, state.contextNode.side);
      hideNodeContextMenu();
    };
    document.getElementById('contextServiceNodeBtn').onclick = () => {
      if (!state.contextNode) return;
      toggleNodeService(state.contextNode.node, state.contextNode.side);
      hideNodeContextMenu();
    };
    document.getElementById('contextOwnedNodeBtn').onclick = () => classifyContextNode('owned');
    document.getElementById('contextTeamNodeBtn').onclick = () => classifyContextNode('team');
    document.getElementById('contextThirdPartyNodeBtn').onclick = () => classifyContextNode('third_party');
    document.getElementById('contextHideNodeBtn').onclick = () => classifyContextNode('hide');
    document.getElementById('undoToastButton').onclick = () => restoreClassificationFromToast();
    document.getElementById('undoToastDismiss').onclick = () => hideUndoToast();
    document.addEventListener('click', event => {
      if (!event.target.closest('#nodeContextMenu')) hideNodeContextMenu();
    });
    document.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openCommandPalette();
        return;
      }
      if (event.key === 'Escape') {
        if (state.commandPaletteOpen) closeCommandPalette();
        hideNodeContextMenu();
      }
    });
    window.addEventListener('popstate', () => {
      state.pendingUrlState = readUrlStateFromLocation();
      if (!state.pendingUrlState) return;
      applyUrlCompareRefs(state.pendingUrlState);
      if (state.pendingUrlState.view && state.pendingUrlState.view !== state.view) {
        setGraph(state.pendingUrlState.view);
      } else {
        applyPendingUrlState();
      }
    });

    function toggleDiffHighlight() {
      state.diffHighlight = !state.diffHighlight;
      updateDiffToggle();
      renderFilterControls();
      renderTopEdges();
      scheduleUrlStateUpdate();
    }

    function updateDiffToggle() {
      const button = document.getElementById('diffToggleBtn');
      const active = state.view === 'compare' && state.diffHighlight;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      setHelp(button, state.diffHighlight
        ? 'Diff is on. Changed nodes and edges are red and stronger, while unchanged context fades back so architecture differences between commits stand out.'
        : 'Diff is off. Compare mode shows both commit graphs with normal architecture colors. Turn Diff on to highlight added, removed, or changed nodes and edges.'
      );
    }

    function toggleCompareChangesOnly() {
      state.compareChangesOnly = !state.compareChangesOnly;
      updateCompareModeControls();
      if (state.view === 'compare' && state.compare) applyFilters();
      scheduleUrlStateUpdate();
    }

    function toggleCompareViewportSync() {
      state.compareSyncViewports = !state.compareSyncViewports;
      if (state.compareSyncViewports) syncCompareViewport('base');
      updateCompareModeControls();
      scheduleUrlStateUpdate();
    }

    function updateCompareModeControls() {
      const changes = document.getElementById('compareChangesOnlyBtn');
      if (changes) {
        changes.classList.toggle('active', state.view === 'compare' && state.compareChangesOnly);
        changes.setAttribute('aria-pressed', String(state.view === 'compare' && state.compareChangesOnly));
        changes.textContent = state.compareChangesOnly ? 'Changes' : 'Context';
        setHelp(changes, state.compareChangesOnly
          ? 'Change-first mode is on. Compare keeps added, removed, and changed items visible first so unchanged architecture does not drown out the diff.'
          : 'Unchanged context is visible. Use this when you need surrounding architecture around the changed items.'
        );
      }
      const sync = document.getElementById('compareSyncBtn');
      if (sync) {
        sync.classList.toggle('active', state.view === 'compare' && state.compareSyncViewports);
        sync.setAttribute('aria-pressed', String(state.view === 'compare' && state.compareSyncViewports));
        sync.textContent = state.compareSyncViewports ? 'Synced' : 'Free';
        setHelp(sync, state.compareSyncViewports
          ? 'Synced camera is on. Panning or zooming one compare pane mirrors the other so before/after positions stay aligned.'
          : 'Synced camera is off. Pan and zoom each compare pane independently.'
        );
      }
    }

    function toggleFilterPanel() {
      state.filtersCollapsed = !state.filtersCollapsed;
      updateFilterPanelToggle();
    }

    function updateFilterPanelToggle() {
      const app = document.getElementById('app');
      const button = document.getElementById('filterPanelToggle');
      app.classList.toggle('sidebar-collapsed', state.filtersCollapsed);
      button.classList.toggle('active', state.filtersCollapsed);
      button.textContent = state.filtersCollapsed ? '>' : '<';
      const label = state.filtersCollapsed ? 'Show filters' : 'Hide filters';
      button.setAttribute('aria-label', label);
      button.setAttribute('aria-expanded', String(!state.filtersCollapsed));
      setHelp(button, label + '. The filter panel controls which categories and components appear on the architecture map.');
    }

    function toggleLegendCollapsed() {
      state.legendCollapsed = !state.legendCollapsed;
      updateLegendLayout(true);
    }

    function updateLegendLayout(force) {
      const legend = document.getElementById('mapLegend');
      const button = document.getElementById('legendToggleBtn');
      if (!legend || !button) return;
      const now = performance.now();
      if (!force && now - state.lastLegendLayoutAt < 250) return;
      state.lastLegendLayoutAt = now;
      legend.classList.toggle('collapsed', state.legendCollapsed);
      button.setAttribute('aria-expanded', String(!state.legendCollapsed));
      button.textContent = state.legendCollapsed ? 'Legend +' : 'Legend';
      if (state.legendCollapsed) {
        legend.classList.remove('auto-compact');
        state.lastLegendLayoutKey = 'collapsed';
        return;
      }
      legend.classList.remove('auto-compact');
      const key = legendAutoCompactKey(legend);
      state.lastLegendLayoutKey = key;
      const compact = legendNeedsCompaction(legend);
      legend.classList.toggle('auto-compact', compact);
    }

    function legendAutoCompactKey(legend) {
      return [
        canvas.clientWidth,
        canvas.clientHeight,
        Math.round(legend.getBoundingClientRect().height || 0),
        [...state.minimapRects.values()].map(nav => {
          const rect = nav.rect || {};
          return [Math.round(rect.x || 0), Math.round(rect.y || 0), Math.round(rect.w || 0), Math.round(rect.h || 0)].join(',');
        }).join('|')
      ].join(':');
    }

    function legendNeedsCompaction(legend) {
      const legendRect = legend.getBoundingClientRect();
      const canvasRect = canvas.getBoundingClientRect();
      if (legendRect.height > 72 || canvas.clientWidth < 980) return true;
      for (const nav of state.minimapRects.values()) {
        const mini = nav.rect;
        if (!mini) continue;
        const miniRect = {
          left: canvasRect.left + mini.x,
          right: canvasRect.left + mini.x + mini.w,
          top: canvasRect.top + mini.y,
          bottom: canvasRect.top + mini.y + mini.h
        };
        if (rectsOverlap(legendRect, miniRect, 10)) return true;
      }
      return false;
    }

    function rectsOverlap(a, b, padding) {
      const pad = padding || 0;
      return a.left < b.right + pad &&
        a.right > b.left - pad &&
        a.top < b.bottom + pad &&
        a.bottom > b.top - pad;
    }

    function applyStaticHelp() {
      setHelp(document.getElementById('refreshBtn'), 'Refresh re-indexes the repository and redraws the current architecture from the latest files and commits.');
      setHelp(document.getElementById('architectureBtn'), 'Architecture shows the current component graph: owned code, team dependencies, important tests, custom edges, and how they connect.');
      setHelp(document.getElementById('addConnectionBtn'), 'Add a manual architecture edge or vertex. Use this when you know about an API, database, service, or dependency relationship CodeAtlas cannot infer automatically.');
      setHelp(document.getElementById('commitsBtn'), 'Commits switches to the git-history map, where commits, authors, files, and co-change evidence explain how the repo evolved.');
      setHelp(document.getElementById('compareViewBtn'), 'Compare opens two commit snapshots side by side so you can inspect architecture changes between revisions.');
      setHelp(document.getElementById('legendToggleBtn'), 'Collapses or expands the map legend. CodeAtlas auto-compacts the legend when it would overlap the minimap or take too much canvas space.');
      setHelp(document.getElementById('compareMapControls'), 'These are the only compare controls. They sit inside the compare map header so the selected base/head commits are changed in one place.');
      setHelp(document.getElementById('runCompareBtn'), 'Runs the selected base/head commit comparison and redraws both snapshots. Use Diff to highlight what changed.');
      setHelp(document.getElementById('compareExplainBtn'), 'Generates a concise compare summary in the Ask panel: refs, important changed nodes, changed edges, risks, and suggested verification focus.');
      setHelp(document.getElementById('baseCommitSelect'), 'Base commit is the older or starting snapshot in compare mode.');
      setHelp(document.getElementById('headCommitSelect'), 'Head commit is the newer or target snapshot in compare mode.');
      setHelp(document.getElementById('searchInput'), 'Filters visible node labels on the canvas. This dims non-matching nodes without changing the indexed graph.');
      setHelp(document.getElementById('componentFilterInput'), 'Filters the component checklist in the left panel. It does not search file contents; it helps you find nodes to show or hide.');
      setHelp(document.getElementById('showAllBtn'), 'Shows every category and clears individual hidden-node choices.');
      setHelp(document.getElementById('hideAllBtn'), 'Hides all node categories. Turn specific categories or components back on to focus the map.');
      setHelp(document.getElementById('detailPanelResizer'), 'Drag this edge left or right to resize the detail panel. CodeAtlas remembers the width locally for this browser.');
      setHelp(document.getElementById('detailTabs'), 'Switches the selection panel between evidence, flow, file/location, and history sections without changing the map.');
      setHelp(document.getElementById('mapLensSelect'), 'Lens presets reconfigure categories, connection types, node budget, and edge weight for common repo-understanding tasks such as APIs, data flow, tests, external services, and git history.');
      setHelp(document.getElementById('applyLensBtn'), 'Applies the selected lens to the map. This is the fastest way to turn a huge repo into a focused question.');
      setHelp(document.getElementById('smartSimplifyBtn'), 'Simplify hides docs/config, third-party, generated, isolated, and git-history noise, then keeps the strongest connected nodes so large repositories stay readable.');
      setHelp(document.getElementById('resetViewBtn'), 'Resets search, component filtering, lens, node budget, and connection filters back to the standard overview.');
      setHelp(document.getElementById('fitSelectionBtn'), 'Centers and zooms the canvas around the selected node, edge, path, or direct neighborhood.');
      setHelp(document.getElementById('showEveryNodeBtn'), 'Switches to the Full lens: all node categories, all connection types, and no node budget.');
      setHelp(document.getElementById('emptyMapResetBtn'), 'Return to the default Overview lens and clear map searches so visible nodes can come back.');
      setHelp(document.getElementById('emptyMapShowAllBtn'), 'Switch to the Full lens with all categories, all connection types, and no node budget.');
      setHelp(document.getElementById('emptyMapRefreshBtn'), 'Refresh the repository index and redraw the graph from the latest local files and commits.');
      setHelp(document.getElementById('minWeightInput'), 'Raises the minimum visible edge weight. Higher values keep stronger repeated relationships and hide one-off links that make large maps messy.');
      setHelp(document.getElementById('edgeContrastInput'), 'Adjusts edge opacity and stroke strength. Raise it when edges feel too faint on dense maps.');
      setHelp(document.getElementById('nodeBudgetSelect'), 'Limits the map to the most connected and important nodes. Use this when a repository has hundreds or thousands of vertices.');
      setHelp(document.getElementById('connectedOnlyInput'), 'Connected only hides isolated nodes from the architecture map. Turn it off when you specifically want to inspect docs, config, generated files, or standalone folders.');
      setHelp(document.getElementById('edgeBundlingInput'), 'Bundles noisy import/reference/dependency edges when the map is dense or zoomed out. Click a bundle to inspect the exact edges it contains.');
      setHelp(document.getElementById('focusSelectionInput'), 'When enabled, the map only keeps the selected node or edge and nearby neighbors. It helps trace flow without losing the surrounding architecture.');
      setHelp(document.getElementById('focusHopsSelect'), 'Controls how far focus mode expands from the selected item: one hop is direct connections, larger hops show wider impact.');
      setHelp(document.getElementById('saveArchitectureBtn'), 'Saves the current manual architecture overlay locally in your browser.');
      setHelp(document.getElementById('askBtn'), 'Asks CodeAtlas a local question about indexed code and commit data. It uses the repository index and does not require an external LLM API.');
      setHelp(document.getElementById('agentContextBtn'), 'Builds a copyable local context pack for an AI coding agent: likely files, snippets, owners, evidence, and verification hints.');
      setHelp(document.getElementById('contextEditNodeBtn'), 'Edit the selected node label/type locally in this browser. Useful for naming services or correcting generated labels.');
      setHelp(document.getElementById('contextServiceNodeBtn'), 'Treat the selected node as a service boundary. This changes the local visualization category, not repository code.');
      setHelp(document.getElementById('contextOwnedNodeBtn'), 'Persist this package as owned in .codeatlas.yml and redraw the map.');
      setHelp(document.getElementById('contextTeamNodeBtn'), 'Persist this package as a team/company dependency in .codeatlas.yml and keep it visible in focused maps.');
      setHelp(document.getElementById('contextThirdPartyNodeBtn'), 'Persist this package as third-party in .codeatlas.yml.');
      setHelp(document.getElementById('contextHideNodeBtn'), 'Persist this package as docs/config/noise in .codeatlas.yml so it is hidden by default.');
      updateScaleControls();
      updateDiffToggle();
      updateFilterPanelToggle();
      updateFitSelectionButton(state.selected);
    }

    function updateScaleControls() {
      document.getElementById('mapLensSelect').value = state.activeLens;
      const stickyLens = document.getElementById('stickyLensLabel');
      if (stickyLens) {
        stickyLens.textContent = (LENS_LABELS[state.activeLens] || state.activeLens || 'Overview');
      }
      document.getElementById('minWeightInput').value = String(state.minEdgeWeight);
      document.getElementById('minWeightLabel').textContent = String(state.minEdgeWeight);
      document.getElementById('edgeContrastInput').value = String(state.edgeContrast);
      document.getElementById('edgeContrastLabel').textContent = String(state.edgeContrast) + '%';
      document.getElementById('nodeBudgetSelect').value = String(state.nodeBudget);
      document.getElementById('connectedOnlyInput').checked = state.connectedOnly;
      document.getElementById('edgeBundlingInput').checked = state.edgeBundling;
      document.getElementById('focusSelectionInput').checked = state.focusSelection;
      document.getElementById('focusHopsSelect').value = String(state.focusHops);
    }

    function resetMapView() {
      clearMapSearches();
      applyMapLens('overview');
    }

    function showEveryNode() {
      clearMapSearches();
      applyMapLens('full');
    }

    function clearMapSearches() {
      document.getElementById('searchInput').value = '';
      document.getElementById('componentFilterInput').value = '';
      state.search = '';
      state.componentFilter = '';
    }

    function applyMapLens(lens) {
      const presets = {
        overview: {
          label: 'overview',
          nodeBudget: 180,
          minEdgeWeight: 1,
          connectedOnly: true,
          categories: ['owned', 'team', 'tests'],
          connections: ['api', 'functions', 'graphql', 'database', 'component', 'projects', 'tests', 'custom']
        },
        subway: {
          label: 'subway',
          nodeBudget: 12,
          minEdgeWeight: 1,
          connectedOnly: true,
          categories: ['owned', 'team', 'tests'],
          connections: ['api', 'functions', 'graphql', 'database', 'component', 'projects', 'tests', 'custom']
        },
        apis: {
          label: 'apis',
          nodeBudget: 220,
          minEdgeWeight: 1,
          connectedOnly: true,
          categories: ['owned', 'team', 'tests'],
          connections: ['api', 'functions', 'graphql', 'projects', 'custom']
        },
        data: {
          label: 'data',
          nodeBudget: 220,
          minEdgeWeight: 1,
          connectedOnly: true,
          categories: ['owned', 'team', 'tests'],
          connections: ['database', 'functions', 'projects', 'custom']
        },
        tests: {
          label: 'tests',
          nodeBudget: 180,
          minEdgeWeight: 1,
          connectedOnly: true,
          categories: ['owned', 'team', 'tests'],
          connections: ['tests', 'functions', 'component']
        },
        external: {
          label: 'external',
          nodeBudget: 220,
          minEdgeWeight: 1,
          connectedOnly: true,
          categories: ['owned', 'team', 'third_party'],
          connections: ['api', 'projects', 'component', 'custom']
        },
        git: {
          label: 'git',
          nodeBudget: 260,
          minEdgeWeight: 2,
          connectedOnly: true,
          categories: ['owned', 'team', 'tests'],
          connections: ['git']
        },
        full: {
          label: 'full',
          nodeBudget: 0,
          minEdgeWeight: 1,
          connectedOnly: false,
          categories: CATEGORY_FILTERS.map(category => category.id),
          connections: CONNECTION_FILTERS.map(connection => connection.id)
        }
      };
      const preset = presets[lens] || presets.overview;
      state.activeLens = preset.label;
      state.hiddenNodeIds.clear();
      state.focusSelection = false;
      state.focusHops = 1;
      state.traceMode = null;
      state.nodeBudget = preset.nodeBudget;
      state.minEdgeWeight = preset.minEdgeWeight;
      state.connectedOnly = preset.connectedOnly !== false;
      applyCategoryPreset(preset);
      setConnectionVisibilitySet(preset.connections);
      updateScaleControls();
      applyFilters();
      renderFilterControls();
    }

    function applySmartSimplify() {
      state.activeLens = 'overview';
      state.hiddenNodeIds.clear();
      state.focusSelection = false;
      state.focusHops = 1;
      state.traceMode = null;
      state.minEdgeWeight = 1;
      state.nodeBudget = state.allNodes.length > 600 ? 220 : 180;
      state.connectedOnly = true;
      applyCategoryPreset({ categories: ['owned', 'team', 'tests'] });
      setConnectionVisibilitySet(['api', 'functions', 'graphql', 'database', 'component', 'projects', 'tests', 'custom']);
      updateScaleControls();
      applyFilters();
      renderFilterControls();
    }

    function applyCategoryPreset(preset) {
      const visible = new Set(preset.categories || []);
      for (const category of CATEGORY_FILTERS) {
        state.categoryVisibility[category.id] = visible.has(category.id);
      }
    }

    function setHelp(element, text) {
      if (!element || !text) return element;
      element.removeAttribute('data-help');
      element.__codeAtlasHelpText = text;
      if (!element.__codeAtlasHelpPending) {
        element.__codeAtlasHelpPending = true;
        queueMicrotask(() => {
          element.__codeAtlasHelpPending = false;
          attachHelpIcon(element, element.__codeAtlasHelpText);
        });
      }
      return element;
    }

    function attachHelpIcon(element, text) {
      if (!element || !text || !element.isConnected) return;
      if (element.matches('button, input, select, textarea, canvas, option, .detail-resizer')) return;
      let icon = element.querySelector(':scope > .help-icon');
      if (!icon) {
        icon = document.createElement('span');
        icon.className = 'help-icon';
        icon.textContent = 'i';
        icon.tabIndex = 0;
        icon.setAttribute('role', 'img');
        icon.setAttribute('aria-label', 'Help');
        icon.addEventListener('click', event => {
          event.preventDefault();
          event.stopPropagation();
        });
        icon.addEventListener('pointerdown', event => {
          event.stopPropagation();
        });
        element.appendChild(icon);
      }
      icon.dataset.help = text;
      icon.setAttribute('aria-describedby', 'helpTooltip');
    }

    function handleHelpTooltipOver(event) {
      const target = event.target.closest('.help-icon[data-help]');
      if (!target) return;
      state.helpTooltipTarget = target;
      showHelpTooltip(target.dataset.help, event);
    }

    function handleHelpTooltipMove(event) {
      if (!state.helpTooltipTarget) return;
      positionHelpTooltip(event);
    }

    function handleHelpTooltipOut(event) {
      const target = event.target.closest('.help-icon[data-help]');
      if (!target || target !== state.helpTooltipTarget) return;
      if (event.relatedTarget && target.contains(event.relatedTarget)) return;
      hideHelpTooltip();
    }

    function handleHelpTooltipFocus(event) {
      const target = event.target.closest('.help-icon[data-help]');
      if (!target) return;
      const rect = target.getBoundingClientRect();
      state.helpTooltipTarget = target;
      showHelpTooltip(target.dataset.help, {
        clientX: rect.right,
        clientY: rect.top
      });
    }

    function handleHelpTooltipBlur(event) {
      const target = event.target.closest('.help-icon[data-help]');
      if (!target || target !== state.helpTooltipTarget) return;
      hideHelpTooltip();
    }

    function showHelpTooltip(text, event) {
      const tooltip = document.getElementById('helpTooltip');
      tooltip.textContent = text || '';
      tooltip.hidden = !text;
      if (text) positionHelpTooltip(event);
    }

    function positionHelpTooltip(event) {
      const tooltip = document.getElementById('helpTooltip');
      if (tooltip.hidden) return;
      const margin = 14;
      let left = event.clientX + margin;
      let top = event.clientY + margin;
      tooltip.style.left = left + 'px';
      tooltip.style.top = top + 'px';
      const rect = tooltip.getBoundingClientRect();
      if (rect.right > window.innerWidth - margin) left = Math.max(margin, event.clientX - rect.width - margin);
      if (rect.bottom > window.innerHeight - margin) top = Math.max(margin, event.clientY - rect.height - margin);
      tooltip.style.left = left + 'px';
      tooltip.style.top = top + 'px';
    }

    function hideHelpTooltip() {
      const tooltip = document.getElementById('helpTooltip');
      tooltip.hidden = true;
      state.helpTooltipTarget = null;
    }

    function loadSavedPaths() {
      try {
        const raw = localStorage.getItem('codeatlas.savedPaths');
        state.savedPaths = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(state.savedPaths)) state.savedPaths = [];
      } catch (err) {
        state.savedPaths = [];
      }
    }

    function persistSavedPaths() {
      try {
        localStorage.setItem('codeatlas.savedPaths', JSON.stringify(state.savedPaths));
      } catch (err) {
        // Local storage can be unavailable in private browsing or embedded contexts.
      }
    }

    function loadViewPresets() {
      try {
        const raw = localStorage.getItem(VIEW_PRESETS_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(preset => preset && preset.name && preset.payload) : [];
      } catch (err) {
        return [];
      }
    }

    function persistViewPresets() {
      try {
        localStorage.setItem(VIEW_PRESETS_KEY, JSON.stringify(state.viewPresets));
      } catch (err) {
        // Local storage can be unavailable in private browsing or embedded contexts.
      }
    }

    function loadAutoReloadPreference() {
      try {
        return localStorage.getItem(AUTO_RELOAD_STALE_KEY) === '1';
      } catch (err) {
        return false;
      }
    }

    function saveAutoReloadPreference(value) {
      state.autoReloadStaleUi = Boolean(value);
      try {
        localStorage.setItem(AUTO_RELOAD_STALE_KEY, state.autoReloadStaleUi ? '1' : '0');
      } catch (err) {
        // Local storage can be unavailable in private browsing or embedded contexts.
      }
    }

    function renderViewPresets() {
      const select = document.getElementById('viewPresetSelect');
      if (!select) return;
      const previous = select.value;
      select.innerHTML = '';
      const empty = document.createElement('option');
      empty.value = '';
      empty.textContent = state.viewPresets.length ? 'Saved presets' : 'No presets';
      select.appendChild(empty);
      for (const preset of state.viewPresets) {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name;
        select.appendChild(option);
      }
      select.value = state.viewPresets.some(preset => preset.id === previous) ? previous : '';
    }

    function currentViewPresetPayload() {
      return {
        lens: state.activeLens || 'overview',
        categories: visibleCategoryIds(),
        connections: visibleConnectionIds(),
        hidden: [...state.hiddenNodeIds],
        minEdgeWeight: state.minEdgeWeight,
        edgeContrast: state.edgeContrast,
        nodeBudget: state.nodeBudget,
        connectedOnly: state.connectedOnly,
        edgeBundling: state.edgeBundling,
        focusSelection: state.focusSelection,
        focusHops: state.focusHops,
        traceMode: state.traceMode
      };
    }

    function applyViewPresetPayload(payload) {
      if (!payload) return;
      if (payload.lens) state.activeLens = payload.lens;
      if (Array.isArray(payload.categories)) setCategoryVisibilitySet(payload.categories);
      if (Array.isArray(payload.connections)) setConnectionVisibilitySet(payload.connections);
      state.hiddenNodeIds = new Set(Array.isArray(payload.hidden) ? payload.hidden : []);
      state.minEdgeWeight = Math.max(1, Math.round(Number(payload.minEdgeWeight || 1)));
      state.edgeContrast = clamp(Math.round(Number(payload.edgeContrast || 64)), 25, 100);
      state.nodeBudget = Math.max(0, Math.round(Number(payload.nodeBudget || 0)));
      state.connectedOnly = Boolean(payload.connectedOnly);
      state.edgeBundling = payload.edgeBundling !== false;
      state.focusSelection = Boolean(payload.focusSelection);
      state.focusHops = clamp(Math.round(Number(payload.focusHops || 1)), 1, 3);
      state.traceMode = payload.traceMode || null;
      updateScaleControls();
      applyFilters();
      renderFilterControls();
      setRepoStatus('View preset applied.');
    }

    function applySelectedViewPreset() {
      const select = document.getElementById('viewPresetSelect');
      const preset = state.viewPresets.find(item => item.id === select.value);
      if (!preset) {
        setRepoStatus('Choose a saved view preset first.');
        return;
      }
      applyViewPresetPayload(preset.payload);
    }

    function saveCurrentViewPreset() {
      const input = document.getElementById('viewPresetNameInput');
      const name = String(input.value || '').trim();
      if (!name) {
        setRepoStatus('Name the view preset before saving.');
        input.focus();
        return;
      }
      const existing = state.viewPresets.find(preset => preset.name.toLowerCase() === name.toLowerCase());
      const payload = currentViewPresetPayload();
      if (existing) {
        existing.payload = payload;
        existing.updatedAt = new Date().toISOString();
      } else {
        state.viewPresets.unshift({
          id: 'preset-' + Date.now().toString(36),
          name,
          payload,
          updatedAt: new Date().toISOString()
        });
      }
      state.viewPresets = state.viewPresets.slice(0, 24);
      persistViewPresets();
      renderViewPresets();
      setRepoStatus('View preset saved: ' + name + '.');
    }

    function exportViewPresets() {
      const payload = {
        schema: 'codeatlas.viewPresets',
        version: 1,
        exportedAt: new Date().toISOString(),
        presets: state.viewPresets
      };
      downloadWorkflowFile('view-presets.json', JSON.stringify(payload, null, 2), 'application/json');
      setRepoStatus('View presets exported.');
    }

    function importViewPresetsFromFile(event) {
      const input = event.target;
      const file = input.files && input.files[0];
      input.value = '';
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const payload = JSON.parse(String(reader.result || ''));
          const presets = Array.isArray(payload.presets) ? payload.presets : Array.isArray(payload) ? payload : [];
          const normalized = presets
            .filter(preset => preset && preset.name && preset.payload)
            .map(preset => ({
              id: preset.id || 'preset-' + Math.random().toString(36).slice(2),
              name: String(preset.name).trim(),
              payload: preset.payload,
              updatedAt: preset.updatedAt || new Date().toISOString()
            }));
          if (!normalized.length) throw new Error('No presets found in file');
          const byName = new Map(state.viewPresets.map(preset => [preset.name.toLowerCase(), preset]));
          for (const preset of normalized) byName.set(preset.name.toLowerCase(), preset);
          state.viewPresets = [...byName.values()].slice(0, 24);
          persistViewPresets();
          renderViewPresets();
          setRepoStatus('Imported ' + normalized.length + ' view preset' + (normalized.length === 1 ? '' : 's') + '.');
        } catch (err) {
          setRepoStatus('Preset import failed: ' + err.message);
        }
      };
      reader.onerror = () => setRepoStatus('Preset import failed: could not read file.');
      reader.readAsText(file);
    }

    function savePath(path) {
      const existing = state.savedPaths.find(item => item.id === path.id);
      if (!existing) state.savedPaths.unshift(path);
      else Object.assign(existing, path);
      persistSavedPaths();
      renderSavedPaths();
    }

    function removeSavedPath(pathId) {
      state.savedPaths = state.savedPaths.filter(path => path.id !== pathId);
      if (state.activePath && state.activePath.id === pathId) state.activePath = null;
      if (state.selected && state.selected.kind === 'path' && state.selected.path.id === pathId) state.selected = null;
      persistSavedPaths();
      renderSavedPaths();
      renderSelection(state.selected);
    }

    function isPathSaved(path) {
      return state.savedPaths.some(item => item.id === path.id);
    }

    function renderSavedPaths() {
      const root = document.getElementById('savedPaths');
      if (!root) return;
      root.innerHTML = '';
      if (!state.savedPaths.length) {
        const empty = document.createElement('div');
        empty.className = 'detail-line detail-empty';
        empty.textContent = 'No saved paths yet.';
        root.appendChild(empty);
        return;
      }
      for (const path of state.savedPaths.slice(0, 12)) {
        const row = document.createElement('div');
        row.className = 'path-row' + (state.activePath && state.activePath.id === path.id ? ' active' : '');
        setHelp(row, 'This saved path represents a source-to-target flow you chose to keep. Click it to highlight that path on the architecture map and reopen its details.');
        row.onclick = () => selectPath(path);
        const title = document.createElement('div');
        title.className = 'path-title';
        title.textContent = path.label;
        const meta = document.createElement('div');
        meta.className = 'path-meta';
        meta.textContent = path.sourceComponent + ' -> ' + path.targetComponent + ' | ' + path.type;
        const actions = document.createElement('div');
        actions.className = 'path-actions';
        const remove = document.createElement('button');
        remove.textContent = 'Remove';
        setHelp(remove, 'Removes this saved path from local browser storage. It does not change the repository or indexed graph.');
        remove.onclick = event => {
          event.stopPropagation();
          removeSavedPath(path.id);
        };
        actions.appendChild(remove);
        row.append(title, meta, actions);
        root.appendChild(row);
      }
    }

    function selectPath(path) {
      if (state.view !== 'architecture') setGraph('architecture');
      state.activePath = path;
      state.selected = { kind: 'path', path };
      focusCameraOnSelection(state.selected);
      renderSavedPaths();
      renderSelection(state.selected);
    }

    function loadUserArchitectures() {
      try {
        const saved = localStorage.getItem(USER_ARCH_KEY);
        const current = localStorage.getItem(USER_ARCH_CURRENT_KEY);
        state.savedArchitectures = saved ? JSON.parse(saved) : [];
        const overlay = current ? JSON.parse(current) : {};
        if (!Array.isArray(state.savedArchitectures)) state.savedArchitectures = [];
        state.customNodes = Array.isArray(overlay.nodes) ? overlay.nodes : [];
        state.customEdges = Array.isArray(overlay.edges) ? overlay.edges : [];
      } catch (err) {
        state.savedArchitectures = [];
        state.customNodes = [];
        state.customEdges = [];
      }
    }

    function persistUserArchitectures() {
      try {
        localStorage.setItem(USER_ARCH_KEY, JSON.stringify(state.savedArchitectures));
        localStorage.setItem(
          USER_ARCH_CURRENT_KEY,
          JSON.stringify({ nodes: state.customNodes, edges: state.customEdges })
        );
      } catch (err) {
        // Local storage can be unavailable in private browsing or embedded contexts.
      }
    }

    function loadNodeOverrides() {
      try {
        const raw = localStorage.getItem(USER_NODE_OVERRIDES_KEY);
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch (err) {
        return {};
      }
    }

    function nodeOverrideScope() {
      const repo = state.raw && state.raw.repo ? state.raw.repo.path || state.raw.repo.name : 'repo';
      return String(repo || 'repo');
    }

    function scopedNodeOverrides() {
      const scope = nodeOverrideScope();
      if (!state.nodeOverrides[scope] || typeof state.nodeOverrides[scope] !== 'object') {
        state.nodeOverrides[scope] = {};
      }
      return state.nodeOverrides[scope];
    }

    function nodeOverrideFor(id) {
      if (!id) return null;
      return scopedNodeOverrides()[id] || null;
    }

    function persistNodeOverrides() {
      try {
        localStorage.setItem(USER_NODE_OVERRIDES_KEY, JSON.stringify(state.nodeOverrides));
      } catch (err) {
        // Overrides remain active for this page even if browser persistence fails.
      }
    }

    function applyNodeOverride(node) {
      const override = nodeOverrideFor(node.id);
      const original = {
        originalLabel: node.originalLabel || node.label,
        originalType: node.originalType || node.type,
        originalTags: node.originalTags || [...(node.tags || [])],
        originalDetails: node.originalDetails || node.details || ''
      };
      if (!override) {
        if (!node.originalLabel) return node;
        return {
          ...node,
          label: original.originalLabel,
          type: original.originalType,
          tags: [...original.originalTags],
          details: original.originalDetails,
          ...original
        };
      }
      const mergedTags = Array.from(new Set([...(original.originalTags || []), ...(override.tags || [])].filter(Boolean)));
      return {
        ...node,
        ...override,
        id: node.id,
        ...original,
        tags: mergedTags
      };
    }

    function saveNodeOverride(nodeId, override) {
      const scoped = scopedNodeOverrides();
      scoped[nodeId] = {
        ...(scoped[nodeId] || {}),
        ...override,
        updatedAt: new Date().toISOString()
      };
      persistNodeOverrides();
    }

    function classificationPackageName(node) {
      const text = nodeCategoryText(node);
      return text.label || text.id.replace(/^component:/, '') || String(node.id || '');
    }

    function classifyContextNode(category) {
      if (!state.contextNode) return;
      const node = state.contextNode.node;
      const packageName = classificationPackageName(node);
      hideNodeContextMenu();
      saveClassificationPackage(packageName, category, {
        node,
        source: 'context'
      });
    }

    function saveClassificationPackage(packageName, category, options) {
      const opts = options || {};
      const cleanPackage = String(packageName || '').trim();
      if (!cleanPackage) {
        setRepoStatus('Classification needs a package or prefix.');
        return;
      }
      setRepoStatus('Saving classification for ' + packageName + '...');
      fetch('/api/classification', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: cleanPackage, category })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Classification update failed');
          return payload;
        })
        .then(payload => {
          applyConfigUpdate(payload.config || {}, payload.build || {});
          const undoClassification = payload.previous_classification || cloneClassificationConfig(state.classification);
          const nextCategory = category === 'hide' ? 'docs_config' : category;
          if (category === 'hide') state.categoryVisibility.docs_config = false;
          else state.categoryVisibility[nextCategory] = true;
          if (category !== 'hide' && opts.node) state.hiddenNodeIds.delete(opts.node.id);
          rebuildGraphCache();
          applyFilters();
          renderFilterControls();
          renderClassificationWizard();
          renderBuildBadge({ build: state.buildInfo || {}, stats: state.raw.stats || {}, config: payload.config || {} });
          setRepoStatus('Saved classification for ' + cleanPackage + '.');
          showUndoToast('Marked ' + cleanPackage + ' as ' + nodeCategoryTextLabel(nextCategory) + '.', {
            packageName: cleanPackage,
            classification: undoClassification
          });
        })
        .catch(err => {
          setRepoStatus('Classification failed: ' + err.message);
        });
    }

    function renderClassificationWizard() {
      const root = document.getElementById('classificationWizard');
      if (!root) return;
      root.innerHTML = '';
      const selectedNode = state.selected && state.selected.kind === 'node' ? state.selected.node : null;
      const suggested = selectedNode ? classificationPackageName(selectedNode) : suggestedOwnedPrefix();
      root.append(
        wizardSelect('classificationCategorySelect', 'Bucket', [
          ['owned', 'Owned prefix'],
          ['team', 'Team/company dependency'],
          ['third_party', 'Third-party package'],
          ['hide', 'Hide/docs/config']
        ]),
        wizardInput('classificationPackageInput', 'Package or prefix', suggested)
      );
      const actions = document.createElement('div');
      actions.className = 'wizard-actions';
      const save = document.createElement('button');
      save.type = 'button';
      save.textContent = 'Save bucket';
      save.onclick = saveClassificationFromWizard;
      const infer = document.createElement('button');
      infer.type = 'button';
      infer.textContent = 'Use selected';
      infer.onclick = () => {
        const input = document.getElementById('classificationPackageInput');
        if (selectedNode && input) input.value = classificationPackageName(selectedNode);
      };
      actions.append(save, infer);
      const status = document.createElement('div');
      status.id = 'classificationWizardStatus';
      status.className = 'wizard-status';
      status.textContent = classificationSummaryText();
      root.append(actions, status);
    }

    function wizardInput(id, label, value) {
      const wrapper = document.createElement('label');
      wrapper.textContent = label;
      const input = document.createElement('input');
      input.id = id;
      input.value = value || '';
      input.placeholder = 'example: my_app or oslo_';
      wrapper.appendChild(input);
      return wrapper;
    }

    function wizardSelect(id, label, options) {
      const wrapper = document.createElement('label');
      wrapper.textContent = label;
      const select = document.createElement('select');
      select.id = id;
      for (const [value, text] of options) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        select.appendChild(option);
      }
      wrapper.appendChild(select);
      return wrapper;
    }

    function saveClassificationFromWizard() {
      const input = document.getElementById('classificationPackageInput');
      const select = document.getElementById('classificationCategorySelect');
      const status = document.getElementById('classificationWizardStatus');
      const packageName = input ? input.value.trim() : '';
      const category = select ? select.value : 'owned';
      if (!packageName) {
        if (status) status.textContent = 'Enter a package or prefix first.';
        if (input) input.focus();
        return;
      }
      if (status) status.textContent = 'Saving...';
      saveClassificationPackage(packageName, category, { source: 'wizard' });
    }

    function suggestedOwnedPrefix() {
      if (state.raw && state.raw.repo && state.raw.repo.name) return state.raw.repo.name.replace(/[-\s]+/g, '_');
      const owned = state.classification && state.classification.owned_prefixes || [];
      return owned[0] || '';
    }

    function classificationSummaryText() {
      const c = state.classification || {};
      const owned = (c.owned_prefixes || []).length;
      const team = (c.team_prefixes || []).length + (c.company_prefixes || []).length + (c.show_packages || []).length;
      const third = (c.third_party_packages || []).length;
      return owned + ' owned / ' + team + ' team / ' + third + ' third-party rules';
    }

    function applyConfigUpdate(config, build) {
      if (config.classification) state.classification = normalizeClassificationConfig(config.classification);
      if (config.ui && Number.isFinite(Number(config.ui.edge_contrast))) {
        state.edgeContrast = clamp(Number(config.ui.edge_contrast), 25, 100);
      }
      state.buildInfo = { ...(state.buildInfo || {}), ...(build || {}) };
      if (state.raw) {
        state.raw.config = { ...(state.raw.config || {}), ...(config || {}) };
        state.raw.build = { ...(state.raw.build || {}), ...(build || {}) };
      }
      updateScaleControls();
      renderClassificationWizard();
    }

    function cloneClassificationConfig(classification) {
      const source = classification || {};
      return {
        owned_prefixes: stringList(source.owned_prefixes),
        team_prefixes: stringList(source.team_prefixes),
        company_prefixes: stringList(source.company_prefixes),
        third_party_packages: stringList(source.third_party_packages),
        hide_packages: stringList(source.hide_packages),
        show_packages: stringList(source.show_packages)
      };
    }

    function showUndoToast(message, undoPayload) {
      state.classificationUndo = undoPayload || null;
      const toast = document.getElementById('undoToast');
      const label = document.getElementById('undoToastMessage');
      label.textContent = message;
      toast.hidden = false;
      if (state.toastTimer) window.clearTimeout(state.toastTimer);
      state.toastTimer = window.setTimeout(() => hideUndoToast(), 9000);
    }

    function hideUndoToast() {
      const toast = document.getElementById('undoToast');
      if (toast) toast.hidden = true;
      state.classificationUndo = null;
      if (state.toastTimer) window.clearTimeout(state.toastTimer);
      state.toastTimer = null;
    }

    function restoreClassificationFromToast() {
      const undo = state.classificationUndo;
      if (!undo || !undo.classification) {
        hideUndoToast();
        return;
      }
      const button = document.getElementById('undoToastButton');
      button.disabled = true;
      fetch('/api/classification/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ classification: undo.classification })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Undo failed');
          return payload;
        })
        .then(payload => {
          applyConfigUpdate(payload.config || {}, payload.build || {});
          rebuildGraphCache();
          applyFilters();
          renderFilterControls();
          renderBuildBadge({ build: state.buildInfo || {}, stats: state.raw.stats || {}, config: payload.config || {} });
          setRepoStatus('Undid classification for ' + (undo.packageName || 'node') + '.');
          hideUndoToast();
        })
        .catch(err => {
          setRepoStatus('Undo failed: ' + err.message);
        })
        .finally(() => {
          button.disabled = false;
        });
    }

    function renderSavedArchitectures() {
      const root = document.getElementById('savedArchitectures');
      if (!root) return;
      root.innerHTML = '';
      if (!state.savedArchitectures.length) {
        const empty = document.createElement('div');
        empty.className = 'detail-line detail-empty';
        empty.textContent = 'No saved architecture overlays yet.';
        root.appendChild(empty);
        return;
      }
      for (const architecture of state.savedArchitectures.slice(0, 10)) {
        const row = document.createElement('div');
        row.className = 'path-row' + (state.activeArchitectureId === architecture.id ? ' active' : '');
        setHelp(row, 'This saved architecture overlay contains manual vertices or edges you added. Click it to apply those additions on top of the indexed graph.');
        row.onclick = () => applySavedArchitecture(architecture);
        const title = document.createElement('div');
        title.className = 'path-title';
        title.textContent = architecture.name;
        const meta = document.createElement('div');
        meta.className = 'path-meta';
        meta.textContent = (architecture.edges || []).length + ' edges | ' + (architecture.nodes || []).length + ' custom vertices';
        const actions = document.createElement('div');
        actions.className = 'path-actions';
        const remove = document.createElement('button');
        remove.textContent = 'Remove';
        setHelp(remove, 'Removes this saved architecture overlay from local browser storage. It does not delete repository code or indexed evidence.');
        remove.onclick = event => {
          event.stopPropagation();
          removeSavedArchitecture(architecture.id);
        };
        actions.appendChild(remove);
        row.append(title, meta, actions);
        root.appendChild(row);
      }
    }

    function saveCurrentArchitecture() {
      const input = document.getElementById('architectureNameInput');
      const name = input.value.trim() || 'Architecture ' + new Date().toLocaleString();
      const architecture = {
        id: 'arch:' + Date.now(),
        name,
        nodes: cloneForStorage(state.customNodes),
        edges: cloneForStorage(state.customEdges),
        createdAt: new Date().toISOString()
      };
      state.savedArchitectures.unshift(architecture);
      state.activeArchitectureId = architecture.id;
      input.value = '';
      persistUserArchitectures();
      renderSavedArchitectures();
      state.selected = { kind: 'architecture', architecture };
      renderSelection(state.selected);
    }

    function applySavedArchitecture(architecture) {
      state.customNodes = cloneForStorage(architecture.nodes || []);
      state.customEdges = cloneForStorage(architecture.edges || []);
      state.activeArchitectureId = architecture.id;
      persistUserArchitectures();
      if (state.view !== 'architecture') setGraph('architecture');
      else setGraph('architecture');
      state.selected = { kind: 'architecture', architecture };
      renderSavedArchitectures();
      renderSelection(state.selected);
    }

    function openAddConnectionForm() {
      if (state.view !== 'architecture') setGraph('architecture');
      state.selected = { kind: 'addConnection' };
      renderSelection(state.selected);
    }

    function removeSavedArchitecture(id) {
      state.savedArchitectures = state.savedArchitectures.filter(item => item.id !== id);
      if (state.activeArchitectureId === id) state.activeArchitectureId = 'working';
      persistUserArchitectures();
      renderSavedArchitectures();
    }

    function cloneForStorage(value) {
      return JSON.parse(JSON.stringify(value || []));
    }
    canvas.addEventListener('click', event => {
      if (state.suppressClick) {
        state.suppressClick = false;
        return;
      }
      if (!state.nodes.length) {
        state.selected = null;
        renderSelection(null);
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (state.view === 'compare' && state.compare) {
        handleCompareClick(x, y);
        return;
      }
      const transform = graphTransform();
      const bestNode = hitNodeAt(x, y, state.nodes, transform);
      if (bestNode) {
        state.selected = { kind: 'node', node: bestNode };
        focusCameraOnSelection(state.selected);
        renderSelection(state.selected);
        return;
      }
      const nodesById = state.graphCache.visibleNodesById || new Map(state.nodes.map(node => [node.id, node]));
      const hitEdge = hitEdgeRenderItemAt(x, y, state.nodes, state.edges, transform, { x: 0, y: 0, w: canvas.clientWidth, h: canvas.clientHeight }, null, nodesById);
      state.selected = hitEdge ? hitEdge.selection : null;
      focusCameraOnSelection(state.selected);
      renderSelection(state.selected);
    });

    function hitNodeAt(x, y, nodes, transform, rect) {
      let bestNode = null;
      let bestNodeDist = Infinity;
      for (const node of nodes) {
        const p = rect ? projectInRect(node, transform, rect) : project(node, transform);
        const dist = Math.hypot(p.x - x, p.y - y);
        if (dist < p.r + 8 && dist < bestNodeDist) {
          bestNode = node;
          bestNodeDist = dist;
        }
      }
      return bestNode;
    }

    function handleCompareClick(x, y) {
      const rects = compareRects(canvas.clientWidth, canvas.clientHeight);
      const baseHit = hitGraphPanel(x, y, state.compare.base, rects.base, 'base');
      const headHit = hitGraphPanel(x, y, state.compare.head, rects.head, 'head');
      const hits = [baseHit, headHit].filter(Boolean).sort((a, b) => a.distance - b.distance);
      state.selected = hits.length ? hits[0].selection : null;
      focusCameraOnSelection(state.selected);
      renderSelection(state.selected);
    }

    function hitGraphPanel(x, y, side, rect, sideName) {
      if (x < rect.x || x > rect.x + rect.w || y < rect.y || y > rect.y + rect.h) return null;
      if (!side.nodes.length) return null;
      const transform = graphTransformFor(side.nodes, rect, sideName);
      const nodesById = side.nodesById || new Map(side.nodes.map(node => [node.id, node]));
      const bestNode = hitNodeAt(x, y, side.nodes, transform, rect);
      if (bestNode) {
        const p = projectInRect(bestNode, transform, rect);
        return { distance: Math.hypot(p.x - x, p.y - y), selection: { kind: 'node', node: bestNode, side: sideName } };
      }
      return hitEdgeRenderItemAt(x, y, side.nodes, side.edges, transform, rect, sideName, nodesById);
    }

    function hitEdgeRenderItemAt(x, y, nodes, edges, transform, rect, side, nodesById) {
      const items = edgeRenderItemsForPanel(nodes, edges, transform, rect, side, nodesById);
      let best = null;
      let bestDist = Infinity;
      for (const item of items) {
        const pa = projectPointInRect(item.sourcePoint, transform, rect);
        const pb = projectPointInRect(item.targetPoint, transform, rect);
        const dist = distanceToSegment(x, y, pa.x, pa.y, pb.x, pb.y);
        const threshold = item.kind === 'bundle' ? 16 : 11;
        if (dist < threshold && dist < bestDist) {
          best = item;
          bestDist = dist;
        }
      }
      if (!best) return null;
      const selection = best.kind === 'bundle'
        ? { kind: 'bundle', bundle: best, side }
        : { kind: 'edge', edge: best.edge, side };
      return { distance: bestDist, selection };
    }

    function handleGraphContextMenu(event) {
      event.preventDefault();
      event.stopPropagation();
      const point = canvasPoint(event);
      const hit = contextNodeAt(point.x, point.y);
      if (!hit) {
        hideNodeContextMenu();
        return;
      }
      state.contextNode = hit;
      state.selected = { kind: 'node', node: hit.node, side: hit.side };
      renderSelection(state.selected);
      showNodeContextMenu(hit, event.clientX, event.clientY);
    }

    function contextNodeAt(x, y) {
      if (state.view === 'compare' && state.compare) {
        const sideName = compareSideForPoint({ x, y });
        if (!sideName) return null;
        const rect = compareRects(canvas.clientWidth, canvas.clientHeight)[sideName];
        const side = state.compare[sideName];
        const transform = graphTransformFor(side.nodes, rect, sideName);
        const node = hitNodeAt(x, y, side.nodes, transform, rect);
        return node ? { node, side: sideName } : null;
      }
      const node = hitNodeAt(x, y, state.nodes, graphTransform());
      return node ? { node, side: null } : null;
    }

    function showNodeContextMenu(hit, clientX, clientY) {
      const menu = document.getElementById('nodeContextMenu');
      const label = document.getElementById('nodeContextLabel');
      const serviceButton = document.getElementById('contextServiceNodeBtn');
      const category = nodeCategory(hit.node);
      label.textContent = (hit.node.label || hit.node.id) + ' - ' + nodeCategoryTextLabel(category);
      serviceButton.textContent = isServiceNode(hit.node) ? 'Unmark service' : 'Mark as service';
      menu.hidden = false;
      const margin = 8;
      let left = clientX;
      let top = clientY;
      menu.style.left = left + 'px';
      menu.style.top = top + 'px';
      const rect = menu.getBoundingClientRect();
      if (rect.right > window.innerWidth - margin) left = Math.max(margin, window.innerWidth - rect.width - margin);
      if (rect.bottom > window.innerHeight - margin) top = Math.max(margin, window.innerHeight - rect.height - margin);
      menu.style.left = left + 'px';
      menu.style.top = top + 'px';
    }

    function hideNodeContextMenu() {
      const menu = document.getElementById('nodeContextMenu');
      if (menu) menu.hidden = true;
      state.contextNode = null;
    }

    function loadGraph() {
      fetch('/api/graph')
        .then(r => r.json())
        .then(data => {
          state.graphLoadError = '';
          applyGraphPayload(data, 'architecture');
          requestAnimationFrame(tick);
        })
        .catch(err => {
          state.graphLoadError = err.message || 'Graph request failed';
          document.getElementById('repoMeta').textContent = 'Failed to load graph: ' + err.message;
          updateEmptyMapOverlay(true);
        });
    }

    function refreshGraph() {
      const button = document.getElementById('refreshBtn');
      const previousText = button.textContent;
      const nextView = state.view === 'commits' ? 'commits' : 'architecture';
      button.disabled = true;
      button.textContent = 'Refreshing...';
      setRepoStatus('Refreshing index and graph...');
      fetch('/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_commits: 1000 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Refresh failed');
          return payload;
        })
        .then(payload => {
          state.graphLoadError = '';
          state.compare = null;
          const status = document.getElementById('compareStatus');
          status.textContent = '';
          status.dataset.mode = '';
          applyGraphPayload(payload, nextView);
        })
        .catch(err => {
          setRepoStatus('Refresh failed: ' + err.message);
        })
        .finally(() => {
          button.disabled = false;
          button.textContent = previousText;
        });
    }

    function applyGraphPayload(payload, view) {
      state.graphLoadError = '';
      state.raw = payload;
      invalidateNodeDetailCache();
      state.buildInfo = payload.build || null;
      state.pendingUrlState = state.pendingUrlState || readUrlStateFromLocation();
      applyRuntimeConfig(payload.config || {});
      applyLargeGraphGuard(payload);
      populateCommitSelectors(payload);
      if (state.pendingUrlState) applyUrlCompareRefs(state.pendingUrlState);
      setRepoStatus(payload.repo.name + ' - ' + payload.repo.path);
      renderBuildBadge(payload);
      renderStats(payload.stats);
      renderDiagnostics(payload.diagnostics || {});
      loadIndexStatus();
      const requestedView = state.pendingUrlState && state.pendingUrlState.view ? state.pendingUrlState.view : view || state.view || 'architecture';
      setGraph(requestedView);
      applyPendingUrlState();
    }

    function applyLargeGraphGuard(payload) {
      const stats = payload.stats || {};
      const huge = Number(stats.components || 0) > 350 ||
        Number(stats.component_edges || 0) > 1200 ||
        Number(stats.files || 0) > 3000 ||
        Number(stats.symbols || 0) > 50000;
      state.performanceGuardActive = Boolean(huge && !state.pendingUrlState);
      if (!state.performanceGuardActive) return;
      state.activeLens = 'overview';
      state.nodeBudget = Math.min(Math.max(Number(state.nodeBudget || 220), 180), 240);
      state.connectedOnly = true;
      state.edgeBundling = true;
      state.focusSelection = false;
      state.traceMode = null;
      setCategoryVisibilitySet(['owned', 'team', 'tests']);
      setConnectionVisibilitySet(['api', 'functions', 'graphql', 'database', 'component', 'projects', 'tests', 'custom']);
      updateScaleControls();
    }

    function setRepoStatus(text) {
      document.getElementById('repoMeta').textContent = text;
    }

    function applyRuntimeConfig(config) {
      if (state.configApplied) return;
      const ui = config.ui || {};
      const defaultLens = ui.default_lens || state.activeLens;
      if (defaultLens && LENS_LABELS[defaultLens] && defaultLens !== state.activeLens) {
        applyMapLens(defaultLens);
      } else if (defaultLens && LENS_LABELS[defaultLens]) {
        state.activeLens = defaultLens;
      }
      if (Number.isFinite(Number(ui.node_budget))) state.nodeBudget = Number(ui.node_budget);
      if (Number.isFinite(Number(ui.min_edge_weight))) state.minEdgeWeight = Math.max(1, Number(ui.min_edge_weight));
      if (ui.connected_only !== undefined) state.connectedOnly = Boolean(ui.connected_only);
      if (Number.isFinite(Number(ui.edge_contrast))) state.edgeContrast = clamp(Number(ui.edge_contrast), 25, 100);
      state.classification = normalizeClassificationConfig(config.classification || {});
      state.configApplied = true;
      updateScaleControls();
    }

    function normalizeClassificationConfig(config) {
      return {
        owned_prefixes: stringList(config.owned_prefixes),
        team_prefixes: stringList(config.team_prefixes),
        company_prefixes: stringList(config.company_prefixes),
        third_party_packages: stringList(config.third_party_packages),
        hide_packages: stringList(config.hide_packages),
        show_packages: stringList(config.show_packages)
      };
    }

    function stringList(value) {
      if (!Array.isArray(value)) return [];
      return value.map(item => String(item || '').trim()).filter(Boolean);
    }

    function renderBuildBadge(payload) {
      const badge = document.getElementById('buildBadge');
      const build = payload.build || {};
      const stats = payload.stats || {};
      const config = payload.config || {};
      const cache = build.cache_enabled ? 'cache on' : 'cache off';
      const indexTime = stats.last_indexed_at ? String(stats.last_indexed_at).slice(0, 16).replace('T', ' ') : 'no index time';
      const configText = config.fingerprint ? 'cfg ' + config.fingerprint : 'default cfg';
      const staleMark = build.server_source_stale || build.ui_version !== EXPECTED_UI_VERSION ? ' stale' : '';
      badge.innerHTML = '<strong>' + escapeHtml(build.ui_version || 'ui') + '</strong> ' +
        escapeHtml(indexTime + ' - ' + configText + ' - ' + cache + staleMark);
      setHelp(badge, [
        'Server/UI version: ' + (build.ui_version || 'unknown'),
        'Expected UI version: ' + EXPECTED_UI_VERSION,
        'Server started: ' + (build.server_started_at || 'unknown'),
        'Source modified: ' + (build.source_modified_at || 'unknown'),
        'Index time: ' + (stats.last_indexed_at || 'unknown'),
        'Config: ' + (build.config_path || 'defaults'),
        'Cache: ' + cache + (build.cache_ttl_seconds ? ', ttl ' + build.cache_ttl_seconds + 's' : ''),
        build.server_source_stale ? 'The server started before the current visualization source file was modified. Restart the CodeAtlas server to load the newest UI code.' : ''
      ].join('\n'));
      renderStaleBanner();
    }

    function populateCommitSelectors(payload) {
      const commits = commitOptionsFromPayload(payload);
      state.commitOptions = commits;
      const baseSelect = document.getElementById('baseCommitSelect');
      const headSelect = document.getElementById('headCommitSelect');
      const previousBase = baseSelect.value;
      const previousHead = headSelect.value;
      fillCommitSelect(baseSelect, commits);
      fillCommitSelect(headSelect, commits);

      const defaultBase = commits[1] ? commits[1].ref : commits[0] ? commits[0].ref : 'HEAD~1';
      const defaultHead = commits[0] ? commits[0].ref : 'HEAD';
      baseSelect.value = matchingCommitRef(previousBase, commits) || defaultBase;
      headSelect.value = matchingCommitRef(previousHead, commits) || defaultHead;
    }

    function commitOptionsFromPayload(payload) {
      const inventoryCommits = payload.inventory && payload.inventory.commits ? payload.inventory.commits : [];
      if (inventoryCommits.length) {
        return inventoryCommits
          .map(commit => ({
            ref: commit.sha || commit.short_sha,
            shortRef: commit.short_sha || String(commit.sha || '').slice(0, 12),
            title: commit.title || 'Untitled commit',
            timestamp: commit.timestamp || '',
            files: commit.files || 0
          }))
          .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));
      }
      return (payload.commit_graph.nodes || [])
        .filter(node => node.type === 'commit')
        .map(node => ({
          ref: node.id.replace(/^commit:/, ''),
          shortRef: node.id.replace(/^commit:/, ''),
          title: node.label || 'Untitled commit',
          timestamp: node.timestamp || '',
          files: node.metrics ? node.metrics.files || 0 : 0
        }))
        .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));
    }

    function fillCommitSelect(select, commits) {
      select.innerHTML = '';
      for (const commit of commits) {
        const option = document.createElement('option');
        option.value = commit.ref;
        option.textContent = commitOptionLabel(commit);
        select.appendChild(option);
      }
      select.disabled = commits.length === 0;
    }

    function commitOptionLabel(commit) {
      const date = commit.timestamp ? commit.timestamp.slice(0, 10) + ' ' : '';
      return date + (commit.shortRef || commit.ref) + ' ' + truncateText(commit.title, 54);
    }

    function matchingCommitRef(value, commits) {
      const clean = String(value || '').trim();
      if (!clean) return '';
      const match = commits.find(commit =>
        clean === commit.ref ||
        clean === commit.shortRef ||
        clean.startsWith(commit.ref) ||
        commit.ref.startsWith(clean) ||
        (commit.shortRef && (clean.startsWith(commit.shortRef) || commit.shortRef.startsWith(clean)))
      );
      return match ? match.ref : '';
    }

    function truncateText(value, length) {
      const text = String(value || '');
      return text.length > length ? text.slice(0, length - 1) + '...' : text;
    }

    function selectedCompareRefs() {
      const baseSelect = document.getElementById('baseCommitSelect');
      const headSelect = document.getElementById('headCommitSelect');
      return {
        base: baseSelect && baseSelect.value ? baseSelect.value : 'HEAD~1',
        head: headSelect && headSelect.value ? headSelect.value : 'HEAD'
      };
    }

    function compareRefsFromSelectors() {
      const { base, head } = selectedCompareRefs();
      return [...new Set([base, head].filter(Boolean))];
    }

    function readUrlStateFromLocation() {
      const params = new URLSearchParams(window.location.search);
      if (params.has('state')) {
        const compactState = urlStateFromCompactPayload(decodeCompactUrlState(params.get('state')));
        if (compactState) return compactState;
      }
      if (!URL_STATE_KEYS.some(key => key !== 'state' && params.has(key))) return null;
      return {
        view: cleanUrlChoice(params.get('view'), ['architecture', 'commits', 'compare']),
        lens: cleanUrlChoice(params.get('lens'), Object.keys(LENS_LABELS)),
        categories: csvParam(params, 'cat'),
        connections: csvParam(params, 'conn'),
        hidden: csvParam(params, 'hidden'),
        selected: params.get('selected') || '',
        side: cleanUrlChoice(params.get('side'), ['base', 'head']),
        detailTab: cleanUrlChoice(params.get('detailTab'), DETAIL_TABS),
        trace: cleanUrlChoice(params.get('trace'), ['neighbors', 'callers', 'callees', 'api', 'tests', 'git']),
        pin: pinnedTraceFromUrlParam(params.get('pin')),
        focus: boolUrlParam(params.get('focus')),
        hops: numberUrlParam(params.get('hops'), 1),
        budget: numberUrlParam(params.get('budget'), null),
        minEdgeWeight: numberUrlParam(params.get('min'), null),
        connectedOnly: boolUrlParam(params.get('connected')),
        edgeContrast: numberUrlParam(params.get('contrast'), null),
        edgeBundling: boolUrlParam(params.get('bundles')),
        viewport: viewportFromUrlParam(params.get('z'), params.get('pan')),
        baseViewport: viewportTripletFromUrlParam(params.get('baseVp')),
        headViewport: viewportTripletFromUrlParam(params.get('headVp')),
        compareChangesOnly: boolUrlParam(params.get('changes')),
        compareSyncViewports: boolUrlParam(params.get('sync')),
        baseRef: params.get('base') || '',
        headRef: params.get('head') || ''
      };
    }

    function urlStateFromCompactPayload(payload) {
      if (!payload || typeof payload !== 'object') return null;
      return {
        view: cleanUrlChoice(payload.view || payload.v, ['architecture', 'commits', 'compare']),
        lens: cleanUrlChoice(payload.lens || payload.l, Object.keys(LENS_LABELS)),
        categories: arrayUrlPayload(payload.categories || payload.cat || payload.c),
        connections: arrayUrlPayload(payload.connections || payload.conn || payload.k),
        hidden: arrayUrlPayload(payload.hidden || payload.hd),
        selected: String(payload.selected || payload.s || ''),
        side: cleanUrlChoice(payload.side, ['base', 'head']),
        detailTab: cleanUrlChoice(payload.detailTab || payload.dt, DETAIL_TABS),
        trace: cleanUrlChoice(payload.trace || payload.t, ['neighbors', 'callers', 'callees', 'api', 'tests', 'git']),
        pin: pinnedTraceFromUrlParam(payload.pin || payload.p),
        focus: boolUrlParam(payload.focus !== undefined ? payload.focus : payload.f),
        hops: numberUrlParam(payload.hops !== undefined ? payload.hops : payload.hp, 1),
        budget: numberUrlParam(payload.budget !== undefined ? payload.budget : payload.b, null),
        minEdgeWeight: numberUrlParam(payload.minEdgeWeight !== undefined ? payload.minEdgeWeight : payload.m, null),
        connectedOnly: boolUrlParam(payload.connectedOnly !== undefined ? payload.connectedOnly : payload.co),
        edgeContrast: numberUrlParam(payload.edgeContrast !== undefined ? payload.edgeContrast : payload.ec, null),
        edgeBundling: boolUrlParam(payload.edgeBundling !== undefined ? payload.edgeBundling : payload.eb),
        viewport: viewportFromPayload(payload.viewport || payload.vp),
        baseViewport: viewportFromPayload(payload.baseViewport || payload.bvp),
        headViewport: viewportFromPayload(payload.headViewport || payload.hvp),
        compareChangesOnly: boolUrlParam(payload.compareChangesOnly !== undefined ? payload.compareChangesOnly : payload.ch),
        compareSyncViewports: boolUrlParam(payload.compareSyncViewports !== undefined ? payload.compareSyncViewports : payload.sy),
        baseRef: String(payload.baseRef || payload.base || ''),
        headRef: String(payload.headRef || payload.head || '')
      };
    }

    function arrayUrlPayload(value) {
      if (Array.isArray(value)) return value.map(item => String(item || '').trim()).filter(Boolean);
      if (value === null || value === undefined || value === '') return [];
      return String(value).split(',').map(item => item.trim()).filter(Boolean);
    }

    function viewportFromPayload(value) {
      if (!value) return null;
      if (Array.isArray(value)) {
        if (value.length < 3) return null;
        const parts = value.map(Number);
        if (parts.some(part => !Number.isFinite(part))) return null;
        return { zoom: clamp(parts[0], CANVAS_ZOOM_MIN, CANVAS_ZOOM_MAX), panX: parts[1], panY: parts[2] };
      }
      if (typeof value === 'object') {
        const zoom = numberUrlParam(value.zoom, null);
        const panX = numberUrlParam(value.panX, null);
        const panY = numberUrlParam(value.panY, null);
        if (!Number.isFinite(zoom) || !Number.isFinite(panX) || !Number.isFinite(panY)) return null;
        return { zoom: clamp(zoom, CANVAS_ZOOM_MIN, CANVAS_ZOOM_MAX), panX, panY };
      }
      return viewportTripletFromUrlParam(value);
    }

    function encodeCompactUrlState(payload) {
      try {
        const json = JSON.stringify(payload);
        const bytes = encodeURIComponent(json).replace(/%([0-9A-F]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
        return btoa(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
      } catch (err) {
        return '';
      }
    }

    function decodeCompactUrlState(value) {
      try {
        const clean = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
        const padded = clean + '='.repeat((4 - clean.length % 4) % 4);
        const raw = atob(padded);
        const encoded = Array.from(raw, char => '%' + char.charCodeAt(0).toString(16).padStart(2, '0')).join('');
        return JSON.parse(decodeURIComponent(encoded));
      } catch (err) {
        return null;
      }
    }

    function cleanUrlChoice(value, allowed) {
      const clean = String(value || '').trim();
      return allowed.includes(clean) ? clean : '';
    }

    function csvParam(params, name) {
      return String(params.get(name) || '')
        .split(',')
        .map(value => decodeURIComponent(value).trim())
        .filter(Boolean);
    }

    function boolUrlParam(value) {
      if (value === null || value === undefined || value === '') return null;
      return ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
    }

    function numberUrlParam(value, fallback) {
      if (value === null || value === undefined || value === '') return fallback;
      const number = Number(value);
      return Number.isFinite(number) ? number : fallback;
    }

    function viewportFromUrlParam(zoomValue, panValue) {
      const zoom = numberUrlParam(zoomValue, null);
      const parts = String(panValue || '').split(',').map(Number);
      if (!Number.isFinite(zoom) || parts.length < 2 || !Number.isFinite(parts[0]) || !Number.isFinite(parts[1])) return null;
      return {
        zoom: clamp(zoom, CANVAS_ZOOM_MIN, CANVAS_ZOOM_MAX),
        panX: parts[0],
        panY: parts[1]
      };
    }

    function viewportTripletFromUrlParam(value) {
      const parts = String(value || '').split(',').map(Number);
      if (parts.length < 3 || parts.some(part => !Number.isFinite(part))) return null;
      return {
        zoom: clamp(parts[0], CANVAS_ZOOM_MIN, CANVAS_ZOOM_MAX),
        panX: parts[1],
        panY: parts[2]
      };
    }

    function pinnedTraceFromUrlParam(value) {
      const parts = String(value || '').split('|');
      if (parts.length < 1 || !parts[0]) return null;
      return {
        nodeId: decodeURIComponent(parts[0]),
        side: cleanUrlChoice(parts[1], ['base', 'head']) || null,
        mode: cleanUrlChoice(parts[2], ['neighbors', 'callers', 'callees', 'api', 'tests', 'git']) || 'neighbors',
        hops: clamp(Math.round(numberUrlParam(parts[3], 1)), 1, 3)
      };
    }

    function applyUrlCompareRefs(urlState) {
      if (!urlState) return;
      const baseSelect = document.getElementById('baseCommitSelect');
      const headSelect = document.getElementById('headCommitSelect');
      if (baseSelect && urlState.baseRef) baseSelect.value = matchingCommitRef(urlState.baseRef, state.commitOptions) || urlState.baseRef;
      if (headSelect && urlState.headRef) headSelect.value = matchingCommitRef(urlState.headRef, state.commitOptions) || urlState.headRef;
    }

    function applyPendingUrlState() {
      const urlState = state.pendingUrlState;
      if (!urlState || state.isRestoringUrlState) return false;
      if (urlState.view === 'compare' && !state.compare) return false;
      state.isRestoringUrlState = true;
      try {
        if (urlState.lens) applyMapLens(urlState.lens);
        if (urlState.categories.length) setCategoryVisibilitySet(urlState.categories);
        if (urlState.connections.length) setConnectionVisibilitySet(urlState.connections);
        state.hiddenNodeIds = new Set(urlState.hidden || []);
        if (urlState.budget !== null) state.nodeBudget = Math.max(0, Math.round(urlState.budget));
        if (urlState.minEdgeWeight !== null) state.minEdgeWeight = Math.max(1, Math.round(urlState.minEdgeWeight));
        if (urlState.connectedOnly !== null) state.connectedOnly = urlState.connectedOnly;
        if (urlState.edgeContrast !== null) state.edgeContrast = clamp(Math.round(urlState.edgeContrast), 25, 100);
        if (urlState.edgeBundling !== null) state.edgeBundling = urlState.edgeBundling;
        if (urlState.focus !== null) state.focusSelection = urlState.focus;
        if (urlState.hops !== null) state.focusHops = clamp(Math.round(urlState.hops), 1, 3);
        if (urlState.detailTab) state.activeDetailTab = urlState.detailTab;
        state.traceMode = urlState.trace || null;
        state.pinnedTrace = urlState.pin;
        if (urlState.compareChangesOnly !== null) state.compareChangesOnly = urlState.compareChangesOnly;
        if (urlState.compareSyncViewports !== null) state.compareSyncViewports = urlState.compareSyncViewports;
        restoreUrlSelection(urlState);
        updateScaleControls();
        updateCompareModeControls();
        applyFilters();
        applyUrlViewport(urlState);
        renderFilterControls();
        renderSelection(state.selected);
        state.pendingUrlState = null;
        state.urlStateApplied = true;
      } finally {
        state.isRestoringUrlState = false;
      }
      scheduleUrlStateUpdate();
      return true;
    }

    function restoreUrlSelection(urlState) {
      if (!urlState.selected) {
        state.selected = null;
        return;
      }
      const node = nodeForUrlSelection(urlState.selected, urlState.side);
      state.selected = node ? { kind: 'node', node, side: urlState.side || null } : null;
    }

    function nodeForUrlSelection(id, side) {
      if (side && state.compare && state.compare[side]) {
        return state.compare[side].allNodes.find(node => node.id === id) || null;
      }
      return state.nodeIndex.get(id) || allKnownNodes().find(node => node.id === id) || null;
    }

    function applyUrlViewport(urlState) {
      if (urlState.viewport) Object.assign(graphViewport(null), urlState.viewport);
      if (urlState.baseViewport) Object.assign(state.compareViewports.base, urlState.baseViewport);
      if (urlState.headViewport) Object.assign(state.compareViewports.head, urlState.headViewport);
    }

    function scheduleUrlStateUpdate() {
      if (state.isRestoringUrlState || !state.raw) return;
      if (state.urlSyncTimer) window.clearTimeout(state.urlSyncTimer);
      state.urlSyncTimer = window.setTimeout(writeUrlStateToLocation, 120);
    }

    function writeUrlStateToLocation(options) {
      options = options || {};
      state.urlSyncTimer = null;
      if (state.isRestoringUrlState || !state.raw) return;
      const payload = currentUrlStatePayload();
      let params = urlParamsFromStatePayload(payload);
      let query = params.toString();
      if (options.compact || shouldUseCompactUrlState(query, payload)) {
        const encoded = encodeCompactUrlState(payload);
        if (encoded) {
          params = new URLSearchParams();
          params.set('state', encoded);
          query = params.toString();
        }
      }
      const next = window.location.pathname + (query ? '?' + query : '') + window.location.hash;
      if (next !== window.location.pathname + window.location.search + window.location.hash) {
        window.history.replaceState(null, '', next);
      }
    }

    function currentUrlStatePayload() {
      const selected = selectedNodeForUrl();
      const payload = {
        v: state.view,
        l: state.activeLens || 'overview',
        c: visibleCategoryIds(),
        k: visibleConnectionIds(),
        f: state.focusSelection ? 1 : 0,
        hp: state.focusHops || 1,
        b: state.nodeBudget || 0,
        m: state.minEdgeWeight || 1,
        co: state.connectedOnly ? 1 : 0,
        ec: state.edgeContrast || 64,
        eb: state.edgeBundling ? 1 : 0,
        dt: state.activeDetailTab || 'evidence',
        vp: [compactNumber(state.zoom), compactNumber(state.panX), compactNumber(state.panY)]
      };
      if (state.hiddenNodeIds.size) payload.hd = [...state.hiddenNodeIds];
      if (selected) {
        payload.s = selected.id;
        if (selected.side) payload.side = selected.side;
      }
      if (state.traceMode) payload.t = state.traceMode;
      if (state.pinnedTrace) payload.p = pinnedTraceToUrlValue(state.pinnedTrace);
      if (state.view === 'compare') {
        const refs = selectedCompareRefs();
        payload.base = refs.base;
        payload.head = refs.head;
        payload.bvp = viewportToUrlTriplet(state.compareViewports.base);
        payload.hvp = viewportToUrlTriplet(state.compareViewports.head);
        payload.ch = state.compareChangesOnly ? 1 : 0;
        payload.sy = state.compareSyncViewports ? 1 : 0;
      }
      return payload;
    }

    function urlParamsFromStatePayload(payload) {
      const params = new URLSearchParams();
      params.set('view', payload.v);
      params.set('lens', payload.l);
      params.set('cat', payload.c.join(','));
      params.set('conn', payload.k.join(','));
      if (payload.hd && payload.hd.length) params.set('hidden', payload.hd.join(','));
      if (payload.s) {
        params.set('selected', payload.s);
        if (payload.side) params.set('side', payload.side);
      }
      if (payload.t) params.set('trace', payload.t);
      if (payload.p) params.set('pin', payload.p);
      if (payload.dt) params.set('detailTab', payload.dt);
      params.set('focus', payload.f ? '1' : '0');
      params.set('hops', String(payload.hp || 1));
      params.set('budget', String(payload.b || 0));
      params.set('min', String(payload.m || 1));
      params.set('connected', payload.co ? '1' : '0');
      params.set('contrast', String(payload.ec || 64));
      params.set('bundles', payload.eb ? '1' : '0');
      params.set('z', payload.vp[0]);
      params.set('pan', payload.vp[1] + ',' + payload.vp[2]);
      if (payload.v === 'compare') {
        params.set('base', payload.base || 'HEAD~1');
        params.set('head', payload.head || 'HEAD');
        params.set('baseVp', payload.bvp.join(','));
        params.set('headVp', payload.hvp.join(','));
        params.set('changes', payload.ch ? '1' : '0');
        params.set('sync', payload.sy ? '1' : '0');
      }
      return params;
    }

    function shouldUseCompactUrlState(query, payload) {
      return query.length > 640 || (payload.hd && payload.hd.length > 12);
    }

    function viewportToUrlTriplet(viewport) {
      return [compactNumber(viewport.zoom), compactNumber(viewport.panX), compactNumber(viewport.panY)];
    }

    function visibleCategoryIds() {
      return CATEGORY_FILTERS.filter(category => isCategoryVisible(category.id)).map(category => category.id);
    }

    function visibleConnectionIds() {
      return CONNECTION_FILTERS.filter(connection => isConnectionVisible(connection.id)).map(connection => connection.id);
    }

    function selectedNodeForUrl() {
      if (!state.selected || state.selected.kind !== 'node' || !state.selected.node) return null;
      return { id: state.selected.node.id, side: state.selected.side || '' };
    }

    function viewportToUrlValue(viewport) {
      return compactNumber(viewport.zoom) + ',' + compactNumber(viewport.panX) + ',' + compactNumber(viewport.panY);
    }

    function pinnedTraceToUrlValue(pin) {
      return [
        encodeURIComponent(pin.nodeId || ''),
        pin.side || '',
        pin.mode || 'neighbors',
        String(pin.hops || 1)
      ].join('|');
    }

    function compactNumber(value) {
      const number = Number(value || 0);
      return Number.isFinite(number) ? String(Math.round(number * 1000) / 1000) : '0';
    }

    function scheduleCompareWarmup() {
      if (state.view !== 'compare') return;
      const refs = compareRefsFromSelectors();
      if (refs.length < 2) return;
      const key = refs.join('..');
      if (state.compareWarmKey === key) return;
      state.compareWarmKey = key;
      const status = document.getElementById('compareStatus');
      if (!state.compareInFlight) {
        status.dataset.mode = 'warm';
        status.classList.remove('error-text');
        status.textContent = 'Preparing compare cache...';
      }
      fetch('/api/compare/warm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refs })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Compare cache failed');
          return payload;
        })
        .then(payload => {
          if (state.compareWarmKey !== key || state.compareInFlight || status.dataset.mode === 'compare') return;
          const misses = (payload.warmed || []).filter(item => item.cache === 'miss').length;
          status.dataset.mode = 'warm';
          status.textContent = misses ? 'Compare cache ready' : 'Compare cache already ready';
        })
        .catch(err => {
          if (state.compareWarmKey !== key || state.compareInFlight || status.dataset.mode === 'compare') return;
          status.textContent = 'Compare cache failed: ' + err.message;
          status.classList.add('error-text');
        });
    }

    function runCompare() {
      const { base, head } = selectedCompareRefs();
      const status = document.getElementById('compareStatus');
      state.compareInFlight = true;
      status.dataset.mode = 'compare';
      status.textContent = 'Loading compare graph...';
      status.classList.remove('error-text');
      fetch('/api/compare?base=' + encodeURIComponent(base) + '&head=' + encodeURIComponent(head))
        .then(async response => {
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || 'Compare failed');
          return payload;
        })
        .then(payload => {
          state.compare = normalizeComparePayload(payload);
          resetCompareViewports();
          status.textContent = compareSummaryText(payload.summary);
          setGraph('compare');
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        })
        .finally(() => {
          state.compareInFlight = false;
        });
    }

    function askQuestion() {
      const question = document.getElementById('chatQuestion').value.trim();
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      if (!question) return;
      status.textContent = 'Thinking...';
      status.classList.remove('error-text');
      answerRoot.classList.remove('workflow-mode');
      answerRoot.textContent = '';
      sourcesRoot.innerHTML = '';
      fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, max_tokens: 3000 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Ask failed');
          return payload;
        })
        .then(payload => {
          status.textContent = payload.estimated_context_tokens + ' context tokens';
          answerRoot.textContent = payload.answer;
          renderChatSources(payload);
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        });
    }

    function renderRepoQuestions() {
      const root = document.getElementById('repoQuestions');
      root.innerHTML = '';
      for (const item of REPO_QUESTIONS) {
        const button = document.createElement('button');
        button.className = 'workflow-btn';
        button.textContent = item.label;
        setHelp(button, item.question + ' Applies the ' + item.lens + ' lens first, then asks CodeAtlas locally.');
        button.onclick = () => runRepoQuestion(item);
        root.appendChild(button);
      }
    }

    function runRepoQuestion(item) {
      if (item.lens) applyMapLens(item.lens);
      document.getElementById('chatQuestion').value = item.question;
      if (item.action) {
        runToolWorkflow(item.action, item);
      } else if (item.label.toLowerCase().includes('agent')) {
        createAgentContext();
      } else if (item.question.includes(':')) {
        runStructuralQuery(item.question);
      } else {
        askQuestion();
      }
    }

    function runStructuralQuery(query) {
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      status.textContent = 'Querying graph...';
      status.classList.remove('error-text');
      answerRoot.classList.remove('workflow-mode');
      answerRoot.textContent = '';
      sourcesRoot.innerHTML = '';
      fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, limit: 25 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Graph query failed');
          return payload;
        })
        .then(payload => {
          status.textContent = payload.type || 'graph query';
          answerRoot.textContent = JSON.stringify(payload, null, 2);
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        });
    }

    function runToolWorkflow(action, item) {
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      const task = document.getElementById('chatQuestion').value.trim() || item.question || '';
      const endpoints = {
        'context-pack': ['/api/context-pack', { task, max_tokens: 6000, format: 'markdown' }],
        'verify-plan': ['/api/verify-plan', { task, base_ref: 'HEAD' }],
        'rules': ['/api/rules', { limit: 50 }],
        'source-outline': ['/api/source-outline', { query: selectedNodeQuery() || task, limit: 40 }]
      };
      const config = endpoints[action];
      if (!config) return;
      status.textContent = 'Running ' + action.replace('-', ' ') + '...';
      status.classList.remove('error-text');
      answerRoot.classList.add('workflow-mode');
      answerRoot.innerHTML = '';
      renderWorkflowLoading(answerRoot, action);
      sourcesRoot.innerHTML = '';
      fetch(config[0], {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config[1])
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || action + ' failed');
          return payload;
        })
        .then(payload => {
          status.textContent = workflowStatus(action, payload);
          renderWorkflowResult(action, payload, answerRoot);
        })
        .catch(err => {
          status.textContent = action + ' failed';
          status.classList.add('error-text');
          renderWorkflowError(answerRoot, action, err.message);
        });
    }

    function selectedNodeQuery() {
      if (state.selected && state.selected.kind === 'node' && state.selected.node) {
        return state.selected.node.label || state.selected.node.id || '';
      }
      return document.getElementById('searchInput').value.trim();
    }

    function workflowStatus(action, payload) {
      if (action === 'context-pack') return ((payload.pack && payload.pack.estimated_tokens) || 0) + ' estimated tokens';
      if (action === 'verify-plan') return (payload.changed_files || []).length + ' changed files';
      if (action === 'rules') return (payload.count || 0) + ' findings';
      if (action === 'source-outline') return (payload.count || 0) + ' files';
      return action;
    }

    function renderWorkflowLoading(root, action) {
      root.innerHTML = '';
      const result = document.createElement('div');
      result.className = 'workflow-result';
      root.appendChild(result);
      appendWorkflowHeader(result, 'Running ' + action.replace('-', ' '), 'Scanning indexed evidence and preparing result cards.');
      const panel = document.createElement('div');
      panel.className = 'workflow-panel';
      panel.innerHTML = '<div class="workflow-progress"><span></span></div><div class="workflow-meta">Large repositories may take a moment. Cached results are reused when possible.</div>';
      result.appendChild(panel);
    }

    function renderWorkflowError(root, action, message) {
      root.innerHTML = '';
      const result = document.createElement('div');
      result.className = 'workflow-result';
      root.appendChild(result);
      appendWorkflowHeader(result, action.replace('-', ' ') + ' failed', 'CodeAtlas could not complete this workflow.');
      appendWorkflowEmpty(result, message || 'Unexpected workflow error.', 'workflow-error');
    }

    function renderWorkflowResult(action, payload, root) {
      root.innerHTML = '';
      const result = document.createElement('div');
      result.className = 'workflow-result';
      root.appendChild(result);
      if (action === 'context-pack') {
        renderContextPackResult(result, payload);
      } else if (action === 'verify-plan') {
        renderVerifyPlanResult(result, payload);
      } else if (action === 'rules') {
        renderRulesResult(result, payload);
      } else if (action === 'source-outline') {
        renderSourceOutlineResult(result, payload);
      } else {
        appendWorkflowCode(result, JSON.stringify(payload, null, 2));
      }
      appendWorkflowExportActions(result, action, payload);
    }

    function renderContextPackResult(root, payload) {
      const pack = payload.pack || {};
      appendWorkflowHeader(root, 'Context pack', [
        ((pack.estimated_tokens || 0) + ' estimated tokens'),
        ((pack.recommended_files || []).length + ' files'),
        ((pack.snippets || []).length + ' snippets'),
        cacheLabel(payload.cache)
      ].join(' - '));
      appendWorkflowStats(root, [
        ['Files', (pack.recommended_files || []).length],
        ['Snippets', (pack.snippets || []).length],
        ['Rules', (pack.rule_findings || []).length],
        ['Verify commands', ((pack.verification || {}).commands || []).length]
      ]);
      appendWorkflowTabs(root, [
        {
          label: 'Summary',
          render: panel => {
            appendWorkflowList(panel, 'Evidence', (pack.evidence || []).slice(0, 6).map(item => ({
              title: item.title || item.source_id || 'Evidence',
              meta: [item.source_type, item.path || item.source_id || ''].filter(Boolean).join(' - ')
            })));
            appendWorkflowList(panel, 'Owners', (pack.ownership || []).slice(0, 5).map(item => ({
              title: item.developer || 'Unknown owner',
              meta: (item.commits || 0) + ' commits, ' + (item.files_touched || 0) + ' files'
            })));
            if (!(pack.evidence || []).length && !(pack.ownership || []).length) {
              appendWorkflowEmpty(panel, 'No memory evidence or ownership records were found for this task.');
            }
          }
        },
        {
          label: 'Files',
          render: panel => appendWorkflowList(panel, 'Recommended files', (pack.recommended_files || []).slice(0, 12).map(file => ({
            title: file,
            meta: 'Included in the AI task context.'
          }))) || appendWorkflowEmpty(panel, 'No specific files were selected for this context pack.')
        },
        {
          label: 'Snippets',
          render: panel => {
            const snippets = pack.snippets || [];
            if (!snippets.length) {
              appendWorkflowEmpty(panel, 'No exact snippets matched this task.');
              return;
            }
            for (const snippet of snippets.slice(0, 8)) {
              appendWorkflowDetails(panel, snippet.file_path + ':' + snippet.lines, snippet.code || '');
            }
          }
        },
        {
          label: 'Verification',
          render: panel => renderVerificationCommands(panel, ((pack.verification || {}).commands) || [], (pack.verification || {}).warnings || [])
        },
        {
          label: 'Raw',
          render: panel => appendWorkflowCode(panel, payload.rendered || JSON.stringify(payload, null, 2))
        }
      ]);
    }

    function renderVerifyPlanResult(root, payload) {
      appendWorkflowHeader(root, 'Verification plan', [
        ((payload.changed_files || []).length + ' changed files'),
        ((payload.test_files || []).length + ' test files'),
        ((payload.commands || []).length + ' commands'),
        cacheLabel(payload.cache)
      ].join(' - '));
      appendWorkflowList(root, 'Changed files', (payload.impacted_files || []).map(item => ({
        title: item.file_path,
        meta: [item.status, item.component, item.reason].filter(Boolean).join(' - '),
        tone: item.status && item.status.startsWith('D') ? 'high' : ''
      })));
      if (!(payload.impacted_files || []).length) {
        appendWorkflowEmpty(root, 'No changed files were detected. The plan falls back to repository-level checks.');
      }
      renderVerificationCommands(root, payload.commands || [], payload.warnings || []);
    }

    function renderVerificationCommands(root, commands, warnings) {
      appendWorkflowList(root, 'Commands', (commands || []).map(item => ({
        title: item.command,
        meta: item.reason,
        copyText: item.command
      })));
      if (!(commands || []).length) {
        appendWorkflowEmpty(root, 'No verification commands were suggested for this context.');
      }
      appendWorkflowList(root, 'Warnings', (warnings || []).map(warning => ({
        title: warning,
        meta: '',
        tone: 'warn'
      })));
    }

    function renderRulesResult(root, payload) {
      const counts = payload.counts || {};
      appendWorkflowHeader(root, 'Rule checks', [
        ((payload.count || 0) + ' findings'),
        ('high ' + (counts.high || 0)),
        ('medium ' + (counts.medium || 0)),
        ('low ' + (counts.low || 0)),
        cacheLabel(payload.cache)
      ].join(' - '));
      renderRulesFilters(root, payload.findings || []);
    }

    function renderSourceOutlineResult(root, payload) {
      appendWorkflowHeader(root, 'Source outline', [
        ((payload.count || 0) + ' files'),
        payload.query ? ('query ' + payload.query) : 'repository outline',
        cacheLabel(payload.cache)
      ].join(' - '));
      appendWorkflowList(root, 'Files', (payload.files || []).slice(0, 12).map(file => ({
        title: file.file_path,
        meta: (file.symbols || []).slice(0, 6).map(symbol => symbol.name + ':' + symbol.line_start).join(', ') || file.language
      })));
      if (!(payload.files || []).length) appendWorkflowEmpty(root, 'No files matched the source-outline query.');
    }

    function appendWorkflowHeader(root, title, meta) {
      const panel = document.createElement('div');
      panel.className = 'workflow-panel';
      const heading = document.createElement('div');
      heading.className = 'workflow-title';
      heading.textContent = title;
      panel.appendChild(heading);
      if (meta) {
        const detail = document.createElement('div');
        detail.className = 'workflow-meta';
        detail.textContent = meta;
        panel.appendChild(detail);
      }
      root.appendChild(panel);
    }

    function cacheLabel(cache) {
      if (!cache || cache.enabled === false) return 'cache off';
      return cache.hit ? 'cached ' + Math.round(cache.age_seconds || 0) + 's' : 'fresh';
    }

    function appendWorkflowStats(root, items) {
      if (!items.length) return;
      const grid = document.createElement('div');
      grid.className = 'workflow-stat-grid';
      for (const [label, value] of items) {
        const stat = document.createElement('div');
        stat.className = 'workflow-stat';
        const labelEl = document.createElement('div');
        labelEl.className = 'workflow-stat-label';
        labelEl.textContent = label;
        const valueEl = document.createElement('div');
        valueEl.className = 'workflow-stat-value';
        valueEl.textContent = String(value);
        stat.append(labelEl, valueEl);
        grid.appendChild(stat);
      }
      root.appendChild(grid);
    }

    function appendWorkflowTabs(root, tabs) {
      const wrapper = document.createElement('div');
      wrapper.className = 'workflow-result';
      const tabBar = document.createElement('div');
      tabBar.className = 'workflow-tabs';
      const panelRoot = document.createElement('div');
      panelRoot.className = 'workflow-result';
      function activateTab(index) {
        Array.from(tabBar.querySelectorAll('button')).forEach((button, buttonIndex) => {
          button.classList.toggle('active', buttonIndex === index);
        });
        Array.from(panelRoot.querySelectorAll('.workflow-tab-panel')).forEach((panel, panelIndex) => {
          panel.hidden = panelIndex !== index;
        });
      }
      tabs.forEach((tab, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = tab.label;
        if (index === 0) button.classList.add('active');
        button.onclick = () => activateTab(index);
        const panel = document.createElement('div');
        panel.className = 'workflow-tab-panel workflow-result';
        panel.hidden = index !== 0;
        tab.render(panel);
        tabBar.appendChild(button);
        panelRoot.appendChild(panel);
      });
      wrapper.append(tabBar, panelRoot);
      root.appendChild(wrapper);
    }

    function appendWorkflowEmpty(root, message, extraClass) {
      const empty = document.createElement('div');
      empty.className = 'workflow-empty' + (extraClass ? ' ' + extraClass : '');
      empty.textContent = message;
      root.appendChild(empty);
      return empty;
    }

    function renderRulesFilters(root, findings) {
      const controls = document.createElement('div');
      controls.className = 'workflow-panel workflow-result';
      const tabs = document.createElement('div');
      tabs.className = 'workflow-filter-tabs';
      const filterRow = document.createElement('div');
      filterRow.className = 'workflow-filter-row';
      const listRoot = document.createElement('div');
      listRoot.className = 'workflow-result';
      const severities = [
        ['all', 'All'],
        ['high', 'High'],
        ['medium', 'Medium'],
        ['low', 'Low']
      ];
      let activeSeverity = 'all';
      let hideTests = false;
      function toneForSeverity(severity) {
        if (severity === 'high') return 'high';
        if (severity === 'low') return 'low';
        return 'warn';
      }
      function renderFilteredFindings() {
        Array.from(tabs.querySelectorAll('button')).forEach(button => {
          button.classList.toggle('active', button.dataset.severity === activeSeverity);
        });
        const filtered = (findings || []).filter(finding => {
          const severity = String(finding.severity || 'medium').toLowerCase();
          if (activeSeverity !== 'all' && severity !== activeSeverity) return false;
          if (hideTests && isWorkflowTestPath(finding.file_path || '')) return false;
          return true;
        });
        listRoot.innerHTML = '';
        if (!filtered.length) {
          appendWorkflowEmpty(listRoot, (findings || []).length ? 'No findings match this filter.' : 'No rule findings. The enabled checks look clean.');
          return;
        }
        appendWorkflowList(listRoot, 'Findings', filtered.slice(0, 50).map(finding => {
          const severity = String(finding.severity || 'medium').toLowerCase();
          const location = [finding.file_path, finding.line ? 'line ' + finding.line : ''].filter(Boolean).join(':');
          return {
            title: finding.title || finding.rule_id || 'Finding',
            meta: [severity, location, finding.message].filter(Boolean).join(' - '),
            tone: toneForSeverity(severity)
          };
        }));
      }
      severities.forEach(([value, label]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.severity = value;
        button.textContent = label;
        button.onclick = () => {
          activeSeverity = value;
          renderFilteredFindings();
        };
        tabs.appendChild(button);
      });
      const hideTestsLabel = document.createElement('label');
      const hideTestsInput = document.createElement('input');
      hideTestsInput.type = 'checkbox';
      hideTestsInput.onchange = () => {
        hideTests = hideTestsInput.checked;
        renderFilteredFindings();
      };
      hideTestsLabel.append(hideTestsInput, document.createTextNode('Hide tests'));
      filterRow.appendChild(hideTestsLabel);
      controls.append(tabs, filterRow);
      root.append(controls, listRoot);
      renderFilteredFindings();
    }

    function isWorkflowTestPath(path) {
      return /(^|\/)(tests?|__tests__)\//.test(path) ||
        /(^|[._-])tests?\./.test(path) ||
        /(\.test|\.spec|_test)\.(js|jsx|ts|tsx|py)$/.test(path);
    }

    function appendWorkflowList(root, title, items) {
      if (!items || !items.length) return false;
      const panel = document.createElement('div');
      panel.className = 'workflow-panel';
      const heading = document.createElement('div');
      heading.className = 'workflow-title';
      heading.textContent = title;
      panel.appendChild(heading);
      const list = document.createElement('div');
      list.className = 'workflow-list';
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'workflow-row ' + (item.tone || '');
        const rowTitle = document.createElement('div');
        rowTitle.className = 'workflow-row-title';
        rowTitle.textContent = item.title || '';
        row.appendChild(rowTitle);
        if (item.meta) {
          const meta = document.createElement('div');
          meta.className = 'workflow-row-meta';
          meta.textContent = item.meta;
          row.appendChild(meta);
        }
        if (item.copyText) {
          const copyButton = document.createElement('button');
          copyButton.type = 'button';
          copyButton.className = 'workflow-copy-btn';
          copyButton.textContent = item.copyLabel || 'Copy command';
          setHelp(copyButton, 'Copies this command to the clipboard.');
          copyButton.onclick = () => copyWorkflowText(copyButton, item.copyText);
          row.appendChild(copyButton);
        }
        list.appendChild(row);
      }
      panel.appendChild(list);
      root.appendChild(panel);
      return true;
    }

    function appendWorkflowDetails(root, title, body) {
      const details = document.createElement('details');
      details.className = 'detail-card';
      const summary = document.createElement('summary');
      summary.textContent = title;
      const pre = document.createElement('pre');
      pre.className = 'workflow-code';
      pre.textContent = body;
      details.append(summary, pre);
      root.appendChild(details);
    }

    function appendWorkflowExportActions(root, action, payload) {
      const actions = document.createElement('div');
      actions.className = 'workflow-actions';
      const jsonButton = document.createElement('button');
      jsonButton.textContent = 'Export JSON';
      setHelp(jsonButton, 'Downloads the full workflow result as JSON, including evidence and metadata.');
      jsonButton.onclick = () => downloadWorkflowFile(action + '.json', JSON.stringify(payload, null, 2), 'application/json');
      actions.appendChild(jsonButton);
      if (payload.rendered) {
        const textButton = document.createElement('button');
        textButton.textContent = 'Export text';
        setHelp(textButton, 'Downloads the rendered workflow result as text or Markdown.');
        textButton.onclick = () => downloadWorkflowFile(action + '.md', payload.rendered, 'text/markdown');
        actions.appendChild(textButton);
      }
      const copyButton = document.createElement('button');
      copyButton.textContent = 'Copy JSON';
      setHelp(copyButton, 'Copies the full workflow result JSON to the clipboard.');
      copyButton.onclick = () => copyWorkflowJson(copyButton, payload);
      actions.appendChild(copyButton);
      root.appendChild(actions);
    }

    function downloadWorkflowFile(filename, body, type) {
      const blob = new Blob([body], { type: type || 'text/plain' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'codeatlas-' + filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function copyWorkflowJson(button, payload) {
      copyWorkflowText(button, JSON.stringify(payload, null, 2));
    }

    function copyWorkflowText(button, text) {
      copyTextToClipboard(text, () => {
        const previous = button.textContent;
        button.textContent = 'Copied';
        window.setTimeout(() => {
          button.textContent = previous;
        }, 1200);
      }, () => {
        const previous = button.textContent;
        button.textContent = 'Copy failed';
        window.setTimeout(() => {
          button.textContent = previous;
        }, 1200);
      });
    }

    function copyTextToClipboard(text, onSuccess, onError) {
      if (!navigator.clipboard) {
        if (onError) onError();
        return;
      }
      navigator.clipboard.writeText(String(text || '')).then(() => {
        if (onSuccess) onSuccess();
      }).catch(() => {
        if (onError) onError();
      });
    }

    function openCommandPalette() {
      state.commandPaletteOpen = true;
      state.commandPaletteQuery = '';
      state.commandPaletteIndex = 0;
      const shell = document.getElementById('commandPalette');
      const input = document.getElementById('commandPaletteInput');
      shell.hidden = false;
      input.value = '';
      renderCommandPaletteResults();
      requestAnimationFrame(() => input.focus());
    }

    function closeCommandPalette() {
      state.commandPaletteOpen = false;
      document.getElementById('commandPalette').hidden = true;
    }

    function handleCommandPaletteKeydown(event) {
      const actions = filteredCommandPaletteActions();
      if (event.key === 'Escape') {
        event.preventDefault();
        closeCommandPalette();
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        state.commandPaletteIndex = nextEnabledCommandIndex(actions, 1);
        renderCommandPaletteResults();
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        state.commandPaletteIndex = nextEnabledCommandIndex(actions, -1);
        renderCommandPaletteResults();
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        const action = actions[state.commandPaletteIndex];
        if (action && !action.disabled) runCommandPaletteAction(action);
      }
    }

    function nextEnabledCommandIndex(actions, direction) {
      if (!actions.length) return 0;
      let index = state.commandPaletteIndex;
      for (let i = 0; i < actions.length; i += 1) {
        index = (index + direction + actions.length) % actions.length;
        if (!actions[index].disabled) return index;
      }
      return state.commandPaletteIndex;
    }

    function renderCommandPaletteResults() {
      const root = document.getElementById('commandPaletteResults');
      const actions = filteredCommandPaletteActions();
      root.innerHTML = '';
      if (!actions.length) {
        const empty = document.createElement('div');
        empty.className = 'command-empty';
        empty.textContent = 'No matching actions';
        root.appendChild(empty);
        return;
      }
      state.commandPaletteIndex = clamp(state.commandPaletteIndex, 0, actions.length - 1);
      let previousKind = '';
      for (const [index, action] of actions.entries()) {
        if (action.kind !== previousKind) {
          previousKind = action.kind;
          const group = document.createElement('div');
          group.className = 'command-group';
          group.textContent = commandKindLabel(action.kind);
          root.appendChild(group);
        }
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'command-row' + (index === state.commandPaletteIndex ? ' active' : '');
        row.disabled = Boolean(action.disabled);
        row.onclick = () => runCommandPaletteAction(action);
        const copy = document.createElement('div');
        copy.style.minWidth = '0';
        const label = document.createElement('span');
        label.className = 'command-label';
        label.textContent = action.label;
        const meta = document.createElement('span');
        meta.className = 'command-meta';
        meta.textContent = action.disabled ? action.disabled : action.detail;
        copy.append(label, meta);
        const kind = document.createElement('span');
        kind.className = 'command-kind';
        kind.textContent = action.kind;
        row.append(copy, kind);
        root.appendChild(row);
      }
    }

    function commandKindLabel(kind) {
      return {
        share: 'Share',
        view: 'View',
        filter: 'Filter',
        trace: 'Trace',
        lens: 'Lens',
        agent: 'Agent'
      }[kind] || String(kind || 'Action');
    }

    function filteredCommandPaletteActions() {
      const query = state.commandPaletteQuery.trim().toLowerCase();
      const actions = commandPaletteActions().map((action, order) => ({ ...action, order }));
      if (!query) return actions;
      const terms = query.split(/\s+/).filter(Boolean);
      return actions
        .map(action => ({ ...action, score: commandActionScore(action, terms) }))
        .filter(action => action.score > 0)
        .sort((a, b) => b.score - a.score || a.order - b.order);
    }

    function commandActionScore(action, terms) {
      const haystack = commandActionHaystack(action);
      if (!terms.every(term => haystack.includes(term))) return 0;
      let score = 1;
      const label = String(action.label || '').toLowerCase();
      const kind = String(action.kind || '').toLowerCase();
      for (const term of terms) {
        if (label === term) score += 80;
        else if (label.startsWith(term)) score += 45;
        else if (label.includes(' ' + term)) score += 24;
        else if (kind.includes(term)) score += 18;
        else score += 8;
      }
      const query = terms.join(' ');
      const hasSelection = state.selected && ['node', 'edge', 'path', 'bundle'].includes(state.selected.kind);
      if (hasSelection && (action.kind === 'trace' || /fit|pin|selection/i.test(action.label))) score += 14;
      if (!hasSelection && action.kind === 'share') score += 4;
      if (/copy|link|share|url/.test(query) && action.kind === 'share') score += 18;
      if (/trace|flow|call|api|test/.test(query) && action.kind === 'trace') score += 18;
      if (/agent|codex|claude|context/.test(query) && action.kind === 'agent') score += 22;
      if (action.disabled) score -= 12;
      return score;
    }

    function commandActionHaystack(action) {
      return [action.label, action.detail, action.kind, action.keywords].join(' ').toLowerCase();
    }

    function commandPaletteActions() {
      const selectedNode = state.selected && state.selected.kind === 'node' ? state.selected.node : null;
      const selectedSide = state.selected && state.selected.kind === 'node' ? state.selected.side : null;
      const hasSelection = state.selected && ['node', 'edge', 'path', 'bundle'].includes(state.selected.kind);
      const hasTabbedSelection = detailTabsVisibleForSelection(state.selected);
      return [
        { label: 'Copy current map link', detail: 'lens, filters, selection, camera', kind: 'share', keywords: 'url permalink state', run: copyCurrentMapLink },
        { label: 'Copy compact map link', detail: 'short state link for dense filtered maps', kind: 'share', keywords: 'url permalink compressed state', run: copyCompactMapLink },
        { label: 'Copy clean map link', detail: 'repository map without current URL state', kind: 'share', keywords: 'url reset clean state', run: copyCleanMapLink },
        { label: 'Reset shared view state', detail: 'remove map state from the address bar', kind: 'share', keywords: 'clear url params reset state', run: clearSharedViewState },
        { label: 'Fit selection', detail: 'center the current node or edge', kind: 'view', keywords: 'zoom pan selected focus', disabled: hasSelection ? '' : 'No selection', run: () => focusCameraOnSelection(state.selected) },
        { label: 'Focus evidence search', detail: 'search inside the selected detail panel', kind: 'view', keywords: 'find evidence card proof snippet', disabled: hasSelection ? '' : 'No selection', run: focusDetailSearch },
        { label: 'Show Evidence tab', detail: 'selection proofs and confidence', kind: 'view', keywords: 'detail panel tab proofs confidence', disabled: hasTabbedSelection ? '' : 'No selection', run: () => setDetailTab('evidence') },
        { label: 'Show Flow tab', detail: 'selection paths and call direction', kind: 'view', keywords: 'detail panel tab trace callers callees', disabled: hasTabbedSelection ? '' : 'No selection', run: () => setDetailTab('flow') },
        { label: 'Show Files tab', detail: 'selection files, locations, and metadata', kind: 'view', keywords: 'detail panel tab source location metadata', disabled: hasTabbedSelection ? '' : 'No selection', run: () => setDetailTab('files') },
        { label: 'Show History tab', detail: 'selection commits and co-change evidence', kind: 'view', keywords: 'detail panel tab git commits history', disabled: hasTabbedSelection ? '' : 'No selection', run: () => setDetailTab('history') },
        { label: 'Show more nodes', detail: 'increase the node budget without going full graph', kind: 'view', keywords: 'budget performance more', run: showMoreNodesFromPalette },
        { label: 'Reduce map complexity', detail: 'turn on guardrails for a dense graph', kind: 'view', keywords: 'simplify performance fewer', run: applySmartSimplify },
        { label: 'Save view preset', detail: 'store current filters and lens locally', kind: 'view', keywords: 'preset bookmark filters lens', run: saveCurrentViewPreset },
        { label: 'Apply selected view preset', detail: 'restore the chosen saved preset', kind: 'view', keywords: 'preset restore filters lens', disabled: state.viewPresets.length ? '' : 'No presets', run: applySelectedViewPreset },
        { label: 'Export view presets', detail: 'download saved map presets as JSON', kind: 'view', keywords: 'preset export json backup', disabled: state.viewPresets.length ? '' : 'No presets', run: exportViewPresets },
        { label: 'Import view presets', detail: 'load saved map presets from JSON', kind: 'view', keywords: 'preset import json restore', run: () => document.getElementById('viewPresetImportInput').click() },
        { label: 'Toggle stale UI auto-reload', detail: state.autoReloadStaleUi ? 'disable stale UI reload countdown' : 'enable stale UI reload countdown', kind: 'view', keywords: 'reload refresh stale ui frontend', run: toggleStaleAutoReload },
        { label: state.compareChangesOnly ? 'Show compare context' : 'Show compare changes only', detail: 'toggle unchanged architecture around compare diffs', kind: 'view', keywords: 'compare diff changes context unchanged', disabled: state.view === 'compare' ? '' : 'Open compare mode', run: toggleCompareChangesOnly },
        { label: state.compareSyncViewports ? 'Unlock compare cameras' : 'Sync compare cameras', detail: 'toggle linked pan and zoom in compare mode', kind: 'view', keywords: 'compare sync lock pan zoom camera', disabled: state.view === 'compare' ? '' : 'Open compare mode', run: toggleCompareViewportSync },
        { label: 'Explain compare diff', detail: 'write a concise compare brief in Ask', kind: 'view', keywords: 'compare explain summary diff changes impact', disabled: state.compare ? '' : 'Run compare first', run: explainCompareDiff },
        { label: 'Hide third-party', detail: 'turn off external dependency nodes', kind: 'filter', keywords: 'external dependencies packages', run: () => setCategoryFromCommand('third_party', false) },
        { label: 'Show third-party', detail: 'turn on external dependency nodes', kind: 'filter', keywords: 'external dependencies packages', run: () => setCategoryFromCommand('third_party', true) },
        { label: 'Hide docs/config', detail: 'turn off docs and config nodes', kind: 'filter', keywords: 'readme setup requirements', run: () => setCategoryFromCommand('docs_config', false) },
        { label: 'Trace callers', detail: 'incoming function/API relationships', kind: 'trace', keywords: 'incoming upstream', disabled: selectedNode ? '' : 'Select a node first', run: () => traceNodeMode(selectedNode, selectedSide, 'callers') },
        { label: 'Trace callees', detail: 'outgoing function/API relationships', kind: 'trace', keywords: 'outgoing downstream', disabled: selectedNode ? '' : 'Select a node first', run: () => traceNodeMode(selectedNode, selectedSide, 'callees') },
        { label: 'Trace API/data flow', detail: 'API, GraphQL, function, project edges', kind: 'trace', keywords: 'route request data', disabled: selectedNode ? '' : 'Select a node first', run: () => traceNodeMode(selectedNode, selectedSide, 'api') },
        { label: 'Trace tests', detail: 'validation paths around the selected node', kind: 'trace', keywords: 'spec validation coverage', disabled: selectedNode ? '' : 'Select a node first', run: () => traceNodeMode(selectedNode, selectedSide, 'tests') },
        { label: 'Pin current trace', detail: 'keep this trace visible while exploring', kind: 'trace', keywords: 'persist path lock selected', disabled: selectedNode ? '' : 'Select a node first', run: () => pinCurrentTrace(selectedNode, selectedSide) },
        { label: 'Clear pinned trace', detail: 'remove the pinned trace overlay', kind: 'trace', keywords: 'unpin path clear', disabled: state.pinnedTrace ? '' : 'No pinned trace', run: clearPinnedTrace },
        { label: 'Overview lens', detail: 'balanced architecture view', kind: 'lens', keywords: 'default reset', run: () => applyMapLens('overview') },
        { label: 'Subway lens', detail: '5 to 12 major components', kind: 'lens', keywords: 'simple progressive', run: () => applyMapLens('subway') },
        { label: 'API/data lens', detail: 'request and boundary paths', kind: 'lens', keywords: 'flow routes endpoints', run: () => applyMapLens('apis') },
        { label: 'Show tests', detail: 'switch to the tests lens', kind: 'lens', keywords: 'validation specs pytest unit', run: () => applyMapLens('tests') },
        { label: 'Full graph', detail: 'show every category and connection type', kind: 'lens', keywords: 'all nodes no budget', run: () => applyMapLens('full') },
        { label: 'Copy agent pack', detail: 'generate and copy agent context', kind: 'agent', keywords: 'codex claude context', run: copyAgentPackFromPalette }
      ];
    }

    function runCommandPaletteAction(action) {
      if (!action || action.disabled) return;
      closeCommandPalette();
      action.run();
      scheduleUrlStateUpdate();
    }

    function setCategoryFromCommand(category, visible) {
      state.categoryVisibility[category] = visible;
      applyFilters();
      renderFilterControls();
    }

    function copyCurrentMapLink() {
      writeUrlStateToLocation();
      copyTextToClipboard(window.location.href, () => {
        document.getElementById('chatStatus').textContent = 'Map link copied';
      }, () => {
        document.getElementById('chatStatus').textContent = 'Map link copy failed';
      });
    }

    function copyCompactMapLink() {
      writeUrlStateToLocation({ compact: true });
      copyTextToClipboard(window.location.href, () => {
        document.getElementById('chatStatus').textContent = 'Compact map link copied';
      }, () => {
        document.getElementById('chatStatus').textContent = 'Compact map link copy failed';
      });
    }

    function copyCleanMapLink() {
      const clean = window.location.origin + window.location.pathname + window.location.hash;
      copyTextToClipboard(clean, () => {
        document.getElementById('chatStatus').textContent = 'Clean map link copied';
      }, () => {
        document.getElementById('chatStatus').textContent = 'Clean map link copy failed';
      });
    }

    function clearSharedViewState() {
      state.pendingUrlState = null;
      const next = window.location.pathname + window.location.hash;
      window.history.replaceState(null, '', next);
      document.getElementById('chatStatus').textContent = 'Shared view state cleared';
    }

    function focusDetailSearch() {
      const input = document.getElementById('detailSearchInput');
      if (!input || input.hidden) return;
      input.focus();
      input.select();
    }

    function showMoreNodesFromPalette() {
      state.performanceGuardActive = false;
      state.nodeBudget = state.nodeBudget > 0 ? Math.min(1200, state.nodeBudget + 120) : 0;
      updateScaleControls();
      applyFilters();
      renderFilterControls();
    }

    function pinCurrentTrace(node, side) {
      if (!node) return;
      state.pinnedTrace = {
        nodeId: node.id,
        side: side || null,
        mode: state.traceMode || 'neighbors',
        hops: state.focusHops || 1
      };
      applyFilters();
      renderFilterSummary();
      updateFocusBreadcrumb();
    }

    function clearPinnedTrace() {
      state.pinnedTrace = null;
      applyFilters();
      renderFilterSummary();
      updateFocusBreadcrumb();
    }

    function clearFocusBreadcrumb() {
      state.focusSelection = false;
      state.traceMode = null;
      state.pinnedTrace = null;
      updateScaleControls();
      applyFilters();
      renderFilterControls();
      updateFocusBreadcrumb(true);
    }

    function updateFocusBreadcrumb(force) {
      const root = document.getElementById('focusBreadcrumb');
      const text = document.getElementById('focusBreadcrumbText');
      if (!root || !text) return;
      const payload = focusBreadcrumbPayload();
      if (!payload) {
        state.lastFocusBreadcrumbSignature = '';
        root.hidden = true;
        return;
      }
      const signature = JSON.stringify(payload);
      if (!force && signature === state.lastFocusBreadcrumbSignature) return;
      state.lastFocusBreadcrumbSignature = signature;
      text.textContent = payload.text;
      setHelp(root, payload.help);
      root.hidden = false;
    }

    function focusBreadcrumbPayload() {
      if (state.pinnedTrace) {
        const mode = state.pinnedTrace.mode || 'neighbors';
        const hops = state.pinnedTrace.hops || 1;
        return {
          text: 'Pinned trace: ' + labelForNode(state.pinnedTrace.nodeId) + ' / ' + traceModeLabel(mode) + ' / ' + hops + ' hop' + (hops === 1 ? '' : 's'),
          help: 'Pinned trace keeps this path visible while you inspect other nodes. Clear it to return to the normal filtered map.'
        };
      }
      if (state.focusSelection && state.selected && ['node', 'edge', 'path', 'bundle'].includes(state.selected.kind)) {
        const mode = state.traceMode || 'neighbors';
        const hops = state.focusHops || 1;
        return {
          text: 'Focused on ' + selectionBreadcrumbLabel(state.selected) + ' / ' + traceModeLabel(mode) + ' / ' + hops + ' hop' + (hops === 1 ? '' : 's'),
          help: 'Focus mode narrows the map around the selected item. Clear it to restore the current lens without focus constraints.'
        };
      }
      return null;
    }

    function selectionBreadcrumbLabel(selection) {
      if (!selection) return 'selection';
      if (selection.kind === 'node') return selection.node.label || selection.node.id || 'node';
      if (selection.kind === 'edge') return labelForNode(selection.edge.source) + ' -> ' + labelForNode(selection.edge.target);
      if (selection.kind === 'path') return selection.path.sourceComponent + ' -> ' + selection.path.targetComponent;
      if (selection.kind === 'bundle') return selection.bundle.sourceLabel + ' -> ' + selection.bundle.targetLabel;
      return selection.kind;
    }

    function traceModeLabel(mode) {
      return {
        neighbors: 'neighbors',
        callers: 'callers',
        callees: 'callees',
        api: 'API/data',
        tests: 'tests',
        git: 'git co-change'
      }[mode] || String(mode || 'focus');
    }

    function copyAgentPackFromPalette() {
      const task = document.getElementById('chatQuestion').value.trim() || selectedNodeQuery() || 'Understand this repository and identify likely edit points.';
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      status.textContent = 'Packing context...';
      status.classList.remove('error-text');
      answerRoot.classList.remove('workflow-mode');
      answerRoot.textContent = '';
      sourcesRoot.innerHTML = '';
      fetch('/api/agent-context', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, max_tokens: 5000 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Agent context failed');
          return payload;
        })
        .then(payload => {
          answerRoot.textContent = payload.markdown || '';
          renderChatSources(payload);
          copyTextToClipboard(payload.markdown || '', () => {
            status.textContent = 'Agent pack copied';
          }, () => {
            status.textContent = 'Agent pack ready';
          });
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        });
    }

    function appendWorkflowCode(root, body) {
      const pre = document.createElement('pre');
      pre.className = 'workflow-code';
      pre.textContent = body;
      root.appendChild(pre);
    }

    function createAgentContext() {
      const task = document.getElementById('chatQuestion').value.trim() || 'Understand this repository and identify likely edit points.';
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      status.textContent = 'Packing context...';
      status.classList.remove('error-text');
      answerRoot.classList.remove('workflow-mode');
      answerRoot.textContent = '';
      sourcesRoot.innerHTML = '';
      fetch('/api/agent-context', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, max_tokens: 5000 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Agent context failed');
          return payload;
        })
        .then(payload => {
          status.textContent = payload.estimated_context_tokens + ' context tokens';
          answerRoot.textContent = payload.markdown || '';
          renderChatSources(payload);
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        });
    }

    function renderDiagnostics(diagnostics) {
      const root = document.getElementById('diagnosticsPanel');
      root.innerHTML = '';
      renderPerfPanel();
      const languages = diagnostics.language_counts || {};
      appendDiagnosticRow(root, 'Languages', Object.entries(languages).map(([name, count]) => name + ' ' + count).join(', ') || 'none');
      appendDiagnosticRow(root, 'Supported', (diagnostics.supported_languages || []).join(', ') || 'unknown');
      appendDiagnosticRow(root, 'Symbols/file', String(diagnostics.symbols_per_file || 0));
      appendDiagnosticRow(root, 'Unresolved calls', String(diagnostics.unresolved_calls || 0));
      appendDiagnosticRow(root, 'External deps', String(diagnostics.external_dependencies || 0));
      appendDiagnosticRow(root, 'Skipped last run', String(diagnostics.files_skipped || 0));
      appendDiagnosticRow(root, 'Parser errors', String(diagnostics.parser_errors || 0));
      appendDiagnosticRow(root, 'Stale index', diagnostics.stale ? 'yes' : 'no');
      appendDiagnosticRow(root, 'Graph worker', graphWorkerStatusText());
      const suggestions = diagnostics.suggestions || [];
      for (const suggestion of suggestions.slice(0, 3)) appendDiagnosticRow(root, 'Hint', suggestion);
    }

    function graphWorkerStatusText() {
      if (!window.Worker) return 'not supported';
      if (state.lastFilterWorkerUsed) return 'active' + (state.graphWorkerLastMs !== null ? ' (' + state.graphWorkerLastMs + 'ms last filter)' : '');
      if (state.graphWorkerSupported) return 'ready';
      return shouldUseGraphWorker() ? 'fallback' : 'standby';
    }

    function maybeRenderPerfPanel() {
      const now = performance.now();
      if (now - state.lastPerfRenderAt < 500) return;
      state.lastPerfRenderAt = now;
      renderPerfPanel();
    }

    function renderPerfPanel() {
      const root = document.getElementById('perfPanel');
      if (!root) return;
      root.innerHTML = '';
      const grid = document.createElement('div');
      grid.className = 'perf-grid';
      appendPerfCell(grid, 'Mode', state.lastFilterWorkerUsed ? 'worker' : 'main');
      appendPerfCell(grid, 'Filter', perfMs(state.lastFilterMs));
      appendPerfCell(grid, 'Draw', perfMs(state.lastDrawMs));
      appendPerfCell(grid, 'Frame', perfMs(state.lastFrameMs));
      appendPerfCell(grid, 'Nodes', String(state.nodes.length));
      appendPerfCell(grid, 'Edges', String(state.edges.length));
      root.appendChild(grid);
    }

    function appendPerfCell(root, label, value) {
      const cell = document.createElement('div');
      cell.className = 'perf-cell';
      const labelEl = document.createElement('span');
      labelEl.textContent = label;
      const valueEl = document.createElement('strong');
      valueEl.textContent = value;
      cell.append(labelEl, valueEl);
      root.appendChild(cell);
    }

    function perfMs(value) {
      return value === null || value === undefined ? '-' : value + 'ms';
    }

    function reportUiError(message, error) {
      const detail = error && error.stack ? error.stack : String(error && error.message || message || 'Unknown UI error');
      state.frontendErrors.unshift({ message: String(message || 'UI error'), detail, at: new Date().toISOString() });
      state.frontendErrors = state.frontendErrors.slice(0, 5);
      const panel = document.getElementById('uiErrorPanel');
      const body = document.getElementById('uiErrorBody');
      if (!panel || !body) return;
      body.textContent = state.frontendErrors.map(item => item.message + '\\n' + item.detail).join('\\n\\n');
      panel.hidden = false;
    }

    function loadIndexStatus() {
      fetch('/api/index-status')
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Index status failed');
          return payload;
        })
        .then(payload => appendIndexStatus(payload))
        .catch(() => renderStaleBanner(null));
    }

    function appendIndexStatus(status) {
      renderStaleBanner(status);
      const root = document.getElementById('diagnosticsPanel');
      appendDiagnosticRow(root, 'Dirty files', String(status.dirty_files || 0));
      appendDiagnosticRow(root, 'New files', String(status.new_files || 0));
      appendDiagnosticRow(root, 'Deleted files', String(status.deleted_files || 0));
      appendDiagnosticRow(root, 'Shared artifact', status.artifact_exists ? 'available' : 'missing');
    }

    function renderStaleBanner(status) {
      const banner = document.getElementById('staleBanner');
      if (!banner) return;
      if (status !== undefined) state.indexStatus = status;
      const build = state.buildInfo || {};
      const activeStatus = state.indexStatus;
      const messages = [];
      const actions = [];
      const staleUiProblem = Boolean(
        (build.ui_version && build.ui_version !== EXPECTED_UI_VERSION) ||
        build.server_source_stale
      );
      if (build.ui_version && build.ui_version !== EXPECTED_UI_VERSION) {
        messages.push('Browser/server UI mismatch: expected ' + EXPECTED_UI_VERSION + ', got ' + build.ui_version + '. Refresh the page or restart the server.');
        actions.push({ label: 'Refresh', run: () => window.location.reload() });
        actions.push({ label: 'Copy restart', copy: restartCommand() });
      }
      if (build.server_source_stale) {
        messages.push('Server source changed after this CodeAtlas server started. Restart the server to load the newest UI.');
        actions.push({ label: 'Copy restart', copy: restartCommand() });
      }
      if (activeStatus && activeStatus.stale) {
        const parts = [];
        if (!activeStatus.indexed) parts.push('no index has been built');
        if (activeStatus.dirty_files) parts.push(activeStatus.dirty_files + ' changed files');
        if (activeStatus.new_files) parts.push(activeStatus.new_files + ' new files');
        if (activeStatus.deleted_files) parts.push(activeStatus.deleted_files + ' deleted files');
        const reason = parts.length ? parts.join(', ') : 'repository files changed';
        const lastIndexed = activeStatus.last_indexed_at ? ' Last indexed ' + String(activeStatus.last_indexed_at).slice(0, 16).replace('T', ' ') + '.' : '';
        messages.push('Index may be stale: ' + reason + '.' + lastIndexed + ' Refresh to rebuild the map.');
        actions.push({ label: 'Copy command', copy: indexCommand() });
        actions.push({ label: 'Refresh', run: refreshGraph });
      }
      if (!messages.length) {
        stopStaleAutoReload();
        banner.hidden = true;
        banner.innerHTML = '';
        banner.classList.remove('critical');
        return;
      }
      if (staleUiProblem) {
        actions.push({
          label: state.autoReloadStaleUi ? 'Auto-reload on' : 'Auto-reload off',
          run: toggleStaleAutoReload
        });
        if (state.autoReloadStaleUi) startStaleAutoReload();
        else stopStaleAutoReload();
      } else {
        stopStaleAutoReload();
      }
      banner.innerHTML = '';
      banner.classList.toggle('critical', staleUiProblem);
      const content = document.createElement('div');
      content.className = 'stale-banner-content';
      const title = document.createElement('div');
      title.className = 'stale-banner-title';
      title.textContent = staleUiProblem ? 'Restart CodeAtlas UI server' : 'Refresh CodeAtlas index';
      const text = document.createElement('div');
      text.className = 'stale-banner-text';
      text.textContent = messages.join(' ');
      const meta = document.createElement('div');
      meta.className = 'stale-banner-meta';
      if (staleUiProblem) {
        appendStalePill(meta, 'expected ' + EXPECTED_UI_VERSION);
        appendStalePill(meta, 'running ' + (build.ui_version || 'unknown'));
        appendStalePill(meta, 'server started ' + shortDateTime(build.server_started_at));
      }
      if (build.server_source_stale) appendStalePill(meta, 'source changed');
      if (activeStatus && activeStatus.last_indexed_at) appendStalePill(meta, 'indexed ' + shortDateTime(activeStatus.last_indexed_at));
      content.append(title, text);
      if (meta.childNodes.length) content.appendChild(meta);
      banner.appendChild(content);
      if (staleUiProblem && state.autoReloadStaleUi) {
        const countdown = document.createElement('span');
        countdown.id = 'staleReloadCountdown';
        countdown.className = 'stale-countdown';
        countdown.textContent = 'Reloading in ' + state.staleReloadSeconds + 's';
        banner.appendChild(countdown);
      }
      const uniqueActions = uniqueBannerActions(actions);
      if (uniqueActions.length) {
        const actionRoot = document.createElement('div');
        actionRoot.className = 'stale-banner-actions';
        for (const action of uniqueActions) {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = action.label;
          button.onclick = () => {
            if (action.copy) {
              const previous = button.textContent;
              copyTextToClipboard(action.copy, () => {
                button.textContent = 'Copied';
                window.setTimeout(() => {
                  button.textContent = previous;
                }, 1200);
              }, () => {
                button.textContent = 'Copy failed';
                window.setTimeout(() => {
                  button.textContent = previous;
                }, 1200);
              });
            } else if (action.run) {
              action.run();
            }
          };
          actionRoot.appendChild(button);
        }
        banner.appendChild(actionRoot);
      }
      banner.hidden = false;
    }

    function appendStalePill(parent, text) {
      const pill = document.createElement('span');
      pill.className = 'stale-pill';
      pill.textContent = text;
      parent.appendChild(pill);
    }

    function shortDateTime(value) {
      if (!value) return 'unknown';
      return String(value).slice(0, 16).replace('T', ' ');
    }

    function toggleStaleAutoReload() {
      saveAutoReloadPreference(!state.autoReloadStaleUi);
      if (state.autoReloadStaleUi) startStaleAutoReload();
      else stopStaleAutoReload();
      renderStaleBanner();
    }

    function startStaleAutoReload() {
      if (state.staleReloadTimer) return;
      state.staleReloadSeconds = 8;
      updateStaleReloadCountdown();
      state.staleReloadTimer = window.setInterval(() => {
        state.staleReloadSeconds -= 1;
        updateStaleReloadCountdown();
        if (state.staleReloadSeconds <= 0) window.location.reload();
      }, 1000);
    }

    function stopStaleAutoReload() {
      if (state.staleReloadTimer) window.clearInterval(state.staleReloadTimer);
      state.staleReloadTimer = null;
      state.staleReloadSeconds = 0;
      updateStaleReloadCountdown();
    }

    function updateStaleReloadCountdown() {
      const node = document.getElementById('staleReloadCountdown');
      if (node) node.textContent = 'Reloading in ' + Math.max(0, state.staleReloadSeconds) + 's';
    }

    function uniqueBannerActions(actions) {
      const seen = new Set();
      const result = [];
      for (const action of actions) {
        const key = action.label + '|' + (action.copy || '');
        if (seen.has(key)) continue;
        seen.add(key);
        result.push(action);
      }
      return result;
    }

    function restartCommand() {
      const repoPath = state.raw && state.raw.repo ? state.raw.repo.path : '';
      const host = window.location.hostname || '127.0.0.1';
      const port = window.location.port || '8765';
      return ['codeatlas serve', shellQuote(repoPath || '.'), '--host', shellQuote(host), '--port', shellQuote(port), '--no-open'].join(' ');
    }

    function indexCommand() {
      const repoPath = state.raw && state.raw.repo ? state.raw.repo.path : '';
      return ['codeatlas index', shellQuote(repoPath || '.')].join(' ');
    }

    function shellQuote(value) {
      const text = String(value || '');
      if (/^[A-Za-z0-9_./:-]+$/.test(text)) return text;
      return "'" + text.replace(/'/g, "'\\''") + "'";
    }

    function appendDiagnosticRow(root, label, value) {
      const row = document.createElement('div');
      row.className = 'diagnostic-row';
      row.innerHTML = '<strong>' + escapeHtml(label) + ':</strong> ' + escapeHtml(value);
      root.appendChild(row);
    }

    function renderChatSources(payload) {
      const root = document.getElementById('chatSources');
      root.innerHTML = '';
      appendChatGroup(root, 'Code', payload.code || [], item =>
        '<strong>' + escapeHtml(item.symbol) + '</strong><br>' +
        escapeHtml(item.file_path + ':' + item.lines + ' - ' + item.reason)
      );
      appendChatGroup(root, 'Commits / Docs', payload.evidence || [], item =>
        '<strong>' + escapeHtml(item.title || item.source_id || 'Evidence') + '</strong><br>' +
        escapeHtml((item.source_type || 'evidence') + ' ' + (item.path || item.source_id || '')) + '<br>' +
        escapeHtml(item.snippet || '')
      );
      appendChatGroup(root, 'Owners', payload.ownership || [], item =>
        '<strong>' + escapeHtml(item.developer || 'Unknown') + '</strong><br>' +
        escapeHtml((item.commits || 0) + ' commits, ' + (item.files_touched || 0) + ' files')
      );
    }

    function appendChatGroup(root, title, items, renderItem) {
      if (!items.length) return;
      const label = document.createElement('div');
      label.className = 'section-title';
      label.textContent = title;
      root.appendChild(label);
      for (const item of items.slice(0, 5)) {
        const div = document.createElement('div');
        div.className = 'chat-item';
        div.innerHTML = renderItem(item);
        root.appendChild(div);
      }
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function setZoom(value, anchor, side) {
      const targetSide = side || (anchor ? compareSideForPoint(anchor) : null);
      if (state.view === 'compare' && !targetSide) return;
      const viewport = graphViewport(targetSide);
      const previous = viewport.zoom;
      const next = clamp(value, CANVAS_ZOOM_MIN, CANVAS_ZOOM_MAX);
      if (anchor && previous > 0 && next !== previous) {
        const center = zoomAnchorCenter(anchor, targetSide);
        const localX = anchor.x - center.x;
        const localY = anchor.y - center.y;
        const ratio = next / previous;
        viewport.panX = localX - (localX - viewport.panX) * ratio;
        viewport.panY = localY - (localY - viewport.panY) * ratio;
      }
      viewport.zoom = next;
      syncCompareViewport(targetSide);
      scheduleUrlStateUpdate();
    }

    function focusCameraOnSelection(selection) {
      if (!selection || !['node', 'edge', 'path', 'bundle'].includes(selection.kind)) return;
      const side = selection.side || (selection.kind === 'path' ? selection.path.side : null);
      if (state.view === 'compare' && !side) return;
      const nodes = selectionCameraNodes(selection, side);
      if (!nodes.length) return;
      fitCameraToNodes(nodes, side);
    }

    function selectionCameraNodes(selection, side) {
      const panel = side && state.compare
        ? state.compare[side]
        : { nodes: state.nodes, edges: state.edges, nodesById: state.graphCache.visibleNodesById };
      const nodesById = panel.nodesById || nodesByIdForPanel(panel.nodes || [], side);
      const ids = new Set();
      if (selection.kind === 'node') {
        ids.add(selection.node.id);
        for (const edge of panel.edges || []) {
          if (edge.source === selection.node.id) ids.add(edge.target);
          if (edge.target === selection.node.id) ids.add(edge.source);
        }
      } else if (selection.kind === 'edge') {
        ids.add(selection.edge.source);
        ids.add(selection.edge.target);
      } else if (selection.kind === 'path') {
        ids.add(selection.path.sourceId);
        ids.add(selection.path.targetId);
      } else if (selection.kind === 'bundle') {
        for (const edge of selection.bundle.edges || []) {
          ids.add(edge.source);
          ids.add(edge.target);
        }
      }
      return [...ids].map(id => nodesById.get(id)).filter(Boolean);
    }

    function fitCameraToNodes(nodes, side) {
      const rect = cameraRectForSide(side);
      const panelNodes = state.view === 'compare' && side && state.compare ? state.compare[side].nodes : state.nodes;
      const base = graphBaseFitForRect(rect, side, panelNodes);
      const bounds = boundsForNodes(nodes);
      if (!base || !bounds) return;
      const spanX = Math.max(120, bounds.maxX - bounds.minX);
      const spanY = Math.max(120, bounds.maxY - bounds.minY);
      const pad = Math.min(180, Math.max(70, Math.min(rect.w, rect.h) * 0.18));
      const usableW = Math.max(180, rect.w - pad * 2);
      const usableH = Math.max(180, rect.h - pad * 2);
      const targetTotalZoom = Math.min(usableW / spanX, usableH / spanY);
      const targetZoom = clamp(targetTotalZoom / base.fitZoom, CANVAS_ZOOM_MIN, Math.min(CANVAS_ZOOM_MAX, 4.8));
      const centerX = (bounds.minX + bounds.maxX) / 2;
      const centerY = (bounds.minY + bounds.maxY) / 2;
      animateViewportTo(side, {
        zoom: targetZoom,
        panX: -(centerX - base.centerX) * base.fitZoom * targetZoom,
        panY: -(centerY - base.centerY) * base.fitZoom * targetZoom
      });
    }

    function cameraRectForSide(side) {
      if (state.view === 'compare' && state.compare && side) {
        return compareRects(canvas.clientWidth, canvas.clientHeight)[side];
      }
      return { x: 0, y: 0, w: canvas.clientWidth, h: canvas.clientHeight };
    }

    function animateViewportTo(side, target) {
      const viewport = graphViewport(side);
      const start = { zoom: viewport.zoom, panX: viewport.panX, panY: viewport.panY };
      const duration = 180;
      const startedAt = performance.now();
      if (state.cameraAnimation) cancelAnimationFrame(state.cameraAnimation);
      const step = now => {
        const t = clamp((now - startedAt) / duration, 0, 1);
        const eased = 1 - Math.pow(1 - t, 3);
        viewport.zoom = start.zoom + (target.zoom - start.zoom) * eased;
        viewport.panX = start.panX + (target.panX - start.panX) * eased;
        viewport.panY = start.panY + (target.panY - start.panY) * eased;
        syncCompareViewport(side);
        if (t < 1) {
          state.cameraAnimation = requestAnimationFrame(step);
        } else {
          state.cameraAnimation = null;
          scheduleUrlStateUpdate();
        }
      };
      state.cameraAnimation = requestAnimationFrame(step);
    }

    function boundsForNodes(nodes) {
      if (!nodes || !nodes.length) return null;
      return nodes.reduce((bounds, node) => ({
        minX: Math.min(bounds.minX, node.x),
        minY: Math.min(bounds.minY, node.y),
        maxX: Math.max(bounds.maxX, node.x),
        maxY: Math.max(bounds.maxY, node.y)
      }), { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
    }

    function handleGraphWheel(event) {
      event.preventDefault();
      event.stopPropagation();
      const intensity = event.ctrlKey || event.metaKey ? 0.01 : 0.0025;
      const delta = clamp(event.deltaY, -140, 140);
      const point = canvasPoint(event);
      const side = compareSideForPoint(point);
      const viewport = graphViewport(side);
      setZoom(viewport.zoom * Math.exp(-delta * intensity), point, side);
    }

    let gestureStartZoom = 1;
    let gestureStartPoint = null;
    let gestureStartSide = null;

    function handleGraphGestureStart(event) {
      event.preventDefault();
      event.stopPropagation();
      gestureStartPoint = canvasPoint(event);
      gestureStartSide = compareSideForPoint(gestureStartPoint);
      gestureStartZoom = graphViewport(gestureStartSide).zoom;
    }

    function handleGraphGestureChange(event) {
      event.preventDefault();
      event.stopPropagation();
      setZoom(gestureStartZoom * event.scale, gestureStartPoint || canvasPoint(event), gestureStartSide);
    }

    function handleGraphPointerDown(event) {
      if (event.button !== 0) return;
      const point = canvasPoint(event);
      const minimapNav = minimapHitAt(point);
      if (minimapNav) {
        event.preventDefault();
        state.isMinimapPanning = true;
        state.activeMinimapNav = minimapNav;
        state.activePanSide = minimapNav.side || null;
        state.suppressClick = true;
        panGraphToMinimapPoint(minimapNav, point);
        canvas.classList.add('panning');
        try {
          canvas.setPointerCapture(event.pointerId);
        } catch (err) {
          // Some browsers skip pointer capture for synthetic events.
        }
        return;
      }
      const side = compareSideForPoint(point);
      if (state.view === 'compare' && !side) return;
      const viewport = graphViewport(side);
      state.isPanning = true;
      state.activePanSide = side;
      state.panStartX = event.clientX;
      state.panStartY = event.clientY;
      state.panBaseX = viewport.panX;
      state.panBaseY = viewport.panY;
      state.suppressClick = false;
      canvas.classList.add('panning');
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (err) {
        // Some browsers skip pointer capture for synthetic events.
      }
    }

    function handleGraphPointerMove(event) {
      if (state.isMinimapPanning) {
        hideEdgeHoverTooltip();
        event.preventDefault();
        const nav = state.activeMinimapNav;
        if (nav) panGraphToMinimapPoint(nav, canvasPoint(event));
        return;
      }
      if (!state.isPanning) {
        updateEdgeHoverFromPointer(event);
        return;
      }
      hideEdgeHoverTooltip();
      event.preventDefault();
      const dx = event.clientX - state.panStartX;
      const dy = event.clientY - state.panStartY;
      if (Math.hypot(dx, dy) > 3) state.suppressClick = true;
      const viewport = graphViewport(state.activePanSide);
      viewport.panX = state.panBaseX + dx;
      viewport.panY = state.panBaseY + dy;
      syncCompareViewport(state.activePanSide);
    }

    function updateEdgeHoverFromPointer(event) {
      const now = performance.now();
      if (now - state.lastHoverHitAt < 32) {
        if (state.hoveredEdge) positionEdgeHoverTooltip(event.clientX, event.clientY);
        return;
      }
      state.lastHoverHitAt = now;
      if (!state.nodes.length) {
        hideEdgeHoverTooltip();
        return;
      }
      const point = canvasPoint(event);
      const hit = hitEdgeAtPoint(point.x, point.y);
      if (!hit) {
        hideEdgeHoverTooltip();
        return;
      }
      const signature = edgeHoverSignature(hit.selection);
      if (!state.hoveredEdge || edgeHoverSignature(state.hoveredEdge) !== signature) {
        state.hoveredEdge = hit.selection;
        renderEdgeHoverTooltip(hit.selection);
      }
      state.hoveredEdgePoint = { x: event.clientX, y: event.clientY };
      positionEdgeHoverTooltip(event.clientX, event.clientY);
    }

    function hitEdgeAtPoint(x, y) {
      if (state.view === 'compare' && state.compare) {
        const sideName = compareSideForPoint({ x, y });
        if (!sideName) return null;
        const rect = compareRects(canvas.clientWidth, canvas.clientHeight)[sideName];
        const side = state.compare[sideName];
        if (!side || !side.nodes.length) return null;
        const transform = graphTransformFor(side.nodes, rect, sideName);
        const nodesById = side.nodesById || new Map(side.nodes.map(node => [node.id, node]));
        return hitEdgeRenderItemAt(x, y, side.nodes, side.edges, transform, rect, sideName, nodesById);
      }
      const transform = graphTransform();
      const nodesById = state.graphCache.visibleNodesById || new Map(state.nodes.map(node => [node.id, node]));
      return hitEdgeRenderItemAt(x, y, state.nodes, state.edges, transform, { x: 0, y: 0, w: canvas.clientWidth, h: canvas.clientHeight }, null, nodesById);
    }

    function renderEdgeHoverTooltip(selection) {
      const tooltip = document.getElementById('edgeHoverTooltip');
      if (!tooltip || !selection) return;
      tooltip.innerHTML = '';
      if (selection.kind === 'bundle') {
        renderBundleHoverTooltip(tooltip, selection.bundle, selection.side);
      } else if (selection.kind === 'edge') {
        renderExactEdgeHoverTooltip(tooltip, selection.edge, selection.side);
      }
      tooltip.hidden = false;
    }

    function renderExactEdgeHoverTooltip(root, edge, side) {
      const evidence = edgeEvidenceSummary(edge, side);
      const title = document.createElement('div');
      title.className = 'edge-hover-title';
      title.innerHTML = segmentedIdentifierHtml(evidence.title);
      const meta = document.createElement('div');
      meta.className = 'edge-hover-meta';
      meta.append(
        hoverBadge(edge.type || 'edge', badgeClass(edge.type)),
        hoverBadge('w ' + (edge.weight || 1), ''),
        hoverBadge(evidence.confidenceLabel + ' confidence', '')
      );
      const reason = document.createElement('div');
      reason.className = 'edge-hover-reason';
      reason.textContent = evidence.subtitle;
      root.append(title, meta, reason);
      if (evidence.proofs && evidence.proofs.length) {
        const proof = document.createElement('div');
        proof.className = 'edge-hover-proof';
        proof.innerHTML = segmentedIdentifierHtml('Proof: ' + evidence.proofs[0].title);
        root.appendChild(proof);
      }
    }

    function renderBundleHoverTooltip(root, bundle, side) {
      const title = document.createElement('div');
      title.className = 'edge-hover-title';
      title.innerHTML = segmentedIdentifierHtml(bundle.sourceLabel + ' -> ' + bundle.targetLabel);
      const meta = document.createElement('div');
      meta.className = 'edge-hover-meta';
      meta.append(
        hoverBadge((bundle.type || 'edge') + ' bundle', badgeClass(bundle.type)),
        hoverBadge('x ' + (bundle.edges || []).length, ''),
        hoverBadge('w ' + (bundle.weight || 1), '')
      );
      const reason = document.createElement('div');
      reason.className = 'edge-hover-reason';
      reason.textContent = 'Bundled to keep the map readable. Click to inspect the exact edges and their evidence.';
      root.append(title, meta, reason);
      const sample = (bundle.edges || [])[0];
      if (sample) {
        const evidence = edgeEvidenceSummary(sample, side);
        const proof = document.createElement('div');
        proof.className = 'edge-hover-proof';
        proof.textContent = 'Sample: ' + evidence.subtitle;
        root.appendChild(proof);
      }
    }

    function hoverBadge(text, className) {
      const badge = document.createElement('span');
      badge.className = 'badge' + (className ? ' ' + className : '');
      badge.textContent = text;
      return badge;
    }

    function positionEdgeHoverTooltip(clientX, clientY) {
      const tooltip = document.getElementById('edgeHoverTooltip');
      if (!tooltip || tooltip.hidden) return;
      const margin = 12;
      let left = clientX + 14;
      let top = clientY + 14;
      const rect = tooltip.getBoundingClientRect();
      if (left + rect.width > window.innerWidth - margin) left = clientX - rect.width - 14;
      if (top + rect.height > window.innerHeight - margin) top = clientY - rect.height - 14;
      tooltip.style.left = Math.max(margin, left) + 'px';
      tooltip.style.top = Math.max(margin, top) + 'px';
    }

    function hideEdgeHoverTooltip() {
      const tooltip = document.getElementById('edgeHoverTooltip');
      state.hoveredEdge = null;
      state.hoveredEdgePoint = null;
      if (tooltip) tooltip.hidden = true;
    }

    function edgeHoverSignature(selection) {
      if (!selection) return '';
      if (selection.kind === 'bundle') return 'bundle|' + (selection.side || '') + '|' + selection.bundle.id;
      if (selection.kind === 'edge') return 'edge|' + (selection.side || '') + '|' + edgeIdForHover(selection.edge);
      return selection.kind;
    }

    function handleGraphPointerEnd(event) {
      if (!state.isPanning && !state.isMinimapPanning) return;
      state.isPanning = false;
      state.isMinimapPanning = false;
      state.activeMinimapNav = null;
      state.activePanSide = null;
      canvas.classList.remove('panning');
      try {
        canvas.releasePointerCapture(event.pointerId);
      } catch (err) {
        // Pointer capture may already be released.
      }
      scheduleUrlStateUpdate();
    }

    function minimapHitAt(point) {
      if (!point || !state.minimapRects || !state.minimapRects.size) return null;
      for (const nav of state.minimapRects.values()) {
        const rect = nav.rect;
        if (point.x >= rect.x && point.x <= rect.x + rect.w && point.y >= rect.y && point.y <= rect.y + rect.h) {
          return nav;
        }
      }
      return null;
    }

    function panGraphToMinimapPoint(nav, point) {
      if (!nav || !point || !nav.scale) return;
      const mini = nav.rect;
      const x = clamp(point.x, mini.x + 6, mini.x + mini.w - 6);
      const y = clamp(point.y, mini.y + 6, mini.y + mini.h - 6);
      const graphX = nav.bounds.minX + (x - nav.offsetX) / nav.scale;
      const graphY = nav.bounds.minY + (y - nav.offsetY) / nav.scale;
      const nodes = state.view === 'compare' && nav.side && state.compare ? state.compare[nav.side].nodes : state.nodes;
      const base = graphBaseFitForRect(nav.graphRect, nav.side, nodes);
      const viewport = graphViewport(nav.side);
      viewport.panX = -(graphX - base.centerX) * base.fitZoom * viewport.zoom;
      viewport.panY = -(graphY - base.centerY) * base.fitZoom * viewport.zoom;
      syncCompareViewport(nav.side);
    }

    function syncCompareViewport(side) {
      if (state.view !== 'compare' || !state.compareSyncViewports || !side) return;
      const other = side === 'base' ? 'head' : side === 'head' ? 'base' : '';
      if (!other || !state.compareViewports[side] || !state.compareViewports[other]) return;
      Object.assign(state.compareViewports[other], state.compareViewports[side]);
    }

    function loadDetailPanelWidth() {
      try {
        const saved = Number(localStorage.getItem(DETAIL_WIDTH_KEY));
        if (Number.isFinite(saved) && saved > 0) return saved;
      } catch (err) {
        // Storage may be blocked; fall back to the default panel width.
      }
      return 360;
    }

    function detailPanelBounds() {
      const max = Math.max(320, Math.min(860, Math.floor(window.innerWidth * 0.62)));
      return { min: 300, max };
    }

    function normalizedDetailWidth(width) {
      const bounds = detailPanelBounds();
      return Math.round(clamp(width, bounds.min, bounds.max));
    }

    function applyDetailPanelWidth() {
      state.detailWidth = normalizedDetailWidth(state.detailWidth);
      document.getElementById('app').style.setProperty('--detail-width', state.detailWidth + 'px');
      const resizer = document.getElementById('detailPanelResizer');
      if (resizer) {
        resizer.setAttribute('aria-valuenow', String(state.detailWidth));
        resizer.setAttribute('aria-valuemin', String(detailPanelBounds().min));
        resizer.setAttribute('aria-valuemax', String(detailPanelBounds().max));
      }
    }

    function saveDetailPanelWidth() {
      try {
        localStorage.setItem(DETAIL_WIDTH_KEY, String(state.detailWidth));
      } catch (err) {
        // Resizing still works for the current page even if persistence fails.
      }
    }

    function handleDetailResizeStart(event) {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      state.isResizingDetail = true;
      state.detailResizeStartX = event.clientX;
      state.detailResizeStartWidth = state.detailWidth;
      document.body.classList.add('resizing-detail');
      event.currentTarget.classList.add('active');
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch (err) {
        // Pointer capture is not guaranteed for all browser/event combinations.
      }
    }

    function handleDetailResizeMove(event) {
      if (!state.isResizingDetail) return;
      event.preventDefault();
      const delta = state.detailResizeStartX - event.clientX;
      state.detailWidth = normalizedDetailWidth(state.detailResizeStartWidth + delta);
      applyDetailPanelWidth();
    }

    function handleDetailResizeEnd(event) {
      if (!state.isResizingDetail) return;
      state.isResizingDetail = false;
      document.body.classList.remove('resizing-detail');
      document.getElementById('detailPanelResizer').classList.remove('active');
      saveDetailPanelWidth();
      try {
        document.getElementById('detailPanelResizer').releasePointerCapture(event.pointerId);
      } catch (err) {
        // Pointer capture may already be released.
      }
    }

    function canvasPoint(event) {
      const rect = canvas.getBoundingClientRect();
      return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top
      };
    }

    function zoomAnchorCenter(anchor, side) {
      if (state.view === 'compare' && state.compare && side) {
        const rect = compareRects(canvas.clientWidth, canvas.clientHeight)[side];
        return { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 };
      }
      return { x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 };
    }

    function compareSideForPoint(point) {
      if (state.view !== 'compare' || !state.compare || !point) return null;
      const rects = compareRects(canvas.clientWidth, canvas.clientHeight);
      for (const side of ['base', 'head']) {
        const rect = rects[side];
        if (point.x >= rect.x && point.x <= rect.x + rect.w && point.y >= rect.y && point.y <= rect.y + rect.h) {
          return side;
        }
      }
      return null;
    }

    function graphViewport(side) {
      if (side && state.compareViewports[side]) return state.compareViewports[side];
      return state;
    }

    function setGraph(view) {
      state.view = view;
      setActiveViewButton();
      invalidateNodeDetailCache();
      if (view === 'compare') {
        if (!state.compare) {
          runCompare();
          return;
        }
        setCompareGraph();
        return;
      }
      const source = view === 'architecture' ? architectureGraphWithOverlay() : state.raw.commit_graph;
      const layoutScope = view === 'commits' ? 'commits' : 'architecture';
      state.allNodes = source.nodes.map((node, index) => positionedNode(node, index, layoutScope));
      state.allEdges = source.edges;
      state.hiddenNodeIds = new Set();
      state.selected = null;
      rebuildNodeIndex();
      rebuildGraphCache();
      prepareGraphLayoutForCurrentFilters(layoutScope, view === 'commits' ? 115 : 165);
      renderStats(state.raw.stats);
      applyFilters();
      renderFilterControls();
    }

    function architectureGraphWithOverlay() {
      const base = state.raw ? state.raw.component_graph : { nodes: [], edges: [] };
      const nodeIds = new Set(base.nodes.map(node => node.id));
      const nodes = [...base.nodes];
      for (const node of state.customNodes) {
        if (nodeIds.has(node.id)) continue;
        nodes.push(node);
        nodeIds.add(node.id);
      }
      const edges = [...base.edges, ...state.customEdges.filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target))];
      return { nodes, edges };
    }

    function normalizeComparePayload(payload) {
      const baseNodes = payload.base.graph.nodes.map((node, index) => positionedNode(node, index, 'architecture'));
      const headNodes = payload.head.graph.nodes.map((node, index) => positionedNode(node, index, 'architecture'));
      return {
        raw: payload,
        base: {
          ref: payload.base.ref,
          sha: payload.base.sha,
          allNodes: baseNodes,
          allEdges: payload.base.graph.edges,
          nodes: [],
          edges: []
        },
        head: {
          ref: payload.head.ref,
          sha: payload.head.sha,
          allNodes: headNodes,
          allEdges: payload.head.graph.edges,
          nodes: [],
          edges: []
        },
        summary: payload.summary
      };
    }

    function resetCompareViewports() {
      state.compareViewports = {
        base: { zoom: 1, panX: 0, panY: 0 },
        head: { zoom: 1, panX: 0, panY: 0 }
      };
    }

    function loadLayoutStore() {
      try {
        const raw = localStorage.getItem(LAYOUT_KEY);
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch (err) {
        return {};
      }
    }

    function saveLayoutStore() {
      try {
        localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.layoutStore));
      } catch (err) {
        // Layout stability is useful, but the map should keep working if storage is blocked.
      }
    }

    function layoutStorageKey(scope) {
      const repo = state.raw && state.raw.repo ? state.raw.repo.path || state.raw.repo.name : 'repo';
      return repo + '::' + (scope === 'commits' ? 'commits' : 'architecture');
    }

    function savedLayoutPosition(nodeId, scope) {
      const positions = state.layoutStore[layoutStorageKey(scope)] || {};
      const point = positions[nodeId];
      if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
      return { x: point.x, y: point.y };
    }

    function deterministicNodePosition(nodeId, index) {
      const hash = hashString(String(nodeId || index));
      const angle = ((hash % 3600) / 3600) * Math.PI * 2;
      const ring = 150 + ((hash >>> 4) % 11) * 38;
      return {
        x: Math.cos(angle) * ring,
        y: Math.sin(angle) * ring
      };
    }

    function hashString(value) {
      let hash = 2166136261;
      for (let i = 0; i < value.length; i += 1) {
        hash ^= value.charCodeAt(i);
        hash = Math.imul(hash, 16777619);
      }
      return hash >>> 0;
    }

    function positionedNode(node, index, scope) {
      const editableNode = applyNodeOverride(node);
      const saved = savedLayoutPosition(editableNode.id, scope || 'architecture');
      const point = saved || deterministicNodePosition(node.id, index);
      return {
        ...editableNode,
        x: point.x,
        y: point.y,
        vx: 0,
        vy: 0,
        _layoutSaved: Boolean(saved)
      };
    }

    function prepareStableLayout(nodes, edges, desiredDistance, scope) {
      if (!nodes.length) return;
      const savedCount = nodes.filter(node => node._layoutSaved).length;
      if (savedCount < Math.max(3, Math.floor(nodes.length * 0.65))) {
        settleGraphLayout(nodes, edges, desiredDistance);
      } else {
        placeNewNodesNearNeighbors(nodes, edges);
      }
      rememberLayoutPositions(nodes, scope);
    }

    function prepareCompareStableLayout() {
      if (!state.compare) return;
      const merged = mergeCompareNodes();
      const edges = [...state.compare.base.allEdges, ...state.compare.head.allEdges];
      prepareStableLayout(merged, edges, 165, 'architecture');
      const positions = new Map(merged.map(node => [node.id, { x: node.x, y: node.y }]));
      applyLayoutPositions(state.compare.base.allNodes, positions);
      applyLayoutPositions(state.compare.head.allNodes, positions);
    }

    function applyLayoutPositions(nodes, positions) {
      for (const node of nodes) {
        const point = positions.get(node.id);
        if (!point) continue;
        node.x = point.x;
        node.y = point.y;
        node.vx = 0;
        node.vy = 0;
        node._layoutSaved = true;
      }
    }

    function placeNewNodesNearNeighbors(nodes, edges) {
      const nodesById = new Map(nodes.map(node => [node.id, node]));
      const placed = new Set(nodes.filter(node => node._layoutSaved).map(node => node.id));
      for (const node of nodes) {
        if (node._layoutSaved) continue;
        const neighbors = [];
        for (const edge of edges) {
          if (edge.source === node.id && placed.has(edge.target)) neighbors.push(nodesById.get(edge.target));
          if (edge.target === node.id && placed.has(edge.source)) neighbors.push(nodesById.get(edge.source));
        }
        const usable = neighbors.filter(Boolean);
        if (usable.length) {
          const center = usable.reduce((acc, neighbor) => {
            acc.x += neighbor.x;
            acc.y += neighbor.y;
            return acc;
          }, { x: 0, y: 0 });
          center.x /= usable.length;
          center.y /= usable.length;
          const hash = hashString(node.id);
          const angle = ((hash % 3600) / 3600) * Math.PI * 2;
          const radius = 70 + ((hash >>> 7) % 5) * 24;
          node.x = center.x + Math.cos(angle) * radius;
          node.y = center.y + Math.sin(angle) * radius;
        }
        node.vx = 0;
        node.vy = 0;
        node._layoutSaved = true;
        placed.add(node.id);
      }
    }

    function rememberLayoutPositions(nodes, scope) {
      const key = layoutStorageKey(scope || 'architecture');
      const positions = state.layoutStore[key] || {};
      for (const node of nodes) {
        positions[node.id] = { x: Math.round(node.x * 100) / 100, y: Math.round(node.y * 100) / 100 };
        node._layoutSaved = true;
      }
      state.layoutStore[key] = positions;
      saveLayoutStore();
    }

    function settleGraphLayout(nodes, edges, desiredDistance) {
      if (!nodes.length) return;
      for (const node of nodes) {
        node.vx = 0;
        node.vy = 0;
      }
      const iterations = nodes.length > 1000 ? 12 : nodes.length > 600 ? 20 : nodes.length > 250 ? 35 : nodes.length > 120 ? 60 : 120;
      for (let i = 0; i < iterations; i++) {
        simulateGraph(nodes, edges, desiredDistance);
      }
      for (const node of nodes) {
        node.vx = 0;
        node.vy = 0;
      }
    }

    function setCompareGraph() {
      prepareCompareStableLayout();
      invalidateNodeDetailCache();
      state.allNodes = mergeCompareNodes();
      state.allEdges = [];
      state.hiddenNodeIds = new Set();
      state.selected = null;
      rebuildNodeIndex();
      rebuildGraphCache();
      renderCompareStats();
      applyFilters();
      renderFilterControls();
      applyPendingUrlState();
    }

    function mergeCompareNodes() {
      const merged = new Map();
      for (const node of [...state.compare.base.allNodes, ...state.compare.head.allNodes]) {
        const existing = merged.get(node.id);
        if (!existing || changeRank(node.change) > changeRank(existing.change)) {
          merged.set(node.id, node);
        }
      }
      return [...merged.values()].sort((a, b) => a.label.localeCompare(b.label));
    }

    function rebuildNodeIndex() {
      const index = new Map();
      const addNode = node => {
        if (node && node.id && !index.has(node.id)) index.set(node.id, node);
      };
      for (const node of state.allNodes || []) addNode(node);
      if (state.compare) {
        for (const node of state.compare.base.allNodes || []) addNode(node);
        for (const node of state.compare.head.allNodes || []) addNode(node);
      }
      state.nodeIndex = index;
    }

    function rebuildGraphCache() {
      const categoryByNodeId = new Map();
      const categoryCounts = Object.fromEntries(CATEGORY_FILTERS.map(category => [category.id, 0]));
      for (const node of allKnownNodes()) {
        const category = computeNodeCategory(node);
        categoryByNodeId.set(node.id, category);
        categoryCounts[category] = (categoryCounts[category] || 0) + 1;
      }
      state.graphCache.categoryByNodeId = categoryByNodeId;
      state.graphCache.categoryCounts = categoryCounts;
    }

    function allKnownNodes() {
      const nodes = new Map();
      for (const node of state.allNodes || []) nodes.set(node.id, node);
      if (state.compare) {
        for (const node of state.compare.base.allNodes || []) nodes.set(node.id, node);
        for (const node of state.compare.head.allNodes || []) nodes.set(node.id, node);
      }
      return [...nodes.values()];
    }

    function prepareGraphLayoutForCurrentFilters(scope, desiredDistance) {
      let layoutNodes = state.allNodes;
      let layoutEdges = state.allEdges;
      if (state.view !== 'commits') {
        const layoutIds = new Set();
        layoutNodes = state.allNodes.filter(node => {
          if (!isCategoryVisible(nodeCategory(node))) return false;
          if (state.hiddenNodeIds.has(node.id)) return false;
          layoutIds.add(node.id);
          return true;
        });
        layoutEdges = state.allEdges.filter(
          edge => layoutIds.has(edge.source) && layoutIds.has(edge.target) && isEdgeConnectionVisible(edge) && edgePassesScale(edge)
        );
        if (state.connectedOnly) {
          const endpoints = endpointIdsForEdges(layoutEdges);
          layoutNodes = layoutNodes.filter(node => endpoints.has(node.id));
        }
      }
      prepareStableLayout(layoutNodes.length ? layoutNodes : state.allNodes, layoutEdges, desiredDistance, scope);
    }

    function applyFilters() {
      if (state.view === 'compare') {
        applyCompareFilters();
        return;
      }
      if (requestGraphWorkerFilter()) return;
      applyArchitectureFiltersMainThread();
    }

    function applyArchitectureFiltersMainThread() {
      const startedAt = performance.now();
      state.lastFilterWorkerUsed = false;
      let visibleIds = new Set();
      const candidateNodes = [];
      for (const node of state.allNodes) {
        if (!isCategoryVisible(nodeCategory(node))) continue;
        if (state.hiddenNodeIds.has(node.id)) continue;
        visibleIds.add(node.id);
        candidateNodes.push(node);
      }
      const visibleEdges = state.allEdges.filter(
        edge => visibleIds.has(edge.source) && visibleIds.has(edge.target) && isEdgeConnectionVisible(edge) && edgePassesScale(edge)
      );
      const limited = applyGraphLimit(candidateNodes, visibleEdges, null);
      visibleIds = limited.visibleIds;
      state.nodes = limited.nodes;
      state.edges = limited.edges;
      state.visibleNodeIds = limited.visibleIds;
      state.graphCache.visibleNodesById = new Map(state.nodes.map(node => [node.id, node]));
      rebuildPinnedTraceCache();
      state.visibilityStatus = {
        view: 'architecture',
        totalNodes: state.allNodes.length,
        visibleNodes: state.nodes.length,
        totalEdges: state.allEdges.length,
        visibleEdges: state.edges.length,
        categoryHidden: state.allNodes.filter(node => !isCategoryVisible(nodeCategory(node))).length,
        manualHidden: state.allNodes.filter(node => isCategoryVisible(nodeCategory(node)) && state.hiddenNodeIds.has(node.id)).length,
        connectionHidden: limited.counts.connectionHidden,
        connectedHidden: limited.counts.connectedHidden,
        focusHidden: limited.counts.focusHidden,
        budgetHidden: limited.counts.budgetHidden
      };
      if (state.selected && state.selected.kind === 'node' && !visibleIds.has(state.selected.node.id)) {
        state.selected = null;
      }
      if (
        state.selected &&
        state.selected.kind === 'edge' &&
        (!visibleIds.has(state.selected.edge.source) || !visibleIds.has(state.selected.edge.target))
      ) {
        state.selected = null;
      }
      if (
        state.selected &&
        state.selected.kind === 'bundle' &&
        !state.selected.bundle.edges.some(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target))
      ) {
        state.selected = null;
      }
      invalidateNodeDetailCache();
      renderFilterSummary();
      renderSelection(state.selected);
      renderTopEdges();
      state.lastFilterMs = Math.round((performance.now() - startedAt) * 10) / 10;
      renderPerfPanel();
      scheduleUrlStateUpdate();
    }

    function requestGraphWorkerFilter() {
      if (!shouldUseGraphWorker()) return false;
      const worker = ensureGraphWorker();
      if (!worker) return false;
      const requestId = ++state.graphWorkerRequestId;
      state.graphWorkerInFlight = true;
      worker.postMessage(graphWorkerPayload(requestId));
      return true;
    }

    function shouldUseGraphWorker() {
      if (state.view !== 'architecture') return false;
      if (state.pinnedTrace) return false;
      if (!window.Worker || !window.Blob || !window.URL) return false;
      return state.allNodes.length > 220 || state.allEdges.length > 420;
    }

    function ensureGraphWorker() {
      if (state.graphWorker) return state.graphWorker;
      try {
        const blob = new Blob([graphWorkerSource()], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);
        const worker = new Worker(url);
        URL.revokeObjectURL(url);
        worker.onmessage = event => handleGraphWorkerResult(event.data);
        worker.onerror = () => {
          state.graphWorkerSupported = false;
          state.graphWorkerInFlight = false;
          if (state.graphWorker) state.graphWorker.terminate();
          state.graphWorker = null;
          applyArchitectureFiltersMainThread();
        };
        state.graphWorker = worker;
        state.graphWorkerSupported = true;
        return worker;
      } catch (err) {
        state.graphWorkerSupported = false;
        state.graphWorker = null;
        return null;
      }
    }

    function graphWorkerPayload(requestId) {
      const categories = {};
      for (const node of state.allNodes) categories[node.id] = nodeCategory(node);
      return {
        requestId,
        nodes: state.allNodes.map(node => ({
          id: node.id,
          label: node.label,
          type: node.type,
          size: node.size || 0,
          metrics: node.metrics || {},
          category: categories[node.id]
        })),
        edges: state.allEdges.map(edge => ({
          id: edge.id || edge.source + '->' + edge.target + ':' + edge.type,
          source: edge.source,
          target: edge.target,
          type: edge.type || '',
          weight: edge.weight || 1,
          examples: Array.isArray(edge.examples) ? edge.examples.length : 0,
          categories: [...edgeConnectionCategories(edge)],
          sourceLabel: labelForNode(edge.source),
          targetLabel: labelForNode(edge.target)
        })),
        categoryVisibility: state.categoryVisibility,
        connectionVisibility: state.connectionVisibility,
        hidden: [...state.hiddenNodeIds],
        connectedOnly: state.connectedOnly,
        minEdgeWeight: state.minEdgeWeight,
        focusSelection: state.focusSelection,
        focusSeeds: selectedFocusSeeds(null) ? [...selectedFocusSeeds(null)] : [],
        traceMode: state.traceMode,
        focusHops: state.focusHops,
        nodeBudget: state.nodeBudget
      };
    }

    function handleGraphWorkerResult(payload) {
      if (!payload || payload.requestId !== state.graphWorkerRequestId) return;
      if (!payload.ok) {
        state.graphWorkerInFlight = false;
        applyArchitectureFiltersMainThread();
        return;
      }
      state.graphWorkerInFlight = false;
      state.graphWorkerLastMs = payload.durationMs || null;
      state.lastFilterMs = payload.durationMs || null;
      state.lastFilterWorkerUsed = true;
      const nodeMap = new Map(state.allNodes.map(node => [node.id, node]));
      const edgeMap = new Map(state.allEdges.map(edge => [edge.id || edge.source + '->' + edge.target + ':' + edge.type, edge]));
      state.nodes = payload.nodeIds.map(id => nodeMap.get(id)).filter(Boolean);
      state.edges = payload.edgeIds.map(id => edgeMap.get(id)).filter(Boolean);
      state.visibleNodeIds = new Set(payload.nodeIds);
      state.graphCache.visibleNodesById = new Map(state.nodes.map(node => [node.id, node]));
      rebuildPinnedTraceCache();
      const counts = payload.counts || emptyLimitCounts();
      state.visibilityStatus = {
        view: 'architecture',
        totalNodes: state.allNodes.length,
        visibleNodes: state.nodes.length,
        totalEdges: state.allEdges.length,
        visibleEdges: state.edges.length,
        categoryHidden: payload.categoryHidden || 0,
        manualHidden: payload.manualHidden || 0,
        connectionHidden: counts.connectionHidden || 0,
        connectedHidden: counts.connectedHidden || 0,
        focusHidden: counts.focusHidden || 0,
        budgetHidden: counts.budgetHidden || 0
      };
      if (state.selected && state.selected.kind === 'node' && !state.visibleNodeIds.has(state.selected.node.id)) state.selected = null;
      if (state.selected && state.selected.kind === 'edge' && (!state.visibleNodeIds.has(state.selected.edge.source) || !state.visibleNodeIds.has(state.selected.edge.target))) state.selected = null;
      invalidateNodeDetailCache();
      renderFilterSummary();
      renderSelection(state.selected);
      renderTopEdges();
      renderPerfPanel();
      scheduleUrlStateUpdate();
    }

    function graphWorkerSource() {
      return `
        self.onmessage = event => {
          const started = Date.now();
          try {
            const data = event.data || {};
            const hidden = new Set(data.hidden || []);
            const categoryVisible = id => data.categoryVisibility[id] !== false;
            const connectionVisible = edge => (edge.categories || []).some(id => data.connectionVisibility[id] !== false);
            const edgePassesScale = edge => Number(edge.weight || 1) >= Number(data.minEdgeWeight || 1);
            const nodeById = new Map((data.nodes || []).map(node => [node.id, node]));
            let categoryHidden = 0;
            let manualHidden = 0;
            let nodes = [];
            for (const node of data.nodes || []) {
              if (!categoryVisible(node.category)) { categoryHidden += 1; continue; }
              if (hidden.has(node.id)) { manualHidden += 1; continue; }
              nodes.push(node);
            }
            let visibleIds = new Set(nodes.map(node => node.id));
            let edges = (data.edges || []).filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target) && connectionVisible(edge) && edgePassesScale(edge));
            const counts = { connectionHidden: 0, connectedHidden: 0, focusHidden: 0, budgetHidden: 0 };
            const connectionFiltersRestrictNodes = !['component', 'projects'].some(id => data.connectionVisibility[id] !== false);
            if (connectionFiltersRestrictNodes) {
              const before = nodes.length;
              const endpoints = endpointIds(edges);
              nodes = nodes.filter(node => endpoints.has(node.id));
              counts.connectionHidden = before - nodes.length;
            }
            if (data.connectedOnly) {
              const before = nodes.length;
              const endpoints = endpointIds(edges);
              nodes = nodes.filter(node => endpoints.has(node.id));
              counts.connectedHidden = before - nodes.length;
            }
            if (data.focusSelection && data.focusSeeds && data.focusSeeds.length) {
              const before = nodes.length;
              const trace = traceModeSubgraph(edges, new Set(data.focusSeeds), data.traceMode, data.focusHops);
              nodes = nodes.filter(node => trace.ids.has(node.id));
              edges = trace.edges.filter(edge => trace.ids.has(edge.source) && trace.ids.has(edge.target));
              counts.focusHidden = before - nodes.length;
            }
            if (data.nodeBudget > 0 && nodes.length > data.nodeBudget) {
              const before = nodes.length;
              const keepIds = topNodeIds(nodes, edges, data.nodeBudget, data.focusSeeds || []);
              nodes = nodes.filter(node => keepIds.has(node.id));
              edges = edges.filter(edge => keepIds.has(edge.source) && keepIds.has(edge.target));
              counts.budgetHidden = before - nodes.length;
            }
            self.postMessage({ ok: true, requestId: data.requestId, nodeIds: nodes.map(node => node.id), edgeIds: edges.map(edge => edge.id), counts, categoryHidden, manualHidden, durationMs: Date.now() - started });
          } catch (err) {
            self.postMessage({ ok: false, requestId: event.data && event.data.requestId, error: String(err && err.message || err) });
          }
        };
        function endpointIds(edges) {
          const ids = new Set();
          for (const edge of edges || []) { ids.add(edge.source); ids.add(edge.target); }
          return ids;
        }
        function expandNeighborhood(edges, seeds, hops) {
          const ids = new Set(seeds);
          let frontier = new Set(seeds);
          const depth = Math.max(1, Math.min(3, Number(hops || 1)));
          for (let step = 0; step < depth; step += 1) {
            const next = new Set();
            for (const edge of edges) {
              if (frontier.has(edge.source) && !ids.has(edge.target)) next.add(edge.target);
              if (frontier.has(edge.target) && !ids.has(edge.source)) next.add(edge.source);
            }
            for (const id of next) ids.add(id);
            frontier = next;
            if (!frontier.size) break;
          }
          return ids;
        }
        function traceModeSubgraph(edges, seeds, mode, hops) {
          if (!mode || mode === 'neighbors') {
            const ids = expandNeighborhood(edges, seeds, hops);
            return { ids, edges: edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
          }
          const filtered = edges.filter(edge => traceEdgeMatchesMode(edge, mode));
          if (mode === 'callers') return directionalTrace(filtered, seeds, hops, 'incoming');
          if (mode === 'callees') return directionalTrace(filtered, seeds, hops, 'outgoing');
          const ids = expandNeighborhood(filtered, seeds, hops);
          return { ids, edges: filtered.filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
        }
        function directionalTrace(edges, seeds, hops, direction) {
          const ids = new Set(seeds);
          const kept = [];
          let frontier = new Set(seeds);
          const depth = Math.max(1, Math.min(4, Number(hops || 1)));
          for (let step = 0; step < depth; step += 1) {
            const next = new Set();
            for (const edge of edges) {
              const matches = direction === 'incoming' ? frontier.has(edge.target) : frontier.has(edge.source);
              if (!matches) continue;
              const neighbor = direction === 'incoming' ? edge.source : edge.target;
              kept.push(edge);
              if (!ids.has(neighbor)) next.add(neighbor);
            }
            for (const id of next) ids.add(id);
            frontier = next;
            if (!frontier.size) break;
          }
          return { ids, edges: kept };
        }
        function traceEdgeMatchesMode(edge, mode) {
          const categories = new Set(edge.categories || []);
          if (mode === 'callers' || mode === 'callees') return categories.has('functions') || categories.has('api');
          if (mode === 'api') return categories.has('api') || categories.has('functions') || categories.has('graphql') || categories.has('projects');
          if (mode === 'data') return categories.has('database') || /db|sql|model|schema|store/i.test(edge.type || '');
          if (mode === 'tests') return categories.has('tests') || /test|spec/i.test((edge.sourceLabel || '') + ' ' + (edge.targetLabel || ''));
          if (mode === 'git') return categories.has('git');
          return true;
        }
        function topNodeIds(nodes, edges, budget, focusSeeds) {
          const scores = new Map(nodes.map(node => [node.id, 0]));
          for (const edge of edges) {
            const weight = Number(edge.weight || 1);
            scores.set(edge.source, (scores.get(edge.source) || 0) + weight);
            scores.set(edge.target, (scores.get(edge.target) || 0) + weight);
          }
          const keepIds = new Set(focusSeeds || []);
          const ranked = nodes.map(node => {
            const metrics = node.metrics || {};
            const metricScore = (Number(metrics.functions || 0) + Number(metrics.methods || 0) + Number(metrics.classes || 0)) * 0.2;
            const fileScore = Number(metrics.files || 0) * 0.6;
            const selectedScore = keepIds.has(node.id) ? 100000 : 0;
            return [node.id, (scores.get(node.id) || 0) + Number(node.size || 0) * 0.05 + metricScore + fileScore + selectedScore];
          }).sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
          for (const [id] of ranked) {
            if (keepIds.size >= budget) break;
            keepIds.add(id);
          }
          return keepIds;
        }
      `;
    }

    function applyCompareFilters() {
      filterCompareSide(state.compare.base, 'base');
      filterCompareSide(state.compare.head, 'head');
      if (state.selected && state.selected.kind === 'node') {
        const side = state.compare[state.selected.side];
        if (!side || !side.nodes.some(node => node.id === state.selected.node.id)) state.selected = null;
      }
      if (state.selected && state.selected.kind === 'edge') {
        const side = state.compare[state.selected.side];
        if (!side || !side.edges.some(edge => edge.id === state.selected.edge.id)) state.selected = null;
      }
      if (state.selected && state.selected.kind === 'bundle') {
        const side = state.compare[state.selected.side];
        if (!side || !state.selected.bundle.edges.some(edge => side.edges.some(visible => visible.id === edge.id))) {
          state.selected = null;
        }
      }
      state.nodes = [...state.compare.base.nodes, ...state.compare.head.nodes];
      state.edges = [...state.compare.base.edges, ...state.compare.head.edges];
      const allNodeIds = new Set([...state.compare.base.allNodes, ...state.compare.head.allNodes].map(node => node.id));
      const visibleNodeIds = new Set(state.nodes.map(node => node.id));
      state.visibleNodeIds = visibleNodeIds;
      state.graphCache.visibleNodesById = new Map(state.nodes.map(node => [node.id, node]));
      rebuildPinnedTraceCache();
      const mergedNodes = mergeCompareNodes();
      const baseCounts = state.compare.base.visibilityCounts || emptyLimitCounts();
      const headCounts = state.compare.head.visibilityCounts || emptyLimitCounts();
      state.visibilityStatus = {
        view: 'compare',
        totalNodes: allNodeIds.size,
        visibleNodes: visibleNodeIds.size,
        totalEdges: state.compare.base.allEdges.length + state.compare.head.allEdges.length,
        visibleEdges: state.compare.base.edges.length + state.compare.head.edges.length,
        categoryHidden: mergedNodes.filter(node => !isCategoryVisible(nodeCategory(node))).length,
        manualHidden: mergedNodes.filter(node => isCategoryVisible(nodeCategory(node)) && state.hiddenNodeIds.has(node.id)).length,
        connectionHidden: baseCounts.connectionHidden + headCounts.connectionHidden,
        connectedHidden: baseCounts.connectedHidden + headCounts.connectedHidden,
        focusHidden: baseCounts.focusHidden + headCounts.focusHidden,
        budgetHidden: baseCounts.budgetHidden + headCounts.budgetHidden,
        compareHidden: baseCounts.compareHidden + headCounts.compareHidden
      };
      invalidateNodeDetailCache();
      renderFilterSummary();
      renderSelection(state.selected);
      renderTopEdges();
      scheduleUrlStateUpdate();
    }

    function filterCompareSide(side, sideName) {
      let visibleIds = new Set();
      const candidateNodes = [];
      for (const node of side.allNodes) {
        if (!isCategoryVisible(nodeCategory(node))) continue;
        if (state.hiddenNodeIds.has(node.id)) continue;
        visibleIds.add(node.id);
        candidateNodes.push(node);
      }
      const visibleEdges = side.allEdges.filter(
        edge => visibleIds.has(edge.source) && visibleIds.has(edge.target) && isEdgeConnectionVisible(edge) && edgePassesScale(edge)
      );
      const changeGate = compareChangeGate(candidateNodes, visibleEdges);
      const limited = applyGraphLimit(changeGate.nodes, changeGate.edges, sideName);
      limited.counts.compareHidden = changeGate.hidden;
      side.nodes = limited.nodes;
      side.edges = limited.edges;
      side.visibilityCounts = limited.counts;
      side.nodesById = new Map(side.nodes.map(node => [node.id, node]));
    }

    function compareChangeGate(candidateNodes, visibleEdges) {
      if (!state.compareChangesOnly) return { nodes: candidateNodes, edges: visibleEdges, hidden: 0 };
      const keepIds = new Set();
      for (const node of candidateNodes) {
        if (hasChange(node)) keepIds.add(node.id);
      }
      const changedEdges = [];
      for (const edge of visibleEdges) {
        if (!hasChange(edge)) continue;
        changedEdges.push(edge);
        keepIds.add(edge.source);
        keepIds.add(edge.target);
      }
      if (!keepIds.size && !changedEdges.length) {
        if (compareHasAnyChanges()) return { nodes: [], edges: [], hidden: candidateNodes.length };
        return { nodes: candidateNodes, edges: visibleEdges, hidden: 0 };
      }
      const nodes = candidateNodes.filter(node => keepIds.has(node.id));
      const edges = changedEdges.filter(edge => keepIds.has(edge.source) && keepIds.has(edge.target));
      return { nodes, edges, hidden: Math.max(0, candidateNodes.length - nodes.length) };
    }

    function compareHasAnyChanges() {
      if (!state.compare || !state.compare.summary) return false;
      const summary = state.compare.summary;
      return Number(summary.added_nodes || 0) +
        Number(summary.removed_nodes || 0) +
        Number(summary.changed_nodes || 0) +
        Number(summary.added_edges || 0) +
        Number(summary.removed_edges || 0) +
        Number(summary.changed_edges || 0) > 0;
    }

    function applyGraphLimit(candidateNodes, visibleEdges, sideName) {
      let nodes = candidateNodes;
      let edges = visibleEdges;
      const counts = emptyLimitCounts();
      const pinnedTrace = pinnedTraceSubgraph(edges, sideName);
      let endpointCache = null;
      const currentEndpoints = () => {
        if (!endpointCache) endpointCache = endpointIdsForEdges(edges);
        return endpointCache;
      };
      if (connectionFiltersRestrictNodes()) {
        const before = nodes.length;
        const endpoints = currentEndpoints();
        nodes = nodes.filter(node => endpoints.has(node.id));
        counts.connectionHidden = before - nodes.length;
      }
      if (state.connectedOnly && state.view !== 'commits' && !(state.view === 'compare' && state.compareChangesOnly)) {
        const before = nodes.length;
        const endpoints = currentEndpoints();
        nodes = nodes.filter(node => endpoints.has(node.id));
        counts.connectedHidden = before - nodes.length;
      }
      const focusSeeds = selectedFocusSeeds(sideName);
      if (focusSeeds && focusSeeds.size) {
        const before = nodes.length;
        const trace = traceModeSubgraph(edges, focusSeeds, state.traceMode, state.focusHops);
        edges = trace.edges;
        const focusIds = trace.ids;
        if (pinnedTrace) {
          for (const id of pinnedTrace.ids) focusIds.add(id);
          edges = uniqueEdges([...edges, ...pinnedTrace.edges]);
        }
        nodes = nodes.filter(node => focusIds.has(node.id));
        edges = edges.filter(edge => focusIds.has(edge.source) && focusIds.has(edge.target));
        counts.focusHidden = before - nodes.length;
      }
      if (state.nodeBudget > 0 && nodes.length > state.nodeBudget) {
        const before = nodes.length;
        const keepIds = topNodeIds(nodes, edges, state.nodeBudget, sideName);
        nodes = nodes.filter(node => keepIds.has(node.id));
        edges = edges.filter(edge => keepIds.has(edge.source) && keepIds.has(edge.target));
        counts.budgetHidden = before - nodes.length;
      }
      return {
        nodes,
        edges,
        visibleIds: new Set(nodes.map(node => node.id)),
        counts
      };
    }

    function uniqueEdges(edges) {
      const map = new Map();
      for (const edge of edges || []) {
        map.set(edge.id || edge.source + '->' + edge.target + ':' + edge.type, edge);
      }
      return [...map.values()];
    }

    function emptyLimitCounts() {
      return {
        connectionHidden: 0,
        connectedHidden: 0,
        focusHidden: 0,
        budgetHidden: 0,
        compareHidden: 0
      };
    }

    function selectedFocusSeeds(sideName) {
      if (!state.focusSelection || !state.selected) return null;
      if (state.view === 'compare' && sideName && state.selected.side && state.selected.side !== sideName) return null;
      if (state.selected.kind === 'node') return new Set([state.selected.node.id]);
      if (state.selected.kind === 'edge') return new Set([state.selected.edge.source, state.selected.edge.target]);
      return null;
    }

    function expandNeighborhood(edges, seeds, hops) {
      const focusIds = new Set(seeds);
      let frontier = new Set(seeds);
      const depth = Math.max(1, Math.min(3, Number(hops || 1)));
      for (let step = 0; step < depth; step += 1) {
        const next = new Set();
        for (const edge of edges) {
          if (frontier.has(edge.source) && !focusIds.has(edge.target)) next.add(edge.target);
          if (frontier.has(edge.target) && !focusIds.has(edge.source)) next.add(edge.source);
        }
        for (const id of next) focusIds.add(id);
        frontier = next;
        if (!frontier.size) break;
      }
      return focusIds;
    }

    function traceModeSubgraph(edges, seeds, mode, hops) {
      if (!mode || mode === 'neighbors') {
        const ids = expandNeighborhood(edges, seeds, hops);
        return { ids, edges: edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
      }
      const filtered = edges.filter(edge => traceEdgeMatchesMode(edge, mode));
      if (mode === 'callers') return directionalTrace(filtered, seeds, hops, 'incoming');
      if (mode === 'callees') return directionalTrace(filtered, seeds, hops, 'outgoing');
      const ids = expandNeighborhood(filtered, seeds, hops);
      return { ids, edges: filtered.filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
    }

    function pinnedTraceSubgraph(edges, sideName) {
      if (!state.pinnedTrace) return null;
      if (state.view === 'compare' && state.pinnedTrace.side && sideName && state.pinnedTrace.side !== sideName) return null;
      if (state.view === 'compare' && state.pinnedTrace.side && !sideName) return null;
      const seeds = new Set([state.pinnedTrace.nodeId]);
      return traceModeSubgraph(edges || [], seeds, state.pinnedTrace.mode || 'neighbors', state.pinnedTrace.hops || 1);
    }

    function pinnedTraceSeeds(edges, sideName) {
      const trace = pinnedTraceSubgraph(edges, sideName);
      return trace ? trace.ids : null;
    }

    function rebuildPinnedTraceCache() {
      const keys = new Set();
      if (state.pinnedTrace) {
        if (state.view === 'compare' && state.compare) {
          for (const sideName of ['base', 'head']) {
            const trace = pinnedTraceSubgraph(state.compare[sideName].edges || [], sideName);
            if (!trace) continue;
            for (const edge of trace.edges) keys.add(edgeKeyForSide(edge, sideName));
          }
        } else {
          const trace = pinnedTraceSubgraph(state.edges || [], null);
          if (trace) {
            for (const edge of trace.edges) keys.add(edgeKeyForSide(edge, null));
          }
        }
      }
      state.graphCache.pinnedEdgeKeys = keys;
    }

    function edgeKeyForSide(edge, side) {
      return (side || 'main') + '::' + (edge.id || edge.source + '->' + edge.target + ':' + edge.type);
    }

    function isPinnedTraceEdge(edge, side) {
      return Boolean(state.graphCache && state.graphCache.pinnedEdgeKeys && state.graphCache.pinnedEdgeKeys.has(edgeKeyForSide(edge, side)));
    }

    function directionalTrace(edges, seeds, hops, direction) {
      const ids = new Set(seeds);
      const kept = [];
      let frontier = new Set(seeds);
      const depth = Math.max(1, Math.min(4, Number(hops || 1)));
      for (let step = 0; step < depth; step += 1) {
        const next = new Set();
        for (const edge of edges) {
          const matches = direction === 'incoming' ? frontier.has(edge.target) : frontier.has(edge.source);
          if (!matches) continue;
          const neighbor = direction === 'incoming' ? edge.source : edge.target;
          kept.push(edge);
          if (!ids.has(neighbor)) next.add(neighbor);
        }
        for (const id of next) ids.add(id);
        frontier = next;
        if (!frontier.size) break;
      }
      return { ids, edges: kept };
    }

    function traceEdgeMatchesMode(edge, mode) {
      const categories = edgeConnectionCategories(edge);
      if (mode === 'callers' || mode === 'callees') return categories.has('functions') || categories.has('api');
      if (mode === 'api') return categories.has('api') || categories.has('functions') || categories.has('graphql') || categories.has('projects');
      if (mode === 'data') return categories.has('database') || /db|sql|model|schema|store/i.test(edge.type || '');
      if (mode === 'tests') return categories.has('tests') || /test|spec/i.test(labelForNode(edge.source) + ' ' + labelForNode(edge.target));
      if (mode === 'git') return categories.has('git');
      return true;
    }

    function topNodeIds(nodes, edges, budget, sideName) {
      const scores = new Map(nodes.map(node => [node.id, 0]));
      for (const edge of edges) {
        const weight = edge.weight || 1;
        scores.set(edge.source, (scores.get(edge.source) || 0) + weight);
        scores.set(edge.target, (scores.get(edge.target) || 0) + weight);
      }
      const selectedIds = selectedAndPinnedSeeds(edges, sideName);
      const rankedNodes = [...nodes]
        .map(node => {
          const metrics = node.metrics || {};
          const sizeScore = Number(node.size || 0) * 0.05;
          const metricScore = (Number(metrics.functions || 0) + Number(metrics.methods || 0) + Number(metrics.classes || 0)) * 0.2;
          const fileScore = Number(metrics.files || 0) * 0.6;
          const selectedScore = selectedIds.has(node.id) ? 100000 : 0;
          return [node.id, (scores.get(node.id) || 0) + sizeScore + metricScore + fileScore + selectedScore];
        })
        .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
      const keepIds = new Set(selectedIds);
      const sortedEdges = [...edges]
        .filter(edge => scores.has(edge.source) && scores.has(edge.target))
        .sort((a, b) => edgeBudgetRank(b, scores) - edgeBudgetRank(a, scores) || String(a.id || '').localeCompare(String(b.id || '')));
      for (const edge of sortedEdges) {
        if (keepIds.size >= budget) break;
        const endpoints = [edge.source, edge.target];
        const missing = endpoints.filter(id => !keepIds.has(id));
        if (!missing.length) continue;
        if (keepIds.size + missing.length > budget) continue;
        for (const id of missing) keepIds.add(id);
      }
      for (const [id] of rankedNodes) {
        if (keepIds.size >= budget) break;
        keepIds.add(id);
      }
      return keepIds;
    }

    function selectedAndPinnedSeeds(edges, sideName) {
      const ids = new Set();
      for (const id of selectedFocusSeeds(sideName) || []) ids.add(id);
      for (const id of pinnedTraceSeeds(edges, sideName) || []) ids.add(id);
      return ids;
    }

    function edgeBudgetRank(edge, scores) {
      const typeBoost = {
        api_call: 80,
        function_call: 70,
        calls: 70,
        graphql: 65,
        database: 60,
        test_covers: 48,
        imports: 42,
        references: 38,
        inherits: 34,
        depends_on: 30,
        custom: 90
      }[String(edge.type || '').toLowerCase()] || 24;
      return (Number(edge.weight || 1) * 100) +
        typeBoost +
        ((scores.get(edge.source) || 0) + (scores.get(edge.target) || 0)) * 0.05;
    }

    function renderFilterControls() {
      renderCategoryFilters();
      renderConnectionFilters();
      renderComponentFilterList(false);
      renderFilterSummary();
    }

    function renderComponentFilterList(preserveScroll) {
      const root = document.getElementById('componentFilters');
      const previousScroll = preserveScroll ? root.scrollTop : 0;
      root.classList.add('virtual-filter-list');
      state.componentFilterNodes = [...state.allNodes]
        .sort((a, b) => a.label.localeCompare(b.label))
        .filter(node => !state.componentFilter || node.label.toLowerCase().includes(state.componentFilter));
      root.onscroll = scheduleComponentFilterWindow;
      renderComponentFilterWindow(previousScroll);
    }

    function scheduleComponentFilterWindow() {
      if (state.componentFilterScrollPending) return;
      state.componentFilterScrollPending = true;
      requestAnimationFrame(() => {
        state.componentFilterScrollPending = false;
        renderComponentFilterWindow();
      });
    }

    function renderComponentFilterWindow(forcedScrollTop) {
      const root = document.getElementById('componentFilters');
      const nodes = state.componentFilterNodes || [];
      const scrollTop = Number.isFinite(forcedScrollTop) ? forcedScrollTop : root.scrollTop;
      root.innerHTML = '';
      if (!nodes.length) {
        const empty = document.createElement('div');
        empty.className = 'filter-summary';
        empty.textContent = 'No components match the filter.';
        root.appendChild(empty);
        return;
      }
      const viewportHeight = root.clientHeight || 420;
      const totalHeight = nodes.length * COMPONENT_ROW_HEIGHT;
      const start = Math.max(0, Math.floor(scrollTop / COMPONENT_ROW_HEIGHT) - COMPONENT_OVERSCAN);
      const end = Math.min(nodes.length, Math.ceil((scrollTop + viewportHeight) / COMPONENT_ROW_HEIGHT) + COMPONENT_OVERSCAN);
      const spacer = document.createElement('div');
      spacer.className = 'virtual-filter-spacer';
      spacer.style.height = totalHeight + 'px';
      const windowEl = document.createElement('div');
      windowEl.className = 'virtual-filter-window';
      windowEl.style.transform = 'translateY(' + (start * COMPONENT_ROW_HEIGHT) + 'px)';
      for (const node of nodes.slice(start, end)) {
        windowEl.appendChild(componentFilterRow(node));
      }
      root.append(spacer, windowEl);
      root.scrollTop = Math.min(scrollTop, Math.max(0, totalHeight - viewportHeight));
    }

    function componentFilterRow(node) {
      const category = nodeCategory(node);
      const categoryHidden = !isCategoryVisible(category);
      const hiddenReason = nodeVisibilityReason(node, category);
      const row = document.createElement('label');
      row.className = 'filter-row' + (categoryHidden ? ' category-muted' : '');
      setHelp(row, 'This filter controls the "' + node.label + '" node. Category: ' + category + '. Visibility: ' + (hiddenReason || 'visible') + '. Check it to show this node; if its category is hidden, CodeAtlas turns that category back on.');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !state.hiddenNodeIds.has(node.id) && !categoryHidden;
      checkbox.onchange = () => {
        if (checkbox.checked) {
          if (categoryHidden) revealSingleNodeFromHiddenCategory(node, category);
          else state.hiddenNodeIds.delete(node.id);
        } else {
          state.hiddenNodeIds.add(node.id);
        }
        applyFilters();
        renderCategoryFilters();
        renderConnectionFilters();
        renderComponentFilterList(true);
        renderFilterSummary();
      };
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = nodeColor(node);
      const name = document.createElement('span');
      name.className = 'filter-name';
      name.textContent = node.label;
      const kind = document.createElement('span');
      kind.className = 'filter-kind';
      const stableKindText = category + ' / ' + (node.type || 'node');
      const fullKindText = stableKindText + (hiddenReason ? ' / ' + hiddenReason : '');
      kind.textContent = stableKindText;
      kind.title = fullKindText;
      row.append(checkbox, swatch, name, kind);
      return row;
    }

    function nodeCategoryTextLabel(category) {
      return {
        owned: 'owned',
        team: 'team dependency',
        third_party: 'third-party',
        docs_config: 'docs/config',
        tests: 'tests',
        generated: 'generated'
      }[category] || String(category || 'node');
    }

    function nodeVisibilityReason(node, category) {
      if (!isCategoryVisible(category)) return 'hidden: category filter';
      if (state.hiddenNodeIds.has(node.id)) return 'hidden: component checklist';
      if (!state.visibleNodeIds || state.visibleNodeIds.has(node.id)) return '';
      if (!nodeHasConnectionAfterLinkFilters(node)) return 'hidden: connection filters';
      if (state.connectedOnly && !nodeHasVisibleEndpointEdge(node)) return 'hidden: connected-only';
      if (state.focusSelection && !nodeIsInsideCurrentTrace(node)) return 'hidden: trace/focus mode';
      if (state.nodeBudget > 0) return 'hidden: node budget';
      return 'hidden: active filters';
    }

    function nodeHasConnectionAfterLinkFilters(node) {
      const category = nodeCategory(node);
      if (!isCategoryVisible(category) || state.hiddenNodeIds.has(node.id)) return false;
      return currentFilterEdges().some(edge =>
        (edge.source === node.id || edge.target === node.id) &&
        isEdgeConnectionVisible(edge) &&
        edgePassesScale(edge)
      );
    }

    function nodeHasVisibleEndpointEdge(node) {
      return currentFilterEdges().some(edge => {
        if (edge.source !== node.id && edge.target !== node.id) return false;
        const otherId = edge.source === node.id ? edge.target : edge.source;
        const other = nodeForId(otherId);
        if (!other) return false;
        return isCategoryVisible(nodeCategory(other)) &&
          !state.hiddenNodeIds.has(other.id) &&
          isEdgeConnectionVisible(edge) &&
          edgePassesScale(edge);
      });
    }

    function nodeIsInsideCurrentTrace(node) {
      const focusSeeds = selectedFocusSeeds(null);
      if (!focusSeeds || !focusSeeds.size) return true;
      const visibleEdges = currentFilterEdges().filter(edge =>
        isEdgeConnectionVisible(edge) &&
        edgePassesScale(edge)
      );
      return traceModeSubgraph(visibleEdges, focusSeeds, state.traceMode, state.focusHops).ids.has(node.id);
    }

    function revealSingleNodeFromHiddenCategory(node, category) {
      state.categoryVisibility[category] = true;
      for (const other of state.allNodes) {
        if (other.id !== node.id && nodeCategory(other) === category) {
          state.hiddenNodeIds.add(other.id);
        }
      }
      state.hiddenNodeIds.delete(node.id);
    }

    function renderConnectionFilters() {
      const root = document.getElementById('connectionFilters');
      root.innerHTML = '';
      const counts = connectionCounts();
      for (const connection of CONNECTION_FILTERS) {
        const row = document.createElement('label');
        row.className = 'filter-row category-row' + (isConnectionVisible(connection.id) ? '' : ' off');
        setHelp(row, connectionHelp(connection.id));
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = isConnectionVisible(connection.id);
        checkbox.onchange = () => {
          state.connectionVisibility[connection.id] = checkbox.checked;
          applyFilters();
          renderFilterControls();
        };
        const swatch = document.createElement('span');
        swatch.className = 'swatch';
        swatch.style.background = connection.color;
        const name = document.createElement('span');
        name.className = 'filter-name';
        name.textContent = connection.label;
        const count = document.createElement('span');
        count.className = 'filter-kind';
        count.textContent = String(counts[connection.id] || 0);
        row.append(checkbox, swatch, name, count);
        root.appendChild(row);
      }
    }

    function renderCategoryFilters() {
      const root = document.getElementById('categoryFilters');
      root.innerHTML = '';
      const counts = categoryCounts();
      for (const category of CATEGORY_FILTERS) {
        const row = document.createElement('label');
        row.className = 'filter-row category-row' + (isCategoryVisible(category.id) ? '' : ' off');
        setHelp(row, categoryHelp(category.id));
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = isCategoryVisible(category.id);
        checkbox.onchange = () => {
          state.categoryVisibility[category.id] = checkbox.checked;
          applyFilters();
          renderFilterControls();
        };
        const swatch = document.createElement('span');
        swatch.className = 'swatch';
        swatch.style.background = category.color;
        const name = document.createElement('span');
        name.className = 'filter-name';
        name.textContent = category.label;
        const count = document.createElement('span');
        count.className = 'filter-kind';
        count.textContent = String(counts[category.id] || 0);
        row.append(checkbox, swatch, name, count);
        root.appendChild(row);
      }
    }

    function categoryHelp(category) {
      return {
        owned: 'Owned nodes are components CodeAtlas found inside the repository and treats as product code.',
        team: 'Team dependencies are external modules that match owned, team, company, or explicit show-package prefixes from .codeatlas.yml.',
        third_party: 'Third-party nodes are external dependencies that are not classified as owned or team packages. They are hidden by default in most lenses.',
        docs_config: 'Docs/config nodes are documentation, config, setup, requirements, generated summaries, and common utility packages. They live outside the default architecture map.',
        tests: 'Tests are test/spec/validation nodes and their relationships.',
        generated: 'Generated nodes are build output, generated code, vendored folders, and dependency directories.'
      }[category] || 'Toggle this category to show or hide this kind of node in the architecture map.';
    }

    function connectionHelp(connection) {
      return {
        api: 'API calls show explicit API-style relationships. Use this to focus on service boundaries, client calls, and manually added API edges.',
        functions: 'Function calls show parsed call relationships, including method/function calls and manually added function_call edges.',
        graphql: 'GraphQL shows query, mutation, resolver, schema, or GraphQL-specific relationships when CodeAtlas can identify them.',
        database: 'Databases shows read/write or storage/model/data relationships, including manually added database edges.',
        component: 'Components shows static component dependency edges such as imports, references, inheritance, and depends_on links. When this is on, CodeAtlas keeps matching component nodes visible even if other connection filters are off.',
        projects: 'Projects/repos highlights relationships that cross into external dependencies, services, repos, or project-like nodes. Turn it off to hide project-like nodes when they have no other visible context; it does not delete component/function edges by itself.',
        tests: 'Tests shows test coverage and validation relationships between tests and the code they exercise.',
        git: 'Git history shows co-change, authored, and touched relationships inferred from commits rather than direct code calls.',
        custom: 'Custom overlay shows manually added architecture connections.'
      }[connection] || 'Toggle this connection type to show or hide matching edges in the architecture map.';
    }

    function categoryCounts() {
      if (state.graphCache && state.graphCache.categoryCounts) return { ...state.graphCache.categoryCounts };
      return Object.fromEntries(CATEGORY_FILTERS.map(category => [category.id, 0]));
    }

    function connectionCounts() {
      const counts = {};
      for (const connection of CONNECTION_FILTERS) counts[connection.id] = 0;
      for (const edge of currentFilterEdges()) {
        for (const category of edgeConnectionCategories(edge)) {
          counts[category] = (counts[category] || 0) + 1;
        }
      }
      return counts;
    }

    function currentFilterEdges() {
      if (state.view === 'compare' && state.compare) {
        return [...state.compare.base.allEdges, ...state.compare.head.allEdges];
      }
      return state.allEdges;
    }

    function renderFilterSummary() {
      const status = state.visibilityStatus || {
        totalNodes: state.allNodes.length,
        visibleNodes: state.view === 'compare' ? new Set(state.nodes.map(node => node.id)).size : state.nodes.length,
        totalEdges: state.allEdges.length,
        visibleEdges: state.edges.length,
        categoryHidden: 0,
        manualHidden: 0,
        connectionHidden: 0,
        connectedHidden: 0,
        focusHidden: 0,
        budgetHidden: 0,
        compareHidden: 0
      };
      const activeCategories = CATEGORY_FILTERS
        .filter(category => isCategoryVisible(category.id))
        .map(category => category.label.toLowerCase())
        .join(', ') || 'none';
      const activeConnections = CONNECTION_FILTERS
        .filter(connection => isConnectionVisible(connection.id))
        .map(connection => connection.label.toLowerCase())
        .join(', ') || 'none';
      const scale = 'min w: ' + state.minEdgeWeight + (state.nodeBudget ? ' | top ' + state.nodeBudget : '') + (state.connectedOnly ? ' | connected only' : '');
      const focus = state.focusSelection ? ' | focus: ' + state.focusHops + ' hop' + (state.focusHops === 1 ? '' : 's') : '';
      document.getElementById('filterSummary').textContent =
        formatCount(status.visibleNodes) + ' of ' + formatCount(status.totalNodes) + ' nodes visible | links: ' + activeConnections + ' | ' + scale + focus;
      renderMapStatus(status, activeCategories, activeConnections);
      updateFocusBreadcrumb();
    }

    function renderMapStatus(status, activeCategories, activeConnections) {
      const root = document.getElementById('mapStatusPanel');
      if (!root) return;
      root.innerHTML = '';
      const hiddenNodes = Math.max(0, (status.totalNodes || 0) - (status.visibleNodes || 0));
      const hiddenEdges = Math.max(0, (status.totalEdges || 0) - (status.visibleEdges || 0));
      const title = document.createElement('div');
      title.className = 'map-status-title';
      const label = document.createElement('span');
      label.textContent = 'View status';
      const visible = document.createElement('strong');
      visible.textContent = formatCount(status.visibleNodes) + ' / ' + formatCount(status.totalNodes) + ' nodes';
      title.append(label, visible);
      root.appendChild(title);

      const grid = document.createElement('div');
      grid.className = 'map-status-grid';
      appendMapMetric(grid, 'Lens', LENS_LABELS[state.activeLens] || state.activeLens || 'Custom');
      appendMapMetric(grid, 'Budget', state.nodeBudget ? 'Top ' + formatCount(state.nodeBudget) : 'All nodes');
      appendMapMetric(grid, 'Connected', state.connectedOnly ? 'Only' : 'All');
      if (state.view === 'compare') appendMapMetric(grid, 'Compare', state.compareChangesOnly ? 'Changes' : 'Context');
      appendMapMetric(grid, 'Hidden nodes', formatCount(hiddenNodes));
      appendMapMetric(grid, 'Hidden links', formatCount(hiddenEdges));
      root.appendChild(grid);

      const reasons = visibilityReasonLines(status);
      const note = document.createElement('div');
      note.className = 'map-status-note' + (hiddenNodes ? ' warning' : '');
      note.textContent = reasons.length ? reasons.join(' ') : 'Full graph is visible for the current lens.';
      root.appendChild(note);

      const detail = document.createElement('div');
      detail.className = 'map-status-note';
      detail.textContent = 'Nodes: ' + activeCategories + '. Links: ' + activeConnections + '.';
      root.appendChild(detail);
      if (state.performanceGuardActive) {
        const guard = document.createElement('div');
        guard.className = 'map-status-note warning';
        guard.textContent = 'Large graph guard active: bundled edges, connected-only nodes, and a capped node budget are keeping the map responsive.';
        root.appendChild(guard);
      }
      if (state.pinnedTrace) {
        const pinned = document.createElement('div');
        pinned.className = 'map-status-note pinned';
        pinned.textContent = 'Pinned trace: ' + labelForNode(state.pinnedTrace.nodeId) + ' / ' + (state.pinnedTrace.mode || 'neighbors') + '.';
        root.appendChild(pinned);
      }
    }

    function updateEmptyMapOverlay(force) {
      const overlay = document.getElementById('emptyMapOverlay');
      if (!overlay) return;
      const empty = currentEmptyMapState();
      if (!empty) {
        state.lastEmptyMapSignature = '';
        overlay.hidden = true;
        return;
      }
      const signature = JSON.stringify(empty);
      if (!force && signature === state.lastEmptyMapSignature) return;
      state.lastEmptyMapSignature = signature;
      document.getElementById('emptyMapTitle').textContent = empty.title;
      document.getElementById('emptyMapBody').textContent = empty.body;
      const reasons = document.getElementById('emptyMapReasons');
      reasons.innerHTML = '';
      for (const reason of empty.reasons) {
        const item = document.createElement('div');
        item.textContent = reason;
        reasons.appendChild(item);
      }
      document.getElementById('emptyMapResetBtn').hidden = !empty.showReset;
      document.getElementById('emptyMapShowAllBtn').hidden = !empty.showAll;
      document.getElementById('emptyMapRefreshBtn').hidden = !empty.showRefresh;
      overlay.hidden = false;
    }

    function currentEmptyMapState() {
      if (state.graphLoadError) {
        return {
          title: 'Failed to load graph',
          body: 'The local UI could not read graph data from the CodeAtlas server.',
          reasons: [state.graphLoadError],
          showReset: false,
          showAll: false,
          showRefresh: true
        };
      }
      if (!state.raw) return null;
      const status = state.visibilityStatus || {};
      const totalNodes = Number(status.totalNodes || state.allNodes.length || 0);
      const visibleNodes = Number(status.visibleNodes || state.nodes.length || 0);
      if (totalNodes === 0) {
        return {
          title: 'No graph data yet',
          body: 'CodeAtlas did not receive any component nodes for this repository.',
          reasons: ['Refresh the index, or check Index Quality Diagnostics for parser and scanner issues.'],
          showReset: false,
          showAll: false,
          showRefresh: true
        };
      }
      if (visibleNodes > 0 || state.graphWorkerInFlight) return null;
      const reasons = visibilityReasonLines(status);
      if (state.search) reasons.push('Canvas search is filtering labels by "' + state.search + '".');
      if (state.focusSelection && !state.selected) reasons.push('Focus mode is on but the selected item is no longer visible.');
      if (!reasons.length) reasons.push('The current lens and filters hide every node.');
      return {
        title: 'No visible nodes',
        body: 'The graph exists, but the current view settings filtered all nodes out.',
        reasons: reasons.slice(0, 5),
        showReset: true,
        showAll: true,
        showRefresh: true
      };
    }

    function appendMapMetric(parent, label, value) {
      const item = document.createElement('div');
      item.className = 'map-status-metric';
      const name = document.createElement('span');
      name.textContent = label;
      const number = document.createElement('strong');
      number.textContent = String(value);
      item.append(name, number);
      parent.appendChild(item);
    }

    function visibilityReasonLines(status) {
      const lines = [];
      if ((status.categoryHidden || 0) > 0) lines.push(formatCount(status.categoryHidden) + ' hidden by category filters.');
      if ((status.manualHidden || 0) > 0) lines.push(formatCount(status.manualHidden) + ' hidden in the component checklist.');
      if ((status.connectionHidden || 0) > 0) lines.push(formatCount(status.connectionHidden) + ' hidden because selected link types have no matching edges.');
      if ((status.compareHidden || 0) > 0) lines.push(formatCount(status.compareHidden) + ' unchanged nodes hidden by compare Changes mode.');
      if ((status.connectedHidden || 0) > 0) lines.push(formatCount(status.connectedHidden) + ' hidden because Connected only removes isolated nodes.');
      if ((status.focusHidden || 0) > 0) lines.push(formatCount(status.focusHidden) + ' hidden by trace/focus mode.');
      if ((status.budgetHidden || 0) > 0) lines.push(formatCount(status.budgetHidden) + ' hidden by the node budget.');
      if (!lines.length && (status.totalNodes || 0) > (status.visibleNodes || 0)) {
        lines.push(formatCount((status.totalNodes || 0) - (status.visibleNodes || 0)) + ' hidden by the active map filters.');
      }
      return lines;
    }

    function formatCount(value) {
      return Number(value || 0).toLocaleString();
    }

    function isCommonNode(node) {
      if (!node) return false;
      const text = nodeCategoryText(node);
      return packageListed(text, state.classification.hide_packages) ||
        COMMON_NODE_IDS.has(text.id) ||
        COMMON_NODE_IDS.has(text.label) ||
        COMMON_NODE_PATTERNS.some(pattern => pattern.test(text.id) || pattern.test(text.label));
    }

    function nodeCategory(node) {
      if (!node) return 'owned';
      const cached = state.graphCache && state.graphCache.categoryByNodeId
        ? state.graphCache.categoryByNodeId.get(node.id)
        : null;
      return cached || computeNodeCategory(node);
    }

    function computeNodeCategory(node) {
      if (!node) return 'owned';
      const text = nodeCategoryText(node);
      if (packageListed(text, state.classification.owned_prefixes) || matchesAnyPrefix(text, state.classification.owned_prefixes)) return 'owned';
      if (packageListed(text, state.classification.third_party_packages)) return 'third_party';
      if (packageListed(text, state.classification.show_packages)) return 'team';
      if (isGeneratedNode(node)) return 'generated';
      if (isDocsConfigNode(node)) return 'docs_config';
      if (isTestNode(node)) return 'tests';
      if (isExternalNode(node)) return isTeamDependencyNode(node) ? 'team' : 'third_party';
      return 'owned';
    }

    function nodeCategoryText(node) {
      const id = String(node.id || '').replace(/^component:/, '').toLowerCase();
      const label = String(node.label || '').toLowerCase();
      const path = String(node.file_path || node.path || '').toLowerCase();
      return { id, label, path, combined: [id, label, path].filter(Boolean).join(' ') };
    }

    function isExternalNode(node) {
      return node.type === 'external' || (node.tags || []).includes('external');
    }

    function isDocsConfigNode(node) {
      return isCommonNode(node);
    }

    function isGeneratedNode(node) {
      const text = nodeCategoryText(node);
      return /(^|[\/._-])(generated|dist|build|coverage|node_modules|vendor)($|[\/._-])/.test(text.combined);
    }

    function isTestNode(node) {
      const text = nodeCategoryText(node);
      return node.type === 'test' || /(^|[\/._-])(__tests__|tests?|specs?)($|[\/._-])/.test(text.combined);
    }

    function isTeamDependencyNode(node) {
      const text = nodeCategoryText(node);
      if (packageListed(text, state.classification.show_packages)) return true;
      if (packageListed(text, state.classification.hide_packages)) return false;
      const prefixes = [
        ...DEFAULT_TEAM_PREFIXES,
        ...state.classification.owned_prefixes,
        ...state.classification.team_prefixes,
        ...state.classification.company_prefixes
      ];
      return matchesAnyPrefix(text, prefixes);
    }

    function packageListed(text, items) {
      return (items || []).some(item => {
        const clean = String(item || '').trim().toLowerCase();
        return clean && (text.id === clean || text.label === clean || text.id.startsWith(clean + '.') || text.label.startsWith(clean + '.'));
      });
    }

    function matchesPrefix(text, prefix) {
      const clean = String(prefix || '').trim().toLowerCase();
      if (!clean) return false;
      return text.id.startsWith(clean) || text.label.startsWith(clean);
    }

    function matchesAnyPrefix(text, prefixes) {
      return (prefixes || []).some(prefix => matchesPrefix(text, prefix));
    }

    function isServiceNode(node) {
      if (!node) return false;
      return node.type === 'service' || (node.tags || []).includes('service');
    }

    function isCategoryVisible(category) {
      return state.categoryVisibility[category] !== false;
    }

    function isConnectionVisible(connection) {
      return state.connectionVisibility[connection] !== false;
    }

    function isEdgeConnectionVisible(edge) {
      if (state.view === 'commits') return true;
      const categories = edgePrimaryConnectionCategories(edge);
      return categories.some(category => isConnectionVisible(category));
    }

    function edgePassesScale(edge) {
      return Number(edge.weight || 1) >= state.minEdgeWeight;
    }

    function connectionFiltersFocused() {
      if (state.view === 'commits') return false;
      return CONNECTION_FILTERS.some(connection => !isConnectionVisible(connection.id));
    }

    function connectionFiltersRestrictNodes() {
      if (state.view === 'commits') return false;
      if (isConnectionVisible('component')) return false;
      return connectionFiltersFocused();
    }

    function endpointIdsForEdges(edges) {
      const ids = new Set();
      for (const edge of edges) {
        ids.add(edge.source);
        ids.add(edge.target);
      }
      return ids;
    }

    function edgeConnectionCategories(edge) {
      const categories = new Set(edgePrimaryConnectionCategories(edge));
      if (edgeCrossesProjectBoundary(edge)) categories.add('projects');
      return [...categories];
    }

    function edgePrimaryConnectionCategories(edge) {
      const type = String(edge.type || '').toLowerCase();
      const categories = new Set();
      if (type === 'api_call') categories.add('api');
      if (type === 'calls' || type === 'function_call') categories.add('functions');
      if (type === 'graphql') categories.add('graphql');
      if (type === 'database') categories.add('database');
      if (type === 'test_covers') categories.add('tests');
      if (['cochange', 'authored', 'touched'].includes(type)) categories.add('git');
      if (type === 'custom') categories.add('custom');
      if (['imports', 'references', 'inherits', 'depends_on'].includes(type)) categories.add('component');
      if (!categories.size) categories.add('component');
      return [...categories];
    }

    function edgeCrossesProjectBoundary(edge) {
      const source = nodeForId(edge.source);
      const target = nodeForId(edge.target);
      return ['team', 'third_party'].includes(nodeCategory(source)) ||
        ['team', 'third_party'].includes(nodeCategory(target));
    }

    function nodeForId(id) {
      if (!id) return null;
      return state.nodeIndex.get(id) || null;
    }

    function setAllCategoryVisibility(visible) {
      for (const category of CATEGORY_FILTERS) {
        state.categoryVisibility[category.id] = visible;
      }
    }

    function setAllConnectionVisibility(visible) {
      for (const connection of CONNECTION_FILTERS) {
        state.connectionVisibility[connection.id] = visible;
      }
    }

    function setCategoryVisibilitySet(ids) {
      const visibleIds = new Set(ids);
      for (const category of CATEGORY_FILTERS) {
        state.categoryVisibility[category.id] = visibleIds.has(category.id);
      }
    }

    function setConnectionVisibilitySet(ids) {
      const visibleIds = new Set(ids);
      for (const connection of CONNECTION_FILTERS) {
        state.connectionVisibility[connection.id] = visibleIds.has(connection.id);
      }
    }

    function renderStats(stats) {
      const root = document.getElementById('stats');
      root.innerHTML = '';
      const cards = [
        { key: 'files', label: 'Files', value: stats.files },
        { key: 'symbols', label: 'Symbols', value: stats.symbols },
        { key: 'components', label: 'Components', value: stats.components },
        { key: 'edges', label: 'Edges', value: stats.component_edges },
        { key: 'commits', label: 'Commits', value: stats.commits }
      ];
      for (const card of cards) {
        root.appendChild(statCard(card, () => selectStat(card.key)));
      }
      updateStatCardActive();
    }

    function renderCompareStats() {
      const summary = state.compare.summary;
      const root = document.getElementById('stats');
      root.innerHTML = '';
      for (const [label, value] of Object.entries({
        Added: summary.added_nodes + summary.added_edges,
        Removed: summary.removed_nodes + summary.removed_edges,
        Changed: summary.changed_nodes + summary.changed_edges,
        Nodes: summary.added_nodes + summary.removed_nodes + summary.changed_nodes,
        Edges: summary.added_edges + summary.removed_edges + summary.changed_edges
      })) {
        const div = document.createElement('div');
        div.className = 'stat';
        div.innerHTML = '<span>' + label + '</span><strong>' + value + '</strong>';
        root.appendChild(div);
      }
    }

    function statCard(card, onClick) {
      const div = document.createElement('div');
      div.className = 'stat clickable';
      div.dataset.stat = card.key;
      div.setAttribute('role', 'button');
      div.tabIndex = 0;
      setHelp(div, statHelp(card.key));
      div.innerHTML = '<span>' + card.label + '</span><strong>' + card.value + '</strong>';
      div.onclick = onClick;
      div.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      };
      return div;
    }

    function statHelp(kind) {
      return {
        files: 'Files are source or project files CodeAtlas indexed. Click to list file paths, owning components, language, line count, and size.',
        symbols: 'Symbols are parsed classes, functions, methods, and other named code objects. Click to inspect where they live and their signatures.',
        components: 'Components are top-level modules, folders, services, external libraries, or custom vertices. Click to list the visible architecture nodes.',
        edges: 'Edges are relationships between components. They can come from code calls/imports/references, test coverage, or git history such as co-change.',
        commits: 'Commits are git history entries included in this map. They help explain ownership, risk, and co-change relationships.'
      }[kind] || 'Click to inspect the indexed items behind this count.';
    }

    function selectStat(kind) {
      state.selected = { kind: 'stat', stat: kind };
      if (!state.inventoryLimits[kind]) state.inventoryLimits[kind] = 120;
      updateStatCardActive();
      renderSelection(state.selected);
    }

    function updateStatCardActive() {
      document.querySelectorAll('.stat[data-stat]').forEach(card => {
        card.classList.toggle(
          'active',
          state.selected && state.selected.kind === 'stat' && state.selected.stat === card.dataset.stat
        );
      });
    }

    function compareSummaryText(summary) {
      const text = [
        summary.added_nodes + summary.added_edges + ' added',
        summary.removed_nodes + summary.removed_edges + ' removed',
        summary.changed_nodes + summary.changed_edges + ' changed'
      ].join(' / ');
      if (!summary.cache) return text;
      return text + ' / cache ' + summary.cache.base + '-' + summary.cache.head;
    }

    function explainCompareDiff() {
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      if (!state.compare) {
        status.textContent = 'Run compare first';
        status.classList.add('error-text');
        return;
      }
      status.classList.remove('error-text');
      status.textContent = 'Compare brief ready';
      answerRoot.classList.remove('workflow-mode');
      answerRoot.textContent = compareDiffBriefMarkdown();
      sourcesRoot.innerHTML = '';
      document.getElementById('chatQuestion').value = 'Explain the compare diff from ' + state.compare.base.ref + ' to ' + state.compare.head.ref;
    }

    function compareDiffBriefMarkdown() {
      const summary = state.compare.summary || {};
      const items = compareImpactItems().slice(0, 8);
      const lines = [
        '# Compare Brief',
        '',
        'Base: ' + state.compare.base.ref + ' (' + shortSha(state.compare.base.sha) + ')',
        'Head: ' + state.compare.head.ref + ' (' + shortSha(state.compare.head.sha) + ')',
        '',
        'Summary: ' + compareSummaryText(summary),
        '',
        'Recommended reading order:'
      ];
      if (!items.length) {
        lines.push('- No visible changed items match the current filters.');
      } else {
        for (const item of items) lines.push('- ' + item.title + ' — ' + item.meta);
      }
      lines.push('', 'Suggested checks:');
      lines.push('- Inspect Before / After cards for changed nodes and edges.');
      lines.push('- Keep Changes mode on first, then toggle Context when you need surrounding unchanged architecture.');
      lines.push('- Run tests near the highest-impact changed nodes and any API/data-flow edges.');
      return lines.join('\n');
    }

    function tick() {
      const frameStartedAt = performance.now();
      resize();
      const drawStartedAt = performance.now();
      draw();
      state.lastDrawMs = Math.round((performance.now() - drawStartedAt) * 10) / 10;
      state.lastFrameMs = Math.round((performance.now() - frameStartedAt) * 10) / 10;
      maybeRenderPerfPanel();
      requestAnimationFrame(tick);
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    }

    function simulateGraph(nodes, edges, desiredDistance) {
      const nodesById = new Map(nodes.map(node => [node.id, node]));
      for (const node of nodes) {
        node.vx += -node.x * 0.0008;
        node.vy += -node.y * 0.0008;
      }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const distSq = Math.max(120, dx * dx + dy * dy);
          const dist = Math.sqrt(distSq);
          const force = 2400 / distSq;
          a.vx += dx / dist * force; a.vy += dy / dist * force;
          b.vx -= dx / dist * force; b.vy -= dy / dist * force;
        }
      }
      for (const edge of edges) {
        const a = nodesById.get(edge.source), b = nodesById.get(edge.target);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        const weight = Math.min(3, Math.log2((edge.weight || 1) + 1));
        const force = clamp((dist - desiredDistance) * 0.014 * weight, -4, 4);
        a.vx += dx / dist * force; a.vy += dy / dist * force;
        b.vx -= dx / dist * force; b.vy -= dy / dist * force;
      }
      for (const node of nodes) {
        node.vx = clamp(node.vx * 0.82, -10, 10);
        node.vy = clamp(node.vy * 0.82, -10, 10);
        node.x = clamp(node.x + node.vx, -1200, 1200);
        node.y = clamp(node.y + node.vy, -1200, 1200);
      }
    }

    function draw() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      state.minimapRects.clear();
      if (state.view === 'compare' && state.compare) {
        drawCompare(w, h);
        updateEmptyMapOverlay();
        updateLegendLayout(false);
        return;
      }
      if (!state.nodes.length) {
        updateEmptyMapOverlay();
        updateLegendLayout(false);
        return;
      }
      drawGraphPanel(state.nodes, state.edges, { x: 0, y: 0, w, h }, null);
      ctx.globalAlpha = 1;
      updateEmptyMapOverlay();
      updateLegendLayout(false);
    }

    function drawCompare(w, h) {
      const rects = compareRects(w, h);
      const left = rects.base;
      const right = rects.head;
      ctx.fillStyle = shouldHighlightDiff() ? 'rgba(239, 123, 123, .09)' : 'rgba(32, 36, 45, .28)';
      ctx.fillRect(0, 0, w, 46);
      ctx.strokeStyle = '#343a46';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(rects.dividerX, 0);
      ctx.lineTo(rects.dividerX, h);
      ctx.stroke();
      drawComparePaneHeader(left, 'base');
      drawComparePaneHeader(right, 'head');
      drawCompareTimeline(rects);
      drawGraphPanel(state.compare.base.nodes, state.compare.base.edges, left, null, 'base');
      drawGraphPanel(state.compare.head.nodes, state.compare.head.edges, right, null, 'head');
    }

    function drawComparePaneHeader(rect, side) {
      const panel = state.compare && state.compare[side];
      if (!panel) return;
      const label = side === 'base' ? 'Before' : 'After';
      const ref = panel.ref || shortSha(panel.sha);
      ctx.save();
      ctx.fillStyle = 'rgba(18, 21, 27, .74)';
      ctx.strokeStyle = side === 'base' ? 'rgba(182, 156, 255, .42)' : 'rgba(34, 211, 238, .42)';
      ctx.lineWidth = 1;
      roundedRect(rect.x + 12, rect.y + 10, Math.min(270, rect.w - 24), 28, 8);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#eef1f5';
      ctx.font = '12px system-ui';
      ctx.textAlign = 'left';
      ctx.fillText(label + ': ' + truncateText(ref, 24), rect.x + 22, rect.y + 29);
      ctx.fillStyle = state.compareChangesOnly ? '#ef7b7b' : '#9ba7b6';
      ctx.textAlign = 'right';
      ctx.fillText(state.compareChangesOnly ? 'changes' : 'context', rect.x + Math.min(270, rect.w - 24) + 2, rect.y + 29);
      ctx.restore();
    }

    function drawCompareTimeline(rects) {
      const y = 24;
      const leftX = rects.dividerX - 48;
      const rightX = rects.dividerX + 48;
      ctx.save();
      ctx.strokeStyle = 'rgba(155, 167, 182, .44)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(rightX, y);
      ctx.stroke();
      ctx.fillStyle = '#b69cff';
      ctx.beginPath();
      ctx.arc(leftX, y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#22d3ee';
      ctx.beginPath();
      ctx.arc(rightX, y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    function compareRects(w, h) {
      const gap = 18;
      const leftWidth = Math.floor((w - gap) / 2);
      return {
        base: { x: 0, y: 0, w: leftWidth, h },
        head: { x: leftWidth + gap, y: 0, w: w - leftWidth - gap, h },
        dividerX: leftWidth + gap / 2
      };
    }

    function drawGraphPanel(nodes, edges, rect, label, side) {
      withGraphPanelClip(rect, () => drawClippedGraphPanel(nodes, edges, rect, label, side));
      drawMinimapOverlay(nodes, rect, side);
    }

    function withGraphPanelClip(rect, drawFn) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(rect.x, rect.y, rect.w, rect.h);
      ctx.clip();
      drawFn();
      ctx.restore();
      ctx.globalAlpha = 1;
    }

    function drawMinimapOverlay(nodes, rect, side) {
      if (!nodes.length || rect.w < 420 || rect.h < 300) return;
      const frameNodes = layoutFrameNodes(nodes, side);
      if (!frameNodes || frameNodes.length < 18) return;
      const bounds = boundsForNodes(frameNodes);
      if (!bounds) return;
      const size = Math.min(150, Math.max(92, Math.min(rect.w, rect.h) * 0.18));
      const bottomInset = minimapBottomInset(rect, size, side);
      const mini = {
        x: rect.x + rect.w - size - 16,
        y: rect.y + rect.h - size - 16 - bottomInset,
        w: size,
        h: size
      };
      const spanX = Math.max(1, bounds.maxX - bounds.minX);
      const spanY = Math.max(1, bounds.maxY - bounds.minY);
      const scale = Math.min((mini.w - 16) / spanX, (mini.h - 16) / spanY);
      const offsetX = mini.x + mini.w / 2 - ((bounds.minX + bounds.maxX) / 2 - bounds.minX) * scale;
      const offsetY = mini.y + mini.h / 2 - ((bounds.minY + bounds.maxY) / 2 - bounds.minY) * scale;
      const projectMini = node => ({
        x: offsetX + (node.x - bounds.minX) * scale,
        y: offsetY + (node.y - bounds.minY) * scale
      });
      state.minimapRects.set(side || 'main', { side: side || null, rect: mini, graphRect: rect, bounds, scale, offsetX, offsetY });
      ctx.save();
      ctx.globalAlpha = 1;
      ctx.fillStyle = 'rgba(18, 21, 27, .72)';
      ctx.strokeStyle = 'rgba(34, 211, 238, .28)';
      ctx.lineWidth = 1;
      roundedRect(mini.x, mini.y, mini.w, mini.h, 8);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = 'rgba(184, 193, 207, .72)';
      ctx.font = '10px system-ui';
      ctx.textAlign = 'left';
      ctx.fillText(side ? side + ' map' : 'map', mini.x + 10, mini.y + 16);
      ctx.beginPath();
      ctx.rect(mini.x + 6, mini.y + 6, mini.w - 12, mini.h - 12);
      ctx.clip();
      const visibleIds = new Set(nodes.map(node => node.id));
      for (const node of frameNodes) {
        const p = projectMini(node);
        ctx.fillStyle = visibleIds.has(node.id) ? nodeColor(node) : 'rgba(100, 116, 139, .45)';
        ctx.globalAlpha = visibleIds.has(node.id) ? 0.9 : 0.28;
        ctx.beginPath();
        ctx.arc(p.x, p.y, visibleIds.has(node.id) ? 2.2 : 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
      drawMinimapViewport(mini, bounds, scale, offsetX, offsetY, rect, side);
      ctx.restore();
    }

    function minimapBottomInset(rect, size, side) {
      if (side || state.legendCollapsed) return 0;
      const legend = document.getElementById('mapLegend');
      if (!legend) return 0;
      const legendRect = legend.getBoundingClientRect();
      if (!legendRect.width || !legendRect.height) return 0;
      const maxInset = Math.max(0, rect.h - size - 104);
      return Math.min(maxInset, Math.ceil(legendRect.height + 18));
    }

    function drawMinimapViewport(mini, bounds, scale, offsetX, offsetY, rect, side) {
      const panelNodes = state.view === 'compare' && side && state.compare ? state.compare[side].nodes : state.nodes;
      const transform = graphTransformFor(panelNodes, rect, side);
      if (!transform || !transform.zoom) return;
      const left = transform.centerX + (rect.x - (rect.x + rect.w / 2) - transform.panX) / transform.zoom;
      const right = transform.centerX + (rect.x + rect.w - (rect.x + rect.w / 2) - transform.panX) / transform.zoom;
      const top = transform.centerY + (rect.y - (rect.y + rect.h / 2) - transform.panY) / transform.zoom;
      const bottom = transform.centerY + (rect.y + rect.h - (rect.y + rect.h / 2) - transform.panY) / transform.zoom;
      const x = offsetX + (Math.min(left, right) - bounds.minX) * scale;
      const y = offsetY + (Math.min(top, bottom) - bounds.minY) * scale;
      const w = Math.abs(right - left) * scale;
      const h = Math.abs(bottom - top) * scale;
      ctx.globalAlpha = 1;
      const vx = clamp(x, mini.x + 6, mini.x + mini.w - 6);
      const vy = clamp(y, mini.y + 6, mini.y + mini.h - 6);
      const vw = Math.max(5, Math.min(w, mini.w - 12));
      const vh = Math.max(5, Math.min(h, mini.h - 12));
      ctx.fillStyle = 'rgba(34, 211, 238, .08)';
      ctx.strokeStyle = 'rgba(238, 241, 245, .82)';
      ctx.lineWidth = 1.35;
      ctx.fillRect(vx, vy, vw, vh);
      ctx.strokeRect(vx, vy, vw, vh);
    }

    function drawClippedGraphPanel(nodes, edges, rect, label, side) {
      if (!nodes.length) {
        ctx.fillStyle = '#9ba7b6';
        ctx.font = '14px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText('No visible nodes', rect.x + rect.w / 2, rect.y + rect.h / 2);
        return;
      }
      if (label) {
        ctx.fillStyle = '#eef1f5';
        ctx.font = '13px system-ui';
        ctx.textAlign = 'left';
        ctx.fillText(label, rect.x + 16, rect.y + 26);
      }
      const nodesById = nodesByIdForPanel(nodes, side);
      const transform = graphTransformFor(nodes, rect, side);
      const focus = graphFocus(edges, side);
      const diffFocus = compareDiffFocus(edges);
      const lowDetail = lowDetailMode(transform, nodes, edges);
      const edgeItems = edgeRenderItemsForPanel(nodes, edges, transform, rect, side, nodesById);
      ctx.lineCap = 'round';
      for (const item of edgeItems) {
        drawEdgeRenderItem(item, transform, rect, side, lowDetail);
      }
      const sorted = [...nodes].sort(
        (a, b) => nodeFocusRank(a, focus) - nodeFocusRank(b, focus)
      );
      for (const node of sorted) {
        const p = projectInRect(node, transform, rect);
        const dim = state.search && !node.label.toLowerCase().includes(state.search);
        const alpha = Math.min(dim ? 0.18 : 1, nodeFocusAlpha(node, focus), compareDiffNodeAlpha(node, diffFocus));
        ctx.fillStyle = nodeColor(node);
        const selected = isSelectedNode(node, side) || isSelectedEdgeEndpoint(node, side) || isSelectedPathEndpoint(node, side);
        const changed = shouldHighlightDiff() && hasChange(node);
        ctx.strokeStyle = selected ? '#eef1f5' : changed ? '#fca5a5' : '#0f1115';
        ctx.lineWidth = selected ? 3 : changed ? 2.5 : 1.5;
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        if (shouldDrawNodeLabel(node, p, lowDetail, focus, dim, alpha, side)) {
          ctx.fillStyle = '#eef1f5';
          ctx.font = '12px system-ui';
          ctx.textAlign = 'center';
          ctx.fillText(shortLabel(node.label), p.x, p.y + p.r + 14);
        }
      }
      ctx.globalAlpha = 1;
    }

    function edgeRenderItemsForPanel(nodes, edges, transform, rect, side, nodesById) {
      if (!state.edgeBundling || shouldHighlightDiff()) {
        return edges.map(edge => exactEdgeRenderItem(edge, nodesById)).filter(Boolean);
      }
      const lowDetail = lowDetailMode(transform, nodes, edges);
      if (!lowDetail && edges.length < 90) {
        return edges.map(edge => exactEdgeRenderItem(edge, nodesById)).filter(Boolean);
      }
      const threshold = lowDetail ? 2 : 3;
      const groups = new Map();
      const exact = [];
      for (const edge of edges) {
        const plan = edgeBundlePlan(edge, nodesById, edges, lowDetail, side);
        if (!plan) {
          exact.push(edge);
          continue;
        }
        const group = groups.get(plan.key) || {
          kind: 'bundle',
          id: 'bundle:' + plan.key,
          type: edge.type,
          sourceLabel: plan.sourceLabel,
          targetLabel: plan.targetLabel,
          sourcePoint: plan.sourcePoint,
          targetPoint: plan.targetPoint,
          edges: [],
          weight: 0
        };
        group.edges.push(edge);
        group.weight += Number(edge.weight || 1);
        group.sourcePoint = plan.sourcePoint;
        group.targetPoint = plan.targetPoint;
        groups.set(plan.key, group);
      }
      const items = [];
      for (const group of groups.values()) {
        if (group.edges.length >= threshold) items.push(finalizeBundle(group));
        else exact.push(...group.edges);
      }
      for (const edge of exact) {
        const item = exactEdgeRenderItem(edge, nodesById);
        if (item) items.push(item);
      }
      return items.sort((a, b) => {
        const bundleDelta = (a.kind === 'bundle' ? 0 : 1) - (b.kind === 'bundle' ? 0 : 1);
        return bundleDelta || (a.weight || 1) - (b.weight || 1);
      });
    }

    function exactEdgeRenderItem(edge, nodesById) {
      const source = nodesById.get(edge.source);
      const target = nodesById.get(edge.target);
      if (!source || !target) return null;
      return {
        kind: 'edge',
        id: edge.id || edge.source + '->' + edge.target + ':' + edge.type,
        edge,
        type: edge.type,
        weight: Number(edge.weight || 1),
        sourcePoint: { x: source.x, y: source.y },
        targetPoint: { x: target.x, y: target.y }
      };
    }

    function edgeBundlePlan(edge, nodesById, panelEdges, lowDetail, side) {
      if (!isBundleableEdge(edge)) return null;
      if (isPinnedTraceEdge(edge, side) || edgeTouchesSelectedNode(edge, side) || isSelectedEdge(edge, side) || isSelectedPathEdge(edge, side)) return null;
      const source = nodesById.get(edge.source);
      const target = nodesById.get(edge.target);
      if (!source || !target) return null;
      const sourceCategory = nodeCategory(source);
      const targetCategory = nodeCategory(target);
      const sourceBundle = bundleableNodeCategory(sourceCategory);
      const targetBundle = bundleableNodeCategory(targetCategory);
      if (targetBundle && !sourceBundle) {
        return {
          key: 'out|' + edge.source + '|' + edge.type + '|' + targetCategory,
          sourceLabel: labelForNode(edge.source),
          targetLabel: nodeCategoryTextLabel(targetCategory),
          sourcePoint: { x: source.x, y: source.y },
          targetPoint: bundleCentroidForCategory(nodesById, panelEdges, targetCategory, 'target', edge.source, edge.type)
        };
      }
      if (sourceBundle && !targetBundle) {
        return {
          key: 'in|' + edge.target + '|' + edge.type + '|' + sourceCategory,
          sourceLabel: nodeCategoryTextLabel(sourceCategory),
          targetLabel: labelForNode(edge.target),
          sourcePoint: bundleCentroidForCategory(nodesById, panelEdges, sourceCategory, 'source', edge.target, edge.type),
          targetPoint: { x: target.x, y: target.y }
        };
      }
      if (lowDetail && edge.source !== edge.target) {
        return {
          key: 'pair|' + edge.source + '|' + edge.target + '|' + edge.type,
          sourceLabel: labelForNode(edge.source),
          targetLabel: labelForNode(edge.target),
          sourcePoint: { x: source.x, y: source.y },
          targetPoint: { x: target.x, y: target.y }
        };
      }
      return null;
    }

    function isBundleableEdge(edge) {
      const type = String(edge.type || '').toLowerCase();
      return ['imports', 'references', 'inherits', 'depends_on', 'cochange'].includes(type);
    }

    function edgeTouchesSelectedNode(edge, side) {
      if (!state.selected || state.selected.kind !== 'node') return false;
      if (side && state.selected.side && side !== state.selected.side) return false;
      const id = state.selected.node.id;
      return edge.source === id || edge.target === id;
    }

    function bundleableNodeCategory(category) {
      return ['team', 'third_party', 'docs_config', 'generated'].includes(category);
    }

    function bundleCentroidForCategory(nodesById, edges, category, direction, fixedId, type) {
      const points = [];
      for (const edge of edges || []) {
        if (String(edge.type || '') !== String(type || '')) continue;
        if (direction === 'target' && edge.source !== fixedId) continue;
        if (direction === 'source' && edge.target !== fixedId) continue;
        const id = direction === 'target' ? edge.target : edge.source;
        const node = nodesById.get(id);
        if (node && nodeCategory(node) === category) points.push(node);
      }
      if (!points.length) return { x: 0, y: 0 };
      const total = points.reduce((acc, node) => {
        acc.x += node.x;
        acc.y += node.y;
        return acc;
      }, { x: 0, y: 0 });
      return { x: total.x / points.length, y: total.y / points.length };
    }

    function finalizeBundle(bundle) {
      bundle.label = String(bundle.type || 'edge') + ' x' + bundle.edges.length;
      bundle.source = bundle.sourceLabel;
      bundle.target = bundle.targetLabel;
      return bundle;
    }

    function drawEdgeRenderItem(item, transform, rect, side, lowDetail) {
      const pa = projectPointInRect(item.sourcePoint, transform, rect);
      const pb = projectPointInRect(item.targetPoint, transform, rect);
      const edge = item.kind === 'bundle' ? item.edges[0] : item.edge;
      const selected = item.kind === 'bundle' ? isSelectedBundle(item, side) : isSelectedEdge(edge, side) || isSelectedPathEdge(edge, side);
      const hovered = item.kind === 'bundle' ? isHoveredBundle(item, side) : isHoveredEdge(edge, side);
      const pinned = item.kind !== 'bundle' && isPinnedTraceEdge(edge, side);
      const alpha = item.kind === 'bundle' ? bundleAlpha(item, side) : edgeAlpha(edge, side);
      ctx.globalAlpha = visibleEdgeAlpha(hovered ? Math.max(alpha, 0.98) : pinned ? Math.max(alpha, 0.9) : alpha, lowDetail);
      ctx.strokeStyle = edgeColor(item.type, edge);
      const baseWidth = Math.min(5.5, 1.15 + Math.log2((item.weight || 1) + 1) * 0.62) * edgeWidthScale();
      const diffEdge = shouldHighlightDiff() && hasChange(edge);
      ctx.lineWidth = hovered ? Math.min(7, baseWidth + 2.2) : selected ? 5.5 : pinned ? Math.min(6, baseWidth + 1.6) : item.kind === 'bundle' ? Math.min(7, baseWidth + 1.1) : diffEdge ? Math.min(5, baseWidth + 1.2) : baseWidth;
      ctx.setLineDash(edgeDashPattern(item.type, item.kind, selected, hovered, pinned, diffEdge));
      ctx.lineDashOffset = 0;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
      ctx.setLineDash([]);
      if (item.kind === 'bundle') drawBundleLabel(item, pa, pb, selected || hovered);
    }

    function edgeDashPattern(type, kind, selected, hovered, pinned, diffEdge) {
      if (selected || hovered || pinned || diffEdge) return [];
      if (kind === 'bundle') return [10, 5];
      const clean = String(type || '').toLowerCase();
      if (['imports', 'inherits', 'depends_on'].includes(clean)) return [7, 5];
      if (['cochange', 'authored', 'touched'].includes(clean)) return [2.5, 5];
      if (['api_call', 'graphql', 'database'].includes(clean)) return [10, 4, 2, 4];
      if (clean === 'test_covers') return [5, 4];
      if (clean === 'custom') return [12, 4];
      return [];
    }

    function drawBundleLabel(bundle, pa, pb, selected) {
      const midX = (pa.x + pb.x) / 2;
      const midY = (pa.y + pb.y) / 2;
      const label = bundle.label || ('edges x' + bundle.edges.length);
      ctx.save();
      ctx.globalAlpha = selected ? 1 : adjustEdgeAlpha(0.88);
      ctx.font = '11px system-ui';
      const width = Math.min(96, Math.max(54, ctx.measureText(label).width + 14));
      ctx.fillStyle = selected ? 'rgba(238, 241, 245, .95)' : 'rgba(23, 26, 33, .88)';
      ctx.strokeStyle = selected ? '#22d3ee' : 'rgba(119, 167, 255, .42)';
      ctx.lineWidth = 1;
      roundedRect(midX - width / 2, midY - 10, width, 20, 6);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = selected ? '#0f1115' : '#eef1f5';
      ctx.textAlign = 'center';
      ctx.fillText(label, midX, midY + 4);
      ctx.restore();
    }

    function visibleEdgeAlpha(alpha, lowDetail) {
      return lowDetail ? Math.max(alpha, LOW_DETAIL_EDGE_ALPHA_FLOOR) : alpha;
    }

    function roundedRect(x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.lineTo(x + w - r, y);
      ctx.quadraticCurveTo(x + w, y, x + w, y + r);
      ctx.lineTo(x + w, y + h - r);
      ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
      ctx.lineTo(x + r, y + h);
      ctx.quadraticCurveTo(x, y + h, x, y + h - r);
      ctx.lineTo(x, y + r);
      ctx.quadraticCurveTo(x, y, x + r, y);
      ctx.closePath();
    }

    function projectPointInRect(point, transform, rect) {
      return {
        x: rect.x + rect.w / 2 + transform.panX + (point.x - transform.centerX) * transform.zoom,
        y: rect.y + rect.h / 2 + transform.panY + (point.y - transform.centerY) * transform.zoom
      };
    }

    function lowDetailMode(transform, nodes, edges) {
      return Boolean(state.edgeBundling) && (transform.zoom < 0.72 || nodes.length > 120 || edges.length > 180);
    }

    function shouldDrawNodeLabel(node, projected, lowDetail, focus, dim, alpha, side) {
      if (dim || alpha <= 0.35 || projected.r <= 9) return false;
      if (isSelectedNode(node, side) || isSelectedEdgeEndpoint(node, side) || isSelectedPathEndpoint(node, side)) return true;
      const metrics = node.metrics || {};
      const important = Number(metrics.files || 0) >= 3 ||
        Number(node.size || 0) >= 24 ||
        ['owned', 'team'].includes(nodeCategory(node));
      if (!lowDetail) {
        if (state.nodes.length > 40) return important && projected.r > 11;
        return true;
      }
      return important && projected.r > 12 && (!focus || nodeFocusRank(node, focus) > 0);
    }

    function bundleAlpha(bundle, side) {
      if (isSelectedBundle(bundle, side)) return 1;
      if (!state.selected || !['node', 'edge', 'path', 'bundle'].includes(state.selected.kind)) return adjustEdgeAlpha(0.72);
      if (state.selected.kind === 'node') {
        const id = state.selected.node.id;
        return bundle.edges.some(edge => edge.source === id || edge.target === id) ? adjustEdgeAlpha(0.86) : adjustEdgeAlpha(0.08);
      }
      if (state.selected.kind === 'bundle') return adjustEdgeAlpha(0.1);
      return adjustEdgeAlpha(0.12);
    }

    function isSelectedBundle(bundle, side) {
      return state.selected && state.selected.kind === 'bundle' &&
        state.selected.bundle.id === bundle.id &&
        (!side || !state.selected.side || state.selected.side === side);
    }

    function graphFocus(edges, side) {
      const hasSelectedFocus = state.selected && ['node', 'edge', 'path', 'bundle'].includes(state.selected.kind);
      const pinnedTrace = pinnedTraceSubgraph(edges, side);
      if (!hasSelectedFocus && !pinnedTrace) return null;
      if (side && state.selected && state.selected.side && side !== state.selected.side && !pinnedTrace) {
        return { selected: new Set(), connected: new Set() };
      }
      const selected = new Set();
      const connected = new Set();
      if (hasSelectedFocus && state.selected.kind === 'node') {
        const id = state.selected.node.id;
        selected.add(id);
        connected.add(id);
        for (const edge of edges) {
          if (edge.source === id) connected.add(edge.target);
          if (edge.target === id) connected.add(edge.source);
        }
      } else if (hasSelectedFocus && state.selected.kind === 'edge') {
        selected.add(state.selected.edge.source);
        selected.add(state.selected.edge.target);
        connected.add(state.selected.edge.source);
        connected.add(state.selected.edge.target);
      } else if (hasSelectedFocus && state.selected.kind === 'path') {
        selected.add(state.selected.path.sourceId);
        selected.add(state.selected.path.targetId);
        connected.add(state.selected.path.sourceId);
        connected.add(state.selected.path.targetId);
      } else if (hasSelectedFocus && state.selected.kind === 'bundle') {
        for (const edge of state.selected.bundle.edges || []) {
          selected.add(edge.source);
          selected.add(edge.target);
          connected.add(edge.source);
          connected.add(edge.target);
        }
      }
      if (pinnedTrace) {
        selected.add(state.pinnedTrace.nodeId);
        for (const id of pinnedTrace.ids) connected.add(id);
      }
      return { selected, connected };
    }

    function nodesByIdForPanel(nodes, side) {
      if (side && state.compare && state.compare[side] && state.compare[side].nodesById) {
        return state.compare[side].nodesById;
      }
      if (!side && state.graphCache && state.graphCache.visibleNodesById) {
        return state.graphCache.visibleNodesById;
      }
      return new Map(nodes.map(node => [node.id, node]));
    }

    function compareDiffFocus(edges) {
      if (!shouldHighlightDiff() || state.selected) return null;
      const nodes = new Set();
      for (const edge of edges) {
        if (!hasChange(edge)) continue;
        nodes.add(edge.source);
        nodes.add(edge.target);
      }
      return nodes;
    }

    function compareDiffNodeAlpha(node, diffFocus) {
      if (!diffFocus) return 1;
      return hasChange(node) || diffFocus.has(node.id) ? 1 : 0.24;
    }

    function shouldHighlightDiff() {
      return state.view === 'compare' && state.diffHighlight;
    }

    function hasChange(item) {
      return Boolean(item && item.change && item.change !== 'unchanged');
    }

    function nodeFocusRank(node, focus) {
      if (!focus) return 1;
      if (focus.selected.has(node.id)) return 3;
      if (focus.connected.has(node.id)) return 2;
      return 0;
    }

    function nodeFocusAlpha(node, focus) {
      const rank = nodeFocusRank(node, focus);
      if (rank >= 3) return 1;
      if (rank === 2) return 0.92;
      if (!focus) return 1;
      return 0.13;
    }

    function graphTransform() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      return graphTransformFor(state.nodes, { x: 0, y: 0, w, h }, null);
    }

    function graphTransformFor(nodes, rect, side) {
      const base = graphBaseFitForRect(rect, side, nodes);
      const viewport = graphViewport(side);
      return {
        centerX: base.centerX,
        centerY: base.centerY,
        zoom: base.fitZoom * viewport.zoom,
        panX: viewport.panX,
        panY: viewport.panY
      };
    }

    function graphBaseFitForRect(rect, side, nodes) {
      const frameNodes = layoutFrameNodes(nodes || [], side);
      const bounds = boundsForNodes(frameNodes) || { minX: 0, minY: 0, maxX: 1, maxY: 1 };
      const spanX = Math.max(1, bounds.maxX - bounds.minX);
      const spanY = Math.max(1, bounds.maxY - bounds.minY);
      const pad = state.view === 'commits' ? 80 : 110;
      const fitZoom = Math.min(
        1.7,
        Math.max(
          0.22,
          Math.min(
            Math.max(220, rect.w - pad * 2) / spanX,
            Math.max(220, rect.h - pad * 2) / spanY
          )
        )
      );
      return {
        centerX: (bounds.minX + bounds.maxX) / 2,
        centerY: (bounds.minY + bounds.maxY) / 2,
        fitZoom
      };
    }

    function layoutFrameNodes(nodes, side) {
      if (state.view === 'compare' && state.compare && side && state.compare[side] && state.compare[side].allNodes.length) {
        return state.compare[side].allNodes;
      }
      if (state.allNodes && state.allNodes.length) return state.allNodes;
      return nodes && nodes.length ? nodes : [{ x: 0, y: 0 }];
    }

    function project(node, transform) {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      return projectInRect(node, transform || graphTransform(), { x: 0, y: 0, w, h });
    }

    function projectInRect(node, transform, rect) {
      const fit = transform || graphTransformFor([node], rect, null);
      const radiusScale = clamp(Math.sqrt(fit.zoom), 0.75, 1.35);
      return {
        x: rect.x + rect.w / 2 + fit.panX + (node.x - fit.centerX) * fit.zoom,
        y: rect.y + rect.h / 2 + fit.panY + (node.y - fit.centerY) * fit.zoom,
        scale: 1,
        r: Math.max(5, Math.min(34, (node.size || 14) * radiusScale))
      };
    }

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function edgeAlpha(edge, side) {
      let alpha = 0.62;
      if (!state.selected || !['node', 'edge', 'path'].includes(state.selected.kind)) {
        alpha = shouldHighlightDiff() ? (hasChange(edge) ? 0.94 : 0.22) : 0.62;
        return adjustEdgeAlpha(alpha);
      }
      if (isSelectedEdge(edge, side)) return 1;
      if (isSelectedPathEdge(edge, side)) return 1;
      if (state.selected.kind === 'node') {
        const id = state.selected.node.id;
        if (side && state.selected.side && side !== state.selected.side) return adjustEdgeAlpha(0.08);
        return adjustEdgeAlpha(edge.source === id || edge.target === id ? 0.9 : 0.08);
      }
      if (state.selected.kind === 'edge') {
        if (side && state.selected.side && side !== state.selected.side) return adjustEdgeAlpha(0.08);
        const selected = state.selected.edge;
        alpha = edge.source === selected.source || edge.target === selected.target ||
          edge.source === selected.target || edge.target === selected.source ? 0.35 : 0.08;
        return adjustEdgeAlpha(alpha);
      }
      if (state.selected.kind === 'path') return adjustEdgeAlpha(0.06);
      return adjustEdgeAlpha(0.62);
    }

    function edgeContrastRatio() {
      return clamp(Number(state.edgeContrast || 64) / 64, 0.38, 1.56);
    }

    function adjustEdgeAlpha(alpha) {
      return clamp(alpha * edgeContrastRatio(), 0.035, 1);
    }

    function edgeWidthScale() {
      return clamp(0.7 + edgeContrastRatio() * 0.3, 0.72, 1.18);
    }

    function isSelectedNode(node, side) {
      return state.selected && state.selected.kind === 'node' &&
        state.selected.node.id === node.id &&
        (!side || !state.selected.side || state.selected.side === side);
    }

    function isSelectedEdge(edge, side) {
      return state.selected && state.selected.kind === 'edge' &&
        state.selected.edge.id === edge.id &&
        (!side || !state.selected.side || state.selected.side === side);
    }

    function isHoveredEdge(edge, side) {
      return state.hoveredEdge && state.hoveredEdge.kind === 'edge' &&
        edgeIdForHover(state.hoveredEdge.edge) === edgeIdForHover(edge) &&
        (!side || !state.hoveredEdge.side || state.hoveredEdge.side === side);
    }

    function isHoveredBundle(bundle, side) {
      return state.hoveredEdge && state.hoveredEdge.kind === 'bundle' &&
        state.hoveredEdge.bundle.id === bundle.id &&
        (!side || !state.hoveredEdge.side || state.hoveredEdge.side === side);
    }

    function edgeIdForHover(edge) {
      return edge ? (edge.id || edge.source + '->' + edge.target + ':' + edge.type) : '';
    }

    function isSelectedEdgeEndpoint(node, side) {
      return state.selected && state.selected.kind === 'edge' &&
        (!side || !state.selected.side || state.selected.side === side) &&
        (state.selected.edge.source === node.id || state.selected.edge.target === node.id);
    }

    function isSelectedPathEndpoint(node, side) {
      return state.selected && state.selected.kind === 'path' &&
        (!side || !state.selected.path.side || state.selected.path.side === side) &&
        (state.selected.path.sourceId === node.id || state.selected.path.targetId === node.id);
    }

    function isSelectedPathEdge(edge, side) {
      if (!state.selected || state.selected.kind !== 'path') return false;
      const path = state.selected.path;
      if (side && path.side && side !== path.side) return false;
      return edge.type === path.type &&
        edge.source === path.sourceId &&
        edge.target === path.targetId;
    }

    function edgeColor(type, edge) {
      if (shouldHighlightDiff() && hasChange(edge)) return '#ef7b7b';
      return {
        calls: '#22d3ee',
        api_call: '#22d3ee',
        function_call: '#38bdf8',
        graphql: '#f472b6',
        database: '#71d49b',
        test_covers: '#f472b6',
        depends_on: '#94a3b8',
        custom: '#facc15',
        references: '#34d399',
        imports: '#b69cff',
        inherits: '#f472b6',
        cochange: '#e4b363',
        authored: '#71d49b',
        touched: '#e4b363'
      }[type] || '#596274';
    }

    function nodeColor(node) {
      if (shouldHighlightDiff() && hasChange(node)) return '#ef7b7b';
      if (node.type === 'developer') return '#71d49b';
      if (node.type === 'commit') return '#e4b363';
      if (isServiceNode(node)) return '#22d3ee';
      if (node.type === 'api' || node.type === 'graphql') return '#22d3ee';
      if (node.type === 'database') return '#71d49b';
      const category = nodeCategory(node);
      if (category === 'team') return '#22d3ee';
      if (category === 'third_party') return '#b69cff';
      if (category === 'docs_config') return '#94a3b8';
      if (category === 'tests') return '#f472b6';
      if (category === 'generated') return '#64748b';
      if (node.type === 'file') return '#60a5fa';
      if (node.type === 'class') return '#a78bfa';
      if (node.risk === 'high') return '#ef7b7b';
      if (node.risk === 'medium') return '#e4b363';
      const label = String(node.label || node.id || '').toLowerCase();
      if (/test|tox|spec|mock/.test(label)) return '#f472b6';
      if (/doc|readme|guide|api-ref/.test(label)) return '#c084fc';
      if (/db|sql|database|migration|models?/.test(label)) return '#71d49b';
      if (/api|graphql|client|service/.test(label)) return '#22d3ee';
      if (/config|setup|requirements|yaml|toml|ini/.test(label)) return '#e4b363';
      return '#77a7ff';
    }

    function renderSelection(selection) {
      updateStatCardActive();
      updateFitSelectionButton(selection);
      renderClassificationWizard();
      updateFocusBreadcrumb();
      scheduleUrlStateUpdate();
      if (!selection || selection.kind !== 'path') {
        state.activePath = null;
        renderSavedPaths();
      }
      const title = document.getElementById('selectionTitle');
      const meta = document.getElementById('selectionMeta');
      configureDetailSearch(selection);
      configureDetailTabs(selection);
      if (!selection) {
        renderEmptySelection(title, meta);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'stat') {
        renderStatSelection(selection, title, meta);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'path') {
        renderPathSelection(selection.path, title, meta);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'addConnection') {
        renderAddConnectionSelection(title, meta);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'editNode') {
        renderNodeEditSelection(selection.node, title, meta, selection.side);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'architecture') {
        renderArchitectureSelection(selection.architecture, title, meta);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'bundle') {
        renderBundleSelection(selection.bundle, title, meta, selection.side);
        applyDetailFilters();
        return;
      }
      if (selection.kind === 'edge') {
        renderEdgeSelection(selection.edge, title, meta, selection.side);
        applyDetailFilters();
        return;
      }
      renderNodeSelection(selection, title, meta);
      applyDetailFilters();
    }

    function renderEmptySelection(title, meta) {
      title.textContent = 'Nothing selected';
      const stack = detailStack(meta);
      const body = appendDetailSection(stack, 'Start Here', [], true);
      const actions = document.createElement('div');
      actions.className = 'empty-selection-actions';
      const startButton = emptySelectionButton('Where start?', () => runRepoQuestion(REPO_QUESTIONS[0]));
      const fitButton = emptySelectionButton('Fit map', () => fitCameraToNodes(state.nodes || []));
      fitButton.disabled = !(state.nodes && state.nodes.length);
      const paletteButton = emptySelectionButton('Command palette', () => openCommandPalette());
      actions.append(startButton, fitButton, paletteButton);
      body.appendChild(actions);
      appendDetailSection(stack, 'Current View', [
        'Lens: ' + (LENS_LABELS[state.activeLens] || state.activeLens || 'Overview'),
        'Visible nodes: ' + (state.nodes || []).length + ' of ' + (state.allNodes || []).length,
        'Visible edges: ' + (state.edges || []).length + ' of ' + (state.allEdges || []).length
      ], false);
    }

    function emptySelectionButton(label, onClick) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.onclick = onClick;
      return button;
    }

    function configureDetailSearch(selection) {
      const input = document.getElementById('detailSearchInput');
      if (!input) return;
      const searchable = selection && !['addConnection', 'editNode'].includes(selection.kind);
      input.hidden = !searchable;
      if (!searchable) {
        state.detailSearch = '';
        input.value = '';
        return;
      }
      input.value = state.detailSearch || '';
      window.requestAnimationFrame(applyDetailSearchFilter);
    }

    function configureDetailTabs(selection) {
      const tabs = document.getElementById('detailTabs');
      const empty = document.getElementById('detailTabEmpty');
      if (!tabs) return;
      const visible = detailTabsVisibleForSelection(selection);
      tabs.hidden = !visible;
      if (empty) empty.hidden = true;
      if (!visible) {
        state.detailTabSelectionKind = '';
        updateDetailTabButtons();
        return;
      }
      const kind = selection && selection.kind ? selection.kind : '';
      const restoringExplicitTab = state.isRestoringUrlState && state.pendingUrlState && state.pendingUrlState.detailTab;
      if (state.detailTabSelectionKind !== kind && !restoringExplicitTab) {
        state.activeDetailTab = preferredDetailTabForSelection(selection);
      }
      state.detailTabSelectionKind = kind;
      updateDetailTabButtons();
    }

    function detailTabsVisibleForSelection(selection) {
      return Boolean(selection && ['node', 'edge', 'path', 'bundle', 'architecture'].includes(selection.kind));
    }

    function preferredDetailTabForSelection(selection) {
      if (!selection) return 'evidence';
      if (selection.kind === 'edge' || selection.kind === 'bundle') return 'evidence';
      if (selection.kind === 'node' || selection.kind === 'path' || selection.kind === 'architecture') return 'flow';
      return 'evidence';
    }

    function setDetailTab(tab) {
      if (!DETAIL_TABS.includes(tab)) return;
      state.activeDetailTab = tab;
      updateDetailTabButtons();
      applyDetailTabFilter();
      scheduleUrlStateUpdate();
    }

    function updateDetailTabButtons() {
      const active = DETAIL_TABS.includes(state.activeDetailTab) ? state.activeDetailTab : 'evidence';
      state.activeDetailTab = active;
      document.querySelectorAll('#detailTabs [data-detail-tab]').forEach(button => {
        const selected = button.dataset.detailTab === active;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-selected', String(selected));
      });
    }

    function applyDetailFilters() {
      applyDetailSearchFilter();
      applyDetailTabFilter();
    }

    function applyDetailSearchFilter() {
      const input = document.getElementById('detailSearchInput');
      const root = document.getElementById('selectionMeta');
      if (!input || input.hidden || !root) {
        updateDetailTabEmpty();
        return;
      }
      const terms = String(state.detailSearch || '').toLowerCase().split(/\s+/).filter(Boolean);
      root.querySelectorAll('.detail-filter-empty').forEach(node => node.remove());
      const candidates = [...root.querySelectorAll('.detail-card, .edge-row, .evidence-proof, .example-detail, .flow-card, .detail-line')];
      for (const node of candidates) {
        if (!terms.length) {
          node.classList.remove('filtered-out');
          continue;
        }
        const text = node.textContent.toLowerCase();
        node.classList.toggle('filtered-out', !terms.every(term => text.includes(term)));
      }
      if (terms.length && candidates.length && !candidates.some(node => !node.classList.contains('filtered-out'))) {
        const empty = document.createElement('div');
        empty.className = 'detail-filter-empty';
        empty.textContent = 'No evidence matches this search.';
        root.prepend(empty);
      }
      updateDetailTabEmpty();
    }

    function applyDetailTabFilter() {
      const root = document.getElementById('selectionMeta');
      const tabs = document.getElementById('detailTabs');
      if (!root || !tabs || tabs.hidden) {
        if (root) root.querySelectorAll('.detail-card.tab-filtered-out').forEach(node => node.classList.remove('tab-filtered-out'));
        updateDetailTabEmpty();
        return;
      }
      const active = DETAIL_TABS.includes(state.activeDetailTab) ? state.activeDetailTab : 'evidence';
      const sections = topLevelDetailSections(root);
      for (const section of sections) {
        const tab = section.dataset.detailTab || detailTabForTitle(section.querySelector('summary')?.textContent || '');
        section.classList.toggle('tab-filtered-out', tab !== active);
      }
      updateDetailTabEmpty();
    }

    function updateDetailTabEmpty() {
      const empty = document.getElementById('detailTabEmpty');
      const tabs = document.getElementById('detailTabs');
      const root = document.getElementById('selectionMeta');
      if (!empty || !tabs || tabs.hidden || !root) {
        if (empty) empty.hidden = true;
        return;
      }
      const sections = topLevelDetailSections(root);
      const hasVisible = sections.some(section =>
        !section.classList.contains('tab-filtered-out') &&
        !section.classList.contains('filtered-out')
      );
      empty.hidden = hasVisible || !sections.length;
      if (!empty.hidden) {
        empty.textContent = 'No ' + detailTabLabel(state.activeDetailTab).toLowerCase() + ' details match this selection.';
      }
    }

    function topLevelDetailSections(root) {
      const stack = root.querySelector('.detail-stack');
      return stack ? [...stack.children].filter(node => node.classList && node.classList.contains('detail-card')) : [];
    }

    function detailTabForTitle(title) {
      const clean = String(title || '').replace(/\s*\(.*\)\s*$/, '').toLowerCase();
      if (clean.includes('evidence') || clean.includes('example') || clean.includes('proof')) return 'evidence';
      if (clean.includes('commit') || clean.includes('co-change') || clean.includes('history')) return 'history';
      if (clean.includes('flow') || clean.includes('trace') || clean.includes('function') || clean.includes('api') || clean.includes('component') || clean.includes('connection') || clean === 'path' || clean === 'added connections') return 'flow';
      if (clean.includes('location') || clean.includes('file') || clean.includes('source') || clean.includes('target') || clean.includes('overview') || clean.includes('overlay') || clean.includes('inference') || clean.includes('override')) return 'files';
      return 'evidence';
    }

    function detailTabLabel(tab) {
      return {
        evidence: 'Evidence',
        flow: 'Flow',
        files: 'Files',
        history: 'History'
      }[tab] || 'Evidence';
    }

    function updateFitSelectionButton(selection) {
      const button = document.getElementById('fitSelectionBtn');
      if (!button) return;
      button.disabled = !selection || !['node', 'edge', 'path', 'bundle'].includes(selection.kind);
    }

    function renderAddConnectionSelection(title, meta) {
      title.textContent = 'Add connection';
      const stack = detailStack(meta);
      const body = appendDetailSection(stack, 'Connection', [], true);
      const form = document.createElement('div');
      form.className = 'form-grid';
      form.append(
        vertexField('source', 'Source vertex'),
        vertexField('target', 'Target vertex'),
        fieldSelect('connectionType', 'Connection type', [
          ['api_call', 'API call'],
          ['function_call', 'Function call'],
          ['graphql', 'GraphQL'],
          ['database', 'Database read/write'],
          ['imports', 'Import'],
          ['references', 'Reference'],
          ['test_covers', 'Test covers'],
          ['depends_on', 'Depends on'],
          ['custom', 'Custom']
        ]),
        fieldInput('connectionNote', 'Evidence / note', 'Why are these connected?')
      );
      const actions = document.createElement('div');
      actions.className = 'form-actions';
      const add = document.createElement('button');
      add.textContent = 'Add edge';
      add.onclick = addUserConnectionFromForm;
      const save = document.createElement('button');
      save.textContent = 'Save architecture';
      save.onclick = saveCurrentArchitecture;
      actions.append(add, save);
      form.append(actions);
      body.appendChild(form);
      appendDetailSection(
        stack,
        'How it works',
        [
          'current architecture: indexed graph plus your saved overlay',
          'custom vertices: components, classes, files, APIs, databases, tests, or external services',
          'saved architectures: click one later to restore and visualize those added connections'
        ],
        false
      );
    }

    function renderArchitectureSelection(architecture, title, meta) {
      title.textContent = architecture.name || 'Saved architecture';
      const stack = detailStack(meta);
      appendDetailSection(
        stack,
        'Overlay',
        [
          'custom vertices: ' + (architecture.nodes || []).length,
          'custom edges: ' + (architecture.edges || []).length,
          architecture.createdAt ? 'saved: ' + architecture.createdAt : ''
        ].filter(Boolean),
        true
      );
      appendDetailSection(
        stack,
        'Added Connections',
        (architecture.edges || []).slice(0, 20).map(edge =>
          '- ' + labelForNode(edge.source) + ' -> ' + labelForNode(edge.target) + ' (' + edge.type + ')'
        ),
        true
      );
    }

    function openNodeEditor(node, side) {
      state.selected = { kind: 'editNode', node, side };
      renderSelection(state.selected);
    }

    function renderNodeEditSelection(node, title, meta, side) {
      title.textContent = 'Edit ' + (node.label || node.id);
      const stack = detailStack(meta);
      const body = appendDetailSection(stack, 'Node Override', [], true);
      const form = document.createElement('div');
      form.className = 'form-grid';
      form.append(
        nodeEditText('nodeEditLabel', 'Display name', node.label || ''),
        nodeEditSelect('nodeEditType', 'Node type', node.type || 'component'),
        nodeEditText('nodeEditTags', 'Tags', (node.tags || []).join(', ')),
        nodeEditTextarea('nodeEditDetails', 'Notes', node.details || ''),
        nodeEditCheckbox('nodeEditService', 'Treat as separate service', isServiceNode(node))
      );
      const actions = document.createElement('div');
      actions.className = 'form-actions';
      const save = document.createElement('button');
      save.textContent = 'Save node';
      setHelp(save, 'Saves this node override locally in your browser. It changes the architecture visualization, not repository source code.');
      save.onclick = () => saveNodeEditFromForm(node.id, side, true);
      const service = document.createElement('button');
      service.textContent = isServiceNode(node) ? 'Unmark service' : 'Mark service';
      setHelp(service, 'Marks this node as a separate service so it gets service styling and can be understood as its own boundary.');
      service.onclick = () => toggleNodeService(node, side, true);
      const reset = document.createElement('button');
      reset.textContent = 'Reset override';
      setHelp(reset, 'Removes your local override for this node and restores CodeAtlas inferred values on the next render.');
      reset.onclick = () => resetNodeOverride(node.id, side);
      actions.append(save, service, reset);
      form.appendChild(actions);
      body.appendChild(form);
      appendDetailSection(
        stack,
        'Original Inference',
        [
          'id: ' + node.id,
          'original label: ' + (node.originalLabel || node.label || ''),
          'original type: ' + (node.originalType || node.type || ''),
          'current category: ' + nodeCategory(node),
          nodeOverrideFor(node.id) ? 'override: saved locally' : 'override: none'
        ],
        false
      );
    }

    function nodeEditText(id, label, value) {
      const wrapper = fieldInput(id, label, '');
      wrapper.querySelector('input').value = value || '';
      return wrapper;
    }

    function nodeEditTextarea(id, label, value) {
      const wrapper = document.createElement('label');
      wrapper.textContent = label;
      const textarea = document.createElement('textarea');
      textarea.id = id;
      textarea.value = value || '';
      wrapper.appendChild(textarea);
      return wrapper;
    }

    function nodeEditSelect(id, label, value) {
      const wrapper = fieldSelect(id, label, nodeTypeOptions());
      wrapper.querySelector('select').value = value || 'component';
      return wrapper;
    }

    function nodeEditCheckbox(id, label, checked) {
      const wrapper = document.createElement('label');
      wrapper.className = 'filter-control inline';
      const input = document.createElement('input');
      input.id = id;
      input.type = 'checkbox';
      input.checked = Boolean(checked);
      const span = document.createElement('span');
      span.textContent = label;
      wrapper.append(input, span);
      return wrapper;
    }

    function nodeTypeOptions() {
      return [
        ['component', 'Component'],
        ['service', 'Service'],
        ['external', 'External service'],
        ['api', 'API'],
        ['graphql', 'GraphQL'],
        ['database', 'Database'],
        ['class', 'Class'],
        ['file', 'File'],
        ['test', 'Test'],
        ['developer', 'Developer'],
        ['commit', 'Commit']
      ];
    }

    function saveNodeEditFromForm(nodeId, side, keepEditing) {
      const label = document.getElementById('nodeEditLabel').value.trim();
      const type = document.getElementById('nodeEditType').value;
      const service = document.getElementById('nodeEditService').checked;
      const details = document.getElementById('nodeEditDetails').value.trim();
      const tags = parseTags(document.getElementById('nodeEditTags').value);
      if (service || type === 'service') tags.add('service');
      if (type === 'external') tags.add('external');
      saveNodeOverride(nodeId, {
        label: label || nodeId,
        type,
        tags: [...tags],
        details
      });
      refreshAfterNodeOverride(nodeId, side, keepEditing ? 'editNode' : 'node');
    }

    function parseTags(value) {
      return new Set(String(value || '')
        .split(',')
        .map(tag => tag.trim())
        .filter(Boolean));
    }

    function toggleNodeService(node, side, keepEditing) {
      const override = nodeOverrideFor(node.id) || {};
      const tags = new Set([...(node.tags || []), ...(override.tags || [])].filter(Boolean));
      const currentlyService = isServiceNode(node);
      let type = override.type || node.type || 'component';
      if (currentlyService) {
        tags.delete('service');
        if (type === 'service') type = node.originalType || 'component';
      } else {
        tags.add('service');
        type = 'service';
      }
      saveNodeOverride(node.id, {
        label: override.label || node.label,
        type,
        tags: [...tags],
        details: override.details || node.details || ''
      });
      refreshAfterNodeOverride(node.id, side, keepEditing ? 'editNode' : 'node');
    }

    function resetNodeOverride(nodeId, side) {
      const scoped = scopedNodeOverrides();
      delete scoped[nodeId];
      persistNodeOverrides();
      refreshAfterNodeOverride(nodeId, side, 'node');
    }

    function refreshAfterNodeOverride(nodeId, side, selectionKind) {
      applyNodeOverridesToCurrentGraph();
      applyFilters();
      renderFilterControls();
      const node = nodeForIdInCurrentView(nodeId, side);
      state.selected = node ? { kind: selectionKind || 'node', node, side } : null;
      renderSelection(state.selected);
    }

    function applyNodeOverridesToCurrentGraph() {
      const apply = node => {
        const position = { x: node.x, y: node.y, vx: node.vx, vy: node.vy, _layoutSaved: node._layoutSaved };
        const updated = applyNodeOverride(node);
        Object.assign(node, updated, position);
      };
      for (const node of state.allNodes) apply(node);
      if (state.compare) {
        for (const node of state.compare.base.allNodes) apply(node);
        for (const node of state.compare.head.allNodes) apply(node);
      }
    }

    function nodeForIdInCurrentView(nodeId, side) {
      if (side && state.compare && state.compare[side]) {
        return state.compare[side].allNodes.find(node => node.id === nodeId) || null;
      }
      return nodeForId(nodeId);
    }

    function vertexField(prefix, label) {
      const wrapper = document.createElement('div');
      wrapper.className = 'form-grid';
      wrapper.append(
        fieldSelect(prefix + 'Vertex', label, vertexOptions()),
        fieldInput(prefix + 'NewLabel', 'New ' + label.toLowerCase(), 'Optional new vertex label'),
        fieldSelect(prefix + 'NewType', 'New vertex type', [
          ['component', 'Component'],
          ['service', 'Service'],
          ['class', 'Class'],
          ['file', 'File'],
          ['api', 'API'],
          ['graphql', 'GraphQL'],
          ['database', 'Database'],
          ['test', 'Test'],
          ['external', 'External service']
        ])
      );
      return wrapper;
    }

    function fieldInput(id, label, placeholder) {
      const wrapper = document.createElement('label');
      wrapper.textContent = label;
      const input = document.createElement('input');
      input.id = id;
      input.placeholder = placeholder || '';
      wrapper.appendChild(input);
      return wrapper;
    }

    function fieldSelect(id, label, options) {
      const wrapper = document.createElement('label');
      wrapper.textContent = label;
      const select = document.createElement('select');
      select.id = id;
      for (const [value, text] of options) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        select.appendChild(option);
      }
      wrapper.appendChild(select);
      return wrapper;
    }

    function vertexOptions() {
      const graph = architectureGraphWithOverlay();
      const options = [['__new__', 'New vertex from label below']];
      for (const node of graph.nodes.slice().sort((a, b) => a.label.localeCompare(b.label))) {
        options.push([node.id, node.label + ' (' + (node.type || 'component') + ')']);
      }
      return options;
    }

    function addUserConnectionFromForm() {
      const source = resolveFormVertex('source');
      const target = resolveFormVertex('target');
      const type = document.getElementById('connectionType').value;
      const note = document.getElementById('connectionNote').value.trim();
      if (!source || !target || source === target) {
        appendPlainLine(document.getElementById('selectionMeta'), 'Choose two different vertices.');
        return;
      }
      const edge = {
        id: 'user-edge:' + source + '->' + target + ':' + type + ':' + Date.now(),
        source,
        target,
        type,
        weight: 1,
        custom: true,
        reasons: note ? [note] : ['User-added connection'],
        examples: []
      };
      state.customEdges.push(edge);
      state.activeArchitectureId = 'working';
      persistUserArchitectures();
      setGraph('architecture');
      state.selected = { kind: 'edge', edge };
      renderSavedArchitectures();
      renderSelection(state.selected);
    }

    function resolveFormVertex(prefix) {
      const selected = document.getElementById(prefix + 'Vertex').value;
      if (selected !== '__new__') return selected;
      const label = document.getElementById(prefix + 'NewLabel').value.trim();
      const type = document.getElementById(prefix + 'NewType').value;
      if (!label) return '';
      const existing = state.customNodes.find(node => node.label.toLowerCase() === label.toLowerCase());
      if (existing) return existing.id;
      const node = customVertex(label, type);
      state.customNodes.push(node);
      return node.id;
    }

    function customVertex(label, type) {
      return {
        id: 'custom:' + type + ':' + slug(label) + ':' + Date.now(),
        label,
        type,
        custom: true,
        tags: ['custom', type],
        metrics: { files: 0, lines: 0, symbols: 0, classes: 0, functions: 0, methods: 0, commits: 0, authors: 0 },
        size: 18,
        details: 'User-added ' + type + ' vertex'
      };
    }

    function slug(value) {
      return String(value || 'vertex').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'vertex';
    }

    function renderPathSelection(path, title, meta) {
      title.textContent = 'Path: ' + path.sourceComponent + ' -> ' + path.targetComponent;
      const stack = detailStack(meta);
      const overview = [
        'connection: ' + path.type,
        'source component: ' + path.sourceComponent,
        'source function: ' + path.sourceFunction,
        'target component: ' + path.targetComponent,
        'target function: ' + path.targetFunction,
        path.call ? 'call: ' + path.call : '',
        path.arguments && path.arguments.length ? 'parameters: ' + path.arguments.join(', ') : '',
        path.location ? 'location: ' + path.location : ''
      ].filter(Boolean);
      const body = appendDetailSection(stack, 'Path', overview, true);
      const actions = document.createElement('div');
      actions.className = 'path-actions';
      const save = document.createElement('button');
      save.textContent = isPathSaved(path) ? 'Saved' : 'Save path';
      save.disabled = isPathSaved(path);
      save.onclick = () => {
        savePath(path);
        save.textContent = 'Saved';
        save.disabled = true;
      };
      actions.appendChild(save);
      body.appendChild(actions);
      appendPathLocations(stack, 'Source Function Locations', path.sourceLocations, path.sourceComponent);
      appendPathLocations(stack, 'Target Function Locations', path.targetLocations, path.targetComponent);
      renderSavedPaths();
    }

    function appendPathLocations(stack, title, locations, fallbackComponent) {
      const lines = [];
      if (!locations || !locations.length) {
        lines.push('component: ' + fallbackComponent);
        lines.push('locations: no indexed local definition found');
      } else {
        for (const location of locations.slice(0, 12)) {
          const lineText = location.line_start ? ':' + location.line_start : '';
          lines.push('- ' + location.component + ' | ' + location.qualified_name + ' | ' + location.file_path + lineText);
        }
      }
      appendDetailSection(stack, title + ' (' + (locations ? locations.length : 0) + ')', lines, false);
    }

    function renderStatSelection(selection, title, meta) {
      const kind = selection.stat;
      const label = statLabel(kind);
      const items = statInventory(kind);
      title.textContent = label + ' (' + items.length + ')';
      const stack = detailStack(meta);
      appendDetailSection(stack, 'Overview', statOverview(kind, items), true);
      const body = appendDetailSection(stack, label, [], true);
      appendInventoryList(body, kind, items);
    }

    function statLabel(kind) {
      return {
        files: 'Files',
        symbols: 'Symbols',
        components: 'Components',
        edges: 'Edges',
        commits: 'Commits'
      }[kind] || 'Items';
    }

    function statInventory(kind) {
      const inventory = state.raw && state.raw.inventory ? state.raw.inventory : {};
      if (kind === 'files') return inventory.files || [];
      if (kind === 'symbols') return inventory.symbols || [];
      const architecture = architectureGraphWithOverlay();
      if (kind === 'components') return [...(architecture.nodes || [])]
        .sort((a, b) => a.label.localeCompare(b.label));
      if (kind === 'edges') return [...(architecture.edges || [])]
        .sort((a, b) => (b.weight || 1) - (a.weight || 1));
      if (kind === 'commits') return inventory.commits || [];
      return [];
    }

    function statOverview(kind, items) {
      const lines = [
        'type: ' + statLabel(kind).toLowerCase(),
        'total: ' + items.length
      ];
      if (kind === 'symbols') {
        const counts = countBy(items, item => String(item.kind || 'unknown').toLowerCase());
        lines.push('classes: ' + (counts.class || 0));
        lines.push('functions: ' + (counts.function || 0));
        lines.push('methods: ' + (counts.method || 0));
      }
      if (kind === 'edges') {
        const counts = countBy(items, item => item.type || 'edge');
        lines.push('calls: ' + (counts.calls || 0));
        lines.push('imports: ' + (counts.imports || 0));
        lines.push('cochange: ' + (counts.cochange || 0));
      }
      if (kind === 'commits') {
        const highRisk = items.filter(item => item.risk === 'high').length;
        const architecture = items.filter(item => item.architectural_impact && item.architectural_impact !== 'low').length;
        lines.push('high risk: ' + highRisk);
        lines.push('architecture impact: ' + architecture);
      }
      return lines;
    }

    function countBy(items, keyFn) {
      const counts = {};
      for (const item of items) {
        const key = keyFn(item);
        counts[key] = (counts[key] || 0) + 1;
      }
      return counts;
    }

    function appendInventoryList(parent, kind, items) {
      if (!items.length) {
        appendPlainLine(parent, 'No ' + statLabel(kind).toLowerCase() + ' found.');
        return;
      }
      const limit = state.inventoryLimits[kind] || 120;
      const list = document.createElement('div');
      list.className = kind === 'edges' ? 'edge-list' : 'inventory-list';
      for (const item of items.slice(0, limit)) {
        list.appendChild(kind === 'edges' ? edgeRow(item, null) : inventoryRow(kind, item));
      }
      parent.appendChild(list);
      if (items.length > limit) {
        const more = document.createElement('button');
        more.className = 'inventory-more';
        more.textContent = 'Show next ' + Math.min(120, items.length - limit) + ' of ' + items.length;
        setHelp(more, 'Loads more items into this list without changing the graph. Useful for large repositories where the inventory is intentionally paged.');
        more.onclick = () => {
          state.inventoryLimits[kind] = limit + 120;
          renderSelection(state.selected);
        };
        parent.appendChild(more);
      }
    }

    function inventoryRow(kind, item) {
      const row = document.createElement('div');
      row.className = 'inventory-row';
      setHelp(row, inventoryHelp(kind, item));
      const title = document.createElement('div');
      title.className = 'inventory-title';
      title.innerHTML = segmentedIdentifierHtml(inventoryTitle(kind, item));
      const meta = document.createElement('div');
      meta.className = 'inventory-meta';
      meta.textContent = inventoryMeta(kind, item).filter(Boolean).join(' | ');
      row.append(title, meta);
      return row;
    }

    function inventoryHelp(kind, item) {
      if (kind === 'files') return 'This is an indexed file. The metadata shows which component owns it, its language, size, and line count.';
      if (kind === 'symbols') return 'This is a parsed symbol such as a function, method, class, or API-like entry. Its component and file location help you trace where behavior is defined.';
      if (kind === 'components') return 'This is an architecture node. CodeAtlas groups files and symbols into these components so the map stays understandable.';
      if (kind === 'commits') return 'This commit is part of the indexed git history. Commit metadata helps explain ownership, risk, and co-change relationships.';
      return 'This row is an indexed item behind the selected count.';
    }

    function inventoryTitle(kind, item) {
      if (kind === 'files') return item.path || 'unknown file';
      if (kind === 'symbols') return item.qualified_name || item.name || 'unknown symbol';
      if (kind === 'components') return item.label || item.id || 'unknown component';
      if (kind === 'commits') return (item.short_sha ? item.short_sha + ' ' : '') + (item.title || 'Untitled commit');
      return item.label || item.name || 'item';
    }

    function inventoryMeta(kind, item) {
      if (kind === 'files') {
        return [
          'component: ' + (item.component || 'unknown'),
          'language: ' + (item.language || 'unknown'),
          'lines: ' + (item.lines || 0),
          'size: ' + formatBytes(item.size_bytes || 0)
        ];
      }
      if (kind === 'symbols') {
        const lines = item.line_start && item.line_end ? item.line_start + '-' + item.line_end : '';
        return [
          'kind: ' + (item.kind || 'unknown'),
          'component: ' + (item.component || 'unknown'),
          item.file_path ? 'file: ' + item.file_path + (lines ? ':' + lines : '') : '',
          item.signature ? 'signature: ' + item.signature : ''
        ];
      }
      if (kind === 'components') {
        const metrics = item.metrics || {};
        return [
          'type: ' + (item.type || 'component'),
          'risk: ' + (item.risk || 'low'),
          'files: ' + (metrics.files || 0),
          'symbols: ' + (metrics.symbols || 0),
          'commits: ' + (metrics.commits || 0)
        ];
      }
      if (kind === 'commits') {
        return [
          item.timestamp ? 'time: ' + item.timestamp : '',
          'author: ' + (item.author || 'Unknown'),
          'files: ' + (item.files || 0),
          'risk: ' + (item.risk || 'low'),
          'impact: ' + (item.architectural_impact || 'low')
        ];
      }
      return [];
    }

    function formatBytes(value) {
      const bytes = Number(value || 0);
      if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
      if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return bytes + ' B';
    }

    function renderNodeSelection(selection, title, meta) {
      const node = selection.node;
      const cached = cachedNodeDetail(selection);
      if (cached) {
        title.textContent = cached.title;
        meta.innerHTML = '';
        meta.appendChild(cached.stack);
        window.requestAnimationFrame(applyDetailSearchFilter);
        return;
      }
      title.textContent = node.label;
      const stack = detailStack(meta);
      const overview = ['type: ' + node.type, 'category: ' + nodeCategory(node)];
      if (selection.side) overview.push('side: ' + selection.side);
      if (node.change) overview.push('change: ' + node.change);
      if (node.risk) overview.push('risk: ' + node.risk);
      if (node.timestamp) overview.push('time: ' + node.timestamp);
      if (node.metrics) {
        for (const [key, value] of Object.entries(node.metrics)) overview.push(key + ': ' + value);
      }
      if (node.tags && node.tags.length) overview.push('tags: ' + node.tags.join(', '));
      if (node.details) overview.push('', node.details);
      appendCompareNodeDiffSection(stack, node, selection.side);
      appendDetailSection(stack, 'Overview', overview, true);
      appendTraceModeSection(stack, node, selection.side);
      renderNodeConnections(stack, node, selection.side);
      cacheNodeDetail(selection, title.textContent, stack);
    }

    function cachedNodeDetail(selection) {
      const key = nodeDetailCacheKey(selection);
      if (!key) return null;
      const cached = state.nodeDetailCache.get(key);
      if (!cached) return null;
      state.nodeDetailCache.delete(key);
      state.nodeDetailCache.set(key, cached);
      return cached;
    }

    function cacheNodeDetail(selection, title, stack) {
      const key = nodeDetailCacheKey(selection);
      if (!key || !stack) return;
      state.nodeDetailCache.set(key, { title, stack });
      while (state.nodeDetailCache.size > 32) {
        const oldest = state.nodeDetailCache.keys().next().value;
        state.nodeDetailCache.delete(oldest);
      }
    }

    function nodeDetailCacheKey(selection) {
      if (!selection || selection.kind !== 'node' || !selection.node) return '';
      return [
        state.detailCacheVersion,
        state.view,
        selection.side || '',
        selection.node.id,
        state.traceMode || '',
        state.focusHops || 1
      ].join('|');
    }

    function invalidateNodeDetailCache() {
      state.detailCacheVersion += 1;
      state.nodeDetailCache.clear();
    }

    function appendCompareNodeDiffSection(stack, node, side) {
      if (!state.compare || !side || !hasChange(node)) return;
      const counterpart = compareCounterpartNode(node, side);
      const before = side === 'base' ? node : counterpart;
      const after = side === 'head' ? node : counterpart;
      const body = appendDetailSection(stack, 'Before / After', [], true);
      body.appendChild(compareDiffGrid([
        ['Before', compareNodeSnapshotText(before, 'base')],
        ['After', compareNodeSnapshotText(after, 'head')]
      ]));
      const deltas = compareNodeDeltaLines(before, after);
      if (deltas.length) renderDetailLines(body, ['metric changes:', ...deltas.map(line => '- ' + line)]);
      renderDetailLines(body, ['why: ' + compareNodeChangeReason(node, before, after)]);
      body.appendChild(compareActions([
        ['Focus this diff', () => focusCompareDiffAroundNode(node, side)],
        [state.compareChangesOnly ? 'Show context' : 'Hide context', () => toggleCompareChangesOnly()]
      ]));
    }

    function appendCompareEdgeDiffSection(stack, edge, side) {
      if (!state.compare || !side || !hasChange(edge)) return;
      const counterpart = compareCounterpartEdge(edge, side);
      const before = side === 'base' ? edge : counterpart;
      const after = side === 'head' ? edge : counterpart;
      const body = appendDetailSection(stack, 'Before / After', [], true);
      body.appendChild(compareDiffGrid([
        ['Before', compareEdgeSnapshotText(before, 'base')],
        ['After', compareEdgeSnapshotText(after, 'head')]
      ]));
      renderDetailLines(body, [
        'why: ' + compareEdgeChangeReason(edge, before, after),
        'proof: ' + compareEdgeProofSummary(edge, counterpart)
      ]);
      body.appendChild(compareActions([
        ['Focus this diff', () => focusCompareDiffAroundEdge(edge, side)],
        [state.compareChangesOnly ? 'Show context' : 'Hide context', () => toggleCompareChangesOnly()]
      ]));
    }

    function compareDiffGrid(items) {
      const grid = document.createElement('div');
      grid.className = 'compare-diff-grid';
      for (const [label, value] of items) {
        const card = document.createElement('div');
        card.className = 'compare-diff-card';
        const title = document.createElement('div');
        title.className = 'compare-diff-label';
        title.textContent = label;
        const copy = document.createElement('div');
        copy.className = 'compare-diff-value';
        copy.innerHTML = segmentedIdentifierHtml(value);
        card.append(title, copy);
        grid.appendChild(card);
      }
      return grid;
    }

    function compareActions(items) {
      const actions = document.createElement('div');
      actions.className = 'compare-actions';
      for (const [label, handler] of items) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.onclick = handler;
        actions.appendChild(button);
      }
      return actions;
    }

    function compareCounterpartNode(node, side) {
      const other = compareOtherSide(side);
      if (!state.compare || !other) return null;
      return state.compare[other].allNodes.find(item => item.id === node.id) || null;
    }

    function compareCounterpartEdge(edge, side) {
      const other = compareOtherSide(side);
      if (!state.compare || !other) return null;
      const id = edgeIdentity(edge);
      return state.compare[other].allEdges.find(item => edgeIdentity(item) === id) || null;
    }

    function compareOtherSide(side) {
      return side === 'base' ? 'head' : side === 'head' ? 'base' : '';
    }

    function edgeIdentity(edge) {
      return edge ? String(edge.id || edge.source + '->' + edge.target + ':' + edge.type) : '';
    }

    function compareNodeSnapshotText(node, side) {
      if (!node) return side + ': missing';
      const metrics = node.metrics || {};
      const bits = [
        side + ': present',
        node.type || 'node',
        'files ' + Number(metrics.files || 0),
        'size ' + Number(node.size || 0)
      ];
      if (node.risk) bits.push('risk ' + node.risk);
      return bits.join(' / ');
    }

    function compareEdgeSnapshotText(edge, side) {
      if (!edge) return side + ': missing';
      return [
        side + ': present',
        edge.type || 'edge',
        'weight ' + Number(edge.weight || 1),
        'proofs ' + ((edge.examples || []).length),
        'signals ' + ((edge.reasons || []).filter(Boolean).length)
      ].join(' / ');
    }

    function compareNodeDeltaLines(before, after) {
      if (!before || !after) return [];
      const keys = new Set([
        'size',
        ...Object.keys(before.metrics || {}),
        ...Object.keys(after.metrics || {})
      ]);
      const lines = [];
      for (const key of keys) {
        const left = key === 'size' ? before.size : (before.metrics || {})[key];
        const right = key === 'size' ? after.size : (after.metrics || {})[key];
        if (String(left || 0) !== String(right || 0)) lines.push(key + ': ' + (left || 0) + ' -> ' + (right || 0));
      }
      return lines.slice(0, 8);
    }

    function compareNodeChangeReason(node, before, after) {
      if (node.change === 'added') return 'this node exists in the head snapshot but not the base snapshot.';
      if (node.change === 'removed') return 'this node exists in the base snapshot but not the head snapshot.';
      if (before && after) return 'the node signature changed between snapshots: type, size, risk, details, or metrics differ.';
      return 'CodeAtlas marked this node as changed in the compare payload.';
    }

    function compareEdgeChangeReason(edge, before, after) {
      if (edge.change === 'added') return 'this relationship exists in the head snapshot but not the base snapshot.';
      if (edge.change === 'removed') return 'this relationship exists in the base snapshot but not the head snapshot.';
      if (before && after) return 'the relationship signature changed: type, weight, examples, or summary evidence differ.';
      return 'CodeAtlas marked this relationship as changed in the compare payload.';
    }

    function compareEdgeProofSummary(edge, counterpart) {
      const currentProof = edgeProofItems(edge)[0];
      const otherProof = counterpart ? edgeProofItems(counterpart)[0] : null;
      if (currentProof) return currentProof.title;
      if (otherProof) return otherProof.title;
      return 'No exact example attached; rely on weight, reasons, and neighboring evidence.';
    }

    function focusCompareDiffAroundNode(node, side) {
      state.selected = { kind: 'node', node, side };
      state.compareChangesOnly = true;
      state.focusSelection = true;
      state.focusHops = 1;
      state.traceMode = 'neighbors';
      updateCompareModeControls();
      updateScaleControls();
      applyFilters();
      renderSelection(state.selected);
    }

    function focusCompareDiffAroundEdge(edge, side) {
      state.selected = { kind: 'edge', edge, side };
      state.compareChangesOnly = true;
      state.focusSelection = true;
      state.focusHops = 1;
      state.traceMode = 'neighbors';
      updateCompareModeControls();
      updateScaleControls();
      applyFilters();
      renderSelection(state.selected);
    }

    function appendTraceModeSection(stack, node, side) {
      const body = appendDetailSection(stack, 'Trace Flow', [], false);
      const actions = document.createElement('div');
      actions.className = 'path-actions';
      for (const item of [
        ['neighbors', 'Expand'],
        ['callers', 'Callers'],
        ['callees', 'Callees'],
        ['api', 'API/data'],
        ['tests', 'Tests'],
        ['git', 'Git']
      ]) {
        const button = document.createElement('button');
        button.className = 'trace-btn';
        button.textContent = item[1];
        setHelp(button, traceModeHelp(item[0]));
        button.onclick = () => traceNodeMode(node, side, item[0]);
        actions.appendChild(button);
      }
      body.appendChild(actions);
      appendTraceTimeline(body, node, side);
    }

    function traceModeHelp(mode) {
      return {
        neighbors: 'Expand keeps the selected node plus nearby relationships visible.',
        callers: 'Callers traces incoming function/API edges toward this node.',
        callees: 'Callees traces outgoing function/API edges away from this node.',
        api: 'API/data keeps API, GraphQL, function, and project-boundary relationships near this node.',
        tests: 'Tests keeps test/spec validation paths near this node.',
        git: 'Git keeps co-change and commit-history relationships near this node.'
      }[mode] || 'Trace this relationship mode.';
    }

    function traceNodeMode(node, side, mode) {
      state.selected = { kind: 'node', node, side };
      state.focusSelection = true;
      state.focusHops = mode === 'neighbors' ? 1 : 2;
      state.traceMode = mode;
      updateScaleControls();
      applyFilters();
      renderSelection(state.selected);
    }

    function appendTraceTimeline(parent, node, side) {
      const edges = traceTimelineEdgesForNode(node, side);
      if (!edges.length) {
        appendPlainLine(parent, 'No trace edges match the current filters.');
        return;
      }
      const list = document.createElement('div');
      list.className = 'trace-timeline';
      for (const edge of edges) {
        const row = document.createElement('div');
        row.className = 'trace-step';
        row.onclick = () => {
          state.selected = { kind: 'edge', edge, side };
          renderSelection(state.selected);
          scheduleUrlStateUpdate();
        };
        const marker = document.createElement('div');
        marker.className = 'trace-step-marker';
        const copy = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'trace-step-title';
        title.innerHTML = segmentedIdentifierHtml(traceStepTitle(edge, node));
        const meta = document.createElement('div');
        meta.className = 'trace-step-meta';
        meta.textContent = traceStepMeta(edge, side);
        copy.append(title, meta);
        row.append(marker, copy);
        list.appendChild(row);
      }
      parent.appendChild(list);
    }

    function traceTimelineEdgesForNode(node, side) {
      const mode = state.traceMode || 'neighbors';
      return nodeEdgesForSelection(node, side)
        .filter(edge => traceEdgeMatchesMode(edge, mode))
        .sort((a, b) => edgeDetailRank(b) - edgeDetailRank(a) || (b.weight || 1) - (a.weight || 1))
        .slice(0, 8);
    }

    function traceStepTitle(edge, node) {
      const source = labelForNode(edge.source);
      const target = labelForNode(edge.target);
      if (node && edge.target === node.id) return source + ' -> ' + target;
      if (node && edge.source === node.id) return source + ' -> ' + target;
      return edgeEvidenceSummary(edge).title;
    }

    function traceStepMeta(edge, side) {
      const evidence = edgeEvidenceSummary(edge, side);
      const facts = evidence.facts || [];
      const proof = facts.find(item => item.label === 'Exact proofs');
      const signals = facts.find(item => item.label === 'Summary signals');
      return [
        edgeDirectionLabel(edge.type),
        evidence.confidenceLabel + ' confidence',
        proof ? proof.value + ' exact' : '',
        signals ? signals.value + ' signals' : ''
      ].filter(Boolean).join(' / ');
    }

    function renderNodeConnections(stack, node, side) {
      const edges = nodeEdgesForSelection(node, side)
        .sort((a, b) => edgeDetailRank(b) - edgeDetailRank(a) || (b.weight || 1) - (a.weight || 1));
      if (!edges.length) {
        appendDetailSection(stack, 'Connections', ['connections: none visible with current filters'], true);
        return;
      }
      const codeEdges = edges.filter(edge => !['cochange', 'authored', 'touched'].includes(edge.type));
      const historyEdges = edges.filter(edge => ['cochange', 'authored', 'touched'].includes(edge.type));
      appendGroupedEdgeGroupSection(stack, 'Functions / APIs', codeEdges, node, side, true);
      appendComponentEdgeSummary(stack, 'Components / Edges', edges, false, side);
      if (historyEdges.length) {
        appendEdgeGroupSection(stack, 'Commit / Co-change Evidence', historyEdges, false);
      }
    }

    function nodeEdgesForSelection(node, side) {
      let edges = state.edges;
      if (side && state.compare && state.compare[side]) edges = state.compare[side].edges;
      return edges.filter(edge => edge.source === node.id || edge.target === node.id);
    }

    function edgeDetailRank(edge) {
      const typeRank = { calls: 4, references: 3, imports: 2, inherits: 2, cochange: 1 };
      return (typeRank[edge.type] || 0) * 1000 + Math.min(edge.examples ? edge.examples.length : 0, 50);
    }

    function renderBundleSelection(bundle, title, meta, side) {
      title.textContent = 'Bundle: ' + bundle.sourceLabel + ' -> ' + bundle.targetLabel;
      const stack = detailStack(meta);
      appendDetailSection(stack, 'Overview', [
        'bundle: ' + (bundle.type || 'edge') + ' x' + bundle.edges.length,
        side ? 'side: ' + side : '',
        'source group: ' + bundle.sourceLabel,
        'target group: ' + bundle.targetLabel,
        'total weight: ' + (bundle.weight || bundle.edges.length),
        'detail: click an exact edge below to inspect evidence'
      ].filter(Boolean), true);
      const body = appendDetailSection(stack, 'Exact Edges', [], true);
      const list = document.createElement('div');
      list.className = 'edge-list';
      for (const edge of bundle.edges
        .slice()
        .sort((a, b) => edgeDetailRank(b) - edgeDetailRank(a) || (b.weight || 1) - (a.weight || 1))
        .slice(0, 60)) {
        list.appendChild(edgeRow(edge, side));
      }
      body.appendChild(list);
      if (bundle.edges.length > 60) {
        appendPlainLine(body, '... ' + (bundle.edges.length - 60) + ' more exact edges hidden to keep this panel responsive.');
      }
      const strongest = bundle.edges
        .slice()
        .sort((a, b) => (b.weight || 1) - (a.weight || 1))
        .slice(0, 5);
      if (strongest.length) {
        const samples = appendDetailSection(stack, 'Evidence Samples', [], false);
        for (const edge of strongest) appendEdgeDetails(samples, edge, false, side);
      }
    }

    function renderEdgeSelection(edge, title, meta, side) {
      const source = labelForNode(edge.source);
      const target = labelForNode(edge.target);
      title.textContent = source + ' -> ' + target;
      const stack = detailStack(meta);
      const overview = [
        'connection: ' + edge.type,
        side ? 'side: ' + side : '',
        edge.change ? 'change: ' + edge.change : '',
        'source: ' + source,
        'target: ' + target,
        'weight: ' + (edge.weight || 1)
      ].filter(Boolean);
      if (edge.file) overview.push('file: ' + edge.file);
      appendEdgeEvidenceSection(stack, edge, side, true);
      appendCompareEdgeDiffSection(stack, edge, side);
      appendEdgeFlowSection(stack, edge, side, false);
      appendDetailSection(stack, 'Overview', overview, false);
      appendExampleSection(stack, 'Function / API Examples', edge.examples || [], false, edge, side);
      if (edge.reasons && edge.reasons.length) {
        const evidence = [];
        for (const reason of edge.reasons.slice(0, 8)) {
          if (reason) evidence.push('- ' + reason);
        }
        appendDetailSection(stack, 'Summary Evidence', evidence, false);
      }
    }

    function detailStack(root) {
      root.innerHTML = '';
      const stack = document.createElement('div');
      stack.className = 'detail-stack';
      root.appendChild(stack);
      return stack;
    }

    function appendDetailSection(stack, title, lines, open) {
      const section = document.createElement('details');
      section.className = 'detail-card ' + detailSectionClass(title);
      section.dataset.detailTab = detailTabForTitle(title);
      section.open = Boolean(open);
      const summary = document.createElement('summary');
      summary.textContent = title;
      setHelp(summary, detailSectionHelp(title, lines));
      const body = document.createElement('div');
      body.className = 'detail-body';
      if (lines.length) renderDetailLines(body, lines);
      section.append(summary, body);
      stack.appendChild(section);
      return body;
    }

    function appendEdgeEvidenceSection(stack, edge, side, open) {
      const body = appendDetailSection(stack, 'Evidence / Confidence', [], open);
      body.appendChild(edgeEvidencePanel(edge, side));
    }

    function edgeEvidencePanel(edge, side) {
      const evidence = edgeEvidenceSummary(edge, side);
      const panel = document.createElement('div');
      panel.className = 'evidence-panel';

      const header = document.createElement('div');
      header.className = 'evidence-header';
      const copy = document.createElement('div');
      const title = document.createElement('div');
      title.className = 'evidence-title';
      title.innerHTML = segmentedIdentifierHtml(evidence.title);
      const subtitle = document.createElement('div');
      subtitle.className = 'evidence-subtitle';
      subtitle.textContent = evidence.subtitle;
      copy.append(title, subtitle);

      const score = document.createElement('div');
      score.className = 'evidence-score';
      score.textContent = evidence.confidenceLabel;
      const meter = document.createElement('div');
      meter.className = 'confidence-meter';
      const fill = document.createElement('div');
      fill.className = 'confidence-fill';
      fill.style.width = evidence.confidence + '%';
      meter.appendChild(fill);
      score.appendChild(meter);
      header.append(copy, score);
      panel.appendChild(header);

      const grid = document.createElement('div');
      grid.className = 'evidence-grid';
      for (const fact of evidence.facts) appendEvidenceFact(grid, fact.label, fact.value);
      panel.appendChild(grid);

      const proofList = document.createElement('div');
      proofList.className = 'evidence-proof-list';
      for (const proof of evidence.proofs) appendEvidenceProof(proofList, proof);
      if (!evidence.proofs.length) {
        const empty = document.createElement('div');
        empty.className = 'evidence-status';
        empty.textContent = 'No exact parser example is attached to this edge yet; confidence comes from aggregate graph signals.';
        proofList.appendChild(empty);
      }
      panel.appendChild(proofList);

      const status = document.createElement('div');
      status.className = 'evidence-status';
      status.textContent = evidence.status;
      panel.appendChild(status);
      const actions = document.createElement('div');
      actions.className = 'path-actions';
      const trace = document.createElement('button');
      trace.className = 'trace-btn';
      trace.textContent = 'Trace edge';
      setHelp(trace, 'Trace edge turns on focus mode and keeps this relationship plus nearby nodes visible so the flow is easier to follow.');
      trace.onclick = () => traceSelectedEdge(edge, side);
      actions.appendChild(trace);
      panel.appendChild(actions);
      return panel;
    }

    function appendEvidenceFact(parent, label, value) {
      const item = document.createElement('div');
      item.className = 'evidence-fact';
      const labelEl = document.createElement('div');
      labelEl.className = 'evidence-fact-label';
      labelEl.textContent = label;
      const valueEl = document.createElement('div');
      valueEl.className = 'evidence-fact-value';
      valueEl.innerHTML = segmentedIdentifierHtml(value);
      item.append(labelEl, valueEl);
      parent.appendChild(item);
    }

    function appendEvidenceProof(parent, proof) {
      const row = document.createElement('div');
      row.className = 'evidence-proof';
      row.innerHTML = segmentedIdentifierHtml(proof.title);
      if (proof.meta) {
        const meta = document.createElement('div');
        meta.className = 'evidence-proof-meta';
        meta.innerHTML = segmentedIdentifierHtml(proof.meta);
        row.appendChild(meta);
      }
      parent.appendChild(row);
    }

    function edgeEvidenceSummary(edge, side) {
      const source = labelForNode(edge.source);
      const target = labelForNode(edge.target);
      const proofs = edgeProofItems(edge);
      const exampleCount = Array.isArray(edge.examples) ? edge.examples.length : 0;
      const reasonCount = Array.isArray(edge.reasons) ? edge.reasons.filter(Boolean).length : 0;
      const confidence = edgeConfidenceScore(edge);
      const type = edgeDirectionLabel(edge.type);
      const risk = edgeRiskStatus(edge);
      const compare = edge.change && edge.change !== 'unchanged' ? 'Compare marks this edge as ' + edge.change + '. ' : '';
      const sideText = side ? 'Shown from the ' + side + ' graph. ' : '';
      return {
        title: source + ' -> ' + target,
        subtitle: edgeEvidenceExplanation(edge, source, target),
        confidence,
        confidenceLabel: edgeConfidenceLabel(confidence),
        facts: [
          { label: 'Connection', value: type },
          { label: 'Exact proofs', value: String(exampleCount) },
          { label: 'Summary signals', value: String(reasonCount) },
          { label: 'Weight', value: String(edge.weight || 1) }
        ],
        proofs,
        status: compare + sideText + risk
      };
    }

    function edgeEvidenceExplanation(edge, source, target) {
      const verb = edgeDirectionVerb(edge.type);
      const examples = edge.examples || [];
      const reasons = edge.reasons || [];
      if (examples.length) {
        return 'CodeAtlas found ' + examples.length + ' concrete example' + (examples.length === 1 ? '' : 's') + ' showing ' + source + ' ' + verb + ' ' + target + '.';
      }
      if (edge.type === 'cochange') {
        return 'Git history shows these components changed together, which is historical coupling rather than a direct call.';
      }
      if (reasons.length) {
        return 'CodeAtlas has summary evidence for this relationship, but no exact function/API example is attached.';
      }
      return 'This edge is visible from graph structure, but it has sparse attached evidence.';
    }

    function edgeProofItems(edge) {
      const items = [];
      for (const example of (edge.examples || []).slice(0, 4)) {
        const call = formatCall(example);
        const source = formatEndpoint(example.source);
        const target = formatEndpoint(example.target);
        const location = formatLocation(example);
        const title = call || source + ' -> ' + target;
        const meta = [
          example.type || edge.type || 'connection',
          location ? 'at ' + location : '',
          example.commit ? 'commit ' + example.commit : ''
        ].filter(Boolean).join(' | ');
        items.push({ title, meta });
      }
      if (items.length < 4) {
        for (const reason of (edge.reasons || []).filter(Boolean).slice(0, 4 - items.length)) {
          items.push({ title: String(reason), meta: 'summary signal' });
        }
      }
      return items;
    }

    function edgeConfidenceScore(edge) {
      const examples = edge.examples || [];
      const reasons = (edge.reasons || []).filter(Boolean);
      const weight = Number(edge.weight || 1);
      let score = 10;
      if (examples.length) score += Math.min(55, 25 + examples.length * 6);
      if (reasons.length) score += Math.min(20, reasons.length * 4);
      score += Math.min(15, Math.log2(Math.max(weight, 1) + 1) * 5);
      if (['calls', 'references', 'imports', 'inherits'].includes(edge.type)) score += 10;
      if (edge.type === 'cochange') score += 5;
      if (!examples.length && !reasons.length) score = Math.min(score, 30);
      return Math.round(clamp(score, 5, 96));
    }

    function edgeConfidenceLabel(score) {
      if (score >= 85) return 'strong';
      if (score >= 60) return 'good';
      if (score >= 35) return 'partial';
      return 'sparse';
    }

    function edgeRiskStatus(edge) {
      const sourceNode = nodeForId(edge.source);
      const targetNode = nodeForId(edge.target);
      const risks = [sourceNode && sourceNode.risk, targetNode && targetNode.risk].filter(Boolean);
      if (risks.includes('high')) return 'Touches a high-risk component; inspect exact proofs before editing.';
      if (risks.includes('medium')) return 'Touches a medium-risk component; review related callers, callees, and tests.';
      if (edge.type === 'cochange') return 'Treat this as historical coupling unless exact code evidence also appears below.';
      if ((edge.examples || []).length) return 'Exact source examples are available below.';
      return 'Use this as a lead and inspect nearby evidence before relying on it.';
    }

    function traceSelectedEdge(edge, side) {
      state.selected = { kind: 'edge', edge, side };
      state.focusSelection = true;
      state.focusHops = Math.max(state.focusHops, 1);
      state.traceMode = 'neighbors';
      updateScaleControls();
      applyFilters();
      renderSelection(state.selected);
    }

    function appendEdgeFlowSection(stack, edge, side, open) {
      const body = appendDetailSection(stack, 'Flow / Direction', [], open);
      body.appendChild(edgeFlowCard(edge, side));
    }

    function edgeFlowCard(edge, side) {
      const source = labelForNode(edge.source);
      const target = labelForNode(edge.target);
      const card = document.createElement('div');
      card.className = 'flow-card';

      const row = document.createElement('div');
      row.className = 'flow-row';
      row.append(
        flowEndpointCard('Source / starts here', source, nodeSubLines(edge.source), 'source'),
        flowArrow(edgeDirectionLabel(edge.type)),
        flowEndpointCard('Target / points here', target, nodeSubLines(edge.target), 'target')
      );
      card.appendChild(row);

      const explainer = document.createElement('div');
      explainer.className = 'flow-explainer';
      explainer.innerHTML =
        '<span class="flow-label">Direction</span> ' +
        '<strong>' + segmentedIdentifierHtml(source) + '</strong> ' +
        '<span aria-hidden="true">-&gt;</span> ' +
        '<strong>' + segmentedIdentifierHtml(target) + '</strong>' +
        ' <span class="detail-type">(' + escapeHtml(edgeDirectionVerb(edge.type)) + ')</span>' +
        (edgeIsDirectional(edge.type)
          ? ''
          : ' This relationship is not strictly directional, but CodeAtlas draws it source -> target for consistency.');
      card.appendChild(explainer);

      const badges = document.createElement('div');
      badges.className = 'flow-badges';
      badges.append(
        flowBadge(edge.type || 'connection', badgeClass(edge.type)),
        flowBadge('weight ' + (edge.weight || 1), '')
      );
      if (edge.change && edge.change !== 'unchanged') badges.appendChild(flowBadge(edge.change, 'authored'));
      if (side) badges.appendChild(flowBadge(side + ' commit', ''));
      card.appendChild(badges);

      if (edge.file) appendFlowFact(card, 'Evidence file', edge.file, 'evidence-line');
      return card;
    }

    function flowEndpointCard(role, name, lines, variant) {
      const endpoint = document.createElement('div');
      endpoint.className = 'flow-endpoint ' + variant;
      const roleEl = document.createElement('div');
      roleEl.className = 'flow-label';
      roleEl.textContent = role;
      const nameEl = document.createElement('div');
      nameEl.className = 'flow-name';
      nameEl.innerHTML = segmentedIdentifierHtml(name || 'unknown');
      endpoint.append(roleEl, nameEl);
      for (const line of uniqueFlowLines(lines).slice(0, 4)) {
        const sub = document.createElement('div');
        sub.className = 'flow-sub';
        sub.textContent = line;
        endpoint.appendChild(sub);
      }
      return endpoint;
    }

    function flowArrow(label) {
      const arrow = document.createElement('div');
      arrow.className = 'flow-arrow';
      const symbol = document.createElement('div');
      symbol.className = 'flow-arrow-symbol';
      symbol.textContent = '->';
      const text = document.createElement('div');
      text.className = 'flow-arrow-label';
      text.textContent = label;
      arrow.append(symbol, text);
      return arrow;
    }

    function flowBadge(text, className) {
      const badge = document.createElement('span');
      badge.className = 'badge' + (className ? ' ' + className : '');
      badge.textContent = text;
      return badge;
    }

    function appendFlowFact(parent, label, value, valueClass) {
      if (!value) return;
      const fact = document.createElement('div');
      fact.className = 'flow-fact';
      const labelEl = document.createElement('div');
      labelEl.className = 'flow-label';
      labelEl.textContent = label;
      const valueEl = document.createElement('div');
      valueEl.className = valueClass || 'evidence-line';
      valueEl.innerHTML = segmentedIdentifierHtml(value);
      fact.append(labelEl, valueEl);
      parent.appendChild(fact);
    }

    function appendParameterChips(parent, args) {
      const fact = document.createElement('div');
      fact.className = 'flow-fact';
      const label = document.createElement('div');
      label.className = 'flow-label';
      label.textContent = 'Parameters passed';
      const chips = document.createElement('div');
      chips.className = 'param-list';
      const normalized = normalizeArguments(args);
      if (!normalized.length) {
        const empty = document.createElement('span');
        empty.className = 'param-chip empty';
        empty.textContent = 'none captured';
        chips.appendChild(empty);
      } else {
        for (const argument of normalized) {
          const chip = document.createElement('span');
          chip.className = 'param-chip';
          chip.textContent = argument;
          chips.appendChild(chip);
        }
      }
      fact.append(label, chips);
      parent.appendChild(fact);
    }

    function exampleFlowCard(example, edge, index, side) {
      const sourceFunction = formatEndpoint(example.source);
      const targetFunction = formatEndpoint(example.target);
      const sourceComponent = edge ? labelForNode(edge.source) : 'source';
      const targetComponent = edge ? labelForNode(edge.target) : 'target';
      const type = (example && example.type) || (edge && edge.type) || 'connection';
      const card = document.createElement('div');
      card.className = 'flow-card example-flow-card';

      const row = document.createElement('div');
      row.className = 'flow-row';
      row.append(
        flowEndpointCard('Caller / ' + sourceComponent, sourceFunction, endpointSubLines(example.source), 'source'),
        flowArrow(edgeDirectionLabel(type)),
        flowEndpointCard('Callee / ' + targetComponent, targetFunction, endpointSubLines(example.target), 'target')
      );
      card.appendChild(row);

      const call = formatCall(example);
      appendFlowFact(card, 'Call expression', call || 'No exact call expression captured for this example.', call ? 'call-line' : 'evidence-line');
      appendParameterChips(card, example.arguments || []);

      const location = formatLocation(example);
      if (location) appendFlowFact(card, 'Found in', location, 'evidence-line');
      if (example.commit) appendFlowFact(card, 'Commit', example.commit, 'evidence-line');
      if (side) appendFlowFact(card, 'Compare side', side, 'evidence-line');
      return card;
    }

    function nodeSubLines(id) {
      const node = nodeForId(id);
      if (!node) return [];
      const lines = [];
      if (node.type) lines.push('type: ' + node.type);
      const category = nodeCategory(node);
      if (category) lines.push('category: ' + category);
      if (node.files) lines.push('files: ' + node.files);
      if (node.functions) lines.push('functions: ' + node.functions);
      if (node.methods) lines.push('methods: ' + node.methods);
      if (node.classes) lines.push('classes: ' + node.classes);
      return lines;
    }

    function endpointSubLines(endpoint) {
      if (!endpoint) return ['no endpoint metadata captured'];
      const lines = [];
      if (endpoint.kind || endpoint.type) lines.push('kind: ' + (endpoint.kind || endpoint.type));
      if (endpoint.module) lines.push('module: ' + endpoint.module);
      if (endpoint.signature) lines.push('signature: ' + endpoint.signature);
      if (endpoint.path) {
        const location = endpoint.line_start ? endpoint.path + ':' + endpoint.line_start : endpoint.path;
        lines.push('file: ' + location);
      }
      return lines;
    }

    function uniqueFlowLines(lines) {
      const seen = new Set();
      const result = [];
      for (const line of lines || []) {
        const clean = String(line || '').trim();
        if (!clean || seen.has(clean)) continue;
        seen.add(clean);
        result.push(clean);
      }
      return result;
    }

    function normalizeArguments(args) {
      if (!Array.isArray(args)) return [];
      return args.map(argument => String(argument || '').trim()).filter(Boolean);
    }

    function edgeDirectionLabel(type) {
      return String(type || 'connects').replace(/_/g, ' ');
    }

    function edgeDirectionVerb(type) {
      return {
        calls: 'calls into',
        function_call: 'calls into',
        api_call: 'calls the API on',
        graphql: 'queries or resolves through',
        database: 'reads or writes data in',
        references: 'references symbols in',
        imports: 'imports from',
        inherits: 'inherits from',
        test_covers: 'tests or validates',
        depends_on: 'depends on',
        custom: 'connects to',
        cochange: 'changed together with',
        authored: 'was authored by or connected to',
        touched: 'was touched with'
      }[type] || 'connects to';
    }

    function edgeIsDirectional(type) {
      return !['cochange', 'authored', 'touched'].includes(type);
    }

    function detailSectionClass(title) {
      const clean = String(title || '').replace(/\s*\(.*\)\s*$/, '').toLowerCase();
      if (clean.startsWith('functions / apis')) return 'section-functions';
      if (clean.startsWith('components / edges')) return 'section-components';
      if (clean.startsWith('evidence / confidence')) return 'section-evidence';
      if (clean.startsWith('trace flow')) return 'section-path';
      if (clean.startsWith('flow / direction')) return 'section-functions';
      if (clean.startsWith('commit / co-change')) return 'section-history';
      if (clean.startsWith('overview')) return 'section-overview';
      if (clean.includes('example')) return 'section-examples';
      if (clean.startsWith('summary evidence')) return 'section-evidence';
      if (clean.startsWith('path')) return 'section-path';
      if (clean.startsWith('connection')) return 'section-components';
      return 'section-generic';
    }

    function detailSectionHelp(title, lines) {
      const cleanTitle = String(title || '').replace(/\s*\(.*\)\s*$/, '').toLowerCase();
      if (cleanTitle === 'overview') {
        return 'Overview is the quick metadata for the selected item: type, category, risk/change flags, counts, source/target, and other fields CodeAtlas knows about it.';
      }
      if (cleanTitle.startsWith('flow / direction')) {
        return 'Flow / Direction explains how to read this edge: which component starts the relationship, which component receives it, whether the edge is directional, and how strong the evidence is.';
      }
      if (cleanTitle.startsWith('functions / apis')) {
        return 'Functions / APIs groups code-level relationships by the other component involved. Expand a component to see the exact calls, imports, references, signatures, parameters, and source locations when available.';
      }
      if (cleanTitle.startsWith('components / edges')) {
        return 'Components / Edges lists every visible relationship touching this node. Click any row to inspect why CodeAtlas thinks those two components are connected.';
      }
      if (cleanTitle.startsWith('evidence / confidence')) {
        return 'Evidence / Confidence summarizes why this edge exists, what exact parser or git proof supports it, and how strong the indexed signals are.';
      }
      if (cleanTitle.startsWith('trace flow')) {
        return 'Trace Flow turns the map into a focused evidence path around the selected node: callers, callees, API/data flow, tests, or git co-change.';
      }
      if (cleanTitle.startsWith('commit / co-change evidence')) {
        return 'Commit / Co-change Evidence comes from git history. It means these areas changed together in commits, which often hints at coupling even when there is no direct function call.';
      }
      if (cleanTitle.includes('function') || cleanTitle.includes('example')) {
        return 'Examples are concrete parser evidence from files. Expand them to see which function or API was used, parameters passed, signatures, and where the evidence was found.';
      }
      if (cleanTitle === 'summary evidence') {
        return 'Summary Evidence is a compact explanation assembled from the index, such as files, commits, or parser facts that support this relationship.';
      }
      if (cleanTitle === 'connections') {
        return 'Connections are relationships still visible after the current filters. If this says none, the node may only connect to hidden categories or filtered-out components.';
      }
      if (cleanTitle === 'path') {
        return 'A path is a saved trace between two functions/components. It helps you revisit an important flow and highlight it on the architecture map.';
      }
      if (cleanTitle === 'overlay' || cleanTitle === 'added connections') {
        return 'Architecture overlays are your manually added vertices and edges. They sit on top of the indexed graph so you can model knowledge CodeAtlas cannot infer automatically.';
      }
      if (cleanTitle === 'how it works') {
        return 'This explains how the current UI action modifies the local architecture overlay and how saved overlays are restored later.';
      }
      if (lines && lines.length) {
        return 'Expand this section to inspect the evidence and metadata behind the selected node, edge, function, path, or architecture item.';
      }
      return 'Expand this section to inspect more detail. CodeAtlas uses these panels to explain what the graph is showing and where the evidence came from.';
    }

    function appendEdgeGroupSection(stack, title, edges, open) {
      const body = appendDetailSection(
        stack,
        title + ' (' + edges.length + ')',
        edges.length ? [] : ['No visible connections in this group.'],
        open
      );
      if (!edges.length) return;
      for (const edge of edges.slice(0, 10)) {
        appendEdgeDetails(body, edge, false, null);
      }
      if (edges.length > 10) appendPlainLine(body, '... ' + (edges.length - 10) + ' more visible connections');
    }

    function appendGroupedEdgeGroupSection(stack, title, edges, node, side, open) {
      const groups = edgeGroupsByCounterpart(edges, node.id);
      const body = appendDetailSection(
        stack,
        title + ' (' + groups.length + ' components, ' + edges.length + ' edges)',
        edges.length ? [] : ['No visible connections in this group.'],
        open
      );
      if (!edges.length) return;
      for (const group of groups.slice(0, 10)) {
        appendCounterpartEdgeGroup(body, group, side);
      }
      if (groups.length > 10) appendRemainingCounterpartGroups(body, groups.slice(10), side);
    }

    function appendRemainingCounterpartGroups(parent, groups, side) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested edge-component-group';
      const summary = document.createElement('summary');
      const edgeCount = groups.reduce((total, group) => total + group.edges.length, 0);
      summary.textContent = groups.length + ' more connected components (' + edgeCount + ' edges)';
      setHelp(summary, 'Expand this to see every remaining component connected to the selected node. These are hidden by default only to keep the panel readable on large repositories.');
      const body = document.createElement('div');
      body.className = 'detail-body';
      section.append(summary, body);
      renderLazyDetails(section, body, () => {
        const renderedGroups = groups.slice(0, 80);
        for (const group of renderedGroups) {
          appendCounterpartEdgeGroup(body, group, side);
        }
        if (groups.length > renderedGroups.length) {
          appendPlainLine(body, '... ' + (groups.length - renderedGroups.length) + ' more components hidden to keep this panel responsive.');
        }
      });
      parent.appendChild(section);
    }

    function edgeGroupsByCounterpart(edges, selectedNodeId) {
      const groups = new Map();
      for (const edge of edges) {
        const counterpartId = edgeCounterpartId(edge, selectedNodeId);
        const group = groups.get(counterpartId) || {
          id: counterpartId,
          label: labelForNode(counterpartId),
          edges: [],
          weight: 0
        };
        group.edges.push(edge);
        group.weight += edge.weight || 1;
        groups.set(counterpartId, group);
      }
      return [...groups.values()].sort(
        (a, b) => b.weight - a.weight || a.label.localeCompare(b.label)
      );
    }

    function edgeCounterpartId(edge, selectedNodeId) {
      if (edge.source === selectedNodeId) return edge.target;
      if (edge.target === selectedNodeId) return edge.source;
      return edge.source || edge.target;
    }

    function appendCounterpartEdgeGroup(parent, group, side) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested edge-component-group';
      const summary = document.createElement('summary');
      summary.textContent = group.label + ' (' + group.edges.length + ' edges, weight ' + group.weight + ')';
      setHelp(summary, 'This group collects all visible relationships between the selected component and ' + group.label + '. The edge count is how many separate relationships were found; total weight is their combined strength. Expand it to see each relationship and its evidence.');
      const body = document.createElement('div');
      body.className = 'detail-body';
      for (const edge of group.edges.slice(0, 8)) {
        appendEdgeDetails(body, edge, false, side);
      }
      if (group.edges.length > 8) appendRemainingEdgeDetails(body, group.edges.slice(8), side, 'Remaining connections');
      section.append(summary, body);
      parent.appendChild(section);
    }

    function appendComponentEdgeSummary(stack, title, edges, open, side) {
      const body = appendDetailSection(stack, title + ' (' + edges.length + ')', [], open);
      const list = document.createElement('div');
      list.className = 'edge-list';
      for (const edge of edges.slice(0, 16)) {
        list.appendChild(edgeRow(edge, side));
      }
      body.appendChild(list);
      if (edges.length > 16) appendRemainingEdgeRows(body, edges.slice(16), side);
    }

    function appendRemainingEdgeDetails(parent, edges, side, title) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested remaining-section';
      const summary = document.createElement('summary');
      summary.textContent = title + ' (' + edges.length + ')';
      setHelp(summary, 'Expand this to inspect the remaining relationships in this group. Each item can contain examples, parameters, signatures, files, and evidence when CodeAtlas captured them.');
      const body = document.createElement('div');
      body.className = 'detail-body';
      section.append(summary, body);
      renderLazyDetails(section, body, () => {
        const renderedEdges = edges.slice(0, 120);
        for (const edge of renderedEdges) {
          appendEdgeDetails(body, edge, false, side);
        }
        if (edges.length > renderedEdges.length) {
          appendPlainLine(body, '... ' + (edges.length - renderedEdges.length) + ' more connections hidden to keep this panel responsive.');
        }
      });
      parent.appendChild(section);
    }

    function appendRemainingEdgeRows(parent, edges, side) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested remaining-section';
      const summary = document.createElement('summary');
      summary.textContent = edges.length + ' more visible edges';
      setHelp(summary, 'Expand this to see every remaining visible edge touching the selected node. Click a row to inspect that relationship in detail.');
      const body = document.createElement('div');
      body.className = 'detail-body';
      const list = document.createElement('div');
      list.className = 'edge-list';
      section.append(summary, body);
      renderLazyDetails(section, body, () => {
        const renderedEdges = edges.slice(0, 200);
        for (const edge of renderedEdges) {
          list.appendChild(edgeRow(edge, side));
        }
        body.appendChild(list);
        if (edges.length > renderedEdges.length) {
          appendPlainLine(body, '... ' + (edges.length - renderedEdges.length) + ' more edges hidden to keep this panel responsive.');
        }
      });
      parent.appendChild(section);
    }

    function renderLazyDetails(section, body, render) {
      let rendered = false;
      appendPlainLine(body, 'Open to render this list.');
      section.addEventListener('toggle', () => {
        if (!section.open || rendered) return;
        rendered = true;
        body.innerHTML = '';
        render();
      });
    }

    function edgeRow(edge, side) {
      const row = document.createElement('div');
      row.className = 'edge-row';
      setHelp(row, edgeHelp(edge, side));
      row.onclick = () => {
        state.selected = { kind: 'edge', edge, side };
        renderSelection(state.selected);
      };
      const route = document.createElement('div');
      route.className = 'edge-route';
      route.innerHTML =
        '<strong>' + segmentedIdentifierHtml(labelForNode(edge.source)) + '</strong>' +
        ' <span aria-hidden="true">-></span> ' +
        '<strong>' + segmentedIdentifierHtml(labelForNode(edge.target)) + '</strong>';
      const meta = document.createElement('div');
      meta.className = 'edge-meta';
      const type = document.createElement('span');
      type.className = 'badge ' + badgeClass(edge.type);
      type.textContent = edge.type || 'edge';
      setHelp(type, edgeTypeHelp(edge.type));
      const weight = document.createElement('span');
      weight.className = 'badge';
      weight.textContent = 'w ' + (edge.weight || 1);
      setHelp(weight, weightHelp(edge));
      meta.append(type, weight);
      row.append(route, meta);
      return row;
    }

    function badgeClass(value) {
      return String(value || '').toLowerCase().replace(/[^a-z0-9_-]/g, '');
    }

    function appendEdgeDetails(parent, edge, open, side) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested edge-detail edge-type-' + badgeClass(edge.type);
      section.open = Boolean(open);
      const summary = document.createElement('summary');
      summary.textContent = edgeSummary(edge);
      setHelp(summary, edgeHelp(edge, side));
      const body = document.createElement('div');
      body.className = 'detail-body';
      body.appendChild(edgeFlowCard(edge, side));
      appendExampleSection(body, 'Examples', edge.examples || [], false, edge, side);
      section.append(summary, body);
      parent.appendChild(section);
    }

    function appendExampleSection(parent, title, examples, open, edge, side) {
      const wrapper = document.createElement('details');
      wrapper.className = 'detail-card examples-section' + (parent.classList.contains('detail-body') ? ' detail-nested' : '');
      wrapper.open = Boolean(open);
      const wrapperSummary = document.createElement('summary');
      wrapperSummary.textContent = title + ' (' + examples.length + ')';
      setHelp(wrapperSummary, exampleSectionHelp(title, examples.length));
      const wrapperBody = document.createElement('div');
      wrapperBody.className = 'detail-body';
      wrapper.append(wrapperSummary, wrapperBody);
      parent.appendChild(wrapper);
      if (!examples.length) {
        appendPlainLine(wrapperBody, 'No function/API examples available for this connection.');
        return;
      }
      for (const [index, example] of examples.slice(0, 8).entries()) {
        appendExampleDetail(wrapperBody, title, example, index, Boolean(open && index === 0), edge, side);
      }
      if (examples.length > 8) {
        appendRemainingExamples(wrapperBody, title, examples.slice(8), 8, edge, side);
      }
    }

    function appendExampleDetail(parent, title, example, index, open, edge, side) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested example-detail';
      section.open = Boolean(open);
      const summary = document.createElement('summary');
      const path = edge ? pathFromExample(example, edge, side) : null;
      setHelp(summary, exampleHelp(example, edge));
      const label = document.createElement('span');
      label.className = 'summary-label';
      label.textContent = title + ' ' + (index + 1) + ': ' + edgeExampleTitle(example);
      summary.appendChild(label);
      if (path) {
        const trace = document.createElement('button');
        trace.className = 'trace-btn';
        trace.textContent = 'Trace';
        setHelp(trace, 'Trace highlights this concrete flow on the architecture map. Use it when you want to follow how one component/function reaches another.');
        trace.onclick = event => {
          event.preventDefault();
          event.stopPropagation();
          selectPath(path);
        };
        summary.appendChild(trace);
      }
      const body = document.createElement('div');
      body.className = 'detail-body';
      body.appendChild(exampleFlowCard(example, edge, index, side));
      if (path) appendExamplePathControls(body, path);
      section.append(summary, body);
      parent.appendChild(section);
    }

    function appendRemainingExamples(parent, title, examples, startIndex, edge, side) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested remaining-section remaining-examples';
      const summary = document.createElement('summary');
      summary.textContent = '... ' + examples.length + ' more examples';
      setHelp(summary, 'Expand this to inspect the hidden examples for this relationship. They use the same caller, callee, parameters, location, Trace, and Save path controls as the first examples.');
      const body = document.createElement('div');
      body.className = 'detail-body';
      let rendered = false;
      section.addEventListener('toggle', () => {
        if (!section.open || rendered) return;
        rendered = true;
        for (const [offset, example] of examples.entries()) {
          appendExampleDetail(body, title, example, startIndex + offset, false, edge, side);
        }
      });
      section.append(summary, body);
      parent.appendChild(section);
    }

    function appendExamplePathControls(parent, path) {
      for (const line of [
        'source project/component: ' + path.sourceComponent,
        'source function: ' + path.sourceFunction,
        'target project/component: ' + path.targetComponent,
        'target function: ' + path.targetFunction
      ]) {
        const div = document.createElement('div');
        div.className = 'detail-line';
        div.innerHTML = colorizeDetailLine(line);
        parent.appendChild(div);
      }
      const actions = document.createElement('div');
      actions.className = 'path-actions';
      const trace = document.createElement('button');
      trace.className = 'trace-btn';
      trace.textContent = 'Trace path';
      setHelp(trace, 'Trace path highlights this saved source-to-target flow on the architecture map so you can visually follow it.');
      trace.onclick = () => selectPath(path);
      const save = document.createElement('button');
      save.className = 'trace-btn';
      save.textContent = isPathSaved(path) ? 'Saved' : 'Save path';
      save.disabled = isPathSaved(path);
      setHelp(save, 'Save path stores this flow locally in your browser so it appears in the Paths section and can be reopened later.');
      save.onclick = () => {
        savePath(path);
        save.textContent = 'Saved';
        save.disabled = true;
      };
      actions.append(trace, save);
      parent.appendChild(actions);
    }

    function pathFromExample(example, edge, side) {
      const sourceFunction = formatEndpoint(example.source);
      const targetFunction = formatEndpoint(example.target);
      const call = formatCall(example);
      const location = formatLocation(example);
      const id = [
        edge.source,
        edge.target,
        edge.type,
        sourceFunction,
        targetFunction,
        call,
        location
      ].join('|');
      return {
        id,
        side: side || null,
        type: edge.type || example.type || 'connection',
        sourceId: edge.source,
        targetId: edge.target,
        sourceComponent: labelForNode(edge.source),
        targetComponent: labelForNode(edge.target),
        sourceFunction,
        targetFunction,
        call,
        arguments: example.arguments || [],
        filePath: example.file_path || (example.source && example.source.path) || '',
        line: example.line || null,
        location,
        sourceLocations: symbolLocationsForEndpoint(example.source),
        targetLocations: symbolLocationsForEndpoint(example.target),
        label: (call || sourceFunction + ' -> ' + targetFunction)
      };
    }

    function symbolLocationsForEndpoint(endpoint) {
      if (!endpoint || !state.raw || !state.raw.inventory) return [];
      const symbols = state.raw.inventory.symbols || [];
      const terms = new Set(
        [
          endpoint.qualified_name,
          endpoint.name,
          endpoint.label,
          endpoint.key ? String(endpoint.key).split(':').pop() : ''
        ]
          .filter(Boolean)
          .map(value => String(value))
      );
      const shortTerms = new Set([...terms].map(value => value.split('.').pop()));
      return symbols
        .filter(symbol =>
          terms.has(symbol.qualified_name) ||
          terms.has(symbol.name) ||
          shortTerms.has(symbol.name) ||
          [...terms].some(term => symbol.qualified_name && symbol.qualified_name.endsWith('.' + term))
        )
        .slice(0, 20);
    }

    function appendPlainLine(parent, line) {
      const div = document.createElement('div');
      div.className = 'detail-line detail-empty';
      div.innerHTML = colorizeDetailLine(line);
      setHelp(div, plainLineHelp(line));
      parent.appendChild(div);
    }

    function plainLineHelp(line) {
      const text = String(line || '').toLowerCase();
      if (text.includes('no visible connections')) {
        return 'No visible connections means CodeAtlas did not find relationships for this group after applying your current filters. Try enabling team, third-party, docs/config, or selecting a different component.';
      }
      if (text.includes('no function/api examples')) {
        return 'This edge exists, but CodeAtlas does not have concrete function/API examples for it. It may be based on imports, git history, manual overlay data, or summary evidence.';
      }
      if (text.startsWith('... ')) {
        return 'There are more items than currently shown. This panel truncates long lists so large repositories remain usable.';
      }
      return 'This line is explanatory metadata for the currently selected item.';
    }

    function edgeSummary(edge) {
      return labelForNode(edge.source) + ' -> ' + labelForNode(edge.target) +
        ' (' + edge.type + ', weight ' + (edge.weight || 1) + ')';
    }

    function edgeHelp(edge, side) {
      const source = labelForNode(edge.source);
      const target = labelForNode(edge.target);
      const pieces = [
        source + ' -> ' + target,
        edgeTypeHelp(edge.type),
        weightHelp(edge)
      ];
      if (side) pieces.push('This row is from the ' + side + ' side of the commit comparison.');
      if (edge.change && edge.change !== 'unchanged') {
        pieces.push('Diff status: ' + edge.change + '. In compare mode, Diff highlights changed edges so architecture changes stand out.');
      }
      if (edge.examples && edge.examples.length) {
        pieces.push('Expand this edge to inspect ' + edge.examples.length + ' concrete function/API example' + (edge.examples.length === 1 ? '' : 's') + ', including calls, parameters, signatures, and source locations when available.');
      } else if (edge.reasons && edge.reasons.length) {
        pieces.push('Expand this edge to see summary evidence explaining why the relationship exists.');
      } else {
        pieces.push('Click this row to select the relationship and show all evidence CodeAtlas has for it.');
      }
      return pieces.join('\n\n');
    }

    function edgeTypeHelp(type) {
      return {
        calls: 'Type: calls. CodeAtlas found function or method calls from the source component into the target component.',
        function_call: 'Type: function_call. This is a user-added or detected function-level call relationship between the two vertices.',
        api_call: 'Type: api_call. This represents an API-style call across components, services, or boundaries.',
        graphql: 'Type: graphql. This represents GraphQL usage, usually a query, mutation, resolver, or schema boundary.',
        database: 'Type: database. This represents a data/database read-write relationship or model/storage dependency.',
        test_covers: 'Type: test_covers. A test or validation component is connected to the code it exercises.',
        depends_on: 'Type: depends_on. The source relies on the target conceptually or operationally, even if CodeAtlas cannot prove a direct call.',
        custom: 'Type: custom. This connection was manually added to the architecture overlay.',
        references: 'Type: references. Code in the source component refers to a symbol, module, or object owned by the target component.',
        imports: 'Type: imports. Code in the source component imports the target module/package. This is a static dependency signal.',
        inherits: 'Type: inherits. A class or type in the source component extends or inherits from something in the target component.',
        cochange: 'Type: cochange. Git history shows these components changed together in the same commits. This hints at coupling even when there is no direct function call.',
        authored: 'Type: authored. A developer or commit-history node is connected because of authorship evidence.',
        touched: 'Type: touched. A commit-history node is connected because it touched files in this component.'
      }[type] || 'Type: ' + (type || 'edge') + '. This is a relationship CodeAtlas found or that was added to the architecture overlay.';
    }

    function weightHelp(edge) {
      const weight = edge.weight || 1;
      if (edge.type === 'cochange') {
        return 'Weight ' + weight + ' means these areas repeatedly changed together in git history; higher weight means stronger historical coupling.';
      }
      if (edge.type === 'calls' || edge.type === 'references' || edge.type === 'imports' || edge.type === 'inherits') {
        return 'Weight ' + weight + ' is the number or strength of evidence items CodeAtlas found for this relationship.';
      }
      return 'Weight ' + weight + ' is CodeAtlas\' strength score for this connection. Higher values usually mean more evidence or repeated occurrences.';
    }

    function exampleSectionHelp(title, count) {
      return title + ' contains concrete source-code examples backing this edge. Expand examples to see what function/API was called or referenced, parameters passed, signatures, and file locations. Count: ' + count + '.';
    }

    function exampleHelp(example, edge) {
      const source = formatEndpoint(example.source);
      const target = formatEndpoint(example.target);
      const call = formatCall(example);
      const location = formatLocation(example);
      const lines = [
        'This example is concrete parser evidence for ' + source + ' -> ' + target + '.',
        edge ? 'It supports the ' + (edge.type || 'connection') + ' edge between ' + labelForNode(edge.source) + ' and ' + labelForNode(edge.target) + '.' : '',
        call ? 'Call shown: ' + call + '.' : '',
        example.arguments && example.arguments.length ? 'Parameters captured: ' + example.arguments.join(', ') + '.' : 'No parameters were captured for this example.',
        location ? 'Evidence location: ' + location + '.' : ''
      ].filter(Boolean);
      return lines.join('\n\n');
    }

    function edgeExampleTitle(example) {
      const title = formatCall(example) || (formatEndpoint(example.source) + ' -> ' + formatEndpoint(example.target));
      return compactFlowTitle(title);
    }

    function compactFlowTitle(value) {
      return truncateText(String(value || '').replace(/\s+/g, ' ').trim(), 84);
    }

    function renderDetailLines(root, lines) {
      root.innerHTML = '';
      for (const line of lines) {
        const div = document.createElement('div');
        div.className = 'detail-line';
        div.innerHTML = colorizeDetailLine(line);
        setHelp(div, detailLineHelp(line));
        root.appendChild(div);
      }
    }

    function detailLineHelp(line) {
      const clean = String(line || '').trim().replace(/^- /, '').toLowerCase();
      if (!clean) return '';
      if (clean.startsWith('parameters:')) return 'Parameters are argument names or values CodeAtlas captured from the call expression. They show what data is being passed across this connection.';
      if (clean.includes('signature:')) return 'A signature is the parsed function or method shape. It helps you understand expected inputs and outputs at this point in the flow.';
      if (clean.startsWith('location:') || clean.startsWith('file:')) return 'Location tells you where the evidence was found in the repository, usually file path and line number.';
      if (clean.startsWith('source') || clean.startsWith('target')) return 'Source is where the relationship starts; target is what it points to. For calls, source calls target. For imports, source imports target.';
      if (clean.startsWith('weight:')) return 'Weight is a strength score for this connection. Higher values usually mean more evidence, repeated occurrences, or stronger co-change history.';
      if (clean.startsWith('change:')) return 'Change appears in compare mode and tells whether this node or edge was added, removed, or changed between commits.';
      if (clean.startsWith('risk:')) return 'Risk is CodeAtlas heuristics from commit and component signals. Higher risk usually means more churn, authors, or broad impact.';
      if (clean.startsWith('category:')) return 'Category explains how the node is grouped for filtering: owned code, team dependencies, third-party packages, docs/config, tests, or generated files.';
      if (clean.startsWith('call:')) return 'Call is the concrete invocation CodeAtlas parsed from source code. It is the strongest evidence for a function/API relationship.';
      if (clean.startsWith('commit:')) return 'Commit points to git history evidence that contributed to this relationship or example.';
      if (clean.includes('->')) return 'This arrow shows a directed relationship from source to target.';
      return 'This is supporting metadata for the selected item.';
    }

    function segmentedIdentifierHtml(value) {
      const text = value == null ? '' : String(value);
      if (!text.includes('.')) return escapeHtml(text);
      const tokenPattern = /[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+(?:\([^()\n]*\))?/g;
      let result = '';
      let cursor = 0;
      for (const match of text.matchAll(tokenPattern)) {
        const token = match[0];
        const start = match.index;
        const end = start + token.length;
        if (!shouldSegmentDottedToken(text, token, start, end)) continue;
        result += escapeHtml(text.slice(cursor, start));
        result += dottedTokenHtml(token);
        cursor = end;
      }
      result += escapeHtml(text.slice(cursor));
      return result;
    }

    function shouldSegmentDottedToken(text, token, start, end) {
      const before = start > 0 ? text[start - 1] : '';
      const after = end < text.length ? text[end] : '';
      if (before === '/' || before === '\\' || after === '/' || after === '\\') return false;
      const identifier = token.split('(', 1)[0];
      const parts = identifier.split('.');
      if (parts.length < 2) return false;
      if (parts.length === 2 && isLikelyFileExtension(parts[1])) return false;
      return parts.every(part => /^[A-Za-z_$][\w$]*$/.test(part));
    }

    function isLikelyFileExtension(value) {
      return new Set([
        'c', 'cc', 'cpp', 'css', 'go', 'h', 'hpp', 'html', 'java', 'js', 'jsx',
        'json', 'md', 'php', 'py', 'rb', 'rs', 'scss', 'sql', 'ts', 'tsx', 'txt',
        'toml', 'vue', 'xml', 'yaml', 'yml'
      ]).has(String(value || '').toLowerCase());
    }

    function dottedTokenHtml(token) {
      const callStart = token.indexOf('(');
      const identifier = callStart >= 0 ? token.slice(0, callStart) : token;
      const suffix = callStart >= 0 ? token.slice(callStart) : '';
      const parts = identifier.split('.');
      const body = parts.map((part, index) =>
        '<span class="identifier-segment identifier-segment-' + (index % 6) + '">' +
        escapeHtml(part) +
        '</span>'
      ).join('<span class="identifier-dot">.</span>');
      return '<span class="identifier-token">' + body + '</span>' + escapeHtml(suffix);
    }

    function colorizeDetailLine(line) {
      if (!line) return '';
      const trimmed = line.trim();
      if (!trimmed) return '';
      const leading = line.match(/^\s*/)[0];
      const clean = line.slice(leading.length);
      const prefix = escapeHtml(leading);
      if (clean.startsWith('- ')) {
        return prefix + '<span class="detail-edge">' + escapeHtml(clean) + '</span>';
      }
      if (clean.startsWith('... ')) {
        return prefix + '<span class="detail-value">' + escapeHtml(clean) + '</span>';
      }
      if (/connections( \(|:)/.test(clean) || clean === 'summary evidence:') {
        return prefix + '<span class="detail-section">' + escapeHtml(clean) + '</span>';
      }
      const keyMatch = clean.match(/^([^:]{1,40}):\s?(.*)$/);
      if (keyMatch) {
        const key = keyMatch[1];
        const value = keyMatch[2] || '';
        const valueClass = detailValueClass(key);
        return prefix +
          '<span class="detail-key">' + escapeHtml(key) + ':</span>' +
          (value ? ' <span class="' + valueClass + '">' + segmentedIdentifierHtml(value) + '</span>' : '');
      }
      return prefix + '<span class="detail-value">' + segmentedIdentifierHtml(clean) + '</span>';
    }

    function detailValueClass(key) {
      const lower = key.toLowerCase();
      if (lower === 'type' || lower === 'connection') return 'detail-type';
      if (lower.includes('component') || lower === 'source' || lower === 'target') return 'detail-component';
      if (lower === 'weight') return 'detail-weight';
      if (lower === 'call') return 'detail-call';
      if (lower === 'parameters') return 'detail-call';
      if (lower.includes('signature')) return 'detail-signature';
      if (lower === 'location' || lower === 'file' || lower.includes('project')) return 'detail-path';
      if (lower === 'change') return 'detail-change';
      return 'detail-value';
    }

    function renderEdgeExample(example) {
      const source = formatEndpoint(example.source);
      const target = formatEndpoint(example.target);
      const lines = ['- ' + source + ' -> ' + target + ' (' + example.type + ')'];
      const call = formatCall(example);
      if (call) lines.push('  call: ' + call);
      if (example.arguments && example.arguments.length) lines.push('  parameters: ' + example.arguments.join(', '));
      if (example.source && example.source.signature) lines.push('  source signature: ' + example.source.signature);
      if (example.target && example.target.signature) lines.push('  target signature: ' + example.target.signature);
      const location = formatLocation(example);
      if (location) lines.push('  location: ' + location);
      if (example.commit) lines.push('  commit: ' + example.commit);
      return lines;
    }

    function formatEndpoint(endpoint) {
      if (!endpoint) return 'unknown';
      return endpoint.qualified_name || endpoint.label || endpoint.name || endpoint.key || 'unknown';
    }

    function formatCall(example) {
      if (!example.display) return '';
      const display = String(example.display || '').trim();
      if (!display) return '';
      if (display.includes('(')) return display;
      const args = example.arguments && example.arguments.length ? '(' + example.arguments.join(', ') + ')' : '';
      return display + args;
    }

    function formatLocation(example) {
      const file = example.file_path || (example.source && example.source.path) || '';
      if (!file) return '';
      return example.line ? file + ':' + example.line : file;
    }

    function renderTopEdges() {
      const title = document.getElementById('topEdgesTitle');
      if (title) title.textContent = state.view === 'compare' ? 'Compare Impact' : 'Top Connections';
      const root = document.getElementById('topEdges');
      root.innerHTML = '';
      if (state.view === 'compare' && state.compare) {
        renderCompareImpact(root);
        return;
      }
      const entries = state.view === 'compare' && state.compare
        ? [
            ...state.compare.base.edges.map(edge => ({ edge, side: 'base' })),
            ...state.compare.head.edges.map(edge => ({ edge, side: 'head' }))
          ]
        : state.edges.map(edge => ({ edge, side: null }));
      const top = entries
        .sort((a, b) => {
          const changeDelta = changeRank(b.edge.change) - changeRank(a.edge.change);
          return changeDelta || (b.edge.weight || 1) - (a.edge.weight || 1);
        })
        .slice(0, 10);
      for (const { edge, side } of top) {
        const div = document.createElement('div');
        div.className = 'pill';
        const prefix = side ? side + ': ' : '';
        const change = edge.change && edge.change !== 'unchanged' ? ', ' + edge.change : '';
        div.textContent = prefix + labelForNode(edge.source) + ' -> ' + labelForNode(edge.target) + ' (' + edge.type + change + ')';
        div.onclick = () => {
          state.selected = { kind: 'edge', edge, side };
          renderSelection(state.selected);
        };
        root.appendChild(div);
      }
    }

    function renderCompareImpact(root) {
      const items = compareImpactItems().slice(0, 12);
      if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'detail-empty';
        empty.textContent = 'No compare changes match the current filters.';
        root.appendChild(empty);
        return;
      }
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'path-row impact-row';
        row.onclick = () => selectCompareImpactItem(item);
        const title = document.createElement('div');
        title.className = 'path-title';
        title.textContent = item.title;
        const meta = document.createElement('div');
        meta.className = 'path-meta';
        meta.textContent = item.meta;
        row.append(title, meta);
        root.appendChild(row);
      }
    }

    function compareImpactItems() {
      if (!state.compare) return [];
      const items = [];
      for (const side of ['base', 'head']) {
        const panel = state.compare[side];
        const visibleNodeIds = new Set((panel.nodes || []).map(node => node.id));
        for (const node of panel.nodes || []) {
          if (!hasChange(node)) continue;
          const edges = (panel.edges || []).filter(edge => edge.source === node.id || edge.target === node.id);
          items.push({
            kind: 'node',
            side,
            node,
            score: compareNodeImpactScore(node, edges),
            title: side + ': ' + node.label,
            meta: node.change + ' node / ' + edges.length + ' visible links / score ' + Math.round(compareNodeImpactScore(node, edges))
          });
        }
        for (const edge of panel.edges || []) {
          if (!hasChange(edge)) continue;
          items.push({
            kind: 'edge',
            side,
            edge,
            score: compareEdgeImpactScore(edge, visibleNodeIds),
            title: side + ': ' + labelForNode(edge.source) + ' -> ' + labelForNode(edge.target),
            meta: edge.change + ' ' + (edge.type || 'edge') + ' / w ' + (edge.weight || 1) + ' / proofs ' + ((edge.examples || []).length)
          });
        }
      }
      return items.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
    }

    function compareNodeImpactScore(node, edges) {
      const metrics = node.metrics || {};
      const riskBoost = node.risk === 'high' ? 180 : node.risk === 'medium' ? 80 : 0;
      const changeBoost = { added: 160, removed: 150, changed: 120 }[node.change] || 0;
      return changeBoost + riskBoost + edges.length * 18 + Number(metrics.files || 0) * 12 + Number(node.size || 0) * 0.08;
    }

    function compareEdgeImpactScore(edge, visibleNodeIds) {
      const typeBoost = {
        api_call: 130,
        function_call: 120,
        calls: 110,
        graphql: 105,
        database: 100,
        test_covers: 80,
        custom: 90,
        imports: 70,
        references: 65,
        cochange: 50
      }[String(edge.type || '').toLowerCase()] || 45;
      const endpointBoost = visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target) ? 40 : 0;
      const changeBoost = { added: 150, removed: 145, changed: 120 }[edge.change] || 0;
      return changeBoost + typeBoost + endpointBoost + Number(edge.weight || 1) * 12 + (edge.examples || []).length * 18;
    }

    function selectCompareImpactItem(item) {
      if (item.kind === 'node') {
        state.selected = { kind: 'node', node: item.node, side: item.side };
      } else if (item.kind === 'edge') {
        state.selected = { kind: 'edge', edge: item.edge, side: item.side };
      }
      renderSelection(state.selected);
      focusCameraOnSelection(state.selected);
      scheduleUrlStateUpdate();
    }

    function nodeForId(id) {
      if (!id) return null;
      return state.nodeIndex.get(id) || null;
    }

    function labelForNode(id) {
      const node = nodeForId(id);
      return node ? node.label : id;
    }

    function changeRank(change) {
      return { added: 3, removed: 3, changed: 2, unchanged: 0 }[String(change || 'unchanged')] || 0;
    }

    function shortSha(sha) {
      return sha ? sha.slice(0, 8) : '';
    }

    function distanceToSegment(px, py, x1, y1, x2, y2) {
      const dx = x2 - x1;
      const dy = y2 - y1;
      if (dx === 0 && dy === 0) return Math.hypot(px - x1, py - y1);
      const t = clamp(((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy), 0, 1);
      const x = x1 + t * dx;
      const y = y1 + t * dy;
      return Math.hypot(px - x, py - y);
    }

    function shortLabel(label) {
      return label.length > 24 ? label.slice(0, 21) + '...' : label;
    }

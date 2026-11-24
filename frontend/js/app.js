/**
 * WorldCams Main Application Controller
 * Coordinates filters, state management, search, modals, admin security, and batch feed deletion.
 */

class AppController {
  constructor() {
    this.feeds = [];
    this.selectedFeed = null;
    this.selectedCardIds = new Set();
    this.isAdminAuthorized = false;
    this.lastSelectionWasRandom = false;
    this.randomAutoSkipTimer = null;
    this.randomSkipCount = 0;
    this.randomSkippedIds = new Set();

    this.currentFilters = {
      country: '',
      org: '',
      port: '',
      product: '',
      auth_status: '',
      search: '',
      is_favorite: null,
      limit: 25000,
      offset: 0
    };

    this.initElements();
    this.initEventListeners();
    this.init();
  }

  initElements() {
    // Toolbar elements
    this.searchInput = document.getElementById('filter-search');
    this.btnClearSearch = document.getElementById('btn-clear-search');
    this.countrySelect = document.getElementById('filter-country');
    this.orgSelect = document.getElementById('filter-org');
    this.portSelect = document.getElementById('filter-port');
    this.productSelect = document.getElementById('filter-product');
    this.btnFilterFavorites = document.getElementById('btn-filter-favorites');
    this.favFilterCount = document.getElementById('fav-filter-count');
    this.btnRandomCamera = document.getElementById('btn-random-camera');
    this.btnReset = document.getElementById('btn-reset-filters');
    this.feedGrid = document.getElementById('feed-grid');
    this.feedCountEl = document.getElementById('feed-list-count');

    // Sidebar Category Tabs & Controls
    this.tabFeedsAll = document.getElementById('tab-feeds-all');
    this.tabFeedsFav = document.getElementById('tab-feeds-fav');
    this.tabCountAll = document.getElementById('tab-count-all');
    this.tabCountFav = document.getElementById('tab-count-fav');
    this.btnSidebarRandomCam = document.getElementById('btn-sidebar-random-cam');
    this.btnPlayerRandomCam = document.getElementById('btn-player-random-cam');

    // Header stats
    this.statTotal = document.getElementById('stat-total');
    this.statUnauth = document.getElementById('stat-unauth');
    this.metricUnauth = document.getElementById('metric-unauth');
    this.statFavorites = document.getElementById('stat-favorites');
    this.metricFavorites = document.getElementById('metric-favorites');
    this.statMtx = document.getElementById('stat-mtx');
    this.statShodan = document.getElementById('stat-shodan');
    this.statAdmin = document.getElementById('stat-admin');
    this.metricAdmin = document.getElementById('metric-admin');
    this.statTor = document.getElementById('stat-tor');
    this.metricTor = document.getElementById('metric-tor');
    this.btnPlayerTorToggle = document.getElementById('btn-player-tor-toggle');
    this.btnPlayerTorRotate = document.getElementById('btn-player-tor-rotate');
    this.btnModalTorRotate = document.getElementById('btn-modal-tor-rotate');
    this.torStatus = {
      enabled: false,
      auto_rotate: true,
      socks_host: '127.0.0.1',
      socks_port: 9050,
      control_port: 9051,
      socks_alive: false,
      circuit_id: 'direct'
    };

    // Batch Management elements
    this.batchSelectAll = document.getElementById('batch-select-all');
    this.batchSelectedCount = document.getElementById('batch-selected-count');
    this.btnBatchDelete = document.getElementById('btn-batch-delete');
    this.batchDeleteNum = document.getElementById('batch-delete-num');

    // Modals
    this.modalShodan = document.getElementById('modal-shodan');
    this.modalEducation = document.getElementById('modal-education');
    this.modalSettings = document.getElementById('modal-settings');

    // Workspace
    this.workspace = document.getElementById('app-workspace');

    // Mobile Navigation & Controls
    this.btnToggleFiltersMobile = document.getElementById('btn-toggle-filters-mobile');
    this.filterGroup = document.querySelector('.filter-group');
    this.filterBadgeDot = document.getElementById('filter-badge-dot');
    this.mobileNavBar = document.getElementById('mobile-nav-bar');
    this.btnMobileBack = document.getElementById('btn-mobile-back-to-feeds');
    this.mobileQuickCamName = document.getElementById('mobile-quick-cam-name');
    this.mobileLiveIndicator = document.getElementById('mobile-live-indicator');
    this.currentMobileTab = 'feeds';
  }

  initEventListeners() {
    // Search input with debounce
    let searchTimer = null;
    this.searchInput.addEventListener('input', (e) => {
      this.btnClearSearch.style.display = e.target.value ? 'block' : 'none';
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        this.currentFilters.search = e.target.value.trim();
        this.loadFeeds();
      }, 300);
    });

    this.btnClearSearch.addEventListener('click', () => {
      this.searchInput.value = '';
      this.btnClearSearch.style.display = 'none';
      this.currentFilters.search = '';
      this.loadFeeds();
    });

    // Dropdown filters
    this.countrySelect.addEventListener('change', (e) => {
      this.currentFilters.country = e.target.value;
      this.loadFeeds();
    });

    this.orgSelect.addEventListener('change', (e) => {
      this.currentFilters.org = e.target.value;
      this.loadFeeds();
    });

    this.portSelect.addEventListener('change', (e) => {
      this.currentFilters.port = e.target.value;
      this.loadFeeds();
    });

    this.productSelect.addEventListener('change', (e) => {
      this.currentFilters.product = e.target.value;
      this.loadFeeds();
    });

    // Favorites Filter Button in Toolbar
    this.btnFilterFavorites?.addEventListener('click', () => {
      const isCurrentlyFav = this.currentFilters.is_favorite === true;
      this.setFavoritesFilter(!isCurrentlyFav);
    });

    // Favorites Metric in Header Navbar
    this.metricFavorites?.addEventListener('click', () => {
      this.setFavoritesFilter(true);
      if (window.innerWidth <= 900) {
        this.switchMobileTab('feeds');
      }
    });

    // Unauthenticated Metric Filter Toggle in Header Navbar
    this.metricUnauth?.addEventListener('click', () => {
      const nextAuth = this.currentFilters.auth_status === 'unauthenticated' ? '' : 'unauthenticated';
      this.currentFilters.auth_status = nextAuth;
      this.metricUnauth.classList.toggle('active', nextAuth === 'unauthenticated');
      this.loadFeeds();
      this.updateFilterBadgeDot();
      if (window.innerWidth <= 900) {
        this.switchMobileTab('feeds');
      }
    });

    // Category Tabs in Sidebar
    this.tabFeedsAll?.addEventListener('click', () => {
      this.setFavoritesFilter(false);
    });

    this.tabFeedsFav?.addEventListener('click', () => {
      this.setFavoritesFilter(true);
    });

    // Random Camera Selector Buttons
    const onRandomClick = () => this.selectRandomFeed();
    this.btnRandomCamera?.addEventListener('click', onRandomClick);
    this.btnSidebarRandomCam?.addEventListener('click', onRandomClick);
    this.btnPlayerRandomCam?.addEventListener('click', onRandomClick);

    this.btnReset.addEventListener('click', () => {
      this.resetFilters();
    });

    // View toggle buttons
    document.querySelectorAll('.view-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.view;
        document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.setViewMode(mode);
      });
    });

    // External sliding handle for Discovered Cameras sidebar
    document.getElementById('btn-slide-feeds-panel')?.addEventListener('click', () => {
      this.toggleFeedsPanel();
    });

    // Resizable Camera Output Panel & Discovered Cameras Sidebar
    this.initStreamPanelResize();
    this.initFeedsPanelResize();

    // Close stream panel in Map/Grid views
    document.getElementById('btn-close-stream-panel')?.addEventListener('click', () => {
      this.streamOpen = false;
      this.workspace?.classList.remove('stream-open');
      if (window.worldMap) {
        setTimeout(() => window.worldMap.invalidateSize(), 60);
      }
    });

    // Inspector Tab Switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetTab = btn.dataset.tab;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(targetTab)?.classList.add('active');
      });
    });

    // Jump from Inspector Remediation to Education Hub
    document.getElementById('btn-jump-education')?.addEventListener('click', () => {
      this.openModal('modal-education');
    });

    // Modal Triggers
    // Protect Camera Search behind Admin Privileges
    document.getElementById('btn-open-shodan')?.addEventListener('click', () => {
      this.requireAdmin(() => {
        this.openModal('modal-shodan');
      });
    });

    document.getElementById('btn-open-education')?.addEventListener('click', () => this.openModal('modal-education'));
    
    // Protect Settings behind Admin Privileges
    document.getElementById('btn-open-settings')?.addEventListener('click', () => {
      this.requireAdmin(() => {
        this.loadSettings();
        this.openModal('modal-settings');
      });
    });

    // Toggle Admin Status Badge in Navbar
    this.metricAdmin?.addEventListener('click', () => {
      this.promptAdminAuth();
    });

    // Tor Anonymizer Toggle & Circuit Rotation
    this.metricTor?.addEventListener('click', () => {
      this.toggleTorProxy();
    });
    this.btnPlayerTorToggle?.addEventListener('click', () => {
      this.toggleTorProxy();
    });
    this.btnPlayerTorRotate?.addEventListener('click', () => {
      this.rotateTorCircuit();
    });
    this.btnModalTorRotate?.addEventListener('click', () => {
      this.rotateTorCircuit();
    });

    // Close buttons for modals
    document.querySelectorAll('[data-modal]').forEach(el => {
      el.addEventListener('click', () => {
        const modalId = el.dataset.modal;
        if (modalId === 'modal-intro' && document.getElementById('intro-dont-show')?.checked) {
          localStorage.setItem('worldcams_hide_intro', '1');
        }
        this.closeModal(modalId);
      });
    });

    document.getElementById('btn-intro-education')?.addEventListener('click', () => {
      if (document.getElementById('intro-dont-show')?.checked) {
        localStorage.setItem('worldcams_hide_intro', '1');
      }
      this.closeModal('modal-intro');
      this.openModal('modal-education');
    });

    // Quick tag pills in Shodan modal
    document.querySelectorAll('.tag-pill').forEach(pill => {
      pill.addEventListener('click', () => {
        document.getElementById('shodan-query-input').value = pill.dataset.query;
      });
    });

    // Custom limit toggle in Shodan modal
    const limitSelect = document.getElementById('shodan-limit');
    const customLimitInput = document.getElementById('shodan-custom-limit');
    if (limitSelect && customLimitInput) {
      limitSelect.addEventListener('change', () => {
        if (limitSelect.value === 'custom') {
          customLimitInput.style.display = 'block';
          customLimitInput.focus();
        } else {
          customLimitInput.style.display = 'none';
        }
      });
    }

    // Shodan Search Execution
    document.getElementById('btn-execute-shodan')?.addEventListener('click', () => {
      this.executeShodanSearch();
    });

    // Save Settings
    document.getElementById('btn-save-settings')?.addEventListener('click', () => {
      this.saveSettings();
    });

    // Favorite toggle button
    document.getElementById('btn-toggle-favorite')?.addEventListener('click', () => {
      this.toggleFavoriteCurrent();
    });

    // Admin Delete feed button in Inspector
    document.getElementById('btn-delete-feed')?.addEventListener('click', () => {
      this.deleteCurrentFeed();
    });

    // Batch Select All checkbox
    this.batchSelectAll?.addEventListener('change', (e) => {
      if (e.target.checked) {
        this.feeds.forEach(f => this.selectedCardIds.add(f.id));
      } else {
        this.selectedCardIds.clear();
      }
      this.updateBatchSelectionUI();
      this.renderFeedGrid();
    });

    // Batch Delete Button
    this.btnBatchDelete?.addEventListener('click', () => {
      this.executeBatchDelete();
    });

    // Mobile Bottom App Navigation
    document.querySelectorAll('.mobile-nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        if (tab === 'recon') {
          this.requireAdmin(() => {
            this.openModal('modal-shodan');
          });
          return;
        }
        this.switchMobileTab(tab);
      });
    });

    // Mobile Collapsible Filter Drawer Toggle
    this.btnToggleFiltersMobile?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!this.filterGroup) return;
      const isOpen = this.filterGroup.classList.toggle('mobile-open');
      this.btnToggleFiltersMobile.classList.toggle('active', isOpen);
    });

    // Mobile Player "Elenco Cam" Back Button
    this.btnMobileBack?.addEventListener('click', () => {
      this.switchMobileTab('feeds');
    });

    // Connect map select handler
    if (window.worldMap) {
      window.worldMap.setSelectHandler((feed) => {
        this.selectFeed(feed);
      });
    }
  }

  async init() {
    this.switchMobileTab('feeds');
    this.setViewMode('triage');
    try {
      const csrfRes = await fetch('/api/security/csrf');
      if (csrfRes.ok) {
        const csrfData = await csrfRes.json();
        if (csrfData && csrfData.csrf_token) {
          window._csrfToken = csrfData.csrf_token;
        }
      }
    } catch (_) {}
    await this.checkAdminStatus();
    await this.checkHealth();
    await this.loadTorStatus();
    await this.loadFacets();
    await this.loadFeeds();
    if (localStorage.getItem('worldcams_hide_intro') !== '1') {
      this.openModal('modal-intro');
    }
    lucide.createIcons();
  }

  setViewMode(mode) {
    this.currentViewMode = mode;
    const mobileClass = `mobile-tab-${this.currentMobileTab || 'feeds'}`;
    const feedsHiddenClass = this.feedsHidden ? 'feeds-hidden' : '';
    const streamOpenClass = this.streamOpen ? 'stream-open' : '';
    this.workspace.className = `app-container view-${mode} ${mobileClass} ${feedsHiddenClass} ${streamOpenClass}`.trim();

    const streamPanel = document.getElementById('stream-panel');
    if (streamPanel) {
      if (mode === 'player') {
        streamPanel.style.width = '';
      } else {
        const savedW = localStorage.getItem('worldcams_stream_width');
        if (savedW) streamPanel.style.width = `${savedW}px`;
      }
    }

    const feedsPanel = document.getElementById('feed-browser-panel');
    const slideBtn = document.getElementById('btn-slide-feeds-panel');
    if (feedsPanel) {
      if (mode === 'grid') {
        feedsPanel.style.width = '';
        if (slideBtn) slideBtn.style.left = '';
      } else {
        const savedFW = localStorage.getItem('worldcams_feeds_width');
        if (savedFW && !this.feedsHidden) {
          feedsPanel.style.width = `${savedFW}px`;
          if (slideBtn) slideBtn.style.left = `${savedFW}px`;
        } else {
          feedsPanel.style.width = '';
          if (slideBtn) slideBtn.style.left = '';
        }
      }
    }

    if (window.worldMap) {
      setTimeout(() => window.worldMap.invalidateSize(), 60);
    }
  }

  initFeedsPanelResize() {
    const handle = document.getElementById('feeds-resize-handle');
    const panel = document.getElementById('feed-browser-panel');
    const slideBtn = document.getElementById('btn-slide-feeds-panel');
    if (!handle || !panel) return;

    const savedW = localStorage.getItem('worldcams_feeds_width');
    if (savedW && this.currentViewMode !== 'grid' && !this.feedsHidden && window.innerWidth > 900) {
      panel.style.width = `${savedW}px`;
      if (slideBtn) slideBtn.style.left = `${savedW}px`;
    }

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    handle.addEventListener('mousedown', (e) => {
      if (window.innerWidth <= 900 || this.currentViewMode === 'grid' || this.feedsHidden) return;
      isResizing = true;
      startX = e.clientX;
      startWidth = panel.getBoundingClientRect().width;
      document.body.classList.add('resizing-feeds');
      e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
      if (!isResizing) return;
      const minW = 240;
      const maxW = Math.floor(window.innerWidth * 0.60);
      const nextW = Math.round(Math.max(minW, Math.min(maxW, startWidth + (e.clientX - startX))));
      panel.style.width = `${nextW}px`;
      if (slideBtn) slideBtn.style.left = `${nextW}px`;
      if (window.worldMap) {
        window.worldMap.invalidateSize();
      }
    });

    window.addEventListener('mouseup', () => {
      if (!isResizing) return;
      isResizing = false;
      document.body.classList.remove('resizing-feeds');
      const finalW = Math.round(panel.getBoundingClientRect().width);
      if (finalW >= 240) {
        localStorage.setItem('worldcams_feeds_width', String(finalW));
      }
      if (window.worldMap) {
        window.worldMap.invalidateSize();
      }
    });

    handle.addEventListener('dblclick', () => {
      panel.style.width = '';
      if (slideBtn) slideBtn.style.left = '';
      localStorage.removeItem('worldcams_feeds_width');
      if (window.worldMap) {
        setTimeout(() => window.worldMap.invalidateSize(), 60);
      }
    });
  }

  initStreamPanelResize() {
    const handle = document.getElementById('stream-resize-handle');
    const panel = document.getElementById('stream-panel');
    if (!handle || !panel) return;

    const savedW = localStorage.getItem('worldcams_stream_width');
    if (savedW && this.currentViewMode !== 'player' && window.innerWidth > 900) {
      panel.style.width = `${savedW}px`;
    }

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    handle.addEventListener('mousedown', (e) => {
      if (window.innerWidth <= 900 || this.currentViewMode === 'player') return;
      isResizing = true;
      startX = e.clientX;
      startWidth = panel.getBoundingClientRect().width;
      document.body.classList.add('resizing-stream');
      e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
      if (!isResizing) return;
      const minW = 340;
      const maxW = Math.floor(window.innerWidth * 0.82);
      const nextW = Math.max(minW, Math.min(maxW, startWidth + (startX - e.clientX)));
      panel.style.width = `${Math.round(nextW)}px`;
      if (window.worldMap) {
        window.worldMap.invalidateSize();
      }
    });

    window.addEventListener('mouseup', () => {
      if (!isResizing) return;
      isResizing = false;
      document.body.classList.remove('resizing-stream');
      const finalW = Math.round(panel.getBoundingClientRect().width);
      if (finalW >= 340) {
        localStorage.setItem('worldcams_stream_width', String(finalW));
      }
      if (window.worldMap) {
        window.worldMap.invalidateSize();
      }
    });

    handle.addEventListener('dblclick', () => {
      panel.style.width = '';
      localStorage.removeItem('worldcams_stream_width');
      if (window.worldMap) {
        setTimeout(() => window.worldMap.invalidateSize(), 60);
      }
    });
  }

  toggleFeedsPanel() {
    this.feedsHidden = !this.feedsHidden;
    this.workspace?.classList.toggle('feeds-hidden', this.feedsHidden);
    const feedsPanel = document.getElementById('feed-browser-panel');
    const btn = document.getElementById('btn-slide-feeds-panel');
    if (!this.feedsHidden && feedsPanel && this.currentViewMode !== 'grid') {
      const savedFW = localStorage.getItem('worldcams_feeds_width');
      if (savedFW) {
        feedsPanel.style.width = `${savedFW}px`;
        if (btn) btn.style.left = `${savedFW}px`;
      }
    }
    if (btn) {
      btn.innerHTML = this.feedsHidden
        ? '<i data-lucide="chevrons-right"></i>'
        : '<i data-lucide="chevrons-left"></i>';
      btn.title = this.feedsHidden ? 'Show Discovered Cameras' : 'Hide Discovered Cameras';
    }
    if (window.lucide) {
      lucide.createIcons();
    }
    if (window.worldMap) {
      setTimeout(() => window.worldMap.invalidateSize(), 60);
    }
  }

  switchMobileTab(tab) {
    this.currentMobileTab = tab;
    document.querySelectorAll('.mobile-nav-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    if (this.workspace) {
      this.workspace.classList.remove('mobile-tab-feeds', 'mobile-tab-player', 'mobile-tab-map');
      this.workspace.classList.add(`mobile-tab-${tab}`);
    }
    if (tab === 'map' && window.worldMap) {
      window.worldMap.invalidateSize();
    }
    // Close mobile filter drawer if open
    if (this.filterGroup && this.filterGroup.classList.contains('mobile-open')) {
      this.filterGroup.classList.remove('mobile-open');
      this.btnToggleFiltersMobile?.classList.remove('active');
    }
  }

  setFavoritesFilter(enableFav) {
    if (enableFav && !this.isAdminAuthorized) {
      this.requireAdmin(() => {
        this.currentFilters.is_favorite = true;
        this.updateFavoritesUIState();
        this.loadFacets();
        this.loadFeeds();
        if (window.worldMap) {
          window.worldMap.recenter();
        }
      });
      return;
    }
    if (enableFav) {
      this.currentFilters.is_favorite = true;
    } else {
      this.currentFilters.is_favorite = null;
    }
    this.updateFavoritesUIState();
    this.loadFeeds();
    if (window.worldMap) {
      window.worldMap.recenter();
    }
  }

  updateFavoritesUIState() {
    const isFav = this.currentFilters.is_favorite === true;
    if (this.btnFilterFavorites) {
      this.btnFilterFavorites.classList.toggle('active', isFav);
    }
    if (this.tabFeedsAll) {
      this.tabFeedsAll.classList.toggle('active', !isFav);
    }
    if (this.tabFeedsFav) {
      this.tabFeedsFav.classList.toggle('active', isFav);
    }
  }

  updateFilterBadgeDot() {
    if (!this.filterBadgeDot) return;
    const hasFilter = !!(
      this.currentFilters.country ||
      this.currentFilters.org ||
      this.currentFilters.port ||
      this.currentFilters.product ||
      this.currentFilters.auth_status ||
      (this.currentFilters.is_favorite === true)
    );
    this.filterBadgeDot.style.display = hasFilter ? 'block' : 'none';
  }

  // ==========================================
  // Admin Authentication & Privileges
  // ==========================================

  getAdminToken() {
    return localStorage.getItem('worldcams_admin_token') || '';
  }

  async verifyToken(token) {
    if (!token || !token.trim()) return false;
    try {
      const res = await fetch('/api/admin/verify', {
        method: 'POST',
        headers: { 'X-Admin-Token': token.trim() }
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  async checkAdminStatus() {
    const token = this.getAdminToken();
    if (!token) {
      this.isAdminAuthorized = false;
      this.updateAdminUI(false);
      return false;
    }
    const isValid = await this.verifyToken(token);
    this.isAdminAuthorized = isValid;
    this.updateAdminUI(isValid);
    return isValid;
  }

  updateAdminUI(isAuthorized) {
    if (this.statAdmin) {
      if (isAuthorized) {
        this.statAdmin.innerHTML = '<i data-lucide="unlock" style="width:11px;height:11px;vertical-align:middle;"></i> UNLOCKED';
        this.statAdmin.style.color = 'var(--accent-emerald)';
      } else {
        this.statAdmin.innerHTML = '<i data-lucide="lock" style="width:11px;height:11px;vertical-align:middle;"></i> LOCKED';
        this.statAdmin.style.color = 'var(--accent-rose)';
      }
    }

    const showFavs = Boolean(isAuthorized);
    if (this.metricFavorites) this.metricFavorites.style.display = showFavs ? 'flex' : 'none';
    if (this.btnFilterFavorites) this.btnFilterFavorites.style.display = showFavs ? 'inline-flex' : 'none';
    if (this.tabFeedsFav) this.tabFeedsFav.style.display = showFavs ? 'flex' : 'none';
    const playerFavBtn = document.getElementById('btn-toggle-favorite');
    if (playerFavBtn) playerFavBtn.style.display = showFavs ? 'inline-flex' : 'none';

    if (!showFavs && this.currentFilters && this.currentFilters.is_favorite !== null) {
      this.currentFilters.is_favorite = null;
      this.updateFavoritesUIState();
    }

    this.updateTorUI(this.torStatus);
    lucide.createIcons();
  }

  async requireAdmin(callback) {
    let token = this.getAdminToken();
    if (token && (await this.verifyToken(token))) {
      this.isAdminAuthorized = true;
      this.updateAdminUI(true);
      return callback(token);
    }

    // Prompt user for Admin Key
    const inputToken = prompt(
      'ADMINISTRATOR AUTHENTICATION REQUIRED\n\nEnter Admin Access Key to proceed:'
    );

    if (!inputToken || !inputToken.trim()) return;

    if (await this.verifyToken(inputToken.trim())) {
      localStorage.setItem('worldcams_admin_token', inputToken.trim());
      this.isAdminAuthorized = true;
      this.updateAdminUI(true);
      return callback(inputToken.trim());
    } else {
      alert('ACCESS DENIED: Invalid Administrator Access Key.');
      this.isAdminAuthorized = false;
      this.updateAdminUI(false);
    }
  }

  async promptAdminAuth() {
    const action = prompt(
      `ADMINISTRATOR ACCESS MANAGEMENT\n\nCurrent Status: ${this.isAdminAuthorized ? 'AUTHORIZED' : 'LOCKED'}\n\nEnter Admin Access Key:`
    );
    if (action !== null) {
      const trimmed = action.trim();
      if (trimmed && (await this.verifyToken(trimmed))) {
        localStorage.setItem('worldcams_admin_token', trimmed);
        this.isAdminAuthorized = true;
        this.updateAdminUI(true);
        await this.loadTorStatus(false);
        await this.loadFacets();
        await this.loadFeeds();
        alert('Admin privileges verified and unlocked successfully.');
      } else {
        localStorage.removeItem('worldcams_admin_token');
        this.isAdminAuthorized = false;
        this.updateAdminUI(false);
        await this.loadTorStatus(false);
        await this.loadFacets();
        await this.loadFeeds();
        alert('Invalid Admin Key. Privileges locked.');
      }
    }
  }

  // ==========================================
  // Health & Metrics
  // ==========================================

  async checkHealth() {
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        const data = await res.json();
        if (data.mediamtx_alive) {
          this.statMtx.innerHTML = '<span class="status-dot"></span> LIVE (WHEP/HLS)';
          this.statMtx.style.color = 'var(--accent-emerald)';
        } else {
          this.statMtx.innerHTML = '<span class="status-dot" style="background:var(--accent-amber)"></span> STANDBY';
          this.statMtx.style.color = 'var(--accent-amber)';
        }

        if (data.shodan_configured) {
          this.statShodan.textContent = 'SHODAN API';
          this.statShodan.style.color = 'var(--accent-emerald)';
        } else {
          this.statShodan.textContent = 'SCRAPER (0 CREDITS)';
          this.statShodan.style.color = 'var(--accent-cyan)';
        }

        if (typeof data.tor_enabled === 'boolean') {
          this.updateTorUI({
            ...this.torStatus,
            enabled: data.tor_enabled,
            socks_alive: Boolean(data.tor_socks_alive),
            circuit_id: data.tor_circuit_id || (data.tor_enabled ? this.torStatus.circuit_id : 'direct')
          });
        }
      }
    } catch (e) {
      this.statMtx.textContent = 'OFFLINE';
    }
  }

  updateTorUI(status) {
    if (!status) return;
    this.torStatus = { ...this.torStatus, ...status };
    const esc = window.escapeHtml || (s => String(s ?? ''));
    const enabled = Boolean(this.torStatus.enabled);
    const alive = Boolean(this.torStatus.socks_alive);
    const isAdmin = Boolean(this.isAdminAuthorized);
    const circuit = isAdmin ? (this.torStatus.circuit_id || (enabled ? 'init' : 'direct')) : '';

    if (this.metricTor && this.statTor) {
      this.metricTor.classList.toggle('tor-on', enabled && alive);
      this.metricTor.classList.toggle('tor-error', enabled && !alive);
      if (enabled && alive) {
        const label = isAdmin && circuit && circuit !== 'hidden' ? `ON // ${esc(circuit)}` : 'ON (PROTECTED)';
        this.statTor.innerHTML = `<i data-lucide="shield-check" style="width: 11px; height: 11px; vertical-align: middle;"></i> ${label}`;
        this.statTor.style.color = '#c084fc';
      } else if (enabled && !alive) {
        this.statTor.innerHTML = '<i data-lucide="shield-alert" style="width: 11px; height: 11px; vertical-align: middle;"></i> ON // OFFLINE';
        this.statTor.style.color = 'var(--accent-rose)';
      } else {
        this.statTor.innerHTML = '<i data-lucide="shield-off" style="width: 11px; height: 11px; vertical-align: middle;"></i> OFF (DIRECT)';
        this.statTor.style.color = 'var(--text-muted)';
      }
    }

    if (this.btnPlayerTorToggle) {
      this.btnPlayerTorToggle.style.display = isAdmin ? 'inline-flex' : 'none';
      this.btnPlayerTorToggle.classList.toggle('tor-active', enabled && alive);
      this.btnPlayerTorToggle.classList.toggle('tor-error', enabled && !alive);
      this.btnPlayerTorToggle.innerHTML = enabled
        ? (alive ? '<i data-lucide="shield-check"></i>' : '<i data-lucide="shield-alert"></i>')
        : '<i data-lucide="shield-off"></i>';
      this.btnPlayerTorToggle.title = enabled
        ? (alive ? `Tor Active (${circuit}) — Click to disable (Admin)` : 'Tor Enabled but Offline — Click to disable (Admin)')
        : 'Tor Proxy OFF (Direct) — Click to enable (Admin)';
    }

    if (this.btnPlayerTorRotate) {
      this.btnPlayerTorRotate.style.display = (isAdmin && enabled) ? 'inline-flex' : 'none';
    }

    const adminFieldsEl = document.getElementById('settings-tor-admin-fields');
    const lockedMsgEl = document.getElementById('settings-tor-locked-msg');
    if (adminFieldsEl) adminFieldsEl.style.display = isAdmin ? 'block' : 'none';
    if (lockedMsgEl) lockedMsgEl.style.display = isAdmin ? 'none' : 'block';
    if (this.btnModalTorRotate) this.btnModalTorRotate.style.display = isAdmin ? 'inline-flex' : 'none';

    const summaryEl = document.getElementById('tor-status-summary');
    const circuitEl = document.getElementById('tor-circuit-label');
    const exitIpEl = document.getElementById('tor-exit-ip-label');
    if (summaryEl) {
      summaryEl.textContent = enabled
        ? (alive ? 'ACTIVE (SOCKS5 ONLINE)' : 'ENABLED // SOCKS5 OFFLINE (FAIL-CLOSED)')
        : 'OFF (DIRECT)';
      summaryEl.style.color = enabled
        ? (alive ? '#c084fc' : 'var(--accent-rose)')
        : 'var(--text-muted)';
    }
    if (circuitEl) circuitEl.textContent = (isAdmin && enabled) ? circuit : 'none';
    if (exitIpEl) exitIpEl.textContent = (isAdmin && this.torStatus.exit_ip) ? this.torStatus.exit_ip : '-';

    if (window.lucide) {
      lucide.createIcons();
    }
  }

  async loadTorStatus(verifyIp = false) {
    try {
      const token = this.getAdminToken();
      const headers = token ? { 'X-Admin-Token': token } : {};
      const res = await fetch(`/api/tor/status?verify_ip=${verifyIp ? 'true' : 'false'}`, { headers });
      if (res.ok) {
        const st = await res.json();
        this.updateTorUI(st);
        return st;
      }
    } catch (e) {
      console.warn('Tor status check failed:', e);
    }
    return null;
  }

  async toggleTorProxy() {
    this.requireAdmin(async (token) => {
      const targetEnabled = !(this.torStatus && this.torStatus.enabled);
      try {
        const res = await fetch('/api/tor/config', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify({ enabled: targetEnabled })
        });
        if (res.status === 401) {
          alert('ACCESS DENIED: Valid Administrator Access Key required to toggle Tor proxy.');
          this.promptAdminAuth();
          return;
        }
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          alert(`Tor configuration error: ${err.detail || res.statusText}`);
          return;
        }
        const st = await res.json();
        this.updateTorUI(st);
        if (this.selectedFeed && window.streamPlayer && (!st.enabled || st.socks_alive)) {
          window.streamPlayer.playFeed(this.selectedFeed);
        }
      } catch (e) {
        alert('Failed to toggle Tor proxy: ' + e.message);
      }
    });
  }

  async rotateTorCircuit() {
    this.requireAdmin(async (token) => {
      const btns = [this.btnPlayerTorRotate, this.btnModalTorRotate].filter(Boolean);
      btns.forEach(b => { b.disabled = true; });
      try {
        const res = await fetch('/api/tor/rotate?verify_ip=true', {
          method: 'POST',
          headers: { 'X-Admin-Token': token }
        });
        if (res.ok) {
          const st = await res.json();
          this.updateTorUI(st);
          if (this.selectedFeed && window.streamPlayer && (!st.enabled || st.socks_alive)) {
            window.streamPlayer.playFeed(this.selectedFeed);
          }
        }
      } catch (e) {
        console.warn('Tor circuit rotation error:', e);
      } finally {
        btns.forEach(b => { b.disabled = false; });
      }
    });
  }

  async loadFacets() {
    try {
      const token = this.getAdminToken();
      const headers = token ? { 'X-Admin-Token': token } : {};
      const res = await fetch('/api/facets', { headers });
      if (res.ok) {
        const facets = await res.json();
        
        const esc = window.escapeHtml || (s => String(s ?? ''));

        // Populate Countries
        this.countrySelect.innerHTML = '<option value="">All Countries</option>';
        facets.countries.forEach(c => {
          this.countrySelect.innerHTML += `<option value="${esc(c.code)}">${esc(c.name)} (${Number(c.count) || 0})</option>`;
        });

        // Populate Orgs
        this.orgSelect.innerHTML = '<option value="">All Organizations</option>';
        facets.orgs.forEach(o => {
          this.orgSelect.innerHTML += `<option value="${esc(o.name)}">${esc(o.name)} (${Number(o.count) || 0})</option>`;
        });

        // Populate Products
        this.productSelect.innerHTML = '<option value="">All Products</option>';
        facets.products.forEach(p => {
          this.productSelect.innerHTML += `<option value="${esc(p.name)}">${esc(p.name)} (${Number(p.count) || 0})</option>`;
        });

        // Populate Ports
        if (this.portSelect && facets.ports) {
          const currentPort = this.portSelect.value;
          const portLabels = {
            '554': 'Port 554 (RTSP)',
            '443': 'Port 443 (HTTPS Web)',
            '8443': 'Port 8443 (HTTPS Web)',
            '80': 'Port 80 (HTTP Web)',
            '81': 'Port 81 (HTTP Web)',
            '82': 'Port 82 (HTTP Web)',
            '83': 'Port 83 (HTTP Web)',
            '84': 'Port 84 (HTTP Web)',
            '85': 'Port 85 (HTTP Web)',
            '88': 'Port 88 (HTTP Web)',
            '90': 'Port 90 (HTTP Web)',
            '8554': 'Port 8554 (Alt RTSP)',
            '8080': 'Port 8080 (HTTP Web)',
            '8081': 'Port 8081 (HTTP Web)',
            '8000': 'Port 8000 (Hikvision Web)',
            '8050': 'Port 8050 (Panasonic Web)',
            '37777': 'Port 37777 (Dahua)'
          };
          this.portSelect.innerHTML = '<option value="">All Ports</option>';
          facets.ports.forEach(p => {
            const pNum = Number(p.name);
            let label = portLabels[p.name];
            if (!label) {
              if (window.isHttpsCameraPort && window.isHttpsCameraPort(pNum)) {
                label = `Port ${esc(p.name)} (HTTPS Web)`;
              } else if (window.isWebCameraPort && window.isWebCameraPort(pNum)) {
                label = `Port ${esc(p.name)} (HTTP Web)`;
              } else {
                label = `Port ${esc(p.name)}`;
              }
            }
            this.portSelect.innerHTML += `<option value="${esc(p.name)}">${label} (${(Number(p.count) || 0).toLocaleString()})</option>`;
          });
          if (currentPort) {
            this.portSelect.value = currentPort;
          }
        }

        // Update stats
        this.statTotal.textContent = facets.total_cameras;
        this.statUnauth.textContent = facets.unauthenticated_count;
        if (this.statFavorites) {
          this.statFavorites.textContent = facets.favorites_count || 0;
        }
        if (this.tabCountFav) {
          this.tabCountFav.textContent = (facets.favorites_count || 0).toLocaleString();
        }
        if (this.favFilterCount) {
          this.favFilterCount.textContent = (facets.favorites_count || 0).toLocaleString();
        }
        if (this.tabCountAll) {
          this.tabCountAll.textContent = (facets.total_cameras || 0).toLocaleString();
        }
      }
    } catch (err) {
      console.error('Failed to load facets:', err);
    }
  }

  async loadFeeds() {
    this.updateFavoritesUIState();
    const params = new URLSearchParams();
    if (this.currentFilters.country) params.append('country', this.currentFilters.country);
    if (this.currentFilters.org) params.append('org', this.currentFilters.org);
    if (this.currentFilters.port) params.append('port', this.currentFilters.port);
    if (this.currentFilters.product) params.append('product', this.currentFilters.product);
    if (this.currentFilters.auth_status) params.append('auth_status', this.currentFilters.auth_status);
    if (this.currentFilters.search) params.append('search', this.currentFilters.search);
    if (this.currentFilters.is_favorite !== null && this.currentFilters.is_favorite !== undefined) {
      params.append('is_favorite', this.currentFilters.is_favorite);
    }
    params.append('limit', '25000');

    try {
      const token = this.getAdminToken();
      const headers = token ? { 'X-Admin-Token': token } : {};
      const res = await fetch(`/api/feeds?${params.toString()}`, { headers });
      if (res.ok) {
        const data = await res.json();
        this.feeds = data.items || [];
        this.feedCountEl.textContent = this.feeds.length.toLocaleString();

        // Clean selected card IDs that no longer exist
        const currentFeedIds = new Set(this.feeds.map(f => f.id));
        for (const id of Array.from(this.selectedCardIds)) {
          if (!currentFeedIds.has(id)) {
            this.selectedCardIds.delete(id);
          }
        }
        this.updateBatchSelectionUI();

        // Render Map Markers
        if (window.worldMap) {
          window.worldMap.renderMarkers(this.feeds);
        }

        // Render Feed Grid
        this.renderFeedGrid();

        // If no feed is currently selected, auto-select the first feed (without forcing tab switch on mobile)
        if (!this.selectedFeed && this.feeds.length > 0) {
          this.selectFeed(this.feeds[0], false);
        }

        this.updateFilterBadgeDot();
      }
    } catch (err) {
      console.error('Failed to load feeds:', err);
    }
  }

  updateBatchSelectionUI() {
    const count = this.selectedCardIds.size;
    if (count > 0) {
      if (this.batchSelectedCount) {
        this.batchSelectedCount.style.display = 'inline-block';
        this.batchSelectedCount.textContent = `${count} selected`;
      }
      if (this.btnBatchDelete) {
        this.btnBatchDelete.style.display = 'inline-flex';
      }
      if (this.batchDeleteNum) {
        this.batchDeleteNum.textContent = count;
      }
      if (this.batchSelectAll && this.feeds.length > 0) {
        this.batchSelectAll.checked = (count === this.feeds.length);
      }
    } else {
      if (this.batchSelectedCount) {
        this.batchSelectedCount.style.display = 'none';
      }
      if (this.btnBatchDelete) {
        this.btnBatchDelete.style.display = 'none';
      }
      if (this.batchSelectAll) {
        this.batchSelectAll.checked = false;
      }
    }
  }

  renderFeedGrid(resetCount = true) {
    if (!this.feedGrid) return;

    if (resetCount) {
      this.visibleCardCount = 100;
      this.feedGrid.innerHTML = '';
    } else {
      const oldBtn = document.getElementById('grid-load-more-container');
      if (oldBtn) oldBtn.remove();
    }

    if (this.feeds.length === 0) {
      this.feedGrid.innerHTML = `
        <div style="padding: 2rem; text-align: center; color: var(--text-faint);">
          <i data-lucide="camera-off" style="width:32px;height:32px;margin-bottom:0.5rem;"></i>
          <p>No camera feeds matched your filters.</p>
        </div>
      `;
      lucide.createIcons();
      return;
    }

    const startIndex = resetCount ? 0 : (this.visibleCardCount - 100);
    const endIndex = Math.min(this.visibleCardCount, this.feeds.length);
    const slice = this.feeds.slice(startIndex, endIndex);

    const fragment = document.createDocumentFragment();

    const esc = window.escapeHtml || (s => String(s ?? ''));

    slice.forEach(feed => {
      const card = document.createElement('div');
      const isSelected = this.selectedFeed && this.selectedFeed.id === feed.id;
      const isBatchSelected = this.selectedCardIds.has(feed.id);
      card.className = `feed-card ${isSelected ? 'active' : ''} ${isBatchSelected ? 'selected' : ''}`;
      card.dataset.feedId = feed.id;
      
      // Only allow trusted static placeholders in grid thumbnails so the browser never makes direct connections to untrusted Shodan camera IPs
      const defaultThumb = 'https://images.unsplash.com/photo-1557597774-9d273605dfa9?w=400&auto=format&fit=crop&q=80';
      const thumb = (feed.screenshot_url && String(feed.screenshot_url).startsWith('https://images.unsplash.com/'))
        ? esc(feed.screenshot_url)
        : defaultThumb;

      const safeId = esc(feed.id);
      const safeProduct = esc(feed.product || 'IP Camera');
      const safeIp = esc(feed.ip || '');
      const safePort = esc(feed.port || '');
      const safeCity = esc(feed.city || 'Unknown');
      const safeCountry = esc(feed.country_name || 'Unknown');
      const safeCountryCode = esc(feed.country_code || 'XX');
      const safeOrg = esc(feed.org || 'Unknown');

      card.innerHTML = `
        <!-- Multi-select checkbox -->
        <div class="feed-card-check-box" title="Select for batch action">
          <input type="checkbox" class="card-checkbox" data-id="${safeId}" ${isBatchSelected ? 'checked' : ''}>
        </div>

        ${this.isAdminAuthorized ? `<!-- Quick Favorite Button (Admin Only) -->
        <button class="card-quick-fav ${feed.is_favorite ? 'favorited' : ''}" data-id="${safeId}" title="${feed.is_favorite ? 'Remove from favorites (Admin Only)' : 'Add to favorites (Admin Only)'}">
          <i data-lucide="bookmark" style="width:11px;height:11px;"></i>
        </button>` : ''}

        <!-- Quick Delete Button (Admin) -->
        <button class="card-quick-del" data-id="${safeId}" title="Permanently delete camera (Admin)">
          <i data-lucide="trash-2" style="width:11px;height:11px;"></i>
        </button>

        <div class="feed-card-thumb">
          <img src="${thumb}" alt="${safeProduct}" loading="lazy">
        </div>
        <div class="feed-card-info">
          <div class="feed-card-header">
            <span class="feed-card-product" title="${safeProduct}">${safeProduct}</span>
          </div>
          <div class="feed-card-ip">
            <span>${safeIp}</span>
            <span class="port-tag">:${safePort}</span>
          </div>
          <div class="feed-card-location">📍 ${safeCity}, ${safeCountry} (${safeCountryCode})</div>
          <div class="feed-card-org">🏢 ${safeOrg}</div>
        </div>
      `;

      // Checkbox click handler
      const chk = card.querySelector('.card-checkbox');
      chk?.addEventListener('click', (e) => {
        e.stopPropagation();
        if (chk.checked) {
          this.selectedCardIds.add(feed.id);
          card.classList.add('selected');
        } else {
          this.selectedCardIds.delete(feed.id);
          card.classList.remove('selected');
        }
        this.updateBatchSelectionUI();
      });

      // Quick Favorite click handler
      const favCardBtn = card.querySelector('.card-quick-fav');
      favCardBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleFavoriteFeed(feed);
      });

      // Quick Delete click handler
      const delBtn = card.querySelector('.card-quick-del');
      delBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        this.deleteFeedById(feed.id, feed.ip, feed.port, feed.product);
      });

      // Card selection for player view
      card.addEventListener('click', () => {
        this.selectFeed(feed);
      });

      fragment.appendChild(card);
    });

    this.feedGrid.appendChild(fragment);

    // If there are more feeds to display, show "Load More" indicator
    if (this.visibleCardCount < this.feeds.length) {
      const remaining = this.feeds.length - this.visibleCardCount;
      const nextBatch = Math.min(remaining, 100);
      const loadMoreContainer = document.createElement('div');
      loadMoreContainer.id = 'grid-load-more-container';
      loadMoreContainer.style.padding = '0.8rem';
      loadMoreContainer.style.textAlign = 'center';
      loadMoreContainer.innerHTML = `
        <button class="btn btn-secondary btn-sm" id="btn-load-more-cards" style="width:100%;font-size:0.8rem;padding:0.6rem;">
          Load Next ${nextBatch.toLocaleString()} Feeds (${remaining.toLocaleString()} more)
        </button>
      `;
      loadMoreContainer.querySelector('#btn-load-more-cards')?.addEventListener('click', () => {
        this.visibleCardCount += 100;
        this.renderFeedGrid(false);
      });
      this.feedGrid.appendChild(loadMoreContainer);
    }

    lucide.createIcons();
  }

  cancelRandomAutoSkip() {
    if (this.randomAutoSkipTimer) {
      clearTimeout(this.randomAutoSkipTimer);
      this.randomAutoSkipTimer = null;
    }
  }

  scheduleRandomSkipOnUnreachable(feedId, reason = '') {
    if (!this.lastSelectionWasRandom || !feedId || this.selectedFeed?.id !== feedId) {
      return;
    }
    this.cancelRandomAutoSkip();
    this.randomSkippedIds.add(feedId);
    const maxConsecutiveSkips = 8;
    if (this.randomSkipCount >= maxConsecutiveSkips) {
      this.randomSkipCount = 0;
      if (window.streamPlayer?.techProto) {
        window.streamPlayer.techProto.textContent = `Auto-skip paused after ${maxConsecutiveSkips} unreachable cameras.`;
      }
      return;
    }
    this.randomSkipCount++;
    const delayMs = 2500;
    if (window.streamPlayer?.techProto) {
      window.streamPlayer.techProto.textContent = `Port unreachable — skipping to next random cam (${this.randomSkipCount}/${maxConsecutiveSkips})...`;
    }
    if (window.streamPlayer?.overlayEl && window.streamPlayer?.overlayMsg) {
      window.streamPlayer.overlayEl.style.display = 'flex';
      window.streamPlayer.overlayMsg.textContent = `${reason || 'Port unreachable'} — Skipping to another random camera in 2.5s...`;
    }
    this.randomAutoSkipTimer = setTimeout(() => {
      this.randomAutoSkipTimer = null;
      if (this.lastSelectionWasRandom && this.selectedFeed?.id === feedId) {
        this.selectRandomFeed(true);
      }
    }, delayMs);
  }

  selectFeed(feed, autoSwitchMobile = true, fromRandom = false) {
    this.cancelRandomAutoSkip();
    this.lastSelectionWasRandom = Boolean(fromRandom);
    if (!fromRandom) {
      this.randomSkipCount = 0;
      this.randomSkippedIds.clear();
    }
    this.selectedFeed = feed;

    // Ensure stream inspector output is shown in every layout (including Map & Grid views)
    if (autoSwitchMobile) {
      this.streamOpen = true;
      this.workspace?.classList.add('stream-open');
      if (window.worldMap) {
        setTimeout(() => window.worldMap.invalidateSize(), 60);
      }
    }

    // Update active state in grid cards
    document.querySelectorAll('.feed-card').forEach(c => c.classList.remove('active'));
    this.renderFeedGrid();

    // Stream playback
    if (window.streamPlayer) {
      window.streamPlayer.playFeed(feed);
    }

    // On mobile devices, automatically switch to Player tab if user selected a cam
    if (window.innerWidth <= 900 && autoSwitchMobile) {
      this.switchMobileTab('player');
    }

    if (this.mobileQuickCamName) {
      this.mobileQuickCamName.textContent = `${feed.product || 'Camera'} [${feed.ip}]`;
    }
    if (this.mobileLiveIndicator) {
      this.mobileLiveIndicator.classList.add('live');
    }

    // Update favorite button state
    const favBtn = document.getElementById('btn-toggle-favorite');
    if (favBtn) {
      favBtn.style.color = feed.is_favorite ? 'var(--accent-amber)' : 'var(--text-muted)';
    }
  }

  async selectRandomFeed(isAutoSkip = false) {
    this.cancelRandomAutoSkip();
    if (!isAutoSkip) {
      this.randomSkipCount = 0;
      if (this.randomSkippedIds.size > 50) {
        this.randomSkippedIds.clear();
      }
    }

    let chosenFeed = null;

    // Pick from currently loaded feeds list if there are multiple feeds
    if (this.feeds && this.feeds.length > 1) {
      // Avoid current feed and recently unreachable feeds skipped during random browsing
      const currentId = this.selectedFeed?.id;
      let available = this.feeds.filter(f => f.id !== currentId && !this.randomSkippedIds.has(f.id));
      if (available.length === 0) {
        this.randomSkippedIds.clear();
        available = this.feeds.filter(f => f.id !== currentId);
      }
      if (available.length === 0) available = this.feeds;
      const idx = Math.floor(Math.random() * available.length);
      chosenFeed = available[idx];
    } else {
      // Otherwise fetch directly from backend /api/feeds/random
      try {
        const res = await fetch('/api/feeds/random');
        if (res.ok) {
          chosenFeed = await res.json();
        }
      } catch (err) {
        console.warn('Failed to fetch random feed from API:', err);
      }
    }

    if (!chosenFeed) {
      if (!isAutoSkip) {
        alert('No camera feeds available to select.');
      }
      return;
    }

    // Select the camera feed (and switch to player on mobile), marking random mode active
    this.selectFeed(chosenFeed, true, true);

    // Pan interactive map to the camera location if coordinates are available
    if (window.worldMap && chosenFeed.latitude && chosenFeed.longitude) {
      window.worldMap.panTo(chosenFeed.latitude, chosenFeed.longitude, 8);
    }

    // Smooth scroll the sidebar card into view
    setTimeout(() => {
      const card = document.querySelector(`.feed-card[data-feed-id="${chosenFeed.id}"]`);
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }, 120);
  }

  async toggleFavoriteFeed(feed) {
    if (!feed) return;

    this.requireAdmin(async (token) => {
      const newStatus = !feed.is_favorite;

      try {
        const res = await fetch(`/api/feeds/${feed.id}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify({ is_favorite: newStatus })
        });
        if (res.status === 401) {
          alert('ACCESS DENIED: Invalid Administrator Access Key.');
          this.promptAdminAuth();
          return;
        }
        if (res.ok) {
          feed.is_favorite = newStatus;
          if (this.selectedFeed && this.selectedFeed.id === feed.id) {
            this.selectedFeed.is_favorite = newStatus;
            const favBtn = document.getElementById('btn-toggle-favorite');
            if (favBtn) {
              favBtn.style.color = newStatus ? 'var(--accent-amber)' : 'var(--text-muted)';
            }
          }

          // If currently filtering by favorites and user un-favorited this cam, refresh list
          if (this.currentFilters.is_favorite === true && !newStatus) {
            await this.loadFeeds();
          } else {
            // Re-render feed grid cards to update bookmark button icon and state
            this.renderFeedGrid(false);
          }
          await this.loadFacets();
        }
      } catch (e) {
        console.error('Failed to toggle favorite:', e);
      }
    });
  }

  async toggleFavoriteCurrent() {
    if (!this.selectedFeed) return;
    await this.toggleFavoriteFeed(this.selectedFeed);
  }

  // ==========================================
  // Camera Deletion (Single & Batch)
  // ==========================================

  async deleteCurrentFeed() {
    if (!this.selectedFeed) {
      alert('Please select a camera to delete.');
      return;
    }
    await this.deleteFeedById(
      this.selectedFeed.id,
      this.selectedFeed.ip,
      this.selectedFeed.port,
      this.selectedFeed.product
    );
  }

  async deleteFeedById(feedId, ip, port, product) {
    this.requireAdmin(async (token) => {
      const confirmMsg = `PERMANENT DELETION WARNING:\n\nAre you sure you want to remove camera [${ip}:${port} - ${product}] from the database?\n\nThis action cannot be undone.`;
      if (!confirm(confirmMsg)) return;

      try {
        const res = await fetch(`/api/feeds/${feedId}`, {
          method: 'DELETE',
          headers: { 'X-Admin-Token': token }
        });

        if (res.status === 401) {
          alert('ACCESS DENIED: Invalid Administrator Access Key.');
          this.promptAdminAuth();
          return;
        }

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          alert('Failed to delete feed: ' + (err.detail || res.statusText));
          return;
        }

        // If currently playing, stop
        if (this.selectedFeed && this.selectedFeed.id === feedId) {
          if (window.streamPlayer) {
            window.streamPlayer.stop();
          }
          this.selectedFeed = null;
          document.getElementById('player-feed-title').textContent = 'Camera removed from database';
          const badge = document.getElementById('player-status-badge');
          if (badge) {
            badge.className = 'live-pill offline';
            badge.innerHTML = '<span class="dot"></span> DELETED';
          }
        }

        this.selectedCardIds.delete(feedId);
        await this.loadFacets();
        await this.loadFeeds();
      } catch (e) {
        alert('Communication error: ' + e.message);
      }
    });
  }

  async executeBatchDelete() {
    const ids = Array.from(this.selectedCardIds);
    if (ids.length === 0) return;

    this.requireAdmin(async (token) => {
      const confirmMsg = `ADMIN BATCH DELETION:\n\nAre you sure you want to permanently delete ALL ${ids.length} selected cameras from the database?\n\nThis action cannot be undone.`;
      if (!confirm(confirmMsg)) return;

      try {
        const res = await fetch('/api/feeds/batch-delete', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify({ feed_ids: ids })
        });

        if (res.status === 401) {
          alert('ACCESS DENIED: Invalid Administrator Access Key.');
          this.promptAdminAuth();
          return;
        }

        const data = await res.json();
        if (res.ok) {
          alert(`Successfully deleted ${data.deleted_count} cameras from the database.`);
          
          if (this.selectedFeed && ids.includes(this.selectedFeed.id)) {
            if (window.streamPlayer) {
              window.streamPlayer.stop();
            }
            this.selectedFeed = null;
            document.getElementById('player-feed-title').textContent = 'Camera removed from database';
          }

          this.selectedCardIds.clear();
          this.updateBatchSelectionUI();
          await this.loadFacets();
          await this.loadFeeds();
        } else {
          alert('Batch deletion error: ' + (data.detail || res.statusText));
        }
      } catch (e) {
        alert('Communication error: ' + e.message);
      }
    });
  }

  resetFilters() {
    this.searchInput.value = '';
    this.btnClearSearch.style.display = 'none';
    this.countrySelect.value = '';
    this.orgSelect.value = '';
    this.portSelect.value = '';
    this.productSelect.value = '';

    this.currentFilters = {
      country: '',
      org: '',
      port: '',
      product: '',
      auth_status: '',
      search: '',
      is_favorite: null,
      limit: 25000,
      offset: 0
    };

    this.metricUnauth?.classList.remove('active');
    this.updateFavoritesUIState();
    this.loadFeeds();
    this.updateFilterBadgeDot();
    if (window.worldMap) {
      window.worldMap.recenter();
    }
  }

  async executeShodanSearch() {
    const query = document.getElementById('shodan-query-input').value.trim();
    const limitSelect = document.getElementById('shodan-limit');
    let limit = parseInt(limitSelect?.value, 10);
    if (limitSelect?.value === 'custom') {
      const customVal = parseInt(document.getElementById('shodan-custom-limit')?.value, 10);
      limit = (!isNaN(customVal) && customVal > 0) ? customVal : 8000;
    } else if (isNaN(limit) || limit <= 0) {
      limit = 8000;
    }

    const mode = document.getElementById('shodan-mode')?.value || 'scraper';
    const importDb = document.getElementById('shodan-import-db').checked;
    const statusMsg = document.getElementById('shodan-status-msg');
    const executeBtn = document.getElementById('btn-execute-shodan');

    if (!query) return;

    this.requireAdmin(async (token) => {
      executeBtn.disabled = true;
      executeBtn.innerHTML = '<span class="overlay-spinner" style="width:14px;height:14px;display:inline-block;"></span> Searching...';
      statusMsg.style.display = 'block';
      statusMsg.className = 'alert-box info';

      if (mode === 'api') {
        const estPages = Math.ceil(limit / 100);
        statusMsg.textContent = `Querying Shodan API (requesting up to ${limit.toLocaleString()} cameras across ${estPages} pages)... Please wait.`;
      } else {
        statusMsg.textContent = `Crawling live camera directory & resolving GeoIP (up to ${limit.toLocaleString()} cameras)...`;
      }

      try {
        const res = await fetch('/api/shodan/search', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify({
            query: query,
            limit: limit,
            import_to_db: importDb,
            mode: mode
          })
        });

        if (res.status === 401) {
          statusMsg.className = 'alert-box danger';
          statusMsg.textContent = 'Unauthorized: Administrator Access Key required to launch searches.';
          return;
        }

        const rawText = await res.text();
        let data = {};
        try {
          data = rawText ? JSON.parse(rawText) : {};
        } catch (_) {
          statusMsg.className = 'alert-box danger';
          statusMsg.textContent = `Server error (HTTP ${res.status}): ${rawText.slice(0, 160) || 'Invalid response format'}`;
          return;
        }
        const esc = window.escapeHtml || (s => String(s ?? ''));
        if (res.ok && data.source !== 'error') {
          statusMsg.className = 'alert-box success';
          const totalFound = (data.total !== undefined ? data.total : data.results?.length || 0).toLocaleString();
          const importedCount = (data.imported_to_db || 0).toLocaleString();
          let msg = `Discovered <strong>${esc(totalFound)}</strong> cameras (Source: ${esc(data.source)}). Ingested <strong>${esc(importedCount)}</strong> new feeds into database.`;
          if (data.message) {
            msg += `<br><small style="color:var(--text-muted);">${esc(data.message)}</small>`;
          }
          if (data.notice) {
            msg += `<br><small style="color:var(--accent-amber);">${esc(data.notice)}</small>`;
          }
          statusMsg.innerHTML = msg;

          // Reload feeds and facets
          await this.loadFacets();
          await this.loadFeeds();

          setTimeout(() => {
            this.closeModal('modal-shodan');
            statusMsg.style.display = 'none';
          }, 3500);
        } else {
          statusMsg.className = 'alert-box danger';
          statusMsg.textContent = data.error || data.detail || 'Search request failed.';
        }
      } catch (e) {
        statusMsg.className = 'alert-box danger';
        statusMsg.textContent = 'Connection error: ' + e.message;
      } finally {
        setTimeout(() => {
          executeBtn.disabled = false;
          executeBtn.innerHTML = '<i data-lucide="radar"></i> Launch Reconnaissance';
          lucide.createIcons();
        }, 1200);
      }
    });
  }

  async loadSettings() {
    try {
      const res = await fetch('/api/shodan/info');
      if (res.ok) {
        const info = await res.json();
        const statusEl = document.getElementById('account-status');
        const shodanInput = document.getElementById('setting-shodan-key');

        if (statusEl) {
          if (info.active) {
            statusEl.textContent = 'ACTIVE & VERIFIED';
            statusEl.style.color = 'var(--accent-emerald)';
            if (shodanInput && !shodanInput.value) {
              shodanInput.placeholder = 'Key configured in .env (enter new to update)';
            }
          } else if (info.configured && info.error) {
            statusEl.textContent = 'ERROR (INVALID OR EXPIRED KEY)';
            statusEl.style.color = 'var(--accent-rose)';
          } else {
            statusEl.textContent = 'NOT CONFIGURED';
            statusEl.style.color = 'var(--text-muted)';
          }
        }
      }
    } catch (e) {
      console.error(e);
    }

    // Load Tor Proxy & Circuit Rotation state
    const torSt = await this.loadTorStatus(false);
    if (torSt) {
      const torEnabledEl = document.getElementById('setting-tor-enabled');
      const torAutoEl = document.getElementById('setting-tor-autorotate');
      const torHostEl = document.getElementById('setting-tor-host');
      const torPortEl = document.getElementById('setting-tor-port');
      const torCtrlPortEl = document.getElementById('setting-tor-ctrl-port');
      if (torEnabledEl) torEnabledEl.checked = Boolean(torSt.enabled);
      if (torAutoEl) torAutoEl.checked = Boolean(torSt.auto_rotate);
      if (torHostEl) torHostEl.value = torSt.socks_host || '127.0.0.1';
      if (torPortEl) torPortEl.value = torSt.socks_port || 9050;
      if (torCtrlPortEl) torCtrlPortEl.value = torSt.control_port || 9051;
    }

    // Load and verify Admin Access Key
    const adminToken = this.getAdminToken();
    const adminInput = document.getElementById('setting-admin-token');
    const adminBadge = document.getElementById('admin-status-badge');
    if (adminInput) adminInput.value = adminToken;
    if (adminBadge) {
      if (!adminToken) {
        adminBadge.textContent = 'LOCKED';
        adminBadge.style.color = 'var(--accent-rose)';
        adminBadge.style.background = 'rgba(244, 63, 94, 0.15)';
        adminBadge.style.borderColor = 'rgba(244, 63, 94, 0.3)';
      } else {
        fetch('/api/admin/verify', {
          method: 'POST',
          headers: { 'X-Admin-Token': adminToken }
        }).then(r => {
          if (r.ok) {
            adminBadge.textContent = 'AUTHORIZED';
            adminBadge.style.color = 'var(--accent-emerald)';
            adminBadge.style.background = 'rgba(16, 185, 129, 0.15)';
            adminBadge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
            this.isAdminAuthorized = true;
            this.updateAdminUI(true);
          } else {
            adminBadge.textContent = 'INVALID KEY';
            adminBadge.style.color = 'var(--accent-rose)';
            adminBadge.style.background = 'rgba(244, 63, 94, 0.15)';
            adminBadge.style.borderColor = 'rgba(244, 63, 94, 0.3)';
            this.isAdminAuthorized = false;
            this.updateAdminUI(false);
          }
        }).catch(() => {});
      }
    }
  }

  async saveSettings() {
    const key = document.getElementById('setting-shodan-key')?.value.trim();
    const adminKey = document.getElementById('setting-admin-token')?.value.trim();

    if (adminKey !== undefined) {
      if (adminKey) {
        localStorage.setItem('worldcams_admin_token', adminKey);
      } else {
        localStorage.removeItem('worldcams_admin_token');
      }
    }

    const token = adminKey || this.getAdminToken();

    try {
      if (key) {
        const res = await fetch('/api/shodan/key', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify({ api_key: key })
        });
        if (res.status === 401) {
          alert('ACCESS DENIED: Administrator Access Key required to update Shodan settings.');
          return;
        }
      }

      const torEnabledEl = document.getElementById('setting-tor-enabled');
      const adminFieldsEl = document.getElementById('settings-tor-admin-fields');
      if (torEnabledEl && adminFieldsEl && adminFieldsEl.style.display !== 'none' && token) {
        const torPayload = {
          enabled: torEnabledEl.checked,
          auto_rotate: Boolean(document.getElementById('setting-tor-autorotate')?.checked),
          socks_host: (document.getElementById('setting-tor-host')?.value || '127.0.0.1').trim() || '127.0.0.1',
          socks_port: parseInt(document.getElementById('setting-tor-port')?.value || '9050', 10) || 9050,
          control_port: parseInt(document.getElementById('setting-tor-ctrl-port')?.value || '9051', 10) || 9051
        };
        const torRes = await fetch('/api/tor/config', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify(torPayload)
        });
        if (!torRes.ok) {
          const torErr = await torRes.json().catch(() => ({}));
          alert('Error saving Tor configuration: ' + (torErr.detail || torRes.statusText));
          return;
        }
        const torSt = await torRes.json();
        this.updateTorUI(torSt);
      }

      await this.checkAdminStatus();
      await this.checkHealth();
      await this.loadSettings();
      this.closeModal('modal-settings');
      alert('Settings saved successfully.');
    } catch (e) {
      alert('Error saving settings: ' + e.message);
    }
  }

  openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('show');
    lucide.createIcons();
  }

  closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('show');
  }
}

// Instantiate on DOM load
window.addEventListener('DOMContentLoaded', () => {
  window.app = new AppController();
});

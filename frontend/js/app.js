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

    this.currentFilters = {
      country: '',
      org: '',
      port: '',
      product: '',
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
    this.statFavorites = document.getElementById('stat-favorites');
    this.metricFavorites = document.getElementById('metric-favorites');
    this.statMtx = document.getElementById('stat-mtx');
    this.statShodan = document.getElementById('stat-shodan');
    this.statAdmin = document.getElementById('stat-admin');
    this.metricAdmin = document.getElementById('metric-admin');

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
      this.loadSettings();
      this.openModal('modal-settings');
    });

    // Toggle Admin Status Badge in Navbar
    this.metricAdmin?.addEventListener('click', () => {
      this.promptAdminAuth();
    });

    // Close buttons for modals
    document.querySelectorAll('[data-modal]').forEach(el => {
      el.addEventListener('click', () => {
        const modalId = el.dataset.modal;
        this.closeModal(modalId);
      });
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

    // Auto-probe RTSP Resolver buttons
    document.getElementById('btn-resolve-rtsp')?.addEventListener('click', () => {
      this.resolveRtspCurrentFeed();
    });
    document.getElementById('btn-probe-vendor')?.addEventListener('click', () => {
      this.resolveRtspCurrentFeed();
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
    await this.checkAdminStatus();
    await this.checkHealth();
    await this.loadFacets();
    await this.loadFeeds();
    lucide.createIcons();
  }

  setViewMode(mode) {
    this.currentViewMode = mode;
    const mobileClass = `mobile-tab-${this.currentMobileTab || 'feeds'}`;
    this.workspace.className = `app-container view-${mode} ${mobileClass}`;
    if (window.worldMap) {
      window.worldMap.invalidateSize();
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
    if (!this.statAdmin) return;
    if (isAuthorized) {
      this.statAdmin.innerHTML = '<i data-lucide="unlock" style="width:11px;height:11px;vertical-align:middle;"></i> UNLOCKED';
      this.statAdmin.style.color = 'var(--accent-emerald)';
    } else {
      this.statAdmin.innerHTML = '<i data-lucide="lock" style="width:11px;height:11px;vertical-align:middle;"></i> LOCKED';
      this.statAdmin.style.color = 'var(--accent-rose)';
    }
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
        alert('Admin privileges verified and unlocked successfully.');
      } else {
        localStorage.removeItem('worldcams_admin_token');
        this.isAdminAuthorized = false;
        this.updateAdminUI(false);
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
      }
    } catch (e) {
      this.statMtx.textContent = 'OFFLINE';
    }
  }

  async loadFacets() {
    try {
      const res = await fetch('/api/facets');
      if (res.ok) {
        const facets = await res.json();
        
        // Populate Countries
        this.countrySelect.innerHTML = '<option value="">All Countries</option>';
        facets.countries.forEach(c => {
          this.countrySelect.innerHTML += `<option value="${c.code}">${c.name} (${c.count})</option>`;
        });

        // Populate Orgs
        this.orgSelect.innerHTML = '<option value="">All Organizations</option>';
        facets.orgs.forEach(o => {
          this.orgSelect.innerHTML += `<option value="${o.name}">${o.name} (${o.count})</option>`;
        });

        // Populate Products
        this.productSelect.innerHTML = '<option value="">All Products</option>';
        facets.products.forEach(p => {
          this.productSelect.innerHTML += `<option value="${p.name}">${p.name} (${p.count})</option>`;
        });

        // Populate Ports
        if (this.portSelect && facets.ports) {
          const currentPort = this.portSelect.value;
          const portLabels = {
            '554': 'Port 554 (RTSP)',
            '443': 'Port 443 (HTTPS Web)',
            '80': 'Port 80 (HTTP Web)',
            '8554': 'Port 8554 (Alt RTSP)',
            '8080': 'Port 8080 (Web RTSP)',
            '8000': 'Port 8000 (Hikvision)',
            '8050': 'Port 8050 (Panasonic)',
            '37777': 'Port 37777 (Dahua)'
          };
          this.portSelect.innerHTML = '<option value="">All Ports</option>';
          facets.ports.forEach(p => {
            const label = portLabels[p.name] || `Port ${p.name}`;
            this.portSelect.innerHTML += `<option value="${p.name}">${label} (${p.count.toLocaleString()})</option>`;
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
    if (this.currentFilters.search) params.append('search', this.currentFilters.search);
    if (this.currentFilters.is_favorite !== null && this.currentFilters.is_favorite !== undefined) {
      params.append('is_favorite', this.currentFilters.is_favorite);
    }
    params.append('limit', '25000');

    try {
      const res = await fetch(`/api/feeds?${params.toString()}`);
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

    slice.forEach(feed => {
      const card = document.createElement('div');
      const isSelected = this.selectedFeed && this.selectedFeed.id === feed.id;
      const isBatchSelected = this.selectedCardIds.has(feed.id);
      card.className = `feed-card ${isSelected ? 'active' : ''} ${isBatchSelected ? 'selected' : ''}`;
      card.dataset.feedId = feed.id;
      
      const thumb = feed.screenshot_url || 'https://images.unsplash.com/photo-1557597774-9d273605dfa9?w=400&auto=format&fit=crop&q=80';

      card.innerHTML = `
        <!-- Multi-select checkbox -->
        <div class="feed-card-check-box" title="Select for batch action">
          <input type="checkbox" class="card-checkbox" data-id="${feed.id}" ${isBatchSelected ? 'checked' : ''}>
        </div>

        <!-- Quick Favorite Button -->
        <button class="card-quick-fav ${feed.is_favorite ? 'favorited' : ''}" data-id="${feed.id}" title="${feed.is_favorite ? 'Remove from favorites' : 'Add to favorites'}">
          <i data-lucide="bookmark" style="width:11px;height:11px;"></i>
        </button>

        <!-- Quick Delete Button (Admin) -->
        <button class="card-quick-del" data-id="${feed.id}" title="Permanently delete camera (Admin)">
          <i data-lucide="trash-2" style="width:11px;height:11px;"></i>
        </button>

        <div class="feed-card-thumb">
          <img src="${thumb}" alt="${feed.product}" loading="lazy">
        </div>
        <div class="feed-card-info">
          <div class="feed-card-header">
            <span class="feed-card-product" title="${feed.product}">${feed.product}</span>
            <span class="badge-risk ${feed.risk_level}">${feed.risk_level}</span>
          </div>
          <div class="feed-card-ip">
            <span>${feed.ip}</span>
            <span class="port-tag">:${feed.port}</span>
          </div>
          <div class="feed-card-location">📍 ${feed.city || 'Unknown'}, ${feed.country_name} (${feed.country_code})</div>
          <div class="feed-card-org">🏢 ${feed.org}</div>
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

  selectFeed(feed, autoSwitchMobile = true) {
    this.selectedFeed = feed;

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

  async selectRandomFeed() {
    let chosenFeed = null;

    // Pick from currently loaded feeds list if there are multiple feeds
    if (this.feeds && this.feeds.length > 1) {
      // Pick a random feed that is different from currently selected feed if possible
      let available = this.feeds;
      if (this.selectedFeed) {
        const filtered = this.feeds.filter(f => f.id !== this.selectedFeed.id);
        if (filtered.length > 0) available = filtered;
      }
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
      alert('No camera feeds available to select.');
      return;
    }

    // Select the camera feed (and switch to player on mobile)
    this.selectFeed(chosenFeed, true);

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
    const newStatus = !feed.is_favorite;

    try {
      const res = await fetch(`/api/feeds/${feed.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_favorite: newStatus })
      });
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
  }

  async toggleFavoriteCurrent() {
    if (!this.selectedFeed) return;
    await this.toggleFavoriteFeed(this.selectedFeed);
  }

  // ==========================================
  // Vendor RTSP Profile & Resolver Engine
  // ==========================================

  async resolveRtspCurrentFeed() {
    if (!this.selectedFeed) {
      alert('Please select a camera from the list first to probe vendor RTSP paths.');
      return;
    }

    const feed = this.selectedFeed;
    const btnIcon = document.getElementById('btn-resolve-rtsp');
    const btnProbe = document.getElementById('btn-probe-vendor');

    const origIconHtml = btnIcon ? btnIcon.innerHTML : '';
    const origProbeHtml = btnProbe ? btnProbe.innerHTML : '';

    if (btnIcon) {
      btnIcon.innerHTML = '<i data-lucide="loader-2" class="spin"></i>';
      btnIcon.disabled = true;
    }
    if (btnProbe) {
      btnProbe.innerHTML = '<i data-lucide="loader-2" class="spin"></i> Probing Vendor Subpaths...';
      btnProbe.disabled = true;
    }
    lucide.createIcons();

    if (window.streamPlayer) {
      window.streamPlayer.setStatus('CONNECTING', `Probing vendor RTSP paths for ${feed.product || 'Camera'}...`);
    }

    try {
      const res = await fetch('/api/streams/resolve-rtsp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feed_id: feed.id,
          ip: feed.ip,
          port: feed.port,
          product: feed.product,
          current_url: feed.rtsp_url
        })
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const result = await res.json();
      console.log('RTSP Resolver probe result:', result);

      if (result.resolved) {
        feed.rtsp_url = result.stream_url;
        if (result.protocol === 'http') {
          feed.screenshot_url = result.stream_url;
        }
        feed.notes = `[Auto-Resolved ${result.vendor.toUpperCase()}] ${result.message}`;
        if (result.auth && result.auth !== 'unauthenticated') {
          feed.auth_status = 'default_creds';
        }

        // Refresh player view with newly discovered stream
        if (window.streamPlayer) {
          window.streamPlayer.playFeed(feed);
        }

        alert(`RTSP Stream Resolved!\n\nVendor: ${result.vendor.toUpperCase()}\nProtocol: ${result.protocol.toUpperCase()}\nPath: ${result.stream_url}\nProbed: ${result.tested_count} candidates\n\nStatus: ${result.message}`);
      } else {
        if (window.streamPlayer) {
          window.streamPlayer.setStatus('OFFLINE', `No open vendor subpath found (${result.tested_count} tested).`);
        }
        alert(`Probe Complete: No active stream found.\n\nVendor Profile: ${result.vendor.toUpperCase()}\nCandidates tested: ${result.tested_count}\nDetails: ${result.message}`);
      }
    } catch (err) {
      console.error('RTSP Resolve error:', err);
      alert('Error during RTSP resolution probe: ' + err.message);
    } finally {
      if (btnIcon) {
        btnIcon.innerHTML = origIconHtml;
        btnIcon.disabled = false;
      }
      if (btnProbe) {
        btnProbe.innerHTML = origProbeHtml;
        btnProbe.disabled = false;
      }
      lucide.createIcons();
    }
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
      search: '',
      is_favorite: null,
      limit: 25000,
      offset: 0
    };

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

        const data = await res.json();
        if (res.ok && data.source !== 'error') {
          statusMsg.className = 'alert-box success';
          const totalFound = (data.total !== undefined ? data.total : data.results?.length || 0).toLocaleString();
          const importedCount = (data.imported_to_db || 0).toLocaleString();
          let msg = `Discovered <strong>${totalFound}</strong> cameras (Source: ${data.source}). Ingested <strong>${importedCount}</strong> new feeds into database.`;
          if (data.message) {
            msg += `<br><small style="color:var(--text-muted);">${data.message}</small>`;
          }
          if (data.notice) {
            msg += `<br><small style="color:var(--accent-amber);">${data.notice}</small>`;
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
          statusMsg.innerHTML = data.error || data.detail || 'Search request failed.';
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

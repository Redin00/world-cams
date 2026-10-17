/**
 * WorldCams Leaflet Map Module
 * Renders global camera distribution, clusters, and interactive popups.
 */

class WorldMap {
  constructor() {
    this.map = null;
    this.markersGroup = null;
    this.onSelectFeedCallback = null;
    this.init();
  }

  init() {
    const mapContainer = document.getElementById('map-view');
    if (!mapContainer) return;

    // Initialize Leaflet map
    this.map = L.map('map-view', {
      center: [25, 10],
      zoom: 2,
      minZoom: 2,
      maxZoom: 18,
      zoomControl: true
    });

    // OpenStreetMap Basemap with CSS Dark Cyber Filter (No API Key Required)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19
    }).addTo(this.map);

    // Initialize MarkerCluster with chunked loading (prevents UI freeze on thousands of cameras)
    this.markersGroup = L.markerClusterGroup({
      showCoverageOnHover: false,
      maxClusterRadius: 45,
      spiderfyOnMaxZoom: true,
      chunkedLoading: true,
      chunkInterval: 100,
      chunkDelay: 20
    });
    this.map.addLayer(this.markersGroup);

    // Recenter button
    const recenterBtn = document.getElementById('btn-recenter-map');
    if (recenterBtn) {
      recenterBtn.addEventListener('click', () => this.recenter());
    }
  }

  setSelectHandler(callback) {
    this.onSelectFeedCallback = callback;
  }

  createMarkerIcon(riskLevel) {
    const riskClass = `pin-${riskLevel || 'high'}`;
    const svgIcon = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>`;
    return L.divIcon({
      className: 'custom-map-icon',
      html: `<div class="custom-marker-pin ${riskClass}">${svgIcon}</div>`,
      iconSize: [24, 24],
      iconAnchor: [12, 12],
      popupAnchor: [0, -12]
    });
  }

  createPopupContent(feed) {
    return `
      <div class="map-popup-card">
        <div class="map-popup-header">
          <span class="map-popup-title">${feed.product || 'IP Camera'}</span>
          <span class="badge-risk ${feed.risk_level || 'high'}">${feed.risk_level || 'high'}</span>
        </div>
        <div class="map-popup-ip">${feed.ip}:${feed.port}</div>
        <div class="map-popup-details">
          <div>📍 ${feed.city || 'Unknown'}, ${feed.country_name || 'Unknown'} (${feed.country_code || 'XX'})</div>
          <div>🏢 ${feed.org || 'Unknown Org'}</div>
          <div>🛡️ Auth: <strong>${feed.auth_status || 'unauthenticated'}</strong></div>
        </div>
        <button class="btn btn-sm btn-primary w-full" id="popup-play-${feed.id}" style="width:100%;margin-top:6px;">
          ▶ Stream Live
        </button>
      </div>
    `;
  }

  renderMarkers(feeds) {
    if (!this.markersGroup) return;
    this.markersGroup.clearLayers();

    const bounds = [];
    const counter = document.getElementById('map-counter');
    const newMarkers = [];

    feeds.forEach(feed => {
      if (feed.latitude && feed.longitude) {
        const lat = feed.latitude;
        const lng = feed.longitude;
        bounds.push([lat, lng]);

        const marker = L.marker([lat, lng], {
          icon: this.createMarkerIcon(feed.risk_level)
        });

        // Lazy popup generated on demand
        marker.bindPopup(() => this.createPopupContent(feed));

        marker.on('popupopen', () => {
          const btn = document.getElementById(`popup-play-${feed.id}`);
          if (btn) {
            btn.addEventListener('click', () => {
              if (this.onSelectFeedCallback) {
                this.onSelectFeedCallback(feed);
              }
            });
          }
        });

        marker.on('click', () => {
          if (this.onSelectFeedCallback && window.innerWidth > 900) {
            this.onSelectFeedCallback(feed);
          }
        });

        newMarkers.push(marker);
      }
    });

    // High performance bulk clustering of all points
    if (newMarkers.length > 0) {
      this.markersGroup.addLayers(newMarkers);
    }

    if (counter) {
      counter.textContent = `Showing ${newMarkers.length.toLocaleString()} of ${feeds.length.toLocaleString()} locations`;
    }

    if (bounds.length > 0 && this.map) {
      this.currentBounds = bounds;
    }
  }

  recenter() {
    if (this.currentBounds && this.currentBounds.length > 0 && this.map) {
      this.map.fitBounds(this.currentBounds, { padding: [40, 40], maxZoom: 12 });
    } else if (this.map) {
      this.map.setView([25, 10], 2);
    }
  }

  invalidateSize() {
    setTimeout(() => {
      this.map?.invalidateSize();
    }, 200);
  }
}

window.worldMap = new WorldMap();

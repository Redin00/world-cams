/**
 * WorldCams Video Player & MediaMTX Bridge Controller
 * Handles WebRTC (WHEP) via MediaMTX, Isolated Proxied HTTP MJPEG/JPEG streams,
 * Server-Side Web Portal Stream Extraction (Zero Code Execution / Anti-Honeypot),
 * and automated vendor profile resolution with smart fallback.
 */

// Global HTML Escaping Utility to prevent Stored/Reflected XSS from untrusted camera banners
window.escapeHtml = function (value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

// Global CSRF Token Reader & Fetch Interceptor for Double-Submit Cookie Protection
window.getCsrfToken = function () {
  const match = document.cookie.match(/(?:^|;\s*)worldcams_csrf=([^;]+)/);
  return (match ? decodeURIComponent(match[1]) : '') || window._csrfToken || '';
};

// Helper functions to classify HTTP/HTTPS Web camera ports (80-99 including 81, 82, 83..., 443, 8443, 8000-8499, etc.) vs RTSP ports
window.isHttpsCameraPort = function (port) {
  return [443, 8443, 4443, 9443].includes(Number(port));
};

window.isWebCameraPort = function (port) {
  const p = Number(port) || 0;
  if ([554, 8554, 10554, 5544, 7070, 7447, 37777].includes(p)) return false;
  if (window.isHttpsCameraPort(p)) return true;
  if (p >= 80 && p <= 99) return true;
  if ((p >= 8000 && p <= 8499) || (p >= 8800 && p <= 8899)) return true;
  if ([800, 3000, 5000, 5001, 5223, 9000, 9001, 9080, 10000, 50000, 50001, 50002, 50003, 60000, 60001].includes(p)) return true;
  return false;
};

(function installCsrfFetchInterceptor() {
  const originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init = {}) {
    const method = (init.method || (input && input.method) || 'GET').toUpperCase();
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      const headers = new Headers(init.headers || {});
      const csrfToken = window.getCsrfToken();
      if (csrfToken && !headers.has('X-CSRF-Token')) {
        headers.set('X-CSRF-Token', csrfToken);
      }
      if (!headers.has('X-Requested-With')) {
        headers.set('X-Requested-With', 'XMLHttpRequest');
      }
      init = Object.assign({}, init, { headers });
    }
    return originalFetch(input, init);
  };
})();

class StreamPlayer {
  constructor() {
    this.videoEl = document.getElementById('live-video');
    this.webrtcFrame = document.getElementById('webrtc-frame');
    this.snapshotEl = document.getElementById('stream-snapshot');
    this.overlayEl = document.getElementById('video-overlay');
    this.overlaySpinner = this.overlayEl ? this.overlayEl.querySelector('.overlay-spinner') : null;
    this.overlayMsg = document.getElementById('overlay-msg');
    this.badgeEl = document.getElementById('player-status-badge');
    this.titleEl = document.getElementById('player-feed-title');
    this.btnOpenWeb = document.getElementById('btn-open-web-cam');
    this.isHlsMode = false;
    this.activeStreamData = null;
    this.hlsInstance = null;
    this.peerConnection = null;
    this.whepSessionUrl = null;
    this.whepFallbackTimer = null;
    this.activeHlsPath = null;
    this.forceHlsFixRequested = false;
    this.hlsNetworkRetries = 0;
    this.hlsMediaRetries = 0;
    
    // Tech info elements
    this.techProto = document.getElementById('tech-proto');
    this.techAuth = document.getElementById('tech-auth');
    this.techPort = document.getElementById('tech-port');
    this.techLoc = document.getElementById('tech-location');

    // Audit inspector elements
    this.auditIp = document.getElementById('audit-ip');
    this.auditProduct = document.getElementById('audit-product');
    this.auditOrg = document.getElementById('audit-org');
    this.auditAsn = document.getElementById('audit-asn');
    this.auditRtsp = document.getElementById('audit-rtsp');
    this.auditPath = document.getElementById('audit-path');
    this.auditBanner = document.getElementById('audit-banner');
    this.auditNotes = document.getElementById('audit-notes-text');

    // Isolated Web Portal Inspector elements (Zero Code Execution)
    this.webConsoleDisplay = document.getElementById('web-console-display');
    this.webConsoleBadgeText = document.getElementById('web-console-badge-text');
    this.webConsoleTitle = document.getElementById('web-console-title');
    this.webConsoleDesc = document.getElementById('web-console-desc');
    this.btnLaunchConsole = document.getElementById('btn-launch-console');
    this.webConsoleFrame = document.getElementById('web-console-frame');
    this.webConsoleIsolationMeta = document.getElementById('web-console-isolation-meta');
    this.webConsoleSourceCode = document.getElementById('web-console-source-code');
    this.btnToggleEmbed = document.getElementById('btn-toggle-embed');

    this.currentFeed = null;
    this.activeFeedId = null;
    this.abortController = null;
    this.connectTimeoutTimer = null;
    this.refreshInterval = null;
    this.telemetryInterval = null;
    this.telemetryToken = 0;
    this.isSnapshotLoaded = false;
    this.hasAutoResolved = false;

    this.initControls();
  }

  initControls() {
    const fsBtn = document.getElementById('btn-fullscreen-player');
    if (fsBtn) {
      fsBtn.addEventListener('click', () => {
        const box = document.getElementById('video-box');
        if (!document.fullscreenElement) {
          box.requestFullscreen?.().catch(console.error);
        } else {
          document.exitFullscreen?.();
        }
      });
    }

    if (this.btnToggleEmbed) {
      this.btnToggleEmbed.addEventListener('click', () => {
        if (!this.webConsoleFrame) return;
        const isHidden = this.webConsoleFrame.style.display === 'none';
        this.webConsoleFrame.style.display = isHidden ? 'block' : 'none';
        this.btnToggleEmbed.innerHTML = isHidden
          ? '<i data-lucide="eye-off"></i> Hide Sanitized Source'
          : '<i data-lucide="code"></i> View Sanitized Source';
        lucide.createIcons();
      });
    }

    window.addEventListener('pagehide', () => {
      const activePath = this.activeStreamData?.stream_path;
      if (activePath) {
        fetch('/api/streams/stop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ stream_name: activePath }),
          keepalive: true
        }).catch(() => {});
      }
    });
  }

  setStatus(state, message) {
    if (!this.badgeEl) return;
    
    const stateClass = state.toLowerCase().replace(/[^a-z0-9]/g, '-');
    const torActive = Boolean(this.currentFeedTorRouted || window.app?.torStatus?.enabled);
    const showTorTag = torActive && (state.startsWith('LIVE') || state.startsWith('ISOLATED WEB'));
    const displayLabel = showTorTag ? `${state} [TOR]` : state;
    this.badgeEl.className = 'live-pill ' + stateClass;
    this.badgeEl.innerHTML = `<span class="dot"></span> ${window.escapeHtml(displayLabel)}`;
    
    if (state.startsWith('ISOLATED WEB PORTAL') || state.startsWith('ACTIVE WEB CONSOLE')) {
      if (this.overlayEl) this.overlayEl.style.display = 'none';
      if (this.snapshotEl) this.snapshotEl.style.display = 'none';
      if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
      if (this.videoEl) this.videoEl.style.display = 'none';
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'flex';
    } else {
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'none';
      if (state === 'CONNECTING') {
        if (this.overlaySpinner) this.overlaySpinner.style.display = 'block';
        if (this.overlayEl) {
          this.overlayEl.style.display = 'flex';
          this.overlayMsg.textContent = message || 'Negotiating stream route...';
        }
      } else if (state.startsWith('LIVE WEBRTC') || state.startsWith('LIVE HLS')) {
        if (this.overlayEl) this.overlayEl.style.display = 'none';
        if (this.snapshotEl) this.snapshotEl.style.display = 'none';
        if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
        if (this.videoEl) this.videoEl.style.display = 'block';
      } else if (state.includes('OFFLINE') || state.includes('UNREACHABLE')) {
        // Show clear non-spinning offline reason banner so port-check / offline errors are always visible
        if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
        if (this.videoEl) this.videoEl.style.display = 'none';
        if (this.snapshotEl) this.snapshotEl.style.display = 'block';
        if (this.overlaySpinner) this.overlaySpinner.style.display = 'none';
        if (this.overlayEl) {
          this.overlayEl.style.display = message ? 'flex' : 'none';
          if (message && this.overlayMsg) this.overlayMsg.textContent = message;
        }
      } else {
        // Direct HTTP Live stream or Snapshot
        if (this.overlayEl) this.overlayEl.style.display = 'none';
        if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
        if (this.videoEl) this.videoEl.style.display = 'none';
        if (this.snapshotEl) this.snapshotEl.style.display = 'block';
      }
    }

    const liveDot = document.getElementById('mobile-live-indicator');
    if (liveDot) {
      if (state.startsWith('LIVE') || state.startsWith('ISOLATED WEB') || state.startsWith('SNAPSHOT')) {
        liveDot.classList.add('live');
      } else if (state.includes('OFFLINE') || state.includes('BLOCKED')) {
        liveDot.classList.remove('live');
      }
    }
    if (state.startsWith('LIVE') && window.app) {
      window.app.randomSkipCount = 0;
    }
  }

  async showWebConsole(feed, message, effectiveUrl = null) {
    if (this.activeFeedId !== feed.id) return;
    const targetId = feed.id;
    const portNum = Number(feed.port) || 80;
    const isHttps = portNum === 443 || (feed.rtsp_url && feed.rtsp_url.startsWith('https://'));
    const isPlainFallback = message && message.includes('Plaintext HTTP');
    const protoLabel = isPlainFallback ? 'HTTP (SSL Fallback)' : (isHttps ? 'HTTPS' : 'HTTP');

    if (this.overlayEl) this.overlayEl.style.display = 'none';
    if (this.snapshotEl) this.snapshotEl.style.display = 'none';
    if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
    if (this.videoEl) this.videoEl.style.display = 'none';

    const encodedId = encodeURIComponent(feed.id);
    if (this.webConsoleDisplay) {
      this.webConsoleDisplay.style.display = 'flex';
      if (this.webConsoleBadgeText) this.webConsoleBadgeText.textContent = `ISOLATED WEB PORTAL (${protoLabel} NO-EXEC)`;
      if (this.webConsoleTitle) this.webConsoleTitle.textContent = `${feed.product || 'Surveillance Device'} — Isolated Portal`;
      if (this.webConsoleDesc) {
        this.webConsoleDesc.textContent = 'Remote portal code execution is blocked (Anti-Honeypot Protection). Extracting stream sources server-side...';
      }
      if (this.btnLaunchConsole) {
        this.btnLaunchConsole.href = `/api/proxy/web?feed_id=${encodedId}`;
        this.btnLaunchConsole.title = 'Open Sanitized Read-Only Portal Source Report (Zero Script Execution)';
      }
      if (this.webConsoleFrame) {
        this.webConsoleFrame.style.display = 'block';
      }
      if (this.webConsoleSourceCode) {
        this.webConsoleSourceCode.textContent = 'Fetching & sanitizing portal source via isolated backend...';
      }
      if (this.btnToggleEmbed) {
        this.btnToggleEmbed.innerHTML = '<i data-lucide="eye-off"></i> Hide Sanitized Source';
      }
      lucide.createIcons();
    }

    this.setStatus(`ISOLATED WEB PORTAL (${protoLabel})`);
    if (this.techProto) this.techProto.textContent = `Isolated ${protoLabel} Portal (Zero-Exec Sandbox, Port ${portNum})`;

    // Query backend isolated portal extractor (JSON mode)
    try {
      const res = await fetch(`/api/proxy/web?feed_id=${encodedId}&format=json`, {
        signal: this.abortController ? this.abortController.signal : undefined
      });
      if (this.activeFeedId !== targetId) return;
      if (res.ok) {
        const data = await res.json();
        if (this.activeFeedId !== targetId) return;

        // If the backend extracted a verified video/snapshot stream from the portal HTML, stream it directly!
        if (data.extracted_stream_url) {
          feed.rtsp_url = data.extracted_stream_url;
          feed.screenshot_url = data.extracted_stream_url;
          this.updateRtspSourceDisplay(data.extracted_stream_url);
          if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'none';
          if (this.snapshotEl) {
            this.snapshotEl.style.display = 'block';
            this.snapshotEl.src = `/api/proxy/stream?feed_id=${encodedId}&_ts=${Date.now()}`;
          }
          this.setStatus(`LIVE ${protoLabel} (EXTRACTED)`);
          if (this.techProto) {
            this.techProto.textContent = `Extracted ${protoLabel} Stream from Isolated Portal`;
          }
          return;
        }

        if (this.webConsoleTitle && data.portal_title) {
          this.webConsoleTitle.textContent = data.portal_title;
        }
        if (this.webConsoleDesc) {
          this.webConsoleDesc.textContent = `Portal requires authentication or exposes no unauthenticated stream. All remote scripts (${data.blocked_scripts_count || 0}) and embedded objects (${data.blocked_objects_count || 0}) were stripped.`;
        }
        if (this.webConsoleIsolationMeta) {
          this.webConsoleIsolationMeta.textContent = `🛡️ Anti-Honeypot Sandbox: ${data.blocked_scripts_count || 0} scripts & ${data.blocked_objects_count || 0} iframes/objects neutralized (Server: ${data.server_header || 'Unknown'}).`;
        }
        if (this.webConsoleSourceCode) {
          this.webConsoleSourceCode.textContent = data.source_preview || '<!-- Empty portal body -->';
        }
      } else if (res.status === 503) {
        let errDetail = 'Tor SOCKS5 proxy unreachable (fail-closed privacy protection)';
        try {
          const errJson = await res.json();
          if (errJson?.detail) errDetail = errJson.detail;
        } catch (_) {}
        this.setStatus('TOR PROXY OFFLINE', errDetail);
        if (this.webConsoleSourceCode) {
          this.webConsoleSourceCode.textContent = `[FAIL-CLOSED 503] ${errDetail}`;
        }
      } else {
        if (this.webConsoleSourceCode) {
          this.webConsoleSourceCode.textContent = 'Portal unreachable or connection refused by remote camera.';
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      if (this.webConsoleSourceCode) {
        this.webConsoleSourceCode.textContent = 'Isolated portal inspection failed: ' + err.message;
      }
    }
  }

  teardownMediaEngines() {
    if (this.whepFallbackTimer) {
      clearTimeout(this.whepFallbackTimer);
      this.whepFallbackTimer = null;
    }
    if (this.hlsInstance) {
      try {
        this.hlsInstance.destroy();
      } catch (_) {}
      this.hlsInstance = null;
    }
    if (this.peerConnection) {
      try {
        this.peerConnection.ontrack = null;
        this.peerConnection.onicecandidate = null;
        this.peerConnection.oniceconnectionstatechange = null;
        this.peerConnection.onconnectionstatechange = null;
        this.peerConnection.close();
      } catch (_) {}
      this.peerConnection = null;
    }
    if (this.whepSessionUrl) {
      const teardownUrl = this.whepSessionUrl;
      this.whepSessionUrl = null;
      fetch(teardownUrl, { method: 'DELETE', keepalive: true }).catch(() => {});
    }
    if (this.videoEl) {
      this.videoEl.onplay = null;
      if (this.videoEl.srcObject) {
        try {
          this.videoEl.srcObject.getTracks().forEach(t => t.stop());
        } catch (_) {}
        this.videoEl.srcObject = null;
      }
      this.videoEl.removeAttribute('src');
      try {
        this.videoEl.load();
      } catch (_) {}
    }
  }

  stop(nextStreamPath = null) {
    window.app?.cancelRandomAutoSkip?.();
    const prevPath = this.activeStreamData?.stream_path || this.currentFeed?.stream_path;
    // 1. Abort any in-flight fetch requests immediately
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    this.abortController = new AbortController();

    // 2. Clear all timers and active intervals
    if (this.connectTimeoutTimer) {
      clearTimeout(this.connectTimeoutTimer);
      this.connectTimeoutTimer = null;
    }
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
      this.refreshInterval = null;
    }
    if (this.telemetryInterval) {
      clearTimeout(this.telemetryInterval);
      clearInterval(this.telemetryInterval);
      this.telemetryInterval = null;
    }
    this.telemetryToken = (this.telemetryToken || 0) + 1;
    this.isSnapshotLoaded = false;
    this.currentFeedTorRouted = false;
    this.isHlsMode = false;
    this.activeStreamData = null;
    this.activeHlsPath = null;
    this.forceHlsFixRequested = false;
    this.hlsNetworkRetries = 0;
    this.hlsMediaRetries = 0;

    // 3. Clear Isolated Portal card & source preview
    if (this.webConsoleDisplay) {
      this.webConsoleDisplay.style.display = 'none';
    }
    if (this.webConsoleFrame) {
      this.webConsoleFrame.style.display = 'none';
    }
    if (this.webConsoleSourceCode) {
      this.webConsoleSourceCode.textContent = '';
    }

    // 4. IMMEDIATELY terminate & clear previous HTTP stream or snapshot
    if (this.snapshotEl) {
      this.snapshotEl.onload = null;
      this.snapshotEl.onerror = null;
      // Assigning a blank 1x1 base64 GIF forcefully aborts any persistent multipart/MJPEG HTTP connection
      this.snapshotEl.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
      this.snapshotEl.style.display = 'none';
    }

    // 5. Tear down native HLS/WebRTC engines and legacy iframe
    if (this.webrtcFrame) {
      this.webrtcFrame.src = 'about:blank';
      this.webrtcFrame.style.display = 'none';
    }
    this.teardownMediaEngines();
    if (this.videoEl) {
      this.videoEl.style.display = 'none';
    }

    // 7. Release previous MediaMTX stream route (skip if re-playing the exact same path to avoid a stop/play race)
    if (prevPath && prevPath !== nextStreamPath) {
      fetch('/api/streams/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stream_name: prevPath }),
        keepalive: true
      }).catch(() => {});
    }

    const liveDot = document.getElementById('mobile-live-indicator');
    if (liveDot) liveDot.classList.remove('live');
  }

  async playHttpStream(feed, customUrl) {
    const portNum = Number(feed.port) || 80;
    this.stop(feed?.stream_path || null);
    this.currentFeed = feed;
    this.activeFeedId = feed.id;
    const targetId = feed.id;
    const signal = this.abortController.signal;

    const isHttps = portNum === 443 || (customUrl && customUrl.startsWith('https://')) || (feed.rtsp_url && feed.rtsp_url.startsWith('https://'));
    const protoLabel = isHttps ? 'HTTPS' : 'HTTP';
    const scheme = isHttps ? 'https' : 'http';
    const encodedId = encodeURIComponent(feed.id);

    if (this.techPort) this.techPort.textContent = `${portNum} (${protoLabel} Live)`;
    if (this.techProto) this.techProto.textContent = `${protoLabel} Isolated Stream Proxy (Port ${portNum})`;
    this.setStatus('CONNECTING', `Extracting & verifying ${protoLabel} media stream on port ${portNum}...`);

    // Enable / update Open Isolated Source Report button
    if (this.btnOpenWeb) {
      this.btnOpenWeb.href = `/api/proxy/web?feed_id=${encodedId}`;
      this.btnOpenWeb.style.display = 'inline-flex';
      this.btnOpenWeb.title = `Open Isolated Portal Source Inspector [${scheme}://${feed.ip}:${portNum}/]`;
      lucide.createIcons();
    }

    // Call backend to determine if device serves a video stream or an isolated Web Portal
    let streamInfo = null;
    try {
      const playRes = await fetch('/api/streams/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feed_id: feed.id, rtsp_url: feed.rtsp_url }),
        signal
      });
      if (this.activeFeedId !== targetId) return;
      if (playRes.status === 503) {
        let errDetail = 'Tor SOCKS5 proxy unreachable (fail-closed privacy protection)';
        try {
          const errJson = await playRes.json();
          if (errJson?.detail) errDetail = errJson.detail;
        } catch (_) {}
        this.setStatus('TOR PROXY OFFLINE', errDetail);
        if (this.techProto) this.techProto.textContent = 'Tor SOCKS5 Unreachable (Fail-Closed)';
        return;
      }
      if (playRes.ok) {
        streamInfo = await playRes.json();
        this.currentFeedTorRouted = Boolean(streamInfo.tor_routed);
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      console.warn('Streams play check notice:', e);
    }

    if (this.activeFeedId !== targetId) return;

    if (streamInfo && streamInfo.status === 'web_ui') {
      this.showWebConsole(feed, streamInfo.message, streamInfo.web_url);
      return;
    }

    if (streamInfo && streamInfo.status === 'offline') {
      if (this.snapshotEl) {
        this.snapshotEl.onload = null;
        this.snapshotEl.onerror = null;
        this.snapshotEl.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
      }
      const offlineMsg = streamInfo.message || `${protoLabel} camera port ${portNum} unreachable or IP changed.`;
      this.setStatus('OFFLINE', offlineMsg);
      if (this.techProto) this.techProto.textContent = `Port ${portNum} Unreachable (Offline / IP Changed)`;
      window.app?.scheduleRandomSkipOnUnreachable?.(targetId, offlineMsg);
      return;
    }

    if (streamInfo && streamInfo.rtsp_source && !streamInfo.rtsp_source.includes('unsplash')) {
      feed.rtsp_url = streamInfo.rtsp_source;
      feed.screenshot_url = streamInfo.rtsp_source;
      this.updateRtspSourceDisplay(streamInfo.rtsp_source);
    }

    // Proxy stream URL: ALL HTTP/HTTPS streams are routed exclusively through our isolated backend proxy
    // (Never connect the user's browser directly to untrusted camera IPs!)
    const proxyStreamUrl = `/api/proxy/stream?feed_id=${encodedId}&_ts=${Date.now()}`;
    const effectiveCheckUrl = ((streamInfo && streamInfo.rtsp_source) || customUrl || feed.rtsp_url || '').toLowerCase();
    const isContinuousMjpeg = (streamInfo && streamInfo.stream_type === 'mjpeg') ||
      ['mjpg', 'mjpeg', 'nphmotionjpeg', 'video.cgi', 'faststream', 'videostream.cgi'].some(k => effectiveCheckUrl.includes(k));
    const torTag = streamInfo?.tor_routed
      ? ` [TOR ${streamInfo.tor_circuit_id || ''}]`.trimEnd()
      : (window.app?.torStatus?.enabled ? ' [TOR]' : '');

    this.snapshotEl.onload = () => {
      if (this.activeFeedId !== targetId) return;
      this.isSnapshotLoaded = true;
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'none';
      this.snapshotEl.style.display = 'block';
      this.setStatus(`LIVE ${protoLabel}`);
      if (this.techProto) {
        this.techProto.textContent = isContinuousMjpeg
          ? `${protoLabel}/MJPEG Isolated Stream (Port ${portNum})${torTag}`
          : `${protoLabel}/JPEG Isolated Snapshot (Port ${portNum})${torTag}`;
      }
    };

    this.snapshotEl.onerror = () => {
      if (this.activeFeedId !== targetId) return;
      this.isSnapshotLoaded = false;
      // If web port, inspect in Isolated Web Portal mode (zero code execution)
      if (window.isWebCameraPort(portNum)) {
        this.showWebConsole(feed, `Isolated camera portal on port ${portNum}. Extracting source...`);
      } else {
        const offlineMsg = `${protoLabel} camera port ${portNum} closed or stream unavailable.`;
        this.setStatus('OFFLINE', offlineMsg);
        if (this.techProto) this.techProto.textContent = `Port ${portNum} Unreachable (Offline)`;
        window.app?.scheduleRandomSkipOnUnreachable?.(targetId, offlineMsg);
      }
    };

    this.snapshotEl.src = proxyStreamUrl;

    // Snapshot refresh timer for static snapshots (every 2.5s)
    this.refreshInterval = setInterval(() => {
      if (this.currentFeed && this.activeFeedId === targetId && this.snapshotEl && this.isSnapshotLoaded) {
        if (!isContinuousMjpeg) {
          const sep = proxyStreamUrl.includes('?') ? '&' : '?';
          this.snapshotEl.src = `${proxyStreamUrl}${sep}_r=${Date.now()}`;
        }
      }
    }, 2500);
  }

  async retryWithDefaultCredentials(feed) {
    if (!feed) return null;
    try {
      const res = await fetch('/api/streams/retry-default-creds', {
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
      if (res.ok) {
        const data = await res.json();
        if (data.resolved && data.stream_url) {
          feed.rtsp_url = data.stream_url;
          feed.auth_status = 'default_creds';
          this.updateRtspSourceDisplay(data.stream_url);
          if (this.techAuth) {
            this.techAuth.textContent = 'DEFAULT CREDS';
            this.techAuth.className = 'value text-warning';
          }
          return data;
        }
      }
    } catch (e) {
      console.warn('Retry with default credentials error:', e);
    }
    return null;
  }

  updateRtspSourceDisplay(url) {
    if (!this.auditRtsp) return;
    if (!url) {
      this.auditRtsp.textContent = '--';
      return;
    }

    // Match rtsp://user:pass@host... or rtsp://user@host...
    const credMatch = url.match(/^(rtsps?:\/\/)([^:@]+)(?::([^@]*))?@(.+)$/i);
    if (credMatch) {
      const scheme = window.escapeHtml(credMatch[1]);
      const user = window.escapeHtml(credMatch[2]);
      const pass = window.escapeHtml(credMatch[3] !== undefined ? credMatch[3] : '');
      const rest = window.escapeHtml(credMatch[4]);

      this.auditRtsp.innerHTML = `
        <span style="color: var(--text-muted);">${scheme}</span><span class="cred-highlight" style="color: var(--accent-amber); font-weight: 700; background: rgba(245, 158, 11, 0.15); padding: 1px 5px; border-radius: 3px; border: 1px solid rgba(245, 158, 11, 0.35);" title="Authenticated with Default Credentials">${user}:${pass}</span><span style="color: var(--text-muted);">@</span><span>${rest}</span>
        <span class="badge-cred-pill" style="display: inline-block; margin-left: 6px; font-size: 0.65rem; background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); padding: 1px 6px; border-radius: 3px; border: 1px solid rgba(245, 158, 11, 0.4); font-weight: 600; vertical-align: middle;">DEFAULT CREDS</span>
      `;
    } else {
      this.auditRtsp.textContent = url;
    }
  }

  getSafeSnapshotSrc(feed) {
    const defaultPlaceholder = 'https://images.unsplash.com/photo-1557597774-9d273605dfa9?w=800&auto=format&fit=crop&q=80';
    if (!feed || !feed.screenshot_url) return defaultPlaceholder;
    if (feed.screenshot_url.startsWith('https://images.unsplash.com/')) {
      return feed.screenshot_url;
    }
    return `/api/proxy/stream?feed_id=${encodeURIComponent(feed.id)}`;
  }

  async playFeed(feed, isRetry = false) {
    this.stop(feed?.stream_path || null);
    this.currentFeed = feed;
    this.activeFeedId = feed.id;
    if (!isRetry) {
      this.hasAutoResolved = false;
    }

    const targetId = feed.id;
    const signal = this.abortController.signal;

    // 1. Update Title & Technical Metadata Bar
    if (this.titleEl) this.titleEl.textContent = `${feed.product} [${feed.ip}:${feed.port}]`;
    if (this.techAuth) {
      this.techAuth.textContent = (feed.auth_status || 'UNAUTH').toUpperCase();
      this.techAuth.className = `value ${feed.auth_status === 'unauthenticated' ? 'text-danger' : 'text-warning'}`;
    }
    if (this.techLoc) this.techLoc.textContent = `${feed.city || 'Unknown'}, ${feed.country_code}`;

    // 2. Update Security Audit Tabs
    if (this.auditIp) this.auditIp.textContent = `${feed.ip}:${feed.port}`;
    if (this.auditProduct) this.auditProduct.textContent = feed.product;
    if (this.auditOrg) this.auditOrg.textContent = feed.org || 'Unknown Org';
    if (this.auditAsn) this.auditAsn.textContent = feed.asn || 'N/A';
    this.updateRtspSourceDisplay(feed.rtsp_url);
    if (this.auditPath) this.auditPath.textContent = feed.stream_path;
    if (this.auditBanner) this.auditBanner.textContent = feed.banner || 'RTSP/1.0 200 OK\r\nServer: IP Camera Streaming Server';
    if (this.auditNotes) this.auditNotes.textContent = feed.notes || 'Exposed surveillance endpoint discovered without access control restriction.';

    const portNum = Number(feed.port) || 554;
    const isWebPort = window.isWebCameraPort(portNum);
    const isAltRtsp = portNum === 8554;
    const rtspUrl = feed.rtsp_url || '';
    const isHttpProtocol = rtspUrl.startsWith('http://') || rtspUrl.startsWith('https://');

    // =========================================================================
    // CASE A: DIRECT OR PROXIED HTTP/HTTPS STREAM (Port 80-99, 443, 8000-8499, etc. or HTTP/HTTPS URL)
    // =========================================================================
    if (isWebPort || isHttpProtocol) {
      const effectiveWebUrl = isHttpProtocol
        ? rtspUrl
        : `${window.isHttpsCameraPort(portNum) ? 'https' : 'http'}://${feed.ip}:${portNum}/`;
      this.playHttpStream(feed, effectiveWebUrl);
      return;
    }

    // =========================================================================
    // CASE B: NATIVE RTSP STREAM (Port 554 / Port 8554 via MediaMTX Gateway)
    // =========================================================================
    if (this.btnOpenWeb) {
      this.btnOpenWeb.style.display = 'none';
    }

    if (this.techPort) this.techPort.textContent = isAltRtsp ? '8554 (Alt RTSP)' : `${portNum} (RTSP Stream)`;
    const torEnabledNow = Boolean(window.app?.torStatus?.enabled);
    if (this.techProto) this.techProto.textContent = isAltRtsp ? 'MediaMTX (Negotiating Alt RTSP 8554...)' : 'MediaMTX (Negotiating RTSP...)';
    this.setStatus(
      'CONNECTING',
      torEnabledNow
        ? 'Probing RTSP path & bridging stream over Tor...'
        : (isAltRtsp ? 'Bridging Alt RTSP 8554 stream with MediaMTX...' : 'Bridging RTSP stream with MediaMTX...')
    );

    // Load preview snapshot via isolated proxy or safe placeholder while connecting
    const fallbackThumb = this.getSafeSnapshotSrc(feed);
    this.snapshotEl.onload = () => {
      if (this.activeFeedId === targetId) {
        this.isSnapshotLoaded = true;
      }
    };
    this.snapshotEl.onerror = () => {
      if (this.activeFeedId === targetId) {
        this.isSnapshotLoaded = false;
      }
    };
    this.snapshotEl.src = fallbackThumb;

    // Safety timeout: if backend /api/streams/play hangs, attempt fallback vendor resolution
    const connectTimeoutMs = torEnabledNow ? 90000 : 30000;
    this.connectTimeoutTimer = setTimeout(async () => {
      if (this.activeFeedId !== targetId) return;
      if (this.badgeEl && this.badgeEl.textContent.includes('CONNECTING')) {
        console.warn('RTSP stream negotiation timeout. Attempting automated subpath resolution...');
        if (!this.hasAutoResolved) {
          this.hasAutoResolved = true;
          this.setStatus('CONNECTING', `Retrying RTSP default credentials for ${feed.product || 'vendor'}...`);
          try {
            const retryRes = await this.retryWithDefaultCredentials(feed);
            if (this.activeFeedId !== targetId) return;
            if (retryRes && retryRes.resolved) {
              this.playFeed(feed, true);
              return;
            }
          } catch (e) {
            console.warn('Credential retry error:', e);
          }

          this.setStatus('CONNECTING', 'Auto-probing working vendor RTSP subpaths...');
          try {
            const probeRes = await fetch('/api/streams/resolve-rtsp', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                feed_id: feed.id,
                ip: feed.ip,
                port: feed.port,
                product: feed.product,
                current_url: feed.rtsp_url
              }),
              signal
            });
            if (this.activeFeedId !== targetId) return;

            if (probeRes.ok) {
              const resData = await probeRes.json();
              if (this.activeFeedId !== targetId) return;

              if (resData.resolved) {
                feed.rtsp_url = resData.stream_url;
                if (resData.protocol === 'http') {
                  this.playHttpStream(feed, resData.stream_url);
                  return;
                }
                this.playFeed(feed, true);
                return;
              }
            }
          } catch (e) {
            if (e.name !== 'AbortError') {
              console.error('Auto-probe error:', e);
            }
          }
        }

        if (this.activeFeedId !== targetId) return;
        const hasLiveSnapshot = feed.screenshot_url && !feed.screenshot_url.includes('unsplash');
        if (this.isSnapshotLoaded && hasLiveSnapshot) {
          this.setStatus('SNAPSHOT (RTSP BLOCKED)', `Camera accessible via HTTP, but RTSP port ${portNum} filtered.`);
          if (this.techProto) this.techProto.textContent = `HTTP Snapshot (RTSP ${portNum} Filtered by Firewall)`;
        } else {
          const timeoutMsg = `Remote RTSP port ${portNum} not responding.`;
          this.setStatus('OFFLINE', timeoutMsg);
          if (this.techProto) this.techProto.textContent = `WAN Port ${portNum} Unreachable`;
          window.app?.scheduleRandomSkipOnUnreachable?.(targetId, timeoutMsg);
        }
      }
    }, connectTimeoutMs);

    try {
      const res = await fetch('/api/streams/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feed_id: feed.id,
          rtsp_url: feed.rtsp_url,
          stream_name: feed.stream_path
        }),
        signal
      });

      // Discard if the user has already switched to another camera while waiting
      if (this.activeFeedId !== targetId) {
        console.log(`Discarding MediaMTX stream for ${targetId}: user already switched.`);
        return;
      }

      if (res.status === 503) {
        let errDetail = 'Tor SOCKS5 proxy unreachable (fail-closed privacy protection)';
        try {
          const errJson = await res.json();
          if (errJson?.detail) errDetail = errJson.detail;
        } catch (_) {}
        if (this.connectTimeoutTimer) {
          clearTimeout(this.connectTimeoutTimer);
          this.connectTimeoutTimer = null;
        }
        this.setStatus('TOR PROXY OFFLINE', errDetail);
        if (this.techProto) this.techProto.textContent = 'Tor SOCKS5 Unreachable (Fail-Closed)';
        return;
      }

      if (!res.ok) {
        throw new Error(`MediaMTX bridge returned status ${res.status}`);
      }

      const streamData = await res.json();
      if (this.activeFeedId !== targetId) return;
      this.currentFeedTorRouted = Boolean(streamData.tor_routed);
      this.currentFeedTorCircuit = streamData.tor_circuit_id || '';

      console.log('Stream route response:', streamData);

      if (streamData.rtsp_source) {
        feed.rtsp_url = streamData.rtsp_source;
        this.updateRtspSourceDisplay(streamData.rtsp_source);
        if (streamData.rtsp_source.includes('@')) {
          feed.auth_status = 'default_creds';
          if (this.techAuth) {
            this.techAuth.textContent = 'DEFAULT CREDS';
            this.techAuth.className = 'value text-warning';
          }
        }
      }

      // If backend determined target port is unreachable/closed (e.g. camera offline or IP changed), stop immediately
      if (streamData.status === 'offline') {
        if (this.connectTimeoutTimer) {
          clearTimeout(this.connectTimeoutTimer);
          this.connectTimeoutTimer = null;
        }
        this.teardownMediaEngines();
        if (this.snapshotEl) {
          this.snapshotEl.onload = null;
          this.snapshotEl.onerror = null;
          this.snapshotEl.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
        }
        const offlineMsg = streamData.message || `Port ${portNum} unreachable — camera offline or IP changed.`;
        this.setStatus('OFFLINE', offlineMsg);
        if (this.techProto) this.techProto.textContent = `Port ${portNum} Unreachable (Offline / IP Changed)`;
        window.app?.scheduleRandomSkipOnUnreachable?.(targetId, offlineMsg);
        return;
      }

      // If backend already exhausted all candidate RTSP paths/default creds, avoid repeating the exact same probe in JS
      if (streamData.status === 'probed_unverified') {
        this.hasAutoResolved = true;
      }

      // If backend auto-resolved to an HTTP stream (port 8000 or HTTP fallback)
      if (streamData.status === 'http_stream' || !streamData.webrtc_url) {
        clearTimeout(this.connectTimeoutTimer);
        this.playHttpStream(feed, streamData.rtsp_source);
        return;
      }

      // Start MediaMTX RTSP playback via native <video> (HLS over same-origin /api/hls or WebRTC over /api/whep with auto-fallback)
      if (this.videoEl) {
        if (this.connectTimeoutTimer) {
          clearTimeout(this.connectTimeoutTimer);
          this.connectTimeoutTimer = null;
        }
        this.activeStreamData = streamData;
        const isHttpsPage = window.location.protocol === 'https:';
        const useHlsDirectly = streamData.playback_mode === 'hls' || isHttpsPage || !streamData.webrtc_url;
        this.isHlsMode = useHlsDirectly;
        this.activeHlsPath = null;
        this.forceHlsFixRequested = false;
        this.setStatus(
          'CONNECTING',
          useHlsDirectly
            ? 'Initializing HLS segments and audio pipeline...'
            : 'Negotiating low-latency WebRTC media stream...'
        );
        this.monitorRtspTelemetry(streamData.stream_path, targetId, feed, isAltRtsp, false);
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        // Request intentionally aborted due to camera switch; do nothing
        return;
      }
      if (this.activeFeedId !== targetId) return;

      console.warn('Stream setup error:', err);
      if (this.connectTimeoutTimer) {
        clearTimeout(this.connectTimeoutTimer);
        this.connectTimeoutTimer = null;
      }
      const hasLiveSnapshot = feed.screenshot_url && !feed.screenshot_url.includes('unsplash');
      if (this.isSnapshotLoaded && hasLiveSnapshot) {
        this.setStatus('SNAPSHOT (RTSP BLOCKED)', `RTSP ${portNum} unreachable. Displaying HTTP snapshot.`);
        if (this.techProto) this.techProto.textContent = 'HTTP Snapshot Active';
      } else {
        this.setStatus('OFFLINE', 'Remote camera offline.');
        if (this.techProto) this.techProto.textContent = 'Host Unreachable';
        window.app?.scheduleRandomSkipOnUnreachable?.(targetId, 'Remote camera offline.');
      }
    }
  }

  attachHlsStream(hlsStreamPath, targetId, feed, isAltRtsp, preserveUnmuted = false) {
    if (!this.videoEl || this.activeFeedId !== targetId) return;
    if (this.activeHlsPath === hlsStreamPath && this.hlsInstance) return;

    const wantUnmuted = preserveUnmuted && !this.videoEl.muted;
    this.teardownMediaEngines();
    this.isHlsMode = true;
    this.activeHlsPath = hlsStreamPath;
    this.hlsNetworkRetries = 0;
    this.hlsMediaRetries = 0;

    const manifestUrl = `/api/hls/${encodeURIComponent(hlsStreamPath)}/index.m3u8`;
    this.videoEl.muted = !wantUnmuted;
    this.videoEl.autoplay = true;
    this.videoEl.playsInline = true;
    this.videoEl.controls = true;

    const safePlay = () => {
      if (!this.videoEl) return;
      this.videoEl.play().catch(() => {
        if (this.videoEl && !this.videoEl.muted) {
          this.videoEl.muted = true;
          this.videoEl.play().catch(() => {});
        }
      });
    };

    if (window.Hls && window.Hls.isSupported()) {
      const overTor = Boolean(this.currentFeedTorRouted);
      const hls = new window.Hls({
        // Match MediaMTX fMP4 variant: standard fMP4 HLS avoids LL-HLS blocking reload 404s behind Nginx/Cloudflare/Tor
        lowLatencyMode: false,
        liveSyncDurationCount: overTor ? 3 : 2,
        liveMaxLatencyDurationCount: overTor ? 10 : 7,
        maxLiveSyncPlaybackRate: overTor ? 1.25 : 1.4,
        enableWorker: true,
        manifestLoadingMaxRetry: 2,
        manifestLoadingRetryDelay: 900,
        manifestLoadingTimeOut: overTor ? 18000 : 12000,
        levelLoadingMaxRetry: 3,
        levelLoadingTimeOut: overTor ? 18000 : 12000,
        fragLoadingMaxRetry: 3,
        fragLoadingTimeOut: overTor ? 22000 : 14000,
      });
      this.hlsInstance = hls;

      const showFatalOffline = (msg) => {
        this.activeHlsPath = null;
        this.teardownMediaEngines();
        const hasLiveSnapshot = feed.screenshot_url && !feed.screenshot_url.includes('unsplash');
        if (this.isSnapshotLoaded && hasLiveSnapshot) {
          this.setStatus('SNAPSHOT (RTSP BLOCKED)', 'HLS stream interrupted. Displaying HTTP snapshot.');
        } else {
          const errMsg = msg || 'HLS stream interrupted or camera disconnected.';
          this.setStatus('RTSP OFFLINE', errMsg);
          window.app?.scheduleRandomSkipOnUnreachable?.(targetId, errMsg);
        }
      };

      const recoverSession404 = async () => {
        const basePath = this.activeStreamData?.stream_path;
        if (!basePath || this.activeFeedId !== targetId || this.hlsInstance !== hls) return;
        try {
          const stRes = await fetch(
            `/api/streams/status/${encodeURIComponent(basePath)}?hls_audio=true${this.forceHlsFixRequested ? '&force_hls_fix=true' : ''}`,
            { signal: this.abortController ? this.abortController.signal : undefined }
          );
          if (this.activeFeedId !== targetId || this.hlsInstance !== hls) return;
          if (stRes.ok) {
            const st = await stRes.json();
            if (this.activeFeedId !== targetId || this.hlsInstance !== hls) return;
            if (st.effective_hls_path) {
              const freshUrl = `/api/hls/${encodeURIComponent(st.effective_hls_path)}/index.m3u8`;
              this.activeHlsPath = st.effective_hls_path;
              hls.loadSource(freshUrl);
              return;
            }
            if (st.ready) {
              // Camera reconnected and is buffering its first keyframe again -> hand cleanly back to telemetry waiter
              this.activeHlsPath = null;
              this.teardownMediaEngines();
              this.setStatus('CONNECTING', 'Camera reconnected — re-buffering HLS stream...');
              this.monitorRtspTelemetry(basePath, targetId, feed, isAltRtsp, this.forceHlsFixRequested);
              return;
            }
          }
        } catch (err) {
          if (err && err.name === 'AbortError') return;
        }
        showFatalOffline('Stream disconnected or unavailable in MediaMTX.');
      };

      hls.on(window.Hls.Events.MEDIA_ATTACHED, () => {
        if (this.activeFeedId === targetId && this.hlsInstance === hls) {
          hls.loadSource(manifestUrl);
        }
      });

      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        if (this.activeFeedId === targetId && this.hlsInstance === hls && this.videoEl) {
          safePlay();
        }
      });

      hls.on(window.Hls.Events.ERROR, (_evt, data) => {
        if (this.activeFeedId !== targetId || this.hlsInstance !== hls || !data?.fatal) return;
        const httpCode = data.response?.code || 0;
        console.warn('HLS fatal error:', data.type, data.details, httpCode);

        // Browser lacks native HEVC/H.265 decoder: transparently transcode to H.264 + AAC on backend
        if (
          data.details === 'manifestIncompatibleCodecsError' &&
          !this.forceHlsFixRequested &&
          this.activeStreamData?.stream_path
        ) {
          this.forceHlsFixRequested = true;
          this.activeHlsPath = null;
          this.teardownMediaEngines();
          this.setStatus('CONNECTING', 'Browser lacks native codec support — transcoding to H.264/AAC...');
          this.monitorRtspTelemetry(this.activeStreamData.stream_path, targetId, feed, isAltRtsp, true);
          return;
        }

        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && this.hlsMediaRetries < 2) {
          this.hlsMediaRetries++;
          hls.recoverMediaError();
          return;
        }

        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
          if (
            httpCode === 500 &&
            !hlsStreamPath.endsWith('_aac') &&
            !this.forceHlsFixRequested &&
            this.activeStreamData?.stream_path
          ) {
            this.forceHlsFixRequested = true;
            this.activeHlsPath = null;
            this.teardownMediaEngines();
            this.setStatus('CONNECTING', 'HLS muxer rejected raw stream — switching to H.264/AAC transcode...');
            this.monitorRtspTelemetry(this.activeStreamData.stream_path, targetId, feed, isAltRtsp, true);
            return;
          }

          // 404 usually means MediaMTX rotated/closed the HLS session after a brief camera blip; verify status before reloading
          if (httpCode === 404) {
            if (this.hlsNetworkRetries < 2) {
              this.hlsNetworkRetries++;
              setTimeout(() => {
                if (this.activeFeedId === targetId && this.hlsInstance === hls) {
                  recoverSession404();
                }
              }, 1200);
              return;
            }
            showFatalOffline('Stream disconnected or unavailable in MediaMTX.');
            return;
          }

          if (this.hlsNetworkRetries < 3) {
            this.hlsNetworkRetries++;
            const reloadManifest = data.details === 'manifestLoadError';
            setTimeout(() => {
              if (this.activeFeedId === targetId && this.hlsInstance === hls) {
                if (reloadManifest) {
                  hls.loadSource(manifestUrl);
                } else {
                  hls.startLoad();
                }
              }
            }, 1000);
            return;
          }
        }

        // Last-resort recovery via backend H.264/AAC transcode sidecar if on raw path
        if (
          !this.forceHlsFixRequested &&
          this.activeStreamData?.stream_path &&
          !hlsStreamPath.endsWith('_aac') &&
          httpCode !== 404
        ) {
          this.forceHlsFixRequested = true;
          this.activeHlsPath = null;
          this.teardownMediaEngines();
          this.setStatus('CONNECTING', 'Recovering stream via H.264/AAC compatibility pipeline...');
          this.monitorRtspTelemetry(this.activeStreamData.stream_path, targetId, feed, isAltRtsp, true);
          return;
        }

        showFatalOffline('HLS stream interrupted or camera disconnected.');
      });

      this.videoEl.onplay = () => {
        if (this.hlsInstance === hls && hls.liveSyncPosition && isFinite(hls.liveSyncPosition)) {
          if (Math.abs(hls.liveSyncPosition - this.videoEl.currentTime) > 4) {
            this.videoEl.currentTime = hls.liveSyncPosition;
          }
        }
      };

      hls.attachMedia(this.videoEl);
    } else if (this.videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      this.videoEl.src = manifestUrl;
      safePlay();
    } else {
      this.setStatus('RTSP OFFLINE', 'Browser does not support HLS playback.');
    }
  }

  async attachWebRtcStream(streamPath, targetId, feed, isAltRtsp) {
    if (!this.videoEl || this.activeFeedId !== targetId) return;
    if (typeof RTCPeerConnection === 'undefined') {
      this.isHlsMode = true;
      this.monitorRtspTelemetry(streamPath, targetId, feed, isAltRtsp, false);
      return;
    }

    this.teardownMediaEngines();
    this.isHlsMode = false;
    this.videoEl.muted = true;
    this.videoEl.autoplay = true;
    this.videoEl.playsInline = true;
    this.videoEl.controls = true;

    let fellBack = false;
    const fallbackToHls = (reason) => {
      if (fellBack || this.activeFeedId !== targetId || this.isHlsMode) return;
      fellBack = true;
      console.warn(`WebRTC -> HLS automatic fallback (${reason})`);
      this.teardownMediaEngines();
      this.isHlsMode = true;
      this.activeHlsPath = null;
      this.setStatus('CONNECTING', 'WebRTC media blocked — switching seamlessly to HLS...');
      this.monitorRtspTelemetry(streamPath, targetId, feed, isAltRtsp, false);
    };

    try {
      const pc = new RTCPeerConnection({ iceServers: [] });
      this.peerConnection = pc;
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      const mediaStream = new MediaStream();
      pc.ontrack = (evt) => {
        if (this.activeFeedId !== targetId || this.peerConnection !== pc) return;
        if (evt.streams && evt.streams[0]) {
          this.videoEl.srcObject = evt.streams[0];
        } else {
          mediaStream.addTrack(evt.track);
          this.videoEl.srcObject = mediaStream;
        }
        this.videoEl.play().catch(() => {});
      };

      pc.oniceconnectionstatechange = () => {
        if (this.peerConnection !== pc) return;
        if (pc.iceConnectionState === 'failed') {
          fallbackToHls('ICE connection failed');
        }
      };

      pc.onconnectionstatechange = () => {
        if (this.peerConnection !== pc) return;
        if (pc.connectionState === 'failed') {
          fallbackToHls('PeerConnection failed');
        }
      };

      const pendingCandidates = [];
      let sessionEtag = '*';
      const sendIcePatch = (cand) => {
        if (!this.whepSessionUrl || !cand?.candidate) return;
        const sdpFrag = `a=ice-ufrag:${cand.usernameFragment || ''}\r\na=candidate:${cand.candidate}\r\n`;
        fetch(this.whepSessionUrl, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/trickle-ice-sdpfrag',
            'If-Match': sessionEtag
          },
          body: sdpFrag
        }).catch(() => {});
      };

      pc.onicecandidate = (evt) => {
        if (this.peerConnection !== pc || !evt.candidate) return;
        if (this.whepSessionUrl) {
          sendIcePatch(evt.candidate);
        } else {
          pendingCandidates.push(evt.candidate);
        }
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Briefly wait up to 350ms for host ICE candidates so single-request WHEP succeeds immediately
      await new Promise((resolve) => {
        if (pc.iceGatheringState === 'complete') return resolve();
        const done = () => {
          pc.removeEventListener('icegatheringstatechange', onState);
          resolve();
        };
        const onState = () => {
          if (pc.iceGatheringState === 'complete') done();
        };
        pc.addEventListener('icegatheringstatechange', onState);
        setTimeout(done, 350);
      });

      if (this.activeFeedId !== targetId || this.peerConnection !== pc) return;

      const whepUrl = this.activeStreamData?.proxy_whep_url || `/api/whep/${encodeURIComponent(streamPath)}`;
      const res = await fetch(whepUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: pc.localDescription.sdp,
        signal: this.abortController ? this.abortController.signal : undefined
      });

      if (this.activeFeedId !== targetId || this.peerConnection !== pc) return;
      if (!res.ok) {
        fallbackToHls(`WHEP HTTP ${res.status}`);
        return;
      }

      const loc = res.headers.get('location');
      if (loc) this.whepSessionUrl = loc;
      sessionEtag = res.headers.get('etag') || '*';
      const answerSdp = await res.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

      while (pendingCandidates.length) {
        sendIcePatch(pendingCandidates.shift());
      }

      // If WebRTC delivers 0 rendered video frames within 3.5s (e.g. port 8189 blocked), auto-fallback to HLS
      this.whepFallbackTimer = setTimeout(() => {
        if (this.activeFeedId !== targetId || this.isHlsMode || this.peerConnection !== pc) return;
        if (!this.videoEl || this.videoEl.videoWidth === 0) {
          fallbackToHls('0 video frames after 3.5s');
        }
      }, 3500);
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      fallbackToHls(err?.message || 'WebRTC setup error');
    }
  }

  monitorRtspTelemetry(streamName, targetId, feed, isAltRtsp, initialForceFix = false) {
    if (this.telemetryInterval) {
      clearTimeout(this.telemetryInterval);
      clearInterval(this.telemetryInterval);
      this.telemetryInterval = null;
    }

    const token = (this.telemetryToken || 0) + 1;
    this.telemetryToken = token;

    const overTor = Boolean(this.currentFeedTorRouted);
    const startedAt = Date.now();
    const connectDeadline = startedAt + (overTor ? 30000 : 14000);
    const hlsBudgetMs = overTor ? 30000 : 20000;
    const rawFallbackTranscodeMs = overTor ? 15000 : 11000;
    const audioPreWaitMs = 5500;
    const audioPostWaitMs = 22000;

    let firstReadyAt = 0;
    let playbackStartedAt = 0;
    let playbackStarted = false;
    let aacSwitched = false;
    let forceHlsFix = Boolean(initialForceFix);

    const stopPoller = () => {
      if (this.telemetryInterval) {
        clearTimeout(this.telemetryInterval);
        this.telemetryInterval = null;
      }
    };

    const scheduleNext = (delayMs = 900) => {
      if (this.activeFeedId !== targetId || this.telemetryToken !== token) return;
      this.telemetryInterval = setTimeout(runPoll, delayMs);
    };

    const runPoll = async () => {
      this.telemetryInterval = null;
      if (this.activeFeedId !== targetId || this.telemetryToken !== token) {
        return;
      }

      const now = Date.now();
      try {
        const statusUrl = this.isHlsMode
          ? `/api/streams/status/${encodeURIComponent(streamName)}?hls_audio=true${forceHlsFix ? '&force_hls_fix=true' : ''}`
          : `/api/streams/status/${encodeURIComponent(streamName)}`;
        const res = await fetch(statusUrl, {
          signal: this.abortController ? this.abortController.signal : undefined
        });
        if (this.activeFeedId !== targetId || this.telemetryToken !== token) return;

        if (res.ok) {
          const status = await res.json();
          if (this.activeFeedId !== targetId || this.telemetryToken !== token) return;

          if (status.ready && (status.bytes_received > 0 || (status.tracks && status.tracks.length > 0))) {
            if (!firstReadyAt) firstReadyAt = now;
            const readyElapsed = now - firstReadyAt;

            const codecUpper = (status.codec || '').toUpperCase();
            let switchedToHlsJustNow = false;
            const nonWebRtcVideo = (
              codecUpper.includes('H265') ||
              codecUpper.includes('HEVC') ||
              codecUpper.includes('JPEG') ||
              codecUpper.includes('MPEG-4 VIDEO') ||
              codecUpper.includes('MPEG-1/2 VIDEO')
            );
            if (nonWebRtcVideo && !this.isHlsMode) {
              this.isHlsMode = true;
              switchedToHlsJustNow = true;
              playbackStarted = false;
              firstReadyAt = now;
            }

            if (this.isHlsMode) {
              if (status.hls_crashed) {
                forceHlsFix = true;
              }
              const effectivePath = status.effective_hls_path || (
                status.aac_stream_path || (status.hls_ready && !forceHlsFix && !status.hls_needs_transcode ? streamName : null)
              );
              const waitingAudioPreStart = (
                !status.aac_stream_path &&
                Boolean(status.aac_pending) &&
                !forceHlsFix &&
                !status.hls_needs_transcode &&
                readyElapsed < audioPreWaitMs
              );
              const targetHlsPath = waitingAudioPreStart ? null : effectivePath;

              if (!playbackStarted && !targetHlsPath) {
                if (readyElapsed < hlsBudgetMs) {
                  // Only escalate raw H.264 to libx264 transcode if raw HLS stayed unready for >11-15s (never on slow-GOP poll 3!)
                  if (!status.hls_ready && !status.aac_pending && !forceHlsFix && status.bytes_received > 0 && readyElapsed >= rawFallbackTranscodeMs) {
                    forceHlsFix = true;
                  }
                  this.setStatus(
                    'CONNECTING',
                    (forceHlsFix || status.hls_needs_transcode)
                      ? 'Transcoding video & audio to browser-compatible H.264/AAC...'
                      : (status.aac_pending
                        ? 'Preparing HLS stream with compatible AAC audio...'
                        : 'Buffering first HLS video keyframe...')
                  );
                  scheduleNext(800);
                  return;
                }

                // Never attach hls.js to an unready/crashed manifest path
                stopPoller();
                this.activeHlsPath = null;
                this.teardownMediaEngines();
                const hasLiveSnapshot = feed.screenshot_url && !feed.screenshot_url.includes('unsplash');
                if (this.isSnapshotLoaded && hasLiveSnapshot) {
                  this.setStatus('SNAPSHOT (RTSP BLOCKED)', 'HLS muxer unavailable. Displaying HTTP snapshot.');
                  if (this.techProto) this.techProto.textContent = 'HTTP Snapshot Active (HLS Muxer Stalled)';
                } else {
                  const stallMsg = 'HLS muxer unavailable or camera stopped transmitting keyframes.';
                  this.setStatus('RTSP OFFLINE', stallMsg);
                  if (this.techProto) this.techProto.textContent = 'HLS Muxer Unavailable';
                  window.app?.scheduleRandomSkipOnUnreachable?.(targetId, stallMsg);
                }
                return;
              }

              if (this.overlayEl) this.overlayEl.style.display = 'none';
              if (this.snapshotEl) this.snapshotEl.style.display = 'none';
              if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
              if (this.videoEl) this.videoEl.style.display = 'block';
              this.setStatus('LIVE HLS');

              if (!aacSwitched && status.aac_stream_path) {
                this.attachHlsStream(status.aac_stream_path, targetId, feed, isAltRtsp, playbackStarted);
                if (!playbackStarted) playbackStartedAt = now;
                playbackStarted = true;
                aacSwitched = true;
              } else if (!playbackStarted && targetHlsPath) {
                this.attachHlsStream(targetHlsPath, targetId, feed, isAltRtsp, false);
                playbackStartedAt = now;
                playbackStarted = true;
                if (targetHlsPath.endsWith('_aac')) aacSwitched = true;
              }
            } else {
              if (this.overlayEl) this.overlayEl.style.display = 'none';
              if (this.snapshotEl) this.snapshotEl.style.display = 'none';
              if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
              if (this.videoEl) this.videoEl.style.display = 'block';
              this.setStatus('LIVE WEBRTC');

              if (!playbackStarted) {
                playbackStarted = true;
                playbackStartedAt = now;
                this.attachWebRtcStream(streamName, targetId, feed, isAltRtsp);
              }
            }

            const tracksArr = Array.isArray(status.tracks) ? status.tracks : [];
            const audioTracks = tracksArr.filter(t => {
              const u = String(t).toUpperCase();
              return !['H264', 'H265', 'HEVC', 'VP8', 'VP9', 'AV1', 'M-JPEG', 'MJPEG', 'MPEG-4 VIDEO', 'MPEG-1/2 VIDEO', 'GENERIC'].some(v => u.includes(v));
            });
            let audioBadge = '';
            if (audioTracks.length === 0) {
              audioBadge = ' [No Audio]';
            } else if (this.isHlsMode && aacSwitched) {
              audioBadge = ` [Audio: ${audioTracks[0]} -> AAC]`;
            } else if (this.isHlsMode && status.aac_pending) {
              audioBadge = ` [Audio: Converting ${audioTracks[0]} -> AAC...]`;
            } else {
              audioBadge = ` [Audio: ${audioTracks[0]}]`;
            }

            const resInfo = status.resolution ? ` [${status.resolution}]` : '';
            const codecInfo = status.codec ? ` ${status.codec}` : (tracksArr.length ? ` ${tracksArr.join('/')}` : '');
            const kbReceived = Math.round((status.bytes_received || 0) / 1024);
            const byteInfo = kbReceived > 0 ? ` (${kbReceived} KB rx)` : '';

            if (this.techProto) {
              const torSuffix = this.currentFeedTorRouted
                ? ` [TOR ${this.currentFeedTorCircuit || ''}]`.trimEnd()
                : '';
              const protoLabel = this.isHlsMode ? 'HLS' : 'WebRTC';
              this.techProto.textContent = (isAltRtsp
                ? `Alt RTSP 8554 ${protoLabel} Verified: ${codecInfo}${resInfo}${audioBadge}${byteInfo}`
                : `MediaMTX ${protoLabel} Verified: ${codecInfo}${resInfo}${audioBadge}${byteInfo}`) + torSuffix;
            }

            // Keep polling while HLS audio sidecar warms up (or right after a non-WebRTC upgrade to HLS)
            if (
              switchedToHlsJustNow ||
              (this.isHlsMode && !aacSwitched && status.aac_pending && (now - playbackStartedAt) < audioPostWaitMs)
            ) {
              scheduleNext(1000);
              return;
            }
            stopPoller();
            return;
          }
        }
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        console.warn('Telemetry check notice:', e);
      }

      if (this.activeFeedId !== targetId || this.telemetryToken !== token) return;

      if (playbackStarted) {
        stopPoller();
        return;
      }

      const activeDeadline = firstReadyAt ? (firstReadyAt + hlsBudgetMs) : connectDeadline;
      if (Date.now() >= activeDeadline) {
        stopPoller();
        if (this.activeFeedId !== targetId || this.telemetryToken !== token) return;

        console.warn('RTSP stream not ready after verification window. Probing alternative vendor paths...');
        if (!this.hasAutoResolved) {
          this.hasAutoResolved = true;
          this.setStatus('CONNECTING', 'RTSP stream unverified. Retrying vendor credentials...');
          try {
            const retryRes = await this.retryWithDefaultCredentials(feed);
            if (this.activeFeedId !== targetId) return;
            if (retryRes && retryRes.resolved) {
              this.playFeed(feed, true);
              return;
            }
          } catch (e) {
            console.warn('Telemetry cred retry error:', e);
          }

          this.setStatus('CONNECTING', 'RTSP stream unverified. Probing candidate vendor subpaths...');
          try {
            const probeRes = await fetch('/api/streams/resolve-rtsp', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                feed_id: feed.id,
                ip: feed.ip,
                port: feed.port,
                product: feed.product,
                current_url: feed.rtsp_url
              }),
              signal: this.abortController ? this.abortController.signal : undefined
            });
            if (this.activeFeedId !== targetId) return;
            if (probeRes.ok) {
              const resData = await probeRes.json();
              if (this.activeFeedId !== targetId) return;
              if (resData.resolved) {
                feed.rtsp_url = resData.stream_url;
                if (resData.protocol === 'http' || resData.protocol === 'https') {
                  this.playHttpStream(feed, resData.stream_url);
                  return;
                }
                this.playFeed(feed, true);
                return;
              }
            }
          } catch (probeErr) {
            console.error('Auto-probe error:', probeErr);
          }
        }

        if (this.activeFeedId !== targetId) return;
        const hasLiveSnapshot = feed.screenshot_url && !feed.screenshot_url.includes('unsplash');
        if (hasLiveSnapshot) {
          if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
          if (this.snapshotEl) {
            this.snapshotEl.src = this.getSafeSnapshotSrc(feed);
            this.snapshotEl.style.display = 'block';
          }
          this.setStatus('SNAPSHOT (RTSP BLOCKED)', `Camera accessible via HTTP, but RTSP port ${feed.port} not delivering frames.`);
          if (this.techProto) this.techProto.textContent = `HTTP Snapshot Active (RTSP ${feed.port} Filtered/Stalled)`;
        } else {
          if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
          const noFrameMsg = `Remote camera on port ${feed.port} not transmitting frames.`;
          this.setStatus('RTSP OFFLINE', noFrameMsg);
          if (this.techProto) this.techProto.textContent = `Port ${feed.port} Host Unreachable / Filtered`;
          window.app?.scheduleRandomSkipOnUnreachable?.(targetId, noFrameMsg);
        }
        return;
      }

      scheduleNext(900);
    };

    // Start first telemetry poll promptly
    this.telemetryInterval = setTimeout(runPoll, 450);
  }
}

window.streamPlayer = new StreamPlayer();

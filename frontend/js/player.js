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
  return match ? decodeURIComponent(match[1]) : '';
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
    this.overlayMsg = document.getElementById('overlay-msg');
    this.badgeEl = document.getElementById('player-status-badge');
    this.titleEl = document.getElementById('player-feed-title');
    this.btnOpenWeb = document.getElementById('btn-open-web-cam');
    
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
  }

  setStatus(state, message) {
    if (!this.badgeEl) return;
    
    const stateClass = state.toLowerCase().replace(/[^a-z0-9]/g, '-');
    this.badgeEl.className = 'live-pill ' + stateClass;
    this.badgeEl.innerHTML = `<span class="dot"></span> ${window.escapeHtml(state)}`;
    
    if (state.startsWith('ISOLATED WEB PORTAL') || state.startsWith('ACTIVE WEB CONSOLE')) {
      if (this.overlayEl) this.overlayEl.style.display = 'none';
      if (this.snapshotEl) this.snapshotEl.style.display = 'none';
      if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
      if (this.videoEl) this.videoEl.style.display = 'none';
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'flex';
    } else {
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'none';
      if (state === 'CONNECTING') {
        if (this.overlayEl) {
          this.overlayEl.style.display = 'flex';
          this.overlayMsg.textContent = message || 'Negotiating stream route...';
        }
      } else if (state.startsWith('LIVE WEBRTC') || state.startsWith('LIVE HLS')) {
        if (this.overlayEl) this.overlayEl.style.display = 'none';
        if (this.snapshotEl) this.snapshotEl.style.display = 'none';
        if (this.videoEl) this.videoEl.style.display = 'none';
        if (this.webrtcFrame) this.webrtcFrame.style.display = 'block';
      } else {
        // Direct HTTP Live stream, Snapshot, or Offline
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

  stop() {
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
      clearInterval(this.telemetryInterval);
      this.telemetryInterval = null;
    }
    this.isSnapshotLoaded = false;

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

    // 5. Detach MediaMTX WebRTC frame
    if (this.webrtcFrame) {
      this.webrtcFrame.src = 'about:blank';
      this.webrtcFrame.style.display = 'none';
    }

    // 6. Reset HTML5 video element fallback
    if (this.videoEl && this.videoEl.srcObject) {
      this.videoEl.srcObject.getTracks().forEach(t => t.stop());
      this.videoEl.srcObject = null;
    }
    if (this.videoEl) {
      this.videoEl.removeAttribute('src');
      this.videoEl.load();
      this.videoEl.style.display = 'none';
    }

    // 7. Release previous MediaMTX stream route
    if (this.currentFeed && this.currentFeed.stream_path) {
      const prevPath = this.currentFeed.stream_path;
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
    this.stop();
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
      if (playRes.ok) {
        streamInfo = await playRes.json();
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
      this.setStatus('OFFLINE', streamInfo.message || `${protoLabel} camera port ${portNum} closed or stream unavailable.`);
      if (this.techProto) this.techProto.textContent = `Port ${portNum} Unreachable (Offline)`;
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

    this.snapshotEl.onload = () => {
      if (this.activeFeedId !== targetId) return;
      this.isSnapshotLoaded = true;
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'none';
      this.snapshotEl.style.display = 'block';
      this.setStatus(`LIVE ${protoLabel}`);
      if (this.techProto) {
        this.techProto.textContent = isContinuousMjpeg
          ? `${protoLabel}/MJPEG Isolated Stream (Port ${portNum})`
          : `${protoLabel}/JPEG Isolated Snapshot (Port ${portNum})`;
      }
    };

    this.snapshotEl.onerror = () => {
      if (this.activeFeedId !== targetId) return;
      this.isSnapshotLoaded = false;
      // If web port, inspect in Isolated Web Portal mode (zero code execution)
      if ([443, 80, 8080, 8000, 8050].includes(portNum)) {
        this.showWebConsole(feed, `Isolated camera portal on port ${portNum}. Extracting source...`);
      } else {
        this.setStatus('OFFLINE', `${protoLabel} camera port ${portNum} closed or stream unavailable.`);
        if (this.techProto) this.techProto.textContent = `Port ${portNum} Unreachable (Offline)`;
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
    this.stop();
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
    const isWebPort = [443, 80, 8080, 8050, 8000].includes(portNum);
    const isAltRtsp = portNum === 8554;
    const rtspUrl = feed.rtsp_url || '';
    const isHttpProtocol = rtspUrl.startsWith('http://') || rtspUrl.startsWith('https://');

    // =========================================================================
    // CASE A: DIRECT OR PROXIED HTTP/HTTPS STREAM (Port 443/80/8080/8050/8000 or HTTP/HTTPS URL)
    // =========================================================================
    if (isWebPort || isHttpProtocol) {
      this.playHttpStream(feed, rtspUrl);
      return;
    }

    // =========================================================================
    // CASE B: NATIVE RTSP STREAM (Port 554 / Port 8554 via MediaMTX Gateway)
    // =========================================================================
    if (this.btnOpenWeb) {
      this.btnOpenWeb.style.display = 'none';
    }

    if (this.techPort) this.techPort.textContent = isAltRtsp ? '8554 (Alt RTSP)' : `${portNum} (RTSP Stream)`;
    if (this.techProto) this.techProto.textContent = isAltRtsp ? 'MediaMTX (Negotiating Alt RTSP 8554...)' : 'MediaMTX (Negotiating RTSP...)';
    this.setStatus('CONNECTING', isAltRtsp ? 'Bridging Alt RTSP 8554 stream with MediaMTX...' : 'Bridging RTSP stream with MediaMTX...');

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
          this.setStatus('RTSP STREAM OFFLINE', `Remote RTSP port ${portNum} not responding.`);
          if (this.techProto) this.techProto.textContent = `WAN Port ${portNum} Unreachable`;
        }
      }
    }, 10000);

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

      if (!res.ok) {
        throw new Error(`MediaMTX bridge returned status ${res.status}`);
      }

      const streamData = await res.json();
      if (this.activeFeedId !== targetId) return;

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

      // If backend auto-resolved to an HTTP stream (port 8000 or HTTP fallback)
      if (streamData.status === 'http_stream' || !streamData.webrtc_url) {
        clearTimeout(this.connectTimeoutTimer);
        this.playHttpStream(feed, streamData.rtsp_source);
        return;
      }

      // Embed MediaMTX WebRTC player frame and initiate live telemetry verification
      if (this.webrtcFrame) {
        if (this.connectTimeoutTimer) {
          clearTimeout(this.connectTimeoutTimer);
          this.connectTimeoutTimer = null;
        }
        this.webrtcFrame.src = `${streamData.webrtc_url}/?autoplay=true&muted=true`;
        this.monitorRtspTelemetry(streamData.stream_path, targetId, feed, isAltRtsp);
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
      }
    }
  }

  monitorRtspTelemetry(streamName, targetId, feed, isAltRtsp) {
    if (this.telemetryInterval) {
      clearInterval(this.telemetryInterval);
      this.telemetryInterval = null;
    }

    let attempts = 0;
    const maxAttempts = 7; // Verify every 900ms for up to ~6.5 seconds

    this.telemetryInterval = setInterval(async () => {
      // If user switched camera in the meantime, terminate poller
      if (this.activeFeedId !== targetId) {
        clearInterval(this.telemetryInterval);
        this.telemetryInterval = null;
        return;
      }

      attempts++;
      try {
        const res = await fetch(`/api/streams/status/${streamName}`);
        if (this.activeFeedId !== targetId) return;

        if (res.ok) {
          const status = await res.json();
          if (this.activeFeedId !== targetId) return;

          // If MediaMTX has connected, decoded tracks, and is actively serving video:
          if (status.ready && (status.bytes_received > 0 || (status.tracks && status.tracks.length > 0))) {
            clearInterval(this.telemetryInterval);
            this.telemetryInterval = null;

            if (this.overlayEl) this.overlayEl.style.display = 'none';
            if (this.snapshotEl) this.snapshotEl.style.display = 'none';
            if (this.videoEl) this.videoEl.style.display = 'none';
            if (this.webrtcFrame) this.webrtcFrame.style.display = 'block';
            this.setStatus('LIVE WEBRTC');

            const resInfo = status.resolution ? ` [${status.resolution}]` : '';
            const codecInfo = status.codec ? ` ${status.codec}` : (status.tracks && status.tracks.length ? ` ${status.tracks.join('/')}` : '');
            const kbReceived = Math.round((status.bytes_received || 0) / 1024);
            const byteInfo = kbReceived > 0 ? ` (${kbReceived} KB rx)` : '';

            if (this.techProto) {
              this.techProto.textContent = isAltRtsp
                ? `Alt RTSP 8554 Verified: ${codecInfo}${resInfo}${byteInfo}`
                : `MediaMTX WebRTC Verified: ${codecInfo}${resInfo}${byteInfo}`;
            }
            return;
          }
        }
      } catch (e) {
        console.warn('Telemetry check notice:', e);
      }

      // If max attempts reached and stream never decoded frames, trigger automated multi-path resolution
      if (attempts >= maxAttempts) {
        clearInterval(this.telemetryInterval);
        this.telemetryInterval = null;
        if (this.activeFeedId !== targetId) return;

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
              })
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

        // If resolution did not yield working RTSP stream, fall back to isolated snapshot or offline
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
          this.setStatus('RTSP OFFLINE', `Remote camera on port ${feed.port} not transmitting frames.`);
          if (this.techProto) this.techProto.textContent = `Port ${feed.port} Host Unreachable / Filtered`;
        }
      }
    }, 900);
  }
}

window.streamPlayer = new StreamPlayer();

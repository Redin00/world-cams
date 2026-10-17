/**
 * WorldCams Video Player & MediaMTX Bridge Controller
 * Handles WebRTC (WHEP) via MediaMTX, Direct & Proxied HTTP MJPEG video streams,
 * and automated vendor profile resolution with smart fallback.
 * Guarantees instantaneous stream teardown on camera switch (no lingering previous streams).
 */

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

    // Web Console elements (for Port 443 / Web UI portals)
    this.webConsoleDisplay = document.getElementById('web-console-display');
    this.webConsoleBadgeText = document.getElementById('web-console-badge-text');
    this.webConsoleTitle = document.getElementById('web-console-title');
    this.webConsoleDesc = document.getElementById('web-console-desc');
    this.btnLaunchConsole = document.getElementById('btn-launch-console');
    this.webConsoleFrame = document.getElementById('web-console-frame');
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
          ? '<i data-lucide="eye-off"></i> Hide Embedded View'
          : '<i data-lucide="eye"></i> Toggle Embedded View';
        lucide.createIcons();
      });
    }
  }

  setStatus(state, message) {
    if (!this.badgeEl) return;
    
    const stateClass = state.toLowerCase().replace(/[^a-z0-9]/g, '-');
    this.badgeEl.className = 'live-pill ' + stateClass;
    this.badgeEl.innerHTML = `<span class="dot"></span> ${state}`;
    
    if (state.startsWith('ACTIVE WEB CONSOLE')) {
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
      if (state.startsWith('LIVE') || state.startsWith('ACTIVE WEB') || state.startsWith('SNAPSHOT')) {
        liveDot.classList.add('live');
      } else if (state.includes('OFFLINE') || state.includes('BLOCKED')) {
        liveDot.classList.remove('live');
      }
    }
  }

  showWebConsole(feed, message, effectiveUrl = null) {
    if (this.activeFeedId !== feed.id) return;
    const portNum = Number(feed.port) || 80;
    const isHttps = portNum === 443 || (feed.rtsp_url && feed.rtsp_url.startsWith('https://'));
    const isPlainFallback = message && message.includes('Plaintext HTTP');
    const protoLabel = isPlainFallback ? 'HTTP (SSL Fallback)' : (isHttps ? 'HTTPS' : 'HTTP');
    const scheme = (isHttps && !isPlainFallback) ? 'https' : 'http';
    const targetUrl = effectiveUrl || `${scheme}://${feed.ip}:${portNum}/`;

    if (this.overlayEl) this.overlayEl.style.display = 'none';
    if (this.snapshotEl) this.snapshotEl.style.display = 'none';
    if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
    if (this.videoEl) this.videoEl.style.display = 'none';

    if (this.webConsoleDisplay) {
      this.webConsoleDisplay.style.display = 'flex';
      if (this.webConsoleBadgeText) this.webConsoleBadgeText.textContent = `ACTIVE WEB CONSOLE (${protoLabel})`;
      if (this.webConsoleTitle) this.webConsoleTitle.textContent = `${feed.product || 'Surveillance Device'} Web Console`;
      if (this.webConsoleDesc) this.webConsoleDesc.textContent = message || `Exposed ${protoLabel} surveillance management interface on port ${portNum}. Live feed requires session login.`;
      if (this.btnLaunchConsole) {
        this.btnLaunchConsole.href = `/api/proxy/web?feed_id=${feed.id}`;
        this.btnLaunchConsole.title = `Open Camera Web Console in New Tab (Bypasses Browser SSL Restrictions)`;
      }
      if (this.webConsoleFrame) {
        this.webConsoleFrame.style.display = 'block';
        this.webConsoleFrame.src = `/api/proxy/web?feed_id=${feed.id}`;
      }
      if (this.btnToggleEmbed) {
        this.btnToggleEmbed.innerHTML = '<i data-lucide="eye-off"></i> Hide Embedded View';
      }
      lucide.createIcons();
    }

    this.setStatus(`ACTIVE WEB CONSOLE (${protoLabel})`);
    if (this.techProto) this.techProto.textContent = `Active ${protoLabel} Web Portal (Port ${portNum})`;
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

    // 3. Clear Web Console card & embedded frame
    if (this.webConsoleDisplay) {
      this.webConsoleDisplay.style.display = 'none';
    }
    if (this.webConsoleFrame) {
      this.webConsoleFrame.src = 'about:blank';
      this.webConsoleFrame.style.display = 'none';
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

    if (this.techPort) this.techPort.textContent = `${portNum} (${protoLabel} Live)`;
    if (this.techProto) this.techProto.textContent = `${protoLabel} MJPEG / Live Stream (Port ${portNum})`;
    this.setStatus('CONNECTING', `Connecting to ${protoLabel} stream on port ${portNum}...`);

    // Enable / update Open Web GUI button
    if (this.btnOpenWeb) {
      this.btnOpenWeb.href = `/api/proxy/web?feed_id=${feed.id}`;
      this.btnOpenWeb.style.display = 'inline-flex';
      this.btnOpenWeb.title = `Open Camera Web Console via Gateway [${scheme}://${feed.ip}:${portNum}/]`;
      lucide.createIcons();
    }

    // Call backend to determine if device serves a video stream or an active Web Console
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

    // Proxy stream URL: feeds live MJPEG or JPEG snapshots through backend proxy
    const proxyStreamUrl = `/api/proxy/stream?feed_id=${feed.id}&_ts=${Date.now()}`;
    const directFallbackUrl = (streamInfo && streamInfo.rtsp_source && !streamInfo.rtsp_source.includes('unsplash'))
      ? streamInfo.rtsp_source
      : (customUrl || (feed.screenshot_url && !feed.screenshot_url.includes('unsplash') ? feed.screenshot_url : `${scheme}://${feed.ip}:${portNum}/`));

    this.snapshotEl.onload = () => {
      // Discard event if user has switched to another camera in the meantime
      if (this.activeFeedId !== targetId) return;
      this.isSnapshotLoaded = true;
      if (this.webConsoleDisplay) this.webConsoleDisplay.style.display = 'none';
      this.snapshotEl.style.display = 'block';
      this.setStatus(`LIVE ${protoLabel}`);
      if (this.techProto) this.techProto.textContent = `${protoLabel}/MJPEG Live Active (Port ${portNum})`;
    };

    let triedFallback = false;
    this.snapshotEl.onerror = () => {
      // Discard event if user has switched to another camera in the meantime
      if (this.activeFeedId !== targetId) return;
      if (!triedFallback && directFallbackUrl && !directFallbackUrl.includes('unsplash')) {
        triedFallback = true;
        // On port 443, avoid raw browser <img> request that triggers SSL_ERROR_RX_RECORD_TOO_LONG
        if (portNum === 443) {
          this.showWebConsole(feed, 'Camera web console on port 443. Live feed requires web session login.');
          return;
        }
        console.warn('Proxy load failed, trying direct URL fallback:', directFallbackUrl);
        this.snapshotEl.src = directFallbackUrl;
        return;
      }
      this.isSnapshotLoaded = false;
      // If web port, gracefully display Web Console portal
      if ([443, 80, 8080, 8000, 8050].includes(portNum)) {
        this.showWebConsole(feed, `Camera web console on port ${portNum}. Live feed requires web session login.`);
      } else {
        this.setStatus('OFFLINE', `${protoLabel} camera port ${portNum} closed or stream unavailable.`);
        if (this.techProto) this.techProto.textContent = `Port ${portNum} Unreachable (Offline)`;
      }
    };

    this.snapshotEl.src = proxyStreamUrl;

    // Snapshot refresh timer for static snapshots (every 2.5s)
    this.refreshInterval = setInterval(() => {
      if (this.currentFeed && this.activeFeedId === targetId && this.snapshotEl && this.isSnapshotLoaded) {
        // Only refresh if not an infinite multipart MJPEG stream
        if (!feed.screenshot_url || (!feed.screenshot_url.includes('video.cgi') && !feed.screenshot_url.includes('faststream') && !feed.screenshot_url.includes('video.mjpg'))) {
          const sep = proxyStreamUrl.includes('?') ? '&' : '?';
          this.snapshotEl.src = `${proxyStreamUrl}${sep}_r=${Date.now()}`;
        }
      }
    }, 2500);
  }

  async playFeed(feed) {
    this.stop();
    this.currentFeed = feed;
    this.activeFeedId = feed.id;
    this.hasAutoResolved = false;

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
    if (this.auditRtsp) this.auditRtsp.textContent = feed.rtsp_url;
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

    // Load preview snapshot as placeholder while connecting
    const fallbackThumb = feed.screenshot_url || 'https://images.unsplash.com/photo-1557597774-9d273605dfa9?w=800&auto=format&fit=crop&q=80';
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

    // Safety timeout: if RTSP connection fails, attempt automatic background vendor subpath resolution
    this.connectTimeoutTimer = setTimeout(async () => {
      if (this.activeFeedId !== targetId) return;
      if (this.badgeEl && this.badgeEl.textContent.includes('CONNECTING')) {
        console.warn('RTSP stream negotiation timeout. Attempting automated subpath resolution...');
        if (!this.hasAutoResolved) {
          this.hasAutoResolved = true;
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
                this.playFeed(feed);
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
    }, 4500);

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
                this.playFeed(feed);
                return;
              }
            }
          } catch (probeErr) {
            console.error('Auto-probe error:', probeErr);
          }
        }

        // If resolution did not yield working RTSP stream, fall back to snapshot or offline
        if (this.activeFeedId !== targetId) return;
        const hasLiveSnapshot = feed.screenshot_url && !feed.screenshot_url.includes('unsplash');
        if (hasLiveSnapshot) {
          if (this.webrtcFrame) this.webrtcFrame.style.display = 'none';
          if (this.snapshotEl) {
            this.snapshotEl.src = feed.screenshot_url;
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

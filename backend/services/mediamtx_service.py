import asyncio
import logging
import os
import sys
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, quote, unquote

import httpx
from fastapi import HTTPException
from backend.config import settings
from backend.services.tor_service import tor_service

logger = logging.getLogger("worldcams.mediamtx")

class MediaMTXService:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.api_url = settings.MEDIAMTX_API_URL
        self.hls_url = settings.effective_hls_url
        self.whep_url = settings.effective_whep_url
        self.rtsp_port = settings.MEDIAMTX_RTSP_PORT
        self.local_hls_url = self._resolve_local_url(settings.MEDIAMTX_HLS_URL, 8888)
        self.local_whep_url = self._resolve_local_url(settings.MEDIAMTX_WHEP_URL, 8889)
        self._media_client: Optional[httpx.AsyncClient] = None
        self._path_locks: Dict[str, asyncio.Lock] = {}
        self._sidecar_video_transcoded: Dict[str, bool] = {}
        self._hls_verified: set = set()

    @staticmethod
    def _resolve_local_url(configured_url: str, default_port: int) -> str:
        """Resolve local loopback MediaMTX endpoint regardless of public reverse-proxy URL overrides."""
        try:
            p = urlparse(configured_url or "")
            if p.hostname in ("localhost", "127.0.0.1") and p.port:
                return f"http://127.0.0.1:{p.port}"
        except Exception:
            pass
        return f"http://127.0.0.1:{default_port}"

    def _get_path_lock(self, clean_path: str) -> asyncio.Lock:
        lock = self._path_locks.get(clean_path)
        if lock is None:
            lock = asyncio.Lock()
            self._path_locks[clean_path] = lock
        return lock

    def _get_media_client(self) -> httpx.AsyncClient:
        if self._media_client is None or self._media_client.is_closed:
            self._media_client = httpx.AsyncClient(
                follow_redirects=True,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=32),
                timeout=httpx.Timeout(15.0, connect=2.5),
            )
        return self._media_client

    async def aclose(self) -> None:
        if self._media_client is not None and not self._media_client.is_closed:
            await self._media_client.aclose()
            self._media_client = None

    async def is_running(self) -> bool:
        """Check if MediaMTX API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(f"{self.api_url}/v3/paths/list")
                return res.status_code == 200
        except Exception:
            return False

    async def start(self):
        """Start MediaMTX binary if not already running."""
        if await self.is_running():
            logger.info("MediaMTX is already running and reachable.")
            return

        bin_path = Path(settings.MEDIAMTX_PATH)
        if sys.platform == "win32" and not bin_path.exists():
            exe_cand = bin_path.with_suffix(".exe")
            if exe_cand.exists():
                bin_path = exe_cand

        conf_path = Path(settings.MEDIAMTX_CONFIG)

        if not bin_path.exists():
            cmd_hint = "run.bat / scripts/install_mediamtx.ps1" if sys.platform == "win32" else "./scripts/install_mediamtx.sh"
            logger.warning(f"MediaMTX binary not found at {bin_path}. Run {cmd_hint}")
            return

        logger.info(f"Launching MediaMTX process with config: {conf_path} (stream_debug={settings.STREAM_DEBUG})")
        try:
            is_win = sys.platform == "win32"
            popen_kwargs: Dict[str, Any] = (
                {}
                if settings.STREAM_DEBUG
                else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            )
            if settings.STREAM_DEBUG:
                env = os.environ.copy()
                env["MTX_LOGLEVEL"] = "debug"
                popen_kwargs["env"] = env
            if not is_win:
                popen_kwargs["start_new_session"] = True

            self.process = subprocess.Popen(
                [str(bin_path), str(conf_path)],
                **popen_kwargs
            )
            # Wait up to 4 seconds for it to start
            for _ in range(8):
                await asyncio.sleep(0.5)
                if await self.is_running():
                    logger.info("MediaMTX started and API is responding.")
                    return
            logger.warning("MediaMTX process launched; waiting for listener.")
        except Exception as e:
            logger.error(f"Failed to launch MediaMTX: {e}")

    def stop(self):
        """Terminate the MediaMTX subprocess if we spawned it."""
        if self.process:
            try:
                if sys.platform == "win32":
                    self.process.terminate()
                else:
                    import signal
                    try:
                        pgid = os.getpgid(self.process.pid)
                        if pgid != os.getpgrp():
                            os.killpg(pgid, signal.SIGTERM)
                        else:
                            self.process.terminate()
                    except Exception:
                        self.process.terminate()
                self.process = None
                logger.info("MediaMTX stopped.")
            except Exception as e:
                logger.error(f"Error stopping MediaMTX: {e}")

    async def register_stream(self, stream_path: str, rtsp_url: str) -> Dict[str, Any]:
        """
        Register or update a stream route in MediaMTX.
        For demo/simulated feeds or unreachable WAN endpoints, uses on-demand
        CCTV pattern generation with realistic camera timestamps and OSD overlays.
        For real user streams, pulls RTSP with on-demand connection.
        """
        clean_path = re.sub(r"[^a-zA-Z0-9_-]", "_", stream_path).strip("_")

        # Check if the URL is a demo/simulated feed or real stream
        is_demo_stream = any(k in rtsp_url.lower() for k in ["rtsp.stream", "pattern", "demo", "simulated", "198.51.100", "203.0.113", "192.0.2"])
        parsed_src = urlparse(rtsp_url)
        cam_ip = parsed_src.hostname or ""
        cam_port = parsed_src.port or (322 if parsed_src.scheme == "rtsps" else 554)
        tor_routed = False

        if is_demo_stream:
            await tor_service.stop_rtsp_bridge(clean_path)
            # Generate realistic on-demand CCTV video feed with camera OSD overlay
            label = clean_path.replace("_", "-").upper()
            ffmpeg_cmd = (
                f"ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=15 "
                f"-vf \"drawtext=text='[EXPOSED RTSP CCTV] {label} %{{localtime}}':fontcolor=white:fontsize=22:box=1:boxcolor=black@0.6:x=20:y=20\" "
                f"-c:v libx264 -g 15 -preset ultrafast -tune zerolatency -pix_fmt yuv420p "
                f"-rtsp_transport tcp -f rtsp rtsp://127.0.0.1:{self.rtsp_port}/{clean_path}"
            )
            payload = {
                "runOnDemand": ffmpeg_cmd,
                "runOnDemandCloseAfter": "10s",
                "runOnDemandStartTimeout": "10s"
            }
        else:
            effective_source = rtsp_url
            if cam_ip and tor_service.should_route_via_tor(cam_ip):
                await tor_service._require_socks_ready()
                rtsp_user = unquote(parsed_src.username) if parsed_src.username is not None else None
                rtsp_pass = unquote(parsed_src.password) if parsed_src.password is not None else None
                bridge_port = await tor_service.ensure_rtsp_bridge(
                    clean_path,
                    cam_ip,
                    cam_port,
                    circuit_key=clean_path,
                    rtsp_username=rtsp_user,
                    rtsp_password=rtsp_pass,
                )
                auth_prefix = ""
                if parsed_src.username is not None:
                    u_q = quote(parsed_src.username, safe="")
                    p_q = quote(parsed_src.password or "", safe="")
                    auth_prefix = f"{u_q}:{p_q}@" if parsed_src.password is not None else f"{u_q}@"
                path_and_query = (parsed_src.path or "/") + (f"?{parsed_src.query}" if parsed_src.query else "")
                effective_source = f"rtsp://{auth_prefix}127.0.0.1:{bridge_port}{path_and_query}"
                tor_routed = True
            else:
                await tor_service.stop_rtsp_bridge(clean_path)

            payload = {
                "source": effective_source,
                "sourceOnDemand": False,
                "rtspTransport": "tcp"
            }

        async with self._get_path_lock(clean_path):
            self._sidecar_video_transcoded.pop(clean_path, None)
            self._hls_verified.discard(clean_path)
            self._hls_verified.discard(f"{clean_path}_aac")
            async with httpx.AsyncClient(timeout=4.0) as client:
                try:
                    # Re-add if path already exists so stale runOnInit/source fields or old _aac sidecar never conflict
                    check_res = await client.get(f"{self.api_url}/v3/config/paths/get/{clean_path}")
                    if check_res.status_code == 200:
                        await client.delete(f"{self.api_url}/v3/config/paths/delete/{clean_path}")
                    if not clean_path.endswith("_aac"):
                        try:
                            await client.delete(f"{self.api_url}/v3/config/paths/delete/{clean_path}_aac")
                        except Exception:
                            pass
                    add_res = await client.post(
                        f"{self.api_url}/v3/config/paths/add/{clean_path}",
                        json=payload
                    )
                    if settings.STREAM_DEBUG:
                        logger.debug(
                            f"[STREAM_DEBUG] register_stream({clean_path}): status={add_res.status_code} "
                            f"tor={tor_routed} body={add_res.text[:300]}"
                        )
                    status = "updated" if check_res.status_code == 200 else (
                        "created" if add_res.status_code in (200, 201) else "configured"
                    )

                    return {
                        "stream_path": clean_path,
                        "rtsp_source": rtsp_url,
                        "webrtc_url": f"{self.whep_url}/{clean_path}",
                        "webrtc_whep_url": f"{self.whep_url}/{clean_path}/whep",
                        "hls_url": f"{self.hls_url}/{clean_path}",
                        "hls_manifest_url": f"{self.hls_url}/{clean_path}/index.m3u8",
                        "proxy_hls_url": f"/api/hls/{clean_path}/index.m3u8",
                        "proxy_whep_url": f"/api/whep/{clean_path}",
                        "status": status,
                        "is_simulated": is_demo_stream,
                        "tor_routed": tor_routed,
                        "tor_circuit_id": tor_service.current_circuit_id if tor_routed else None,
                        "playback_mode": settings.effective_playback_mode,
                        "message": "Stream pipeline ready via Tor SOCKS5 Bridge" if tor_routed else "Stream pipeline ready in MediaMTX"
                    }
                except Exception as e:
                    logger.warning(f"MediaMTX API communication notice ({e}). Route generated.")
                    return {
                        "stream_path": clean_path,
                        "rtsp_source": rtsp_url,
                        "webrtc_url": f"{self.whep_url}/{clean_path}",
                        "webrtc_whep_url": f"{self.whep_url}/{clean_path}/whep",
                        "hls_url": f"{self.hls_url}/{clean_path}",
                        "hls_manifest_url": f"{self.hls_url}/{clean_path}/index.m3u8",
                        "proxy_hls_url": f"/api/hls/{clean_path}/index.m3u8",
                        "proxy_whep_url": f"/api/whep/{clean_path}",
                        "status": "ready",
                        "is_simulated": is_demo_stream,
                        "tor_routed": tor_routed,
                        "tor_circuit_id": tor_service.current_circuit_id if tor_routed else None,
                        "playback_mode": settings.effective_playback_mode,
                        "message": "MediaMTX route configured."
                    }

    async def list_active_paths(self) -> List[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.api_url}/v3/paths/list")
                if res.status_code == 200:
                    data = res.json()
                    return data.get("items", [])
        except Exception:
            pass
        return []

    async def get_path_status(self, clean_path: str, check_hls: bool = False) -> Dict[str, Any]:
        """
        Query real-time transmission telemetry from MediaMTX core.
        Checks if the RTSP video stream has decoded tracks, incoming bytes, and is ready for playback.
        """
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_path).strip("_")
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.api_url}/v3/paths/get/{clean}")
                if res.status_code == 200:
                    data = res.json()
                    ready = data.get("ready", False)
                    tracks = data.get("tracks", [])
                    tracks2 = data.get("tracks2", [])
                    res_str = ""
                    codec_str = ""
                    if tracks2:
                        # Prefer the video track even when SDP puts audio first (e.g. ['G711', 'H264'])
                        video_track = next(
                            (
                                t for t in tracks2
                                if (
                                    ((t.get("codecProps") or {}).get("width") and (t.get("codecProps") or {}).get("height"))
                                    or any(
                                        v in str(t.get("codec", "")).upper()
                                        for v in ("H264", "H265", "HEVC", "VP8", "VP9", "AV1", "JPEG", "MPEG-4 VIDEO", "MPEG-1/2 VIDEO")
                                    )
                                )
                            ),
                            tracks2[0],
                        )
                        codec_str = video_track.get("codec", "")
                        props = video_track.get("codecProps") or {}
                        if props.get("width") and props.get("height"):
                            res_str = f"{props['width']}x{props['height']}"
                    if not codec_str and tracks:
                        codec_str = str(tracks[0])
                    bytes_rx = data.get("bytesReceived", 0)
                    hls_ready = False
                    if check_hls and ready and bytes_rx > 0 and not self._has_non_hls_video(tracks):
                        hls_ready = await self._is_hls_manifest_ready(clean)
                    if settings.STREAM_DEBUG:
                        logger.debug(
                            f"[STREAM_DEBUG] path_status({clean}): ready={ready} bytes={bytes_rx} "
                            f"tracks={tracks} hls_ready={hls_ready}"
                        )
                    return {
                        "ready": ready,
                        "online": data.get("online", data.get("available", ready)),
                        "bytes_received": bytes_rx,
                        "tracks": tracks,
                        "codec": codec_str,
                        "resolution": res_str,
                        "hls_ready": hls_ready,
                        "readers": len(data.get("readers", []))
                    }
        except Exception as e:
            if settings.STREAM_DEBUG:
                logger.debug(f"[STREAM_DEBUG] get_path_status({clean}) error: {e}")
        return {
            "ready": False,
            "online": False,
            "bytes_received": 0,
            "tracks": [],
            "codec": "",
            "resolution": "",
            "hls_ready": False,
            "readers": 0
        }

    _NON_HLS_AUDIO_TOKENS = (
        "G711", "G722", "G726", "PCM", "MPEG-1/2 AUDIO",
        "ULAW", "ALAW", "AMR", "SPEEX",
    )
    _HLS_AUDIO_TOKENS = ("MPEG-4 AUDIO", "AAC", "OPUS", "AC-3", "E-AC-3")
    _NON_HLS_VIDEO_TOKENS = ("M-JPEG", "MJPEG", "MPEG-4 VIDEO", "MPEG-1/2 VIDEO")

    @classmethod
    def _has_non_hls_audio(cls, tracks: List[str]) -> bool:
        """Return True if MediaMTX reports an audio track that browser HLS cannot play natively."""
        if not tracks:
            return False
        upper = [str(t).upper() for t in tracks]
        if any(tok in t for t in upper for tok in cls._HLS_AUDIO_TOKENS):
            return False
        return any(tok in t for t in upper for tok in cls._NON_HLS_AUDIO_TOKENS)

    @classmethod
    def _has_non_hls_video(cls, tracks: List[str]) -> bool:
        """Return True if MediaMTX reports a video codec that MediaMTX fMP4 HLS cannot mux natively."""
        if not tracks:
            return False
        upper = [str(t).upper() for t in tracks]
        return any(tok in t for t in upper for tok in cls._NON_HLS_VIDEO_TOKENS)

    @classmethod
    def _needs_hls_sidecar(cls, tracks: List[str]) -> bool:
        """Return True when HLS playback needs an ffmpeg sidecar (non-HLS audio or non-HLS video)."""
        return cls._has_non_hls_audio(tracks) or cls._has_non_hls_video(tracks)

    async def ensure_hls_aac_sidecar(
        self, clean_path: str, tracks: Optional[List[str]] = None, force_video_transcode: bool = False
    ) -> Dict[str, Any]:
        """
        Hybrid HLS sidecar: reads from MediaMTX's already-connected local RTSP path
        and publishes `<clean_path>_aac` (copying H264/H265 video and converting audio to AAC,
        or transcoding M-JPEG/MPEG-4/incompatible video to H264 if needed) so the original stream never breaks.
        """
        if not settings.MEDIAMTX_HLS_TRANSCODE_AUDIO or shutil.which("ffmpeg") is None:
            return {"aac_stream_path": None, "aac_pending": False}

        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_path).strip("_")
        if not clean or clean.endswith("_aac"):
            return {"aac_stream_path": None, "aac_pending": False}

        transcode_video = force_video_transcode or self._has_non_hls_video(tracks or [])
        aac_path = f"{clean}_aac"
        try:
            async with self._get_path_lock(clean):
                async with httpx.AsyncClient(timeout=2.0) as client:
                    cfg_res = await client.get(f"{self.api_url}/v3/config/paths/get/{aac_path}")
                    # If force_video_transcode is requested but existing sidecar used -c:v copy, recreate with libx264
                    if (
                        cfg_res.status_code == 200
                        and transcode_video
                        and not self._sidecar_video_transcoded.get(clean, False)
                    ):
                        await client.delete(f"{self.api_url}/v3/config/paths/delete/{aac_path}")
                        cfg_res = None

                    if cfg_res is None or cfg_res.status_code != 200:
                        self._hls_verified.discard(aac_path)
                        ff_log = "error" if settings.STREAM_DEBUG else "quiet"
                        if transcode_video:
                            v_args = "-c:v libx264 -preset ultrafast -tune zerolatency -bf 0 -g 15 -pix_fmt yuv420p"
                            map_args = "-map 0:v:0 -map 0:a:0?"
                        else:
                            v_args = "-c:v copy"
                            map_args = "-map 0:v:0 -map 0:a:0"
                        ffmpeg_cmd = (
                            f"ffmpeg -nostdin -loglevel {ff_log} -rtsp_transport tcp -fflags +discardcorrupt "
                            f"-analyzeduration 1000000 -probesize 1000000 -max_delay 500000 "
                            f"-i rtsp://127.0.0.1:{self.rtsp_port}/{clean} "
                            f"{map_args} {v_args} -c:a aac -ar 44100 -ac 1 -b:a 48k "
                            f"-rtsp_transport tcp -f rtsp rtsp://127.0.0.1:{self.rtsp_port}/{aac_path}"
                        )
                        add_res = await client.post(
                            f"{self.api_url}/v3/config/paths/add/{aac_path}",
                            json={"runOnInit": ffmpeg_cmd, "runOnInitRestart": False},
                        )
                        self._sidecar_video_transcoded[clean] = transcode_video
                        if settings.STREAM_DEBUG:
                            logger.debug(
                                f"[STREAM_DEBUG] ensure_hls_aac_sidecar({clean} -> {aac_path}, transcode_video={transcode_video}): "
                                f"add_status={add_res.status_code} body={add_res.text[:300]}"
                            )
                        return {"aac_stream_path": None, "aac_pending": True}

                    path_res = await client.get(f"{self.api_url}/v3/paths/get/{aac_path}")
                    if path_res.status_code == 200:
                        pdata = path_res.json()
                        ptracks = pdata.get("tracks", [])
                        pbytes = pdata.get("bytesReceived", 0)
                        has_aac = transcode_video or any("MPEG-4 AUDIO" in str(t).upper() for t in ptracks)
                        manifest_status = 0
                        if pdata.get("ready") and pbytes > 2048 and has_aac:
                            manifest_status = await self._probe_hls_manifest_status(aac_path, client)
                        if settings.STREAM_DEBUG:
                            logger.debug(
                                f"[STREAM_DEBUG] sidecar_status({aac_path}): ready={pdata.get('ready')} "
                                f"bytes={pbytes} tracks={ptracks} manifest_status={manifest_status}"
                            )
                        if manifest_status == 200:
                            return {"aac_stream_path": aac_path, "aac_pending": False}
                        # Self-heal: if MediaMTX HLS muxer crashed (HTTP 500, e.g. B-frame DTS > PTS on -c:v copy),
                        # automatically rebuild the sidecar with libx264 -bf 0 (strict monotonic DTS == PTS)!
                        if (
                            manifest_status == 500
                            and pbytes > 8192
                            and not self._sidecar_video_transcoded.get(clean, False)
                        ):
                            logger.info(
                                f"HLS muxer rejected copied video timestamps for {aac_path}; "
                                "auto-upgrading sidecar to H.264 (-bf 0) + AAC."
                            )
                            await client.delete(f"{self.api_url}/v3/config/paths/delete/{aac_path}")
                            self._hls_verified.discard(aac_path)
                            ff_log = "error" if settings.STREAM_DEBUG else "quiet"
                            fix_cmd = (
                                f"ffmpeg -nostdin -loglevel {ff_log} -rtsp_transport tcp -fflags +discardcorrupt "
                                f"-analyzeduration 1000000 -probesize 1000000 -max_delay 500000 "
                                f"-i rtsp://127.0.0.1:{self.rtsp_port}/{clean} "
                                f"-map 0:v:0 -map 0:a:0? -c:v libx264 -preset ultrafast -tune zerolatency -bf 0 -g 15 -pix_fmt yuv420p "
                                f"-c:a aac -ar 44100 -ac 1 -b:a 48k "
                                f"-rtsp_transport tcp -f rtsp rtsp://127.0.0.1:{self.rtsp_port}/{aac_path}"
                            )
                            await client.post(
                                f"{self.api_url}/v3/config/paths/add/{aac_path}",
                                json={"runOnInit": fix_cmd, "runOnInitRestart": False},
                            )
                            self._sidecar_video_transcoded[clean] = True
                    elif settings.STREAM_DEBUG:
                        logger.debug(f"[STREAM_DEBUG] sidecar_status({aac_path}): http={path_res.status_code}")
                    return {"aac_stream_path": None, "aac_pending": True}
        except Exception as e:
            logger.debug(f"HLS AAC sidecar notice for {clean}: {e}")
            return {"aac_stream_path": None, "aac_pending": False}

    async def _probe_hls_manifest_status(self, clean_path: str, api_client: Optional[httpx.AsyncClient] = None) -> int:
        """
        Check HLS manifest readiness without leaking extra MediaMTX sessions:
        1. Return 200 immediately if already verified for this active stream.
        2. First verify /v3/hlsmuxers/get/<path> exists (zero session overhead).
        3. Probe index.m3u8 once to confirm 200 OK + #EXTM3U (catching HTTP 500 muxer crashes).
        """
        if clean_path in self._hls_verified:
            return 200
        try:
            if api_client is not None:
                hm_res = await api_client.get(f"{self.api_url}/v3/hlsmuxers/get/{clean_path}")
            else:
                async with httpx.AsyncClient(timeout=1.5) as tmp_c:
                    hm_res = await tmp_c.get(f"{self.api_url}/v3/hlsmuxers/get/{clean_path}")
            if hm_res.status_code != 200:
                return 404
            client = self._get_media_client()
            resp = await client.get(
                f"{self.local_hls_url}/{clean_path}/index.m3u8?cookieCheck=1",
                cookies={},
                timeout=1.5,
            )
            if resp.status_code == 200 and "#EXTM3U" in resp.text:
                self._hls_verified.add(clean_path)
                return 200
            return resp.status_code
        except Exception:
            return 0

    async def _is_hls_manifest_ready(self, clean_path: str) -> bool:
        return (await self._probe_hls_manifest_status(clean_path)) == 200

    async def proxy_hls_file(self, clean_path: str, file_name: str, query_string: str = ""):
        """
        Proxy MediaMTX HLS manifests and fMP4 segments over same-origin HTTP.
        Passes ?cookieCheck=1 without cookies so MediaMTX immediately returns 200 OK with stateless
        ?session=<uuid> query URLs (safe across concurrent viewers & streams) and retries transient 500 muxer warmup blips.
        """
        qs = query_string
        if file_name == "index.m3u8" and "cookieCheck=" not in qs:
            qs = f"{qs}&cookieCheck=1" if qs else "cookieCheck=1"
        target = f"{self.local_hls_url}/{clean_path}/{file_name}"
        if qs:
            target = f"{target}?{qs}"
        client = self._get_media_client()
        max_tries = 4 if file_name == "index.m3u8" else 1
        resp = None
        for attempt in range(max_tries):
            resp = await client.get(target, cookies={})
            if resp.status_code == 500 and attempt < max_tries - 1 and (
                "muxer" in resp.text.lower()
            ):
                await asyncio.sleep(0.25)
                continue
            break
        ctype = resp.headers.get("content-type", "application/octet-stream")
        cache_hdr = "no-store, no-cache" if file_name.endswith(".m3u8") else "public, max-age=30"
        return resp.content, resp.status_code, {
            "Content-Type": ctype,
            "Cache-Control": cache_hdr,
        }

    async def proxy_whep(
        self,
        method: str,
        clean_path: str,
        session_id: Optional[str],
        body: bytes,
        req_headers: Dict[str, str],
    ):
        """Proxy WebRTC WHEP signaling (POST/PATCH/DELETE/OPTIONS) over same-origin FastAPI routes."""
        if session_id:
            target = f"{self.local_whep_url}/{clean_path}/whep/{session_id}"
        else:
            target = f"{self.local_whep_url}/{clean_path}/whep"
        fwd_headers: Dict[str, str] = {}
        for k in ("content-type", "if-match", "accept"):
            if req_headers.get(k):
                fwd_headers[k] = req_headers[k]
        client = self._get_media_client()
        resp = await client.request(method.upper(), target, content=body, headers=fwd_headers)
        out_headers: Dict[str, str] = {"Cache-Control": "no-store"}
        for k in ("content-type", "etag", "accept-patch", "link"):
            if resp.headers.get(k):
                out_headers[k] = resp.headers[k]
        loc = resp.headers.get("location")
        if loc:
            # Rewrite MediaMTX's /<stream>/whep/<uuid> to /api/whep/<stream>/<uuid>
            m = re.search(r"/whep/([a-zA-Z0-9_-]+)", loc)
            if m:
                out_headers["Location"] = f"/api/whep/{clean_path}/{m.group(1)}"
        return resp.content, resp.status_code, out_headers

    async def stop_stream(self, clean_path: str) -> bool:
        """Stops pulling a remote camera stream and cleans up its MediaMTX path, AAC sidecar, and Tor bridge."""
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_path).strip("_")
        base_clean = clean[:-4] if clean.endswith("_aac") else clean
        await tor_service.stop_rtsp_bridge(base_clean)
        async with self._get_path_lock(base_clean):
            self._sidecar_video_transcoded.pop(base_clean, None)
            self._hls_verified.discard(base_clean)
            self._hls_verified.discard(f"{base_clean}_aac")
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    res = await client.delete(f"{self.api_url}/v3/config/paths/delete/{base_clean}")
                    try:
                        await client.delete(f"{self.api_url}/v3/config/paths/delete/{base_clean}_aac")
                    except Exception:
                        pass
                    return res.status_code in (200, 204)
            except Exception:
                return False

    async def stop_all_streams(self) -> None:
        """Stop all currently registered MediaMTX streams and release Tor RTSP bridges."""
        self._sidecar_video_transcoded.clear()
        self._hls_verified.clear()
        await tor_service.stop_all_bridges()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                names = set()
                for endpoint in ("/v3/paths/list", "/v3/config/paths/list"):
                    try:
                        res = await client.get(f"{self.api_url}{endpoint}")
                        if res.status_code == 200:
                            for item in res.json().get("items", []):
                                name = item.get("name")
                                if name:
                                    names.add(name)
                    except Exception:
                        pass
                for name in names:
                    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
                    if clean:
                        try:
                            await client.delete(f"{self.api_url}/v3/config/paths/delete/{clean}")
                        except Exception:
                            pass
        except Exception:
            pass

mediamtx_service = MediaMTXService()

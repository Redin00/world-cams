import asyncio
import logging
import os
import sys
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, quote

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
                bridge_port = await tor_service.ensure_rtsp_bridge(
                    clean_path, cam_ip, cam_port, circuit_key=clean_path
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

    async def get_path_status(self, clean_path: str) -> Dict[str, Any]:
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
                    return {
                        "ready": ready,
                        "online": data.get("online", data.get("available", ready)),
                        "bytes_received": data.get("bytesReceived", 0),
                        "tracks": tracks,
                        "codec": codec_str,
                        "resolution": res_str,
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
            "readers": 0
        }

    _NON_HLS_AUDIO_TOKENS = (
        "G711", "G722", "G726", "LPCM", "MPEG-1/2 AUDIO",
        "PCMU", "PCMA", "ULAW", "ALAW", "AMR", "SPEEX",
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

    async def ensure_hls_aac_sidecar(self, clean_path: str, tracks: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Hybrid HLS sidecar: reads from MediaMTX's already-connected local RTSP path
        and publishes `<clean_path>_aac` (copying H264/H265 video and converting audio to AAC,
        or transcoding M-JPEG/MPEG-4 video to H264 if needed) so the original stream never breaks.
        """
        if not settings.MEDIAMTX_HLS_TRANSCODE_AUDIO or shutil.which("ffmpeg") is None:
            return {"aac_stream_path": None, "aac_pending": False}

        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_path).strip("_")
        if not clean or clean.endswith("_aac"):
            return {"aac_stream_path": None, "aac_pending": False}

        transcode_video = self._has_non_hls_video(tracks or [])
        aac_path = f"{clean}_aac"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                cfg_res = await client.get(f"{self.api_url}/v3/config/paths/get/{aac_path}")
                if cfg_res.status_code != 200:
                    ff_log = "error" if settings.STREAM_DEBUG else "quiet"
                    if transcode_video:
                        v_args = "-c:v libx264 -preset ultrafast -tune zerolatency -g 15 -pix_fmt yuv420p"
                        map_args = "-map 0:v:0 -map 0:a:0?"
                    else:
                        v_args = "-c:v copy"
                        map_args = "-map 0:v:0 -map 0:a:0"
                    ffmpeg_cmd = (
                        f"ffmpeg -nostdin -loglevel {ff_log} -rtsp_transport tcp -fflags +discardcorrupt "
                        f"-analyzeduration 1000000 -probesize 1000000 -max_delay 500000 "
                        f"-i rtsp://127.0.0.1:{self.rtsp_port}/{clean} "
                        f"{map_args} {v_args} -c:a aac -ar 44100 -ac 1 -b:a 48k "
                        f"-avoid_negative_ts make_zero -rtsp_transport tcp -f rtsp rtsp://127.0.0.1:{self.rtsp_port}/{aac_path}"
                    )
                    add_res = await client.post(
                        f"{self.api_url}/v3/config/paths/add/{aac_path}",
                        json={"runOnInit": ffmpeg_cmd, "runOnInitRestart": False},
                    )
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
                    hm_res = await client.get(f"{self.api_url}/v3/hlsmuxers/get/{aac_path}")
                    hm_ready = hm_res.status_code == 200
                    has_aac = transcode_video or any("MPEG-4 AUDIO" in str(t).upper() for t in ptracks)
                    if settings.STREAM_DEBUG:
                        logger.debug(
                            f"[STREAM_DEBUG] sidecar_status({aac_path}): ready={pdata.get('ready')} "
                            f"bytes={pbytes} tracks={ptracks} hlsmuxer={hm_res.status_code}"
                        )
                    if pdata.get("ready") and pbytes > 2048 and has_aac and hm_ready:
                        return {"aac_stream_path": aac_path, "aac_pending": False}
                elif settings.STREAM_DEBUG:
                    logger.debug(f"[STREAM_DEBUG] sidecar_status({aac_path}): http={path_res.status_code}")
                return {"aac_stream_path": None, "aac_pending": True}
        except Exception as e:
            logger.debug(f"HLS AAC sidecar notice for {clean}: {e}")
            return {"aac_stream_path": None, "aac_pending": False}

    async def stop_stream(self, clean_path: str) -> bool:
        """Stops pulling a remote camera stream and cleans up its MediaMTX path, AAC sidecar, and Tor bridge."""
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_path).strip("_")
        await tor_service.stop_rtsp_bridge(clean)
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.delete(f"{self.api_url}/v3/config/paths/delete/{clean}")
                if clean and not clean.endswith("_aac"):
                    try:
                        await client.delete(f"{self.api_url}/v3/config/paths/delete/{clean}_aac")
                    except Exception:
                        pass
                return res.status_code in (200, 204)
        except Exception:
            return False

    async def stop_all_streams(self) -> None:
        """Stop all currently registered MediaMTX streams and release Tor RTSP bridges."""
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

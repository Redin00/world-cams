import asyncio
import base64
import hashlib
import logging
import re
import secrets
import time
from typing import Optional, List, Tuple, Dict, Any, Callable, Awaitable
from urllib.parse import urlparse, urljoin
import httpx
from fastapi import HTTPException
from backend.config import get_legacy_ssl_context
from backend.security import (
    inspect_stream_chunk_safety,
    extract_stream_sources_from_html,
    is_https_camera_port,
    is_web_camera_port,
)
from backend.services.tor_service import tor_service, TorSocksConnectError

logger = logging.getLogger("worldcams.resolver")

# Complete IP Camera RTSP & HTTP streaming profiles based on SmartRTSP guide
# (https://www.smartrtsp.com/guides/rtsp-url-list & https://www.smartrtsp.com/guides/ip-camera-rtsp-url-table)
VENDOR_RTSP_PROFILES: Dict[str, Dict[str, Any]] = {
    "hikvision": {
        "ports": [554, 8554, 8000, 10554],
        "paths": [
            "/Streaming/Channels/101",
            "/Streaming/Channels/102",
            "/Streaming/Channels/1",
            "/Streaming/Channels/201",
            "/h264/ch1/main/av_stream",
            "/h264/ch1/sub/av_stream",
            "/ch1/main/av_stream",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", ""), ("admin", "12345")]
    },
    "dahua": {
        "ports": [554, 8554, 37777],
        "paths": [
            "/cam/realmonitor?channel=1&subtype=0",
            "/cam/realmonitor?channel=1&subtype=1",
            "/cam/realmonitor?channel=1&subtype=0&unicast=true&proto=Onvif",
            "/cam/realmonitor?channel=2&subtype=0",
            "/h264Preview_01",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", "admin")]
    },
    "amcrest": {
        "ports": [554, 8554, 37777],
        "paths": [
            "/cam/realmonitor?channel=1&subtype=0",
            "/cam/realmonitor?channel=1&subtype=1",
            "/cam/realmonitor?channel=1&subtype=0&unicast=true&proto=Onvif",
            "/cam/realmonitor?channel=2&subtype=0",
            "/h264Preview_01",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", "admin")]
    },
    "lorex": {
        "ports": [554, 8554, 8000],
        "paths": [
            "/cam/realmonitor?channel=1&subtype=0",
            "/Streaming/Channels/101",
            "/cam/realmonitor?channel=1&subtype=1",
            "/Streaming/Channels/102",
            "/h264Preview_01",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", ""), ("admin", "12345")]
    },
    "reolink": {
        "ports": [554, 8554],
        "paths": [
            "/h264Preview_01_main",
            "/Preview_01_main",
            "/h264Preview_01_sub",
            "/Preview_01_sub",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", "")]
    },
    "tapo": {
        "ports": [554, 8554],
        "paths": [
            "/stream1",
            "/stream2",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", "admin"), ("admin", "12345"), ("admin", "123456"), ("admin", "")]
    },
    "uniview": {
        "ports": [554, 8554],
        "paths": [
            "/media/video1",
            "/media/video2",
            "/unicast/c1/s0/live",
            "/unicast/c1/s1/live",
            "/video1",
            "/onvif1"
        ],
        "creds": [("admin", "123456"), ("admin", "admin"), ("admin", "12345")]
    },
    "hanwha": {
        "ports": [554, 8554],
        "paths": [
            "/profile1/media.smp",
            "/profile2/media.smp",
            "/onvif/profile2/media.smp",
            "/LiveChannel/0/media.smp",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", "4356")]
    },
    "axis": {
        "ports": [554, 8554, 80, 8080, 8000],
        "paths": [
            "/axis-media/media.amp",
            "/axis-media/media.amp?videocodec=h264",
            "/mjpg/video.mjpg",
            "/mpeg4/media.amp",
            "/axis-media/media.3gp",
            "/onvif1"
        ],
        "creds": [("root", "")]
    },
    "bosch": {
        "ports": [554, 8554, 80],
        "paths": [
            "/video?inst=1",
            "/video?inst=1&rec=0",
            "/video?inst=2",
            "/rtsp_tunnel",
            "/snap.jpg",
            "/video.mp4",
            "/onvif1"
        ],
        "creds": [("admin", "admin")]
    },
    "vivotek": {
        "ports": [554, 8554],
        "paths": [
            "/live.sdp",
            "/live1s.sdp",
            "/live2.sdp",
            "/video.mp4",
            "/onvif1"
        ],
        "creds": [("admin", "")]
    },
    "foscam": {
        "ports": [554, 8554, 88],
        "paths": [
            "/videoMain",
            "/videoSub",
            "/11",
            "/12",
            "/video1",
            "/onvif1",
            "/live"
        ],
        "creds": [("admin", "admin")]
    },
    "xiongmai": {
        "ports": [554, 8554, 34567],
        "paths": [
            "/onvif1",
            "/onvif2",
            "/ch0_0.h264",
            "/live/ch0",
            "/ch0_0.264",
            "/h264",
            "/stream1",
            "/user=admin_password=_channel=1_stream=0.sdp"
        ],
        "creds": [("admin", ""), ("default", ""), ("admin", "admin"), ("admin", "123456")]
    },
    "eufy": {
        "ports": [554, 8554],
        "paths": [
            "/live0",
            "/live1",
            "/live",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "12345")]
    },
    "tiandy": {
        "ports": [554, 8554],
        "paths": [
            "/h264/ch01/main/av_stream",
            "/h264/ch01/sub/av_stream",
            "/h264/ch1/main/av_stream",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "12345"), ("admin", "123456"), ("admin", "")]
    },
    "sony": {
        "ports": [554, 8554],
        "paths": [
            "/media/video1",
            "/media/video2",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "12345")]
    },
    "panasonic": {
        "ports": [554, 8554, 80, 8080, 8050, 8000],
        "paths": [
            "/nphMotionJpeg?Resolution=640x480&Quality=Standard",
            "/MediaInput/h264",
            "/stream_1",
            "/SnapshotJPEG?Resolution=640x480",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "12345")]
    },
    "geovision": {
        "ports": [554, 8554],
        "paths": [
            "/CH001.sdp",
            "/CH002.sdp",
            "/live.sdp",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "12345")]
    },
    "grandstream": {
        "ports": [554, 8554],
        "paths": [
            "/0",
            "/1",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "123456")]
    },
    "mobotix": {
        "ports": [554, 8554, 80, 8080, 8000],
        "paths": [
            "/live",
            "/control/faststream.jpg?stream=full",
            "/live/faststream.jpg",
            "/stream/video.h264",
            "/onvif1"
        ],
        "creds": [("admin", "meinsm"), ("admin", "admin")]
    },
    "huawei": {
        "ports": [554, 8554],
        "paths": [
            "/LiveMedia/ch1/Media1",
            "/LiveMedia/ch1/Media2",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "admin123")]
    },
    "v380": {
        "ports": [554, 8554, 10554],
        "paths": [
            "/ch0_0.264",
            "/ch0_0.h264",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", ""), ("admin", "123456")]
    },
    "pelco": {
        "ports": [554, 8554],
        "paths": [
            "/h264.sdp",
            "/stream1",
            "/onvif1"
        ],
        "creds": [("admin", "admin"), ("admin", "")]
    },
    "kedacom": {
        "ports": [554, 8554],
        "paths": [
            "/session0?streamid=main",
            "/session0?streamid=sub",
            "/onvif1"
        ],
        "creds": [("admin", "admin")]
    },
    "dlink": {
        "ports": [554, 8554],
        "paths": [
            "/play1.sdp",
            "/live1.sdp",
            "/video.mp4",
            "/onvif1"
        ],
        "creds": [("admin", ""), ("admin", "admin"), ("admin", "12345")]
    },
    "generic": {
        "ports": [554, 8554],
        "paths": [
            "/onvif1",
            "/live",
            "/11",
            "/Streaming/Channels/101",
            "/cam/realmonitor?channel=1&subtype=0",
            "/stream1",
            "/live/ch0",
            "/ch0_0.h264",
            "/videoMain",
            "/h264/ch01/main/av_stream",
            "/h264/ch1/main/av_stream",
            "/live.sdp",
            "/Preview_01_main",
            "/profile1/media.smp",
            "/media/video1",
            "/axis-media/media.amp",
            "/0",
            "/live0",
            "/12",
            "/stream2",
            "/video?inst=1",
            "/CH001.sdp",
            "/MediaInput/h264"
        ],
        "creds": [
            ("admin", "admin"),
            ("admin", "12345"),
            ("admin", "123456"),
            ("admin", ""),
            ("root", "pass"),
            ("root", "root"),
            ("admin", "admin123"),
            ("default", ""),
            ("admin", "4321"),
            ("service", "service")
        ]
    }
}

# Exhaustive prioritized list of RTSP candidate paths across the 50 IP camera brands from SmartRTSP.
# Tested continuously when the camera model is undetermined or when the initial stream path fails.
GENERIC_FALLBACK_PATHS: List[str] = [
    "/Streaming/Channels/101",
    "/cam/realmonitor?channel=1&subtype=0",
    "/onvif1",
    "/stream1",
    "/Preview_01_main",
    "/h264Preview_01_main",
    "/profile1/media.smp",
    "/media/video1",
    "/axis-media/media.amp",
    "/video?inst=1",
    "/live.sdp",
    "/live/ch0",
    "/ch0_0.h264",
    "/ch0_0.264",
    "/videoMain",
    "/live0",
    "/h264/ch01/main/av_stream",
    "/h264/ch1/main/av_stream",
    "/CH001.sdp",
    "/0",
    "/MediaInput/h264",
    "/LiveMedia/ch1/Media1",
    "/unicast/c1/s0/live",
    "/Streaming/Channels/1",
    "/Streaming/Channels/102",
    "/cam/realmonitor?channel=1&subtype=1",
    "/stream2",
    "/Preview_01_sub",
    "/profile2/media.smp",
    "/live2.sdp",
    "/videoSub",
    "/11",
    "/play1.sdp",
    "/h264.sdp",
    "/session0?streamid=main",
    "/live"
]

# High-probability live video stream & snapshot endpoints on HTTP web ports (80, 8080, 8050, 8000)
WEB_SNAPSHOT_PATHS: List[str] = [
    "/nphMotionJpeg?Resolution=640x480&Quality=Standard",
    "/axis-cgi/mjpg/video.cgi",
    "/control/faststream.jpg?stream=full",
    "/mjpg/video.mjpg",
    "/axis-cgi/jpg/image.cgi",
    "/cgi-bin/snapshot.cgi",
    "/cgi-bin/mjpg/video.cgi?channel=1&subtype=1",
    "/SnapshotJPEG?Resolution=640x480",
    "/ISAPI/Streaming/channels/101/picture",
    "/onvif-http/snapshot?channel=1",
    "/snapshot.jpg",
    "/image.jpg",
    "/video.cgi",
    "/mjpeg",
    "/live.jpg",
    "/webcapture.jpg?command=snap&channel=1",
    "/snap.jpg"
]

class VendorRtspResolver:
    """
    Automated Vendor RTSP Profile & Resolver Engine.
    Discovers active stream paths on ports 554, 8554 (Alt RTSP) and web ports (80, 8080, 8050, 8000)
    using SmartRTSP vendor profiles, parallel non-blocking socket probes, continuous candidate testing,
    RTSP Digest Authentication (MD5), and HTTP stream validation.
    """

    def detect_vendor_key(self, product_str: str) -> str:
        """Identify manufacturer profile key from product name, banner or vendor string."""
        p = (product_str or "").lower()
        if "amcrest" in p:
            return "amcrest"
        if "lorex" in p:
            return "lorex"
        if any(k in p for k in ["hikvision", "annke", "swann", "bluestar", "tovision", "digital watchdog", "flir", "hiwatch"]):
            return "hikvision"
        if any(k in p for k in ["dahua", "imou", "icrealtime"]):
            return "dahua"
        if "reolink" in p:
            return "reolink"
        if any(k in p for k in ["tapo", "tp-link", "tplink", "mercury", "sunell", "tenda", "honeywell", "lilin"]):
            return "tapo"
        if any(k in p for k in ["uniview", "unv"]):
            return "uniview"
        if any(k in p for k in ["samsung", "hanwha", "techwin", "wisenet", "ipolis"]):
            return "hanwha"
        if "axis" in p:
            return "axis"
        if "bosch" in p:
            return "bosch"
        if any(k in p for k in ["vivotek", "abus"]):
            return "vivotek"
        if "foscam" in p:
            return "foscam"
        if any(k in p for k in ["xiongmai", "xm", "netip"]):
            return "xiongmai"
        if "eufy" in p:
            return "eufy"
        if any(k in p for k in ["tiandy", "jovision", "infinova", "ezviz"]):
            return "tiandy"
        if "sony" in p:
            return "sony"
        if any(k in p for k in ["panasonic", "i-pro", "ipro"]):
            return "panasonic"
        if "geovision" in p:
            return "geovision"
        if "grandstream" in p:
            return "grandstream"
        if "mobotix" in p:
            return "mobotix"
        if any(k in p for k in ["huawei", "holosens"]):
            return "huawei"
        if any(k in p for k in ["v380", "xiaomi", "mijia", "aqara"]):
            return "v380"
        if any(k in p for k in ["pelco", "arecont"]):
            return "pelco"
        if "kedacom" in p:
            return "kedacom"
        if any(k in p for k in ["d-link", "dlink"]):
            return "dlink"
        return "generic"

    def get_vendor_credentials(self, product_or_vendor: str) -> List[Tuple[str, str]]:
        """
        Get vendor default credentials list directly from VENDOR_RTSP_PROFILES.
        Resolves vendor profile key from product name, banner, realm, or vendor string.
        """
        raw_key = (product_or_vendor or "").lower().strip()
        if raw_key in VENDOR_RTSP_PROFILES and "creds" in VENDOR_RTSP_PROFILES[raw_key]:
            return list(VENDOR_RTSP_PROFILES[raw_key]["creds"])

        vendor_key = self.detect_vendor_key(product_or_vendor)
        profile = VENDOR_RTSP_PROFILES.get(vendor_key)
        if profile and "creds" in profile:
            return list(profile["creds"])

        return list(VENDOR_RTSP_PROFILES["generic"]["creds"])

    async def retry_rtsp_with_default_creds(
        self,
        ip: str,
        port: int,
        path: str = "",
        product: str = "",
        realm: Optional[str] = None,
        nonce: Optional[str] = None,
        timeout: float = 1.2
    ) -> Tuple[bool, str, Optional[Tuple[str, str]]]:
        """
        Retry connecting to RTSP stream with a default password list filtered by vendor
        from VENDOR_RTSP_PROFILES.
        If realm/nonce are known, tests credentials directly on the target path.
        Otherwise probes the target path and vendor candidate paths to negotiate Digest/Basic auth.
        Returns (is_success, resolved_rtsp_url, matched_credential).
        """
        if tor_service.should_route_via_tor(ip):
            await tor_service._require_socks_ready()

        vendor_key = self.detect_vendor_key(product)
        if vendor_key == "generic" and realm:
            realm_vendor = self.detect_vendor_key(realm)
            if realm_vendor != "generic":
                vendor_key = realm_vendor

        creds = list(self.get_vendor_credentials(vendor_key if vendor_key != "generic" else product))
        profile = VENDOR_RTSP_PROFILES.get(vendor_key, VENDOR_RTSP_PROFILES["generic"])
        default_vendor_paths = profile.get("paths", ["/Streaming/Channels/101"])

        clean_path = (path or "").strip()
        if clean_path and not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        # 1. If realm or nonce are already known from a prior 401 response on a specific path
        if realm is not None or nonce is not None:
            target_path = clean_path if (clean_path and clean_path not in ("/", "/live")) else default_vendor_paths[0]
            ok, auth_url, matched = await self._probe_creds_persistent(
                ip, port, target_path, creds, realm=realm, nonce=nonce, timeout=timeout
            )
            if ok and matched:
                logger.info(f"[Auto-Auth] RTSP {ip}:{port}{target_path} authenticated for {vendor_key.upper()} with default credentials: {matched[0]}:***")
                return True, auth_url, matched
            return False, f"rtsp://{ip}:{port}{target_path}", None

        # 2. If realm/nonce not yet known, build candidate paths for the vendor
        paths_to_try: List[str] = []
        # If caller supplied a specific non-placeholder path, test it first
        if clean_path and clean_path not in ("/", "/live", "/onvif1"):
            paths_to_try.append(clean_path)

        # Add all vendor profile paths
        for vp in default_vendor_paths:
            if vp not in paths_to_try:
                paths_to_try.append(vp)

        if clean_path == "/onvif1" and "/onvif1" not in paths_to_try:
            paths_to_try.append("/onvif1")

        fallback_path = paths_to_try[0] if paths_to_try else "/Streaming/Channels/101"

        for cand_path in paths_to_try:
            if not creds:
                break
            # Probe path with first credential (performs CSeq 1 -> 401 -> CSeq 2 handshake)
            is_ok, code, auth_url, cur_realm, cur_nonce = await self.probe_rtsp_path(
                ip, port, cand_path, creds=creds[0], timeout=timeout
            )
            if is_ok:
                logger.info(f"[Auto-Auth] RTSP {ip}:{port}{cand_path} authenticated for {vendor_key.upper()} with default credentials: {creds[0][0]}:***")
                return True, auth_url, creds[0]

            if code == 401:
                # If vendor was generic, check if the 401 realm identifies the manufacturer
                active_creds = list(creds)
                if vendor_key == "generic" and cur_realm:
                    detected = self.detect_vendor_key(cur_realm)
                    if detected != "generic":
                        vendor_key = detected
                        for vc in self.get_vendor_credentials(detected):
                            if vc not in active_creds:
                                active_creds.append(vc)

                # Test remaining credentials over ONE persistent TCP socket on this verified 401 endpoint
                ok, next_auth_url, matched = await self._probe_creds_persistent(
                    ip, port, cand_path, active_creds[1:], realm=cur_realm, nonce=cur_nonce, timeout=timeout
                )
                if ok and matched:
                    logger.info(f"[Auto-Auth] RTSP {ip}:{port}{cand_path} authenticated for {vendor_key.upper()} with default credentials: {matched[0]}:***")
                    return True, next_auth_url, matched
                # Continue to next candidate path in case another path accepts credentials
                continue

            if code == 0:
                # Host/port unreachable or timed out; do not waste time on further paths
                break

        return False, f"rtsp://{ip}:{port}{fallback_path}", None

    @staticmethod
    def _is_rtsp_200(resp_str: str) -> bool:
        return bool(re.match(r"^RTSP/\d\.\d\s+200\b", resp_str or ""))

    @staticmethod
    def _is_rtsp_401(resp_str: str) -> bool:
        return bool(re.match(r"^RTSP/\d\.\d\s+401\b", resp_str or ""))

    @staticmethod
    async def _read_rtsp_message(
        reader: asyncio.StreamReader,
        timeout: float,
        leftover: bytes = b"",
        max_header_bytes: int = 8192,
    ) -> Tuple[str, bytes, bool]:
        """
        Read one complete RTSP response (headers + optional Content-Length body) from a persistent TCP stream.
        Returns (headers_str, remaining_leftover_bytes, connection_closed).
        Keeping stream framing intact lets us probe many RTSP paths over a single Tor TCP socket!
        """
        buf = leftover
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.1)
        sep_idx = -1
        sep_len = 4
        eof = False

        while len(buf) < max_header_bytes:
            sep_idx = buf.find(b"\r\n\r\n")
            if sep_idx != -1:
                sep_len = 4
                break
            sep_idx = buf.find(b"\n\n")
            if sep_idx != -1:
                sep_len = 2
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(min(2048, max_header_bytes - len(buf))), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                eof = True
                break
            buf += chunk

        if sep_idx == -1:
            return buf.decode(errors="ignore"), b"", True

        hdr_bytes = buf[:sep_idx]
        rest = buf[sep_idx + sep_len:]
        hdr_str = hdr_bytes.decode(errors="ignore")

        cl_m = re.search(r"(?im)^Content-Length:\s*(\d+)", hdr_str)
        body_len = int(cl_m.group(1)) if cl_m else 0
        if body_len > 65536:
            return hdr_str, b"", True

        while len(rest) < body_len:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return hdr_str, b"", True
            try:
                chunk = await asyncio.wait_for(reader.read(min(4096, body_len - len(rest))), timeout=remaining)
            except asyncio.TimeoutError:
                return hdr_str, b"", True
            if not chunk:
                return hdr_str, b"", True
            rest += chunk

        new_leftover = rest[body_len:] if body_len > 0 else rest
        conn_close = eof or bool(re.search(r"(?im)^Connection:\s*close\b", hdr_str))
        return hdr_str, new_leftover, conn_close

    @staticmethod
    def _is_hard_refused(exc: BaseException) -> bool:
        if isinstance(exc, ConnectionRefusedError):
            return True
        if isinstance(exc, TorSocksConnectError) and exc.reply_code == 5:
            return True
        return False

    async def _probe_paths_persistent(
        self,
        host: str,
        port: int,
        paths: List[str],
        timeout: float,
        stop_event: asyncio.Event,
        disconnect_check: Optional[Callable[[], Awaitable[bool]]] = None,
        req_timeout_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Probe a sequence of RTSP paths reusing ONE persistent TCP connection
        (transparently reconnecting if the camera closes the socket between requests).
        """
        user_agent = "Lavf/58.76.100"
        is_tor = tor_service.should_route_via_tor(host)
        connect_timeout = max(timeout, 12.0) if is_tor else timeout
        req_timeout = req_timeout_override or (max(timeout, 4.5) if is_tor else timeout)

        reader: Optional[asyncio.StreamReader] = None
        writer: Optional[asyncio.StreamWriter] = None
        leftover = b""
        cseq = 0
        tested = 0
        reachable = False
        hard_refused = False
        supports_keepalive = True
        auth_401: List[Tuple[str, Optional[str], Optional[str]]] = []

        async def _close_conn():
            nonlocal reader, writer, leftover
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            reader = None
            writer = None
            leftover = b""

        async def _ensure_conn() -> bool:
            nonlocal reader, writer, leftover, cseq
            if writer is not None and not writer.is_closing():
                return True
            await _close_conn()
            reader, writer = await tor_service.open_connection(
                host, port, timeout=connect_timeout, circuit_key=f"{host}:{port}"
            )
            leftover = b""
            cseq = 0
            return True

        try:
            for path in paths:
                if stop_event.is_set():
                    break
                if disconnect_check and await disconnect_check():
                    return {"aborted": True, "tested": tested, "reachable": reachable, "hard_refused": hard_refused, "auth_401": auth_401}

                uri = f"rtsp://{host}:{port}{path}"
                resp_str = ""
                if not supports_keepalive:
                    await _close_conn()

                for attempt in range(2):
                    if stop_event.is_set():
                        break
                    reused = writer is not None and not writer.is_closing()
                    try:
                        await _ensure_conn()
                        cseq += 1
                        req = (
                            f"DESCRIBE {uri} RTSP/1.0\r\n"
                            f"CSeq: {cseq}\r\n"
                            f"User-Agent: {user_agent}\r\n"
                            f"Accept: application/sdp\r\n\r\n"
                        )
                        writer.write(req.encode())
                        await writer.drain()
                        cur_read_timeout = min(req_timeout, 3.0) if (reused and is_tor) else req_timeout
                        resp_str, leftover, closed = await self._read_rtsp_message(
                            reader, cur_read_timeout, leftover=leftover
                        )
                        if closed:
                            if reused and not resp_str:
                                supports_keepalive = False
                            await _close_conn()
                        if resp_str:
                            break
                        if reused:
                            supports_keepalive = False
                        await _close_conn()
                    except HTTPException:
                        raise
                    except Exception as exc:
                        await _close_conn()
                        if self._is_hard_refused(exc):
                            hard_refused = True
                            return {"resolved": False, "tested": tested + 1, "reachable": False, "hard_refused": True, "auth_401": auth_401}
                        if not reachable:
                            return {"resolved": False, "tested": tested + 1, "reachable": False, "hard_refused": False, "auth_401": auth_401}
                        if attempt == 1:
                            break

                tested += 1
                if not resp_str:
                    if not reachable:
                        break
                    continue

                tokens = resp_str.split(None, 2)
                code = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else 0
                if code != 0:
                    reachable = True

                if self._is_rtsp_200(resp_str):
                    stop_event.set()
                    return {
                        "resolved": True,
                        "url": uri,
                        "tested": tested,
                        "reachable": True,
                        "hard_refused": False,
                        "auth_401": auth_401,
                    }

                if self._is_rtsp_401(resp_str):
                    realm_m = re.search(r'realm="([^"]*)"', resp_str, re.IGNORECASE)
                    nonce_m = re.search(r'nonce="([^"]+)"', resp_str, re.IGNORECASE)
                    realm = realm_m.group(1) if realm_m else None
                    nonce = nonce_m.group(1) if nonce_m else None
                    if not any(existing[0] == path for existing in auth_401):
                        auth_401.append((path, realm, nonce))
        finally:
            await _close_conn()

        return {"resolved": False, "tested": tested, "reachable": reachable, "hard_refused": hard_refused, "auth_401": auth_401}

    async def _probe_paths_adaptive(
        self,
        host: str,
        port: int,
        candidate_paths: List[str],
        timeout: float,
        disconnect_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Dict[str, Any]:
        """
        Adaptive RTSP path prober for both direct LAN/WAN and Tor:
        1. Probes path 0 on connection #1 (auto-detecting reachability & RTT).
        2. Tests path 1 on the same socket to detect whether the camera supports RTSP keep-alive.
        3. If keep-alive works: runs 2 (Tor) / 4 (direct) persistent workers across remaining paths (~3-5s over Tor).
        4. If one-shot camera: switches immediately to clean fresh-socket batches on the warm Tor circuit
           so it never wastes timeouts hanging on closed/ignored reused sockets.
        """
        if not candidate_paths:
            return {"resolved": False, "tested": 0, "reachable": False, "hard_refused": False, "auth_401": [], "aborted": False}

        is_tor = tor_service.should_route_via_tor(host)
        connect_timeout = max(timeout, 12.0) if is_tor else timeout
        req_timeout = max(timeout, 4.5) if is_tor else timeout
        user_agent = "Lavf/58.76.100"

        tested = 0
        reachable = False
        hard_refused = False
        auth_401: List[Tuple[str, Optional[str], Optional[str]]] = []
        reader: Optional[asyncio.StreamReader] = None
        writer: Optional[asyncio.StreamWriter] = None
        leftover = b""

        async def _close_first():
            nonlocal reader, writer, leftover
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            reader = None
            writer = None
            leftover = b""

        def _record_401(path_str: str, resp_str: str):
            rm = re.search(r'realm="([^"]*)"', resp_str, re.IGNORECASE)
            nm = re.search(r'nonce="([^"]+)"', resp_str, re.IGNORECASE)
            realm = rm.group(1) if rm else None
            nonce = nm.group(1) if nm else None
            if not any(existing[0] == path_str for existing in auth_401):
                auth_401.append((path_str, realm, nonce))

        keepalive_supported = False
        first_rtt = 1.0
        next_start_idx = 1

        try:
            # Step 1: Probe Path 0 on Connection #1
            p0 = candidate_paths[0]
            uri0 = f"rtsp://{host}:{port}{p0}"
            try:
                reader, writer = await tor_service.open_connection(
                    host, port, timeout=connect_timeout, circuit_key=f"{host}:{port}"
                )
            except HTTPException:
                raise
            except Exception as exc:
                return {
                    "resolved": False,
                    "tested": 1,
                    "reachable": False,
                    "hard_refused": self._is_hard_refused(exc),
                    "auth_401": auth_401,
                    "aborted": False,
                }

            t_req0 = time.monotonic()
            req0 = (
                f"DESCRIBE {uri0} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"User-Agent: {user_agent}\r\n"
                f"Accept: application/sdp\r\n\r\n"
            )
            writer.write(req0.encode())
            await writer.drain()
            resp0, leftover, closed0 = await self._read_rtsp_message(reader, req_timeout, leftover=leftover)
            first_rtt = max(0.05, time.monotonic() - t_req0)
            tested += 1

            if resp0:
                toks = resp0.split(None, 2)
                if len(toks) > 1 and toks[1].isdigit():
                    reachable = True
                if self._is_rtsp_200(resp0):
                    return {"resolved": True, "url": uri0, "tested": tested, "reachable": True, "hard_refused": False, "auth_401": auth_401, "aborted": False}
                if self._is_rtsp_401(resp0):
                    _record_401(p0, resp0)
            if not reachable:
                await _close_first()
                return {"resolved": False, "tested": tested, "reachable": False, "hard_refused": False, "auth_401": auth_401, "aborted": False}

            if len(candidate_paths) == 1:
                await _close_first()
                return {"resolved": False, "tested": tested, "reachable": True, "hard_refused": False, "auth_401": auth_401, "aborted": False}

            if disconnect_check and await disconnect_check():
                await _close_first()
                return {"resolved": False, "tested": tested, "reachable": True, "hard_refused": False, "auth_401": auth_401, "aborted": True}

            # Step 2: Test Path 1 on the SAME socket (if still open) to detect keep-alive vs one-shot camera
            p1 = candidate_paths[1]
            uri1 = f"rtsp://{host}:{port}{p1}"
            if not closed0 and writer is not None and not writer.is_closing():
                reuse_timeout = max(1.8, min(3.5, first_rtt * 4.0)) if is_tor else min(req_timeout, 1.5)
                try:
                    req1 = (
                        f"DESCRIBE {uri1} RTSP/1.0\r\n"
                        f"CSeq: 2\r\n"
                        f"User-Agent: {user_agent}\r\n"
                        f"Accept: application/sdp\r\n\r\n"
                    )
                    writer.write(req1.encode())
                    await writer.drain()
                    resp1, leftover, closed1 = await self._read_rtsp_message(reader, reuse_timeout, leftover=leftover)
                    if resp1:
                        tested += 1
                        next_start_idx = 2
                        keepalive_supported = not closed1
                        if self._is_rtsp_200(resp1):
                            return {"resolved": True, "url": uri1, "tested": tested, "reachable": True, "hard_refused": False, "auth_401": auth_401, "aborted": False}
                        if self._is_rtsp_401(resp1):
                            _record_401(p1, resp1)
                except HTTPException:
                    raise
                except Exception:
                    keepalive_supported = False
        finally:
            await _close_first()

        remaining = candidate_paths[next_start_idx:]
        if not remaining:
            return {"resolved": False, "tested": tested, "reachable": reachable, "hard_refused": hard_refused, "auth_401": auth_401, "aborted": False}

        # Step 3A: Keep-alive supported -> fast persistent workers (up to 16 total paths over Tor)
        if keepalive_supported:
            if is_tor:
                remaining = remaining[:14]
            worker_count = min(2 if is_tor else 4, len(remaining))
            stop_event = asyncio.Event()
            adaptive_req_to = max(2.0, min(4.0, first_rtt * 4.5)) if is_tor else req_timeout
            slices = [remaining[i::worker_count] for i in range(worker_count)]
            tasks = [
                asyncio.create_task(
                    self._probe_paths_persistent(
                        host, port, sl, timeout=timeout, stop_event=stop_event,
                        disconnect_check=disconnect_check, req_timeout_override=adaptive_req_to,
                    )
                )
                for sl in slices if sl
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            aborted = False
            for wr in results:
                if isinstance(wr, HTTPException):
                    raise wr
                if isinstance(wr, dict):
                    tested += int(wr.get("tested", 0))
                    if wr.get("aborted"):
                        aborted = True
                    for item in wr.get("auth_401", []):
                        if not any(ex[0] == item[0] for ex in auth_401):
                            auth_401.append(item)
                    if wr.get("resolved") and wr.get("url"):
                        return {"resolved": True, "url": wr["url"], "tested": tested, "reachable": True, "hard_refused": False, "auth_401": auth_401, "aborted": False}
            return {"resolved": False, "tested": tested, "reachable": reachable, "hard_refused": hard_refused, "auth_401": auth_401, "aborted": aborted}

        # Step 3B: One-shot camera -> clean fresh-socket batches on the warm Tor circuit (zero keep-alive hangs)
        if is_tor:
            remaining = remaining[:11]
        batch_size = 3 if is_tor else 6
        batch_timeout = max(4.5, min(7.0, first_rtt * 5.0 + 2.0)) if is_tor else timeout
        for i in range(0, len(remaining), batch_size):
            if disconnect_check and await disconnect_check():
                return {"resolved": False, "tested": tested, "reachable": reachable, "hard_refused": False, "auth_401": auth_401, "aborted": True}
            batch = remaining[i:i + batch_size]
            tasks = [asyncio.create_task(self.probe_rtsp_path(host, port, p, creds=None, timeout=batch_timeout)) for p in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            tested += len(batch)
            for res in results:
                if isinstance(res, HTTPException):
                    raise res
                if isinstance(res, tuple):
                    ok, code, url, rlm, nnc = res
                    if ok:
                        return {"resolved": True, "url": url, "tested": tested, "reachable": True, "hard_refused": False, "auth_401": auth_401, "aborted": False}
                    if code == 401:
                        p_only = urlparse(url).path + (f"?{urlparse(url).query}" if urlparse(url).query else "")
                        if not any(ex[0] == p_only for ex in auth_401):
                            auth_401.append((p_only, rlm, nnc))

        return {"resolved": False, "tested": tested, "reachable": reachable, "hard_refused": hard_refused, "auth_401": auth_401, "aborted": False}

    async def _probe_creds_persistent(
        self,
        host: str,
        port: int,
        path: str,
        creds_list: List[Tuple[str, str]],
        realm: Optional[str] = None,
        nonce: Optional[str] = None,
        timeout: float = 1.2,
    ) -> Tuple[bool, str, Optional[Tuple[str, str]]]:
        """
        Test a list of credentials against a single RTSP 401 path reusing ONE persistent TCP socket
        (reconnecting only if the camera closes the socket between attempts).
        """
        if not creds_list:
            return False, f"rtsp://{host}:{port}{path}", None

        uri = f"rtsp://{host}:{port}{path}"
        user_agent = "Lavf/58.76.100"
        is_tor = tor_service.should_route_via_tor(host)
        connect_timeout = max(timeout, 12.0) if is_tor else timeout
        req_timeout = max(timeout, 4.5) if is_tor else timeout
        active_creds = creds_list[:6] if (is_tor and len(creds_list) > 6) else creds_list

        reader: Optional[asyncio.StreamReader] = None
        writer: Optional[asyncio.StreamWriter] = None
        leftover = b""
        cseq = 0
        cur_realm = realm
        cur_nonce = nonce
        cur_qop: Optional[str] = None
        cur_alg: Optional[str] = None

        async def _close_conn():
            nonlocal reader, writer, leftover
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            reader = None
            writer = None
            leftover = b""

        async def _ensure_conn():
            nonlocal reader, writer, leftover, cseq
            if writer is not None and not writer.is_closing():
                return
            await _close_conn()
            reader, writer = await tor_service.open_connection(
                host, port, timeout=connect_timeout, circuit_key=f"{host}:{port}"
            )
            leftover = b""
            cseq = 0

        def _extract_challenge(resp: str):
            nonlocal cur_realm, cur_nonce, cur_qop, cur_alg
            rm = re.search(r'realm="([^"]*)"', resp, re.IGNORECASE)
            nm = re.search(r'nonce="([^"]+)"', resp, re.IGNORECASE)
            qm = re.search(r'qop=(?:"([^"]+)"|([^,\s\r\n]+))', resp, re.IGNORECASE)
            am = re.search(r'algorithm=(?:"([^"]+)"|([^,\s\r\n]+))', resp, re.IGNORECASE)
            if rm:
                cur_realm = rm.group(1)
            if nm:
                cur_nonce = nm.group(1)
            if qm:
                cur_qop = qm.group(1) or qm.group(2)
            if am:
                cur_alg = am.group(1) or am.group(2)

        try:
            for user, pwd in active_creds:
                # Up to 2 steps per cred: (1) send with current/cached challenge; (2) if server issued a fresh session nonce, retry on same socket
                for step in range(2):
                    try:
                        await _ensure_conn()
                        if cur_realm is not None and cur_nonce is not None:
                            auth_hdr = self._compute_digest(
                                "DESCRIBE", uri, user, pwd, cur_realm, cur_nonce, qop=cur_qop, algorithm=cur_alg
                            )
                        else:
                            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("utf-8")
                            auth_hdr = f"Basic {token}"

                        cseq += 1
                        req = (
                            f"DESCRIBE {uri} RTSP/1.0\r\n"
                            f"CSeq: {cseq}\r\n"
                            f"Authorization: {auth_hdr}\r\n"
                            f"User-Agent: {user_agent}\r\n"
                            f"Accept: application/sdp\r\n\r\n"
                        )
                        writer.write(req.encode())
                        await writer.drain()
                        resp, leftover, closed = await self._read_rtsp_message(
                            reader, req_timeout, leftover=leftover
                        )
                        if closed:
                            await _close_conn()

                        if self._is_rtsp_200(resp):
                            return True, f"rtsp://{user}:{pwd}@{host}:{port}{path}", (user, pwd)

                        if self._is_rtsp_401(resp):
                            prev_nonce = cur_nonce
                            _extract_challenge(resp)
                            # If camera issued a brand-new per-connection nonce (e.g. first request on this socket), retry this cred once with the fresh nonce!
                            if step == 0 and cur_nonce and cur_nonce != prev_nonce:
                                continue
                            break

                        if not resp:
                            # Socket closed silently before reply; reconnect once and retry this cred
                            await _close_conn()
                            if step == 0:
                                continue
                            break
                        break
                    except HTTPException:
                        raise
                    except Exception:
                        await _close_conn()
                        if step == 0:
                            continue
                        return False, uri, None
        finally:
            await _close_conn()

        return False, uri, None

    @staticmethod
    async def _read_rtsp_headers(
        reader: asyncio.StreamReader,
        timeout: float,
        max_bytes: int = 4096,
    ) -> str:
        """Read RTSP response headers cleanly even when split across multiple TCP segments (common over Tor)."""
        buf = b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.1)
        while len(buf) < max_bytes:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(min(1024, max_bytes - len(buf))), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
            if b"\r\n\r\n" in buf or b"\n\n" in buf:
                break
        return buf.decode(errors="ignore")

    @staticmethod
    def _compute_digest(
        method: str,
        uri: str,
        user: str,
        pwd: str,
        realm: str,
        nonce: str,
        qop: Optional[str] = None,
        cnonce: Optional[str] = None,
        nc: str = "00000001",
        algorithm: Optional[str] = None,
    ) -> str:
        """Compute RFC 2069 / RFC 2617 / RFC 7616 RTSP Digest Authentication header (MD5 or SHA-256, optional qop=auth)."""
        alg_upper = (algorithm or "MD5").strip().strip('"').upper()
        hash_fn = hashlib.sha256 if alg_upper.startswith("SHA-256") else hashlib.md5
        alg_param = f', algorithm={alg_upper}' if alg_upper.startswith("SHA-256") else ""
        ha1 = hash_fn(f"{user}:{realm}:{pwd}".encode("utf-8")).hexdigest()
        ha2 = hash_fn(f"{method}:{uri}".encode("utf-8")).hexdigest()
        qop_tokens = [t.strip().strip('"').lower() for t in (qop or "").split(",") if t.strip()]
        if "auth" in qop_tokens:
            active_cnonce = cnonce or secrets.token_hex(8)
            resp = hash_fn(
                f"{ha1}:{nonce}:{nc}:{active_cnonce}:auth:{ha2}".encode("utf-8")
            ).hexdigest()
            return (
                f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
                f'qop=auth, nc={nc}, cnonce="{active_cnonce}", response="{resp}"{alg_param}'
            )
        resp = hash_fn(f"{ha1}:{nonce}:{ha2}".encode("utf-8")).hexdigest()
        return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"{alg_param}'

    async def probe_rtsp_path(
        self,
        host: str,
        port: int,
        path: str,
        creds: Optional[Tuple[str, str]] = None,
        timeout: float = 1.2
    ) -> Tuple[bool, int, str, Optional[str], Optional[str]]:
        """
        Lightweight async socket probe using RTSP DESCRIBE.
        Handles unauthenticated access, RFC standard RTSP Digest Authentication (with or without qop=auth),
        and Basic Authentication.
        Returns (is_success, http_code, target_rtsp_url, realm, nonce).
        """
        uri = f"rtsp://{host}:{port}{path}"
        user_agent = "Lavf/58.76.100"

        req_initial = (
            f"DESCRIBE {uri} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"User-Agent: {user_agent}\r\n"
            f"Accept: application/sdp\r\n\r\n"
        )

        is_tor = tor_service.should_route_via_tor(host)
        eff_timeout = max(timeout, 12.0) if is_tor else timeout
        read_timeout = max(timeout, 5.0) if is_tor else timeout

        try:
            reader, writer = await tor_service.open_connection(
                host,
                port,
                timeout=eff_timeout,
                circuit_key=f"{host}:{port}",
            )
            writer.write(req_initial.encode())
            await writer.drain()

            resp_str = await self._read_rtsp_headers(reader, read_timeout)

            if self._is_rtsp_200(resp_str):
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                resolved_url = f"rtsp://{creds[0]}:{creds[1]}@{host}:{port}{path}" if creds else uri
                return True, 200, resolved_url, None, None

            # If rejected with 401 Unauthorized, extract Digest realm, nonce, optional qop, and algorithm
            realm, nonce, qop, alg = None, None, None, None
            if self._is_rtsp_401(resp_str):
                realm_m = re.search(r'realm="([^"]*)"', resp_str, re.IGNORECASE)
                nonce_m = re.search(r'nonce="([^"]+)"', resp_str, re.IGNORECASE)
                qop_m = re.search(r'qop=(?:"([^"]+)"|([^,\s\r\n]+))', resp_str, re.IGNORECASE)
                alg_m = re.search(r'algorithm=(?:"([^"]+)"|([^,\s\r\n]+))', resp_str, re.IGNORECASE)
                if realm_m:
                    realm = realm_m.group(1)
                if nonce_m:
                    nonce = nonce_m.group(1)
                if qop_m:
                    qop = qop_m.group(1) or qop_m.group(2)
                if alg_m:
                    alg = alg_m.group(1) or alg_m.group(2)

                # If creds provided, perform Digest or Basic Auth handshake
                if creds:
                    user, pwd = creds
                    if realm is not None and nonce is not None:
                        auth_hdr = self._compute_digest("DESCRIBE", uri, user, pwd, realm, nonce, qop=qop, algorithm=alg)
                    else:
                        token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("utf-8")
                        auth_hdr = f"Basic {token}"

                    req_auth = (
                        f"DESCRIBE {uri} RTSP/1.0\r\n"
                        f"CSeq: 2\r\n"
                        f"Authorization: {auth_hdr}\r\n"
                        f"User-Agent: {user_agent}\r\n"
                        f"Accept: application/sdp\r\n\r\n"
                    )
                    resp_auth = ""
                    try:
                        writer.write(req_auth.encode())
                        await writer.drain()
                        resp_auth = await self._read_rtsp_headers(reader, eff_timeout)
                    except Exception:
                        resp_auth = ""

                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass

                    if self._is_rtsp_200(resp_auth):
                        auth_url = f"rtsp://{user}:{pwd}@{host}:{port}{path}"
                        return True, 200, auth_url, realm, nonce

                    # If server closed the socket immediately after 401 (resp_auth is empty), retry on a new socket
                    if not resp_auth:
                        ok_direct, direct_url = await self.probe_rtsp_digest_auth(
                            host, port, path, creds, realm, nonce, timeout=eff_timeout
                        )
                        if ok_direct:
                            return True, 200, direct_url, realm, nonce

                    return False, 401, uri, realm, nonce

            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

            tokens = resp_str.split(None, 2)
            code = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else 0
            return False, code, uri, realm, nonce
        except HTTPException:
            raise
        except Exception:
            pass

        return False, 0, uri, None, None

    async def probe_rtsp_digest_auth(
        self,
        host: str,
        port: int,
        path: str,
        creds: Tuple[str, str],
        realm: Optional[str] = None,
        nonce: Optional[str] = None,
        timeout: float = 1.2
    ) -> Tuple[bool, str]:
        """
        Socket probe testing credentials against a known realm/nonce (Digest or Basic).
        Also handles RTSP servers that issue a new session nonce per TCP connection by
        completing CSeq: 2 on the same socket if a fresh nonce is returned.
        """
        uri = f"rtsp://{host}:{port}{path}"
        user, pwd = creds
        user_agent = "Lavf/58.76.100"

        if realm is not None and nonce is not None:
            auth_hdr = self._compute_digest("DESCRIBE", uri, user, pwd, realm, nonce)
        else:
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("utf-8")
            auth_hdr = f"Basic {token}"

        req = (
            f"DESCRIBE {uri} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"Authorization: {auth_hdr}\r\n"
            f"User-Agent: {user_agent}\r\n"
            f"Accept: application/sdp\r\n\r\n"
        )
        eff_timeout = max(timeout, 8.5) if tor_service.should_route_via_tor(host) else timeout
        try:
            reader, writer = await tor_service.open_connection(
                host,
                port,
                timeout=eff_timeout,
                circuit_key=f"{host}:{port}",
            )
            writer.write(req.encode())
            await writer.drain()
            resp = await self._read_rtsp_headers(reader, eff_timeout)

            if self._is_rtsp_200(resp):
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True, f"rtsp://{user}:{pwd}@{host}:{port}{path}"

            # If server challenged on CSeq: 1, authenticate on CSeq: 2
            if self._is_rtsp_401(resp):
                new_realm_m = re.search(r'realm="([^"]*)"', resp, re.IGNORECASE)
                new_nonce_m = re.search(r'nonce="([^"]+)"', resp, re.IGNORECASE)
                qop_m = re.search(r'qop=(?:"([^"]+)"|([^,\s\r\n]+))', resp, re.IGNORECASE)
                alg_m = re.search(r'algorithm=(?:"([^"]+)"|([^,\s\r\n]+))', resp, re.IGNORECASE)
                new_realm = new_realm_m.group(1) if new_realm_m else (realm or "")
                active_nonce = new_nonce_m.group(1) if new_nonce_m else nonce
                active_qop = (qop_m.group(1) or qop_m.group(2)) if qop_m else None
                active_alg = (alg_m.group(1) or alg_m.group(2)) if alg_m else None

                if active_nonce is not None:
                    auth_hdr2 = self._compute_digest(
                        "DESCRIBE", uri, user, pwd, new_realm, active_nonce, qop=active_qop, algorithm=active_alg
                    )
                    req2 = (
                        f"DESCRIBE {uri} RTSP/1.0\r\n"
                        f"CSeq: 2\r\n"
                        f"Authorization: {auth_hdr2}\r\n"
                        f"User-Agent: {user_agent}\r\n"
                        f"Accept: application/sdp\r\n\r\n"
                    )
                    writer.write(req2.encode())
                    await writer.drain()
                    resp2 = await self._read_rtsp_headers(reader, eff_timeout)
                    if self._is_rtsp_200(resp2):
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass
                        return True, f"rtsp://{user}:{pwd}@{host}:{port}{path}"

            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except HTTPException:
            raise
        except Exception:
            pass
        return False, ""

    async def probe_http_path(
        self,
        host: str,
        port: int,
        path: str,
        timeout: float = 1.2,
        is_https: bool = False
    ) -> Tuple[bool, str, str]:
        """
        Async probe for HTTP/HTTPS web camera endpoints (ports 443, 80, 8080, 8050, 8000).
        Returns (is_success, resolved_url, stream_type).
        Only accepts genuine images or MJPEG/video streams (rejects HTML, SVG, scripts, and malware binaries!).
        Uses stream reading + magic-byte inspection so honeypots disguising HTML/malware as image/jpeg are blocked.
        Includes automatic fallback from HTTPS to plaintext HTTP if SSL_ERROR_RX_RECORD_TOO_LONG occurs on port 443.
        """
        eff_timeout = max(timeout, 4.0) if tor_service.should_route_via_tor(host) else timeout
        schemes = ["https", "http"] if (port == 443 or is_https) else ["http"]
        for scheme in schemes:
            url = f"{scheme}://{host}:{port}{path}"
            verify_arg = get_legacy_ssl_context() if scheme == "https" else False
            try:
                async with await tor_service.create_http_client(
                    url,
                    verify=verify_arg,
                    timeout=eff_timeout,
                    follow_redirects=False,
                    circuit_key=f"{host}:{port}",
                ) as client:
                    async with client.stream("GET", url) as res:
                        ctype = res.headers.get("content-type", "").lower()
                        if res.status_code == 200:
                            is_mjpeg = "multipart/x-mixed-replace" in ctype or "mjpg" in ctype or "video" in ctype
                            is_img = "image" in ctype or "jpeg" in ctype or "png" in ctype
                            if (is_mjpeg or is_img) and "svg" not in ctype and "html" not in ctype:
                                first_chunk = b""
                                async for chunk in res.aiter_bytes():
                                    if chunk:
                                        first_chunk = chunk[:512]
                                        break
                                is_safe, _ = inspect_stream_chunk_safety(first_chunk, ctype)
                                if is_safe:
                                    return True, url, ("mjpeg" if is_mjpeg else "jpeg")
            except HTTPException:
                raise
            except Exception as e:
                err_str = str(e).lower()
                # If HTTPS failed due to SSL mismatch (e.g. RECORD_TOO_LARGE, WRONG_VERSION_NUMBER), retry in plain HTTP
                if scheme == "https" and any(k in err_str for k in ["ssl", "record", "version", "handshake", "connect"]):
                    continue
                break
        return False, f"https://{host}:{port}{path}" if port == 443 else f"http://{host}:{port}{path}", ""

    async def resolve_camera(
        self,
        ip: str,
        port: int,
        product: str = "",
        current_url: str = "",
        continuous: bool = True,
        disconnect_check: Optional[Callable[[], Awaitable[bool]]] = None,
        exclude_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Automated Vendor RTSP Profile & Continuous Resolver Engine.
        Executes adaptive multi-path probing across vendor profiles and SmartRTSP catalog.
        - Step 1: Tests candidate paths WITHOUT credentials first (auto-detecting persistent vs one-shot cameras).
        - Step 2: Only if no path works without credentials, tests default vendor credentials across candidate paths.
        - Falls back to HTTP MJPEG video / snapshot validation if RTSP is unreachable.
        """
        if tor_service.should_route_via_tor(ip):
            await tor_service._require_socks_ready()

        vendor_key = self.detect_vendor_key(product)
        profile = VENDOR_RTSP_PROFILES.get(vendor_key, VENDOR_RTSP_PROFILES["generic"])
        generic_profile = VENDOR_RTSP_PROFILES["generic"]
        excluded_set = set(exclude_paths or [])

        # Build prioritized unique list of candidate paths:
        candidate_paths: List[str] = []

        # 1. If current_url has a non-root path not already ruled out, check it first
        if current_url:
            try:
                parsed = urlparse(current_url)
                if parsed.path and parsed.path not in ("/", ""):
                    full_p = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                    if full_p not in excluded_set:
                        candidate_paths.append(full_p)
            except Exception:
                pass

        # 2. Add detected vendor profile paths
        for p in profile.get("paths", []):
            if p not in candidate_paths and p not in excluded_set:
                candidate_paths.append(p)

        # 3. If continuous probing requested (or vendor is generic), add all remaining SmartRTSP paths
        if continuous or vendor_key == "generic":
            for p in GENERIC_FALLBACK_PATHS:
                if p not in candidate_paths and p not in excluded_set:
                    candidate_paths.append(p)

        # Build credentials list directly from vendor profile (with generic fallback if undetermined)
        creds_list: List[Tuple[str, str]] = self.get_vendor_credentials(product or vendor_key)
        if vendor_key == "generic":
            for c in generic_profile.get("creds", []):
                if c not in creds_list:
                    creds_list.append(c)

        tested_count = 0
        auth_required_candidates: List[Tuple[str, Optional[str], Optional[str]]] = []  # (path, realm, nonce)
        rtsp_reachable = False
        hard_refused = False

        # =====================================================================
        # Phase 1: RTSP Probing (Ports 554, 8554 or explicit non-web rtsp:// URLs)
        # Step 1A: Adaptive probing WITHOUT credentials (keep-alive vs one-shot auto-detection)
        # =====================================================================
        if not is_web_camera_port(port) and (port in (554, 8554, 10554, 5544, 7070, 7447, 37777) or "rtsp://" in current_url):
            is_tor = tor_service.should_route_via_tor(ip)
            logger.info(
                f"Adaptive RTSP probing (unauthenticated pass, tor={is_tor}) for {ip}:{port} "
                f"(Vendor: {vendor_key.upper()}, Candidates: {len(candidate_paths)})..."
            )
            probe_res = await self._probe_paths_adaptive(
                ip, port, candidate_paths, timeout=1.6, disconnect_check=disconnect_check
            )
            tested_count += int(probe_res.get("tested", 0))
            rtsp_reachable = bool(probe_res.get("reachable"))
            hard_refused = bool(probe_res.get("hard_refused"))
            for item in probe_res.get("auth_401", []):
                if not any(existing[0] == item[0] for existing in auth_required_candidates):
                    auth_required_candidates.append(item)

            if probe_res.get("resolved") and probe_res.get("url"):
                url = probe_res["url"]
                return {
                    "resolved": True,
                    "reachable": True,
                    "hard_refused": False,
                    "protocol": "rtsp",
                    "stream_url": url,
                    "vendor": vendor_key,
                    "tested_count": tested_count,
                    "auth": "unauthenticated",
                    "message": f"Resolved unauthenticated RTSP stream on path {urlparse(url).path}"
                }

            if probe_res.get("aborted"):
                logger.info(f"Aborted RTSP probing for {ip}:{port} (client disconnected).")
                return {
                    "resolved": False,
                    "reachable": rtsp_reachable,
                    "hard_refused": hard_refused,
                    "protocol": "aborted",
                    "stream_url": current_url,
                    "vendor": vendor_key,
                    "tested_count": tested_count,
                    "message": "Client disconnected; probing stopped."
                }

            # Step 1B: ONLY AFTER unauthenticated pass, test default vendor credentials over persistent TCP sockets
            if auth_required_candidates and creds_list:
                max_auth_paths = 2 if is_tor else 3
                for target_p, target_realm, target_nonce in auth_required_candidates[:max_auth_paths]:
                    if disconnect_check and await disconnect_check():
                        logger.info(f"Aborted RTSP credential probing for {ip}:{port} (client disconnected).")
                        return {
                            "resolved": False,
                            "reachable": rtsp_reachable,
                            "protocol": "aborted",
                            "stream_url": current_url,
                            "vendor": vendor_key,
                            "tested_count": tested_count,
                            "message": "Client disconnected; probing stopped."
                        }
                    logger.info(f"Testing default vendor credentials on 401 endpoint: {target_p} (Vendor: {vendor_key.upper()}, Realm: {target_realm})...")
                    retry_ok, auth_url, used_cred = await self.retry_rtsp_with_default_creds(
                        ip=ip,
                        port=port,
                        path=target_p,
                        product=product or vendor_key,
                        realm=target_realm,
                        nonce=target_nonce,
                        timeout=1.2
                    )
                    tested_count += len(creds_list)
                    if retry_ok and used_cred:
                        return {
                            "resolved": True,
                            "reachable": True,
                            "protocol": "rtsp",
                            "stream_url": auth_url,
                            "vendor": vendor_key,
                            "tested_count": tested_count,
                            "auth": "default_creds",
                            "matched_cred": used_cred,
                            "message": f"Resolved authenticated RTSP stream with default credentials ({used_cred[0]}:{used_cred[1]}): {auth_url}"
                        }
            elif not auth_required_candidates and creds_list and rtsp_reachable:
                # If no paths returned an explicit 401 challenge but host is responsive, try top vendor paths with default creds
                max_fallback_paths = 2 if is_tor else 3
                for target_p in candidate_paths[:max_fallback_paths]:
                    if disconnect_check and await disconnect_check():
                        logger.info(f"Aborted RTSP credential probing for {ip}:{port} (client disconnected).")
                        return {
                            "resolved": False,
                            "reachable": rtsp_reachable,
                            "protocol": "aborted",
                            "stream_url": current_url,
                            "vendor": vendor_key,
                            "tested_count": tested_count,
                            "message": "Client disconnected; probing stopped."
                        }
                    retry_ok, auth_url, used_cred = await self.retry_rtsp_with_default_creds(
                        ip=ip,
                        port=port,
                        path=target_p,
                        product=product or vendor_key,
                        timeout=1.2
                    )
                    tested_count += len(creds_list)
                    if retry_ok and used_cred:
                        return {
                            "resolved": True,
                            "reachable": True,
                            "protocol": "rtsp",
                            "stream_url": auth_url,
                            "vendor": vendor_key,
                            "tested_count": tested_count,
                            "auth": "default_creds",
                            "matched_cred": used_cred,
                            "message": f"Resolved authenticated RTSP stream with default credentials ({used_cred[0]}:{used_cred[1]}): {auth_url}"
                        }

        # =====================================================================
        # Phase 2: HTTP/HTTPS Web Camera Probing (Ports 80-99, 443, 8000-8499, etc. or RTSP fallback)
        # Continuous batch probing across live MJPEG / JPEG snapshot paths
        # =====================================================================
        if is_web_camera_port(port) or not current_url.startswith("rtsp://"):
            is_https = is_https_camera_port(port) or current_url.startswith("https://")
            scheme = "https" if is_https else "http"
            probe_timeout = 2.2 if is_https else 1.5
            logger.info(f"Parallel {scheme.upper()} Web stream probing for {ip}:{port} (timeout={probe_timeout}s)...")
            http_tasks = [self.probe_http_path(ip, port, path, timeout=probe_timeout, is_https=is_https) for path in WEB_SNAPSHOT_PATHS]
            http_results = await asyncio.gather(*http_tasks, return_exceptions=True)
            tested_count += len(WEB_SNAPSHOT_PATHS)

            for h_res in http_results:
                if isinstance(h_res, HTTPException):
                    raise h_res
                if isinstance(h_res, tuple) and h_res[0]:
                    return {
                        "resolved": True,
                        "protocol": scheme,
                        "stream_url": h_res[1],
                        "stream_type": h_res[2],
                        "vendor": vendor_key,
                        "tested_count": tested_count,
                        "auth": "open_web",
                        "message": f"Resolved active {scheme.upper()} Web camera stream ({h_res[2].upper()}): {h_res[1]}"
                    }

            # Check if camera serves an active Web UI portal (HTML interface)
            # Statically parse the HTML source in isolated mode (zero code execution) to extract embedded video/snapshot streams!
            schemes_to_test = ["https", "http"] if is_https else ["http"]
            for s in schemes_to_test:
                verify_arg = get_legacy_ssl_context() if s == "https" else False
                try:
                    portal_url = f"{s}://{ip}:{port}/"
                    async with await tor_service.create_http_client(
                        portal_url,
                        verify=verify_arg,
                        timeout=4.0 if tor_service.should_route_via_tor(ip) else 1.8,
                        follow_redirects=False,
                        circuit_key=f"{ip}:{port}",
                    ) as client:
                        root_res = await client.get(portal_url)
                        # Handle same-host redirect safely
                        if root_res.status_code in (301, 302, 303, 307, 308):
                            loc = root_res.headers.get("location", "")
                            if loc:
                                next_url = urljoin(portal_url, loc)
                                if (urlparse(next_url).hostname or "").lower() == ip.lower():
                                    root_res = await client.get(next_url)

                        if root_res.status_code in (200, 301, 302, 401):
                            proto_note = "HTTPS" if s == "https" else "Plaintext HTTP (SSL Fallback)"
                            # Attempt to extract embedded video/snapshot source URLs from portal HTML
                            if root_res.status_code == 200 and root_res.text:
                                extracted = extract_stream_sources_from_html(root_res.text[:262144], portal_url)
                                for cand_http in extracted.get("http_candidates", []):
                                    cand_parsed = urlparse(cand_http)
                                    cand_path = (cand_parsed.path or "/") + (f"?{cand_parsed.query}" if cand_parsed.query else "")
                                    ok_c, resolved_c, stype_c = await self.probe_http_path(
                                        ip, port, cand_path, timeout=probe_timeout, is_https=(s == "https")
                                    )
                                    tested_count += 1
                                    if ok_c:
                                        return {
                                            "resolved": True,
                                            "protocol": s,
                                            "stream_url": resolved_c,
                                            "stream_type": stype_c,
                                            "vendor": vendor_key,
                                            "tested_count": tested_count,
                                            "auth": "extracted_from_portal",
                                            "message": f"Extracted live {stype_c.upper()} stream source from isolated web portal ({proto_note}): {resolved_c}"
                                        }

                                for cand_rtsp in extracted.get("rtsp_candidates", []):
                                    rp = urlparse(cand_rtsp)
                                    r_port = rp.port or 554
                                    r_path = (rp.path or "/") + (f"?{rp.query}" if rp.query else "")
                                    ok_r, _, resolved_r, _, _ = await self.probe_rtsp_path(ip, r_port, r_path, timeout=1.0)
                                    tested_count += 1
                                    if ok_r:
                                        return {
                                            "resolved": True,
                                            "protocol": "rtsp",
                                            "stream_url": resolved_r,
                                            "vendor": vendor_key,
                                            "tested_count": tested_count,
                                            "auth": "extracted_from_portal",
                                            "message": f"Extracted live RTSP stream source from isolated web portal: {resolved_r}"
                                        }

                            return {
                                "resolved": True,
                                "protocol": f"{s}_ui",
                                "stream_url": f"{s}://{ip}:{port}/",
                                "stream_type": "web_ui",
                                "vendor": vendor_key,
                                "tested_count": tested_count + 1,
                                "auth": "web_interface",
                                "message": f"Isolated Camera Web Portal on port {port} ({proto_note}) — Code Execution Blocked"
                            }
                except HTTPException:
                    raise
                except Exception as e:
                    err_str = str(e).lower()
                    if s == "https" and any(k in err_str for k in ["ssl", "record", "version", "handshake", "connect"]):
                        continue
                    break

        return {
            "resolved": False,
            "reachable": rtsp_reachable,
            "hard_refused": hard_refused,
            "protocol": "unknown",
            "stream_url": current_url,
            "vendor": vendor_key,
            "tested_count": tested_count,
            "message": f"Probed {tested_count} vendor paths for {vendor_key.upper()}. Target host did not respond with 200 OK."
        }

rtsp_resolver = VendorRtspResolver()

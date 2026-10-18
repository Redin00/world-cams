import asyncio
import base64
import hashlib
import logging
import re
from typing import Optional, List, Tuple, Dict, Any
from urllib.parse import urlparse
import httpx
from backend.config import get_legacy_ssl_context

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
            "/Streaming/Channels/101",
            "/cam/realmonitor?channel=1&subtype=0",
            "/stream1",
            "/Preview_01_main",
            "/profile1/media.smp",
            "/media/video1",
            "/axis-media/media.amp",
            "/video?inst=1",
            "/live.sdp",
            "/live/ch0",
            "/ch0_0.h264",
            "/videoMain",
            "/live0",
            "/h264/ch01/main/av_stream",
            "/CH001.sdp",
            "/0",
            "/MediaInput/h264",
            "/live"
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
            for cred in creds:
                success, auth_url = await self.probe_rtsp_digest_auth(
                    ip, port, target_path, cred, realm, nonce, timeout=timeout
                )
                if success:
                    logger.info(f"[Auto-Auth] RTSP {ip}:{port}{target_path} authenticated for {vendor_key.upper()} with default credentials: {cred[0]}:***")
                    return True, auth_url, cred
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

                # Test remaining credentials on this verified 401 endpoint
                for cred in active_creds[1:]:
                    success, next_auth_url = await self.probe_rtsp_digest_auth(
                        ip, port, cand_path, cred, cur_realm, cur_nonce, timeout=timeout
                    )
                    if success:
                        logger.info(f"[Auto-Auth] RTSP {ip}:{port}{cand_path} authenticated for {vendor_key.upper()} with default credentials: {cred[0]}:***")
                        return True, next_auth_url, cred
                # Continue to next candidate path in case another path accepts credentials
                continue

            if code == 0:
                # Host/port unreachable or timed out; do not waste time on further paths
                break

        return False, f"rtsp://{ip}:{port}{fallback_path}", None

    @staticmethod
    def _compute_digest(method: str, uri: str, user: str, pwd: str, realm: str, nonce: str) -> str:
        """Compute RFC 2069 / RFC 2617 standard RTSP Digest Authentication header."""
        ha1 = hashlib.md5(f"{user}:{realm}:{pwd}".encode("utf-8")).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode("utf-8")).hexdigest()
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode("utf-8")).hexdigest()
        return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"'

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
        Handles unauthenticated access, RFC standard RTSP Digest Authentication, and Basic Authentication.
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

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout
            )
            writer.write(req_initial.encode())
            await writer.drain()

            raw_resp = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            resp_str = raw_resp.decode(errors="ignore")

            if resp_str.startswith("RTSP/1.0 200"):
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True, 200, uri, None, None

            # If rejected with 401 Unauthorized, extract Digest realm and nonce (or Basic realm)
            realm, nonce = None, None
            if "401 Unauthorized" in resp_str:
                realm_m = re.search(r'realm="([^"]*)"', resp_str, re.IGNORECASE)
                nonce_m = re.search(r'nonce="([^"]+)"', resp_str, re.IGNORECASE)
                if realm_m:
                    realm = realm_m.group(1)
                if nonce_m:
                    nonce = nonce_m.group(1)

                # If creds provided, perform Digest or Basic Auth handshake
                if creds:
                    user, pwd = creds
                    if realm is not None and nonce is not None:
                        auth_hdr = self._compute_digest("DESCRIBE", uri, user, pwd, realm, nonce)
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
                        raw_auth = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                        resp_auth = raw_auth.decode(errors="ignore")
                    except Exception:
                        resp_auth = ""

                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass

                    if resp_auth.startswith("RTSP/1.0 200"):
                        auth_url = f"rtsp://{user}:{pwd}@{host}:{port}{path}"
                        return True, 200, auth_url, realm, nonce

                    # If server closed the socket immediately after 401 (resp_auth is empty), retry on a new socket
                    if not resp_auth:
                        ok_direct, direct_url = await self.probe_rtsp_digest_auth(
                            host, port, path, creds, realm, nonce, timeout=timeout
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
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout
            )
            writer.write(req.encode())
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            resp = raw.decode(errors="ignore")

            if resp.startswith("RTSP/1.0 200"):
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True, f"rtsp://{user}:{pwd}@{host}:{port}{path}"

            # If server issued a fresh per-connection Digest nonce on CSeq: 1, authenticate on CSeq: 2
            if "401 Unauthorized" in resp:
                new_realm_m = re.search(r'realm="([^"]*)"', resp, re.IGNORECASE)
                new_nonce_m = re.search(r'nonce="([^"]+)"', resp, re.IGNORECASE)
                new_realm = new_realm_m.group(1) if new_realm_m else (realm or "")
                new_nonce = new_nonce_m.group(1) if new_nonce_m else None

                if new_nonce and new_nonce != nonce:
                    auth_hdr2 = self._compute_digest("DESCRIBE", uri, user, pwd, new_realm, new_nonce)
                    req2 = (
                        f"DESCRIBE {uri} RTSP/1.0\r\n"
                        f"CSeq: 2\r\n"
                        f"Authorization: {auth_hdr2}\r\n"
                        f"User-Agent: {user_agent}\r\n"
                        f"Accept: application/sdp\r\n\r\n"
                    )
                    writer.write(req2.encode())
                    await writer.drain()
                    raw2 = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                    resp2 = raw2.decode(errors="ignore")
                    if resp2.startswith("RTSP/1.0 200"):
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
        Only accepts genuine images or MJPEG/video streams (rejects HTML!).
        Uses stream reading so infinite MJPEG video streams don't hang waiting for EOF.
        Includes automatic fallback from HTTPS to plaintext HTTP if SSL_ERROR_RX_RECORD_TOO_LONG occurs on port 443.
        """
        schemes = ["https", "http"] if (port == 443 or is_https) else ["http"]
        for scheme in schemes:
            url = f"{scheme}://{host}:{port}{path}"
            verify_arg = get_legacy_ssl_context() if scheme == "https" else False
            try:
                async with httpx.AsyncClient(timeout=timeout, verify=verify_arg, follow_redirects=True) as client:
                    async with client.stream("GET", url) as res:
                        ctype = res.headers.get("content-type", "").lower()
                        if res.status_code == 200:
                            if "multipart/x-mixed-replace" in ctype or "mjpg" in ctype or "video" in ctype:
                                return True, url, "mjpeg"
                            if "image" in ctype or "jpeg" in ctype or "png" in ctype:
                                return True, url, "jpeg"
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
        continuous: bool = True
    ) -> Dict[str, Any]:
        """
        Automated Vendor RTSP Profile & Continuous Resolver Engine.
        Executes continuous multi-path probing across vendor profiles and SmartRTSP catalog.
        - Step 1: Tests ALL candidate paths WITHOUT credentials first.
        - Step 2: Only if no path works without credentials, tests default vendor credentials across candidate paths.
        - Falls back to HTTP MJPEG video / snapshot validation if RTSP is unreachable.
        """
        vendor_key = self.detect_vendor_key(product)
        profile = VENDOR_RTSP_PROFILES.get(vendor_key, VENDOR_RTSP_PROFILES["generic"])
        generic_profile = VENDOR_RTSP_PROFILES["generic"]

        # Build prioritized unique list of candidate paths:
        candidate_paths: List[str] = []

        # 1. If current_url has a specific path (and not a generic /live or /onvif1 placeholder), check it first
        if current_url:
            try:
                parsed = urlparse(current_url)
                if parsed.path and parsed.path not in ("/", "/live", "/onvif1", ""):
                    full_p = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                    candidate_paths.append(full_p)
            except Exception:
                pass

        # 2. Add detected vendor profile paths
        for p in profile.get("paths", []):
            if p not in candidate_paths:
                candidate_paths.append(p)

        # 3. If continuous probing requested (or vendor is generic), add all remaining SmartRTSP paths
        if continuous or vendor_key == "generic":
            for p in GENERIC_FALLBACK_PATHS:
                if p not in candidate_paths:
                    candidate_paths.append(p)

        # Build credentials list directly from vendor profile (with generic fallback if undetermined)
        creds_list: List[Tuple[str, str]] = self.get_vendor_credentials(product or vendor_key)
        if vendor_key == "generic":
            for c in generic_profile.get("creds", []):
                if c not in creds_list:
                    creds_list.append(c)

        tested_count = 0
        auth_required_candidates: List[Tuple[str, Optional[str], Optional[str]]] = []  # (path, realm, nonce)

        # =====================================================================
        # Phase 1: RTSP Probing (Ports 554, 8554 or explicit rtsp:// URLs)
        # Step 1A: Probe ALL candidate paths WITHOUT credentials first!
        # =====================================================================
        if port in (554, 8554) or (port not in (443, 80, 8080, 8050, 8000) and "rtsp://" in current_url):
            logger.info(f"Continuous RTSP probing (unauthenticated pass) for {ip}:{port} (Vendor: {vendor_key.upper()}, Candidates: {len(candidate_paths)})...")

            # Process ALL candidate paths in concurrent batches of 8 without credentials
            BATCH_SIZE = 8
            for i in range(0, len(candidate_paths), BATCH_SIZE):
                batch = candidate_paths[i:i + BATCH_SIZE]
                tasks = [self.probe_rtsp_path(ip, port, path, creds=None, timeout=1.0) for path in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                tested_count += len(batch)

                batch_reachable = False
                for res in results:
                    if isinstance(res, tuple):
                        success, code, url, realm, nonce = res
                        if code != 0:
                            batch_reachable = True
                        if success:
                            return {
                                "resolved": True,
                                "protocol": "rtsp",
                                "stream_url": url,
                                "vendor": vendor_key,
                                "tested_count": tested_count,
                                "auth": "unauthenticated",
                                "message": f"Resolved unauthenticated RTSP stream on path {urlparse(url).path}"
                            }
                        elif code == 401:
                            p_only = urlparse(url).path + (f"?{urlparse(url).query}" if urlparse(url).query else "")
                            if not any(existing[0] == p_only for existing in auth_required_candidates):
                                auth_required_candidates.append((p_only, realm, nonce))

                # Only stop early if the target IP:port is completely unreachable (all sockets timed out / refused)
                if not batch_reachable:
                    break

            # Step 1B: ONLY AFTER all candidate paths were tested without credentials,
            # try default vendor credentials across the paths that required authentication
            if auth_required_candidates and creds_list:
                for target_p, target_realm, target_nonce in auth_required_candidates:
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
                            "protocol": "rtsp",
                            "stream_url": auth_url,
                            "vendor": vendor_key,
                            "tested_count": tested_count,
                            "auth": "default_creds",
                            "matched_cred": used_cred,
                            "message": f"Resolved authenticated RTSP stream with default credentials ({used_cred[0]}:{used_cred[1]}): {auth_url}"
                        }

        # =====================================================================
        # Phase 2: HTTP/HTTPS Web Camera Probing (Ports 443, 80, 8080, 8050, 8000 or RTSP fallback)
        # Continuous batch probing across live MJPEG / JPEG snapshot paths
        # =====================================================================
        if port in (443, 80, 8080, 8050, 8000) or not current_url.startswith("rtsp://"):
            is_https = (port == 443) or current_url.startswith("https://")
            scheme = "https" if is_https else "http"
            probe_timeout = 2.2 if is_https else 1.5
            logger.info(f"Parallel {scheme.upper()} Web stream probing for {ip}:{port} (timeout={probe_timeout}s)...")
            http_tasks = [self.probe_http_path(ip, port, path, timeout=probe_timeout, is_https=is_https) for path in WEB_SNAPSHOT_PATHS]
            http_results = await asyncio.gather(*http_tasks, return_exceptions=True)
            tested_count += len(WEB_SNAPSHOT_PATHS)

            for h_res in http_results:
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
            schemes_to_test = ["https", "http"] if is_https else ["http"]
            for s in schemes_to_test:
                verify_arg = get_legacy_ssl_context() if s == "https" else False
                try:
                    async with httpx.AsyncClient(timeout=1.5, verify=verify_arg, follow_redirects=True) as client:
                        root_res = await client.get(f"{s}://{ip}:{port}/")
                        if root_res.status_code in (200, 301, 302, 401):
                            proto_note = "HTTPS" if s == "https" else "Plaintext HTTP (SSL Fallback)"
                            return {
                                "resolved": True,
                                "protocol": f"{s}_ui",
                                "stream_url": f"{s}://{ip}:{port}/",
                                "stream_type": "web_ui",
                                "vendor": vendor_key,
                                "tested_count": tested_count + 1,
                                "auth": "web_interface",
                                "message": f"Active Camera Web Interface on port {port} ({proto_note})"
                            }
                except Exception as e:
                    err_str = str(e).lower()
                    if s == "https" and any(k in err_str for k in ["ssl", "record", "version", "handshake", "connect"]):
                        continue
                    break

        return {
            "resolved": False,
            "protocol": "unknown",
            "stream_url": current_url,
            "vendor": vendor_key,
            "tested_count": tested_count,
            "message": f"Probed {tested_count} vendor paths for {vendor_key.upper()}. Target host did not respond with 200 OK."
        }

rtsp_resolver = VendorRtspResolver()

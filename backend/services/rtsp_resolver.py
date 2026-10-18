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

# Vendor default credential table according to manufacturer specifications:
# Hikvision: admin | (blank) or 12345
# Dahua: admin | admin
# Axis: root | (blank)
# Amcrest: admin | admin
# Lorex: admin | (blank) or 12345
# Foscam: admin | admin
# Reolink: admin | (blank)
# Hanwha (Samsung): admin | 4356
# Bosch: admin | admin
# Vivotek: admin | (blank)
VENDOR_DEFAULT_CREDS_TABLE: Dict[str, List[Tuple[str, str]]] = {
    "hikvision": [("admin", ""), ("admin", "12345")],
    "dahua": [("admin", "admin")],
    "axis": [("root", "")],
    "amcrest": [("admin", "admin")],
    "lorex": [("admin", ""), ("admin", "12345")],
    "foscam": [("admin", "admin")],
    "reolink": [("admin", "")],
    "hanwha": [("admin", "4356")],
    "samsung": [("admin", "4356")],
    "bosch": [("admin", "admin")],
    "vivotek": [("admin", "")],
}

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
        if any(k in p for k in ["hikvision", "annke", "swann", "bluestar", "tovision", "digital watchdog", "flir"]):
            return "hikvision"
        if any(k in p for k in ["dahua", "imou"]):
            return "dahua"
        if "reolink" in p:
            return "reolink"
        if any(k in p for k in ["tapo", "tp-link", "tplink", "mercury", "sunell", "tenda", "honeywell", "lilin"]):
            return "tapo"
        if any(k in p for k in ["uniview", "unv"]):
            return "uniview"
        if any(k in p for k in ["samsung", "hanwha", "techwin", "wisenet"]):
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
        Get vendor default credentials list based on detected vendor.
        Filters by manufacturer if found in the default credentials table:
        Hikvision: admin | (blank) or 12345
        Dahua: admin | admin
        Axis: root | (blank)
        Amcrest: admin | admin
        Lorex: admin | (blank) or 12345
        Foscam: admin | admin
        Reolink: admin | (blank)
        Hanwha (Samsung): admin | 4356
        Bosch: admin | admin
        Vivotek: admin | (blank)
        Get vendor default credentials list directly from VENDOR_RTSP_PROFILES.
        Resolves vendor profile key from product name, banner, or vendor string.
        """
        raw = (product_or_vendor or "").lower().strip()
        if raw in VENDOR_DEFAULT_CREDS_TABLE:
            return list(VENDOR_DEFAULT_CREDS_TABLE[raw])
        raw_key = (product_or_vendor or "").lower().strip()
        if raw_key in VENDOR_RTSP_PROFILES and "creds" in VENDOR_RTSP_PROFILES[raw_key]:
            return list(VENDOR_RTSP_PROFILES[raw_key]["creds"])

        vendor_key = self.detect_vendor_key(product_or_vendor)
        if vendor_key in VENDOR_DEFAULT_CREDS_TABLE:
            return list(VENDOR_DEFAULT_CREDS_TABLE[vendor_key])
        profile = VENDOR_RTSP_PROFILES.get(vendor_key)
        if profile and "creds" in profile:
            return list(profile["creds"])

        if vendor_key in VENDOR_RTSP_PROFILES and "creds" in VENDOR_RTSP_PROFILES[vendor_key]:
            return list(VENDOR_RTSP_PROFILES[vendor_key]["creds"])

        return list(VENDOR_RTSP_PROFILES.get("generic", {}).get("creds", [("admin", "admin"), ("admin", "12345"), ("admin", "")]))

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
        Retry connecting to RTSP stream with a default password list filtered by vendor.
        If realm/nonce are known, tests credentials directly.
        Otherwise probes the target path or vendor paths to negotiate Digest/Basic auth.
        Returns (is_success, resolved_rtsp_url, matched_credential).
        """
        vendor_key = self.detect_vendor_key(product)
        creds = self.get_vendor_credentials(product)

        target_path = path or ""
        if not target_path or target_path in ("/", "/live"):
            profile = VENDOR_RTSP_PROFILES.get(vendor_key, VENDOR_RTSP_PROFILES["generic"])
            target_path = profile.get("paths", ["/Streaming/Channels/101"])[0]

        # 1. If realm and nonce are already provided from a 401 probe
        if realm and nonce:
            for cred in creds:
                success, auth_url = await self.probe_rtsp_digest_auth(
                    ip, port, target_path, cred, realm, nonce, timeout=timeout
                )
                if success:
                    logger.info(f"[Auto-Auth] RTSP {ip}:{port}{target_path} authenticated for {vendor_key.upper()} with default credentials: {cred[0]}:***")
                    return True, auth_url, cred

        # 2. If realm/nonce not provided or direct probes didn't succeed, probe path with each cred using two-step CSeq handshake
        for cred in creds:
            is_ok, code, auth_url, cur_realm, cur_nonce = await self.probe_rtsp_path(
                ip, port, target_path, creds=cred, timeout=timeout
            )
            if is_ok:
                logger.info(f"[Auto-Auth] RTSP {ip}:{port}{target_path} authenticated for {vendor_key.upper()} with default credentials: {cred[0]}:***")
                return True, auth_url, cred

        # None of the default credentials succeeded
        return False, f"rtsp://{ip}:{port}{target_path}", None

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
        Handles both unauthenticated access and RFC standard RTSP Digest Authentication.
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

            # If rejected with 401 Unauthorized, extract Digest realm and nonce
            realm, nonce = None, None
            if "401 Unauthorized" in resp_str:
                realm_m = re.search(r'realm="([^"]+)"', resp_str)
                nonce_m = re.search(r'nonce="([^"]+)"', resp_str)
                if realm_m:
                    realm = realm_m.group(1)
                if nonce_m:
                    nonce = nonce_m.group(1)

                # If creds provided and realm/nonce available, perform Digest Auth handshake
                if creds and realm and nonce:
                    user, pwd = creds
                    auth_hdr = self._compute_digest("DESCRIBE", uri, user, pwd, realm, nonce)
                    req_auth = (
                        f"DESCRIBE {uri} RTSP/1.0\r\n"
                        f"CSeq: 2\r\n"
                        f"Authorization: {auth_hdr}\r\n"
                        f"User-Agent: {user_agent}\r\n"
                        f"Accept: application/sdp\r\n\r\n"
                    )
                    writer.write(req_auth.encode())
                    await writer.drain()
                    raw_auth = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                    resp_auth = raw_auth.decode(errors="ignore")
                    if resp_auth.startswith("RTSP/1.0 200"):
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass
                        auth_url = f"rtsp://{user}:{pwd}@{host}:{port}{path}"
                        return True, 200, auth_url, realm, nonce

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
        """Direct single socket probe testing credentials against a known realm/nonce (Digest or Basic)."""
        uri = f"rtsp://{host}:{port}{path}"
        user, pwd = creds
        if realm and nonce:
            auth_hdr = self._compute_digest("DESCRIBE", uri, user, pwd, realm, nonce)
        else:
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("utf-8")
            auth_hdr = f"Basic {token}"
        req = (
            f"DESCRIBE {uri} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"Authorization: {auth_hdr}\r\n"
            f"User-Agent: Lavf/58.76.100\r\n"
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
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            resp = raw.decode(errors="ignore")
            if resp.startswith("RTSP/1.0 200"):
                return True, f"rtsp://{user}:{pwd}@{host}:{port}{path}"
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
        - Prioritizes vendor-specific paths when known.
        - If vendor is unknown or stream fails, continuously tests candidate paths in fast concurrent batches.
        - Features RFC RTSP Digest Auth negotiation for cameras with default factory credentials.
        - Falls back to HTTP MJPEG video / snapshot validation if RTSP is unreachable.
        """
        vendor_key = self.detect_vendor_key(product)
        profile = VENDOR_RTSP_PROFILES.get(vendor_key, VENDOR_RTSP_PROFILES["generic"])
        generic_profile = VENDOR_RTSP_PROFILES["generic"]

        # Build prioritized unique list of candidate paths:
        candidate_paths: List[str] = []

        # 1. If current_url has a specific path (and not a generic /live), check it first
        if current_url:
            try:
                parsed = urlparse(current_url)
                if parsed.path and parsed.path not in ("/", "/live", ""):
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
        auth_required_candidates: List[Tuple[str, str, str]] = []  # (path, realm, nonce)

        # =====================================================================
        # Phase 1: RTSP Probing (Ports 554, 8554 or explicit rtsp:// URLs)
        # Continuous batch probing across candidate paths
        # =====================================================================
        if port in (554, 8554) or (port not in (443, 80, 8080, 8050, 8000) and "rtsp://" in current_url):
            logger.info(f"Continuous RTSP probing for {ip}:{port} (Vendor: {vendor_key.upper()}, Candidates: {len(candidate_paths)})...")

            # Process candidate paths in concurrent batches of 8 for optimal speed without socket flooding
            BATCH_SIZE = 8
            for i in range(0, len(candidate_paths), BATCH_SIZE):
                batch = candidate_paths[i:i + BATCH_SIZE]
                tasks = [self.probe_rtsp_path(ip, port, path, creds=None, timeout=1.2) for path in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                tested_count += len(batch)

                for res in results:
                    if isinstance(res, tuple):
                        success, code, url, realm, nonce = res
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
                        elif code == 401 and realm and nonce:
                            p_only = urlparse(url).path + (f"?{urlparse(url).query}" if urlparse(url).query else "")
                            auth_required_candidates.append((p_only, realm, nonce))

                # If we've already found valid 401 candidates, test credentials before going deeper
                if auth_required_candidates:
                    break

            # If any candidate paths responded with 401 Unauthorized, test default vendor credentials
            if auth_required_candidates and creds_list:
                for target_p, target_realm, target_nonce in auth_required_candidates[:3]:
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

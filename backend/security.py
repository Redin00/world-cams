import hashlib
import hmac
import html
import ipaddress
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Optional, List, Tuple, Dict, Any
from urllib.parse import urlparse, urljoin, unquote, unquote_plus

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings

logger = logging.getLogger("worldcams.security")

# =====================================================================
# 1. Cryptographic CSRF Token Management & Constant-Time Auth
# =====================================================================

_CSRF_SECRET = hashlib.sha256(
    f"worldcams-csrf-key-{settings.ADMIN_TOKEN}-{secrets.token_hex(16)}".encode("utf-8")
).digest()


def generate_csrf_token() -> str:
    """Generate an HMAC-SHA256 signed CSRF token."""
    nonce = secrets.token_urlsafe(24)
    sig = hmac.new(_CSRF_SECRET, nonce.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def verify_csrf_token(token: Optional[str]) -> bool:
    """Verify an HMAC-SHA256 signed CSRF token in constant time."""
    if not token or "." not in token or len(token) > 256:
        return False
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False
    nonce, sig = parts
    if not nonce or not sig:
        return False
    expected_sig = hmac.new(_CSRF_SECRET, nonce.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def verify_admin_token_constant_time(token: Optional[str]) -> bool:
    """Constant-time comparison of administrator token to prevent timing side-channel attacks."""
    if not token or not settings.ADMIN_TOKEN:
        return False
    provided = token.strip().encode("utf-8")
    expected = settings.ADMIN_TOKEN.strip().encode("utf-8")
    return hmac.compare_digest(provided, expected)


# =====================================================================
# 2. Sliding-Window Rate Limiter & Brute-Force Protection
# =====================================================================

class RateLimiter:
    """
    In-memory per-IP sliding window rate limiter for bot flood mitigation
    and brute-force protection on authentication endpoints.
    """
    def __init__(self):
        self._general_requests: Dict[str, deque] = defaultdict(deque)
        self._admin_failures: Dict[str, deque] = defaultdict(deque)
        self.general_window = 60.0
        self.general_max_requests = 360  # Max 360 API requests per minute per IP
        self.auth_window = 60.0
        self.auth_max_failures = 8       # Max 8 failed admin auth attempts per minute per IP

    def _prune(self, q: deque, now: float, window: float):
        while q and (now - q[0]) > window:
            q.popleft()

    def check_general_rate(self, client_ip: str) -> bool:
        now = time.monotonic()
        q = self._general_requests[client_ip]
        self._prune(q, now, self.general_window)
        if len(q) >= self.general_max_requests:
            return False
        q.append(now)
        return True

    def is_auth_locked_out(self, client_ip: str) -> bool:
        now = time.monotonic()
        q = self._admin_failures[client_ip]
        self._prune(q, now, self.auth_window)
        return len(q) >= self.auth_max_failures

    def record_auth_failure(self, client_ip: str):
        now = time.monotonic()
        q = self._admin_failures[client_ip]
        self._prune(q, now, self.auth_window)
        q.append(now)

    def clear_auth_failures(self, client_ip: str):
        self._admin_failures.pop(client_ip, None)


rate_limiter = RateLimiter()


# =====================================================================
# 3. WAF Signatures: Path Traversal, Bot Probes, SQLi, XSS, CmdInj
# =====================================================================

# Automated vulnerability scanner User-Agents
BOT_SCANNER_UA_RE = re.compile(
    r"(?i)\b(sqlmap|nikto|nmap|masscan|nuclei|dirbuster|gobuster|wfuzz|ffuf|"
    r"acunetix|nessus|openvas|havij|commix|zgrab|morfeus|jaeles|xray|hydra|"
    r"medusa|patator|burpcollaborator|dirsearch|feroxbuster)\b"
)

# Sensitive files and common automated bot path probes
BOT_PROBE_PATHS_RE = re.compile(
    r"(?i)(^|/)"
    r"(\.env(\..+)?|\.git(/|$)|\.svn(/|$)|\.hg(/|$)|\.htaccess|\.htpasswd|"
    r"\.ds_store|\.aws(/|$)|\.ssh(/|$)|world_cams\.db|mediamtx\.yml|"
    r"wp-admin(/|$)|wp-login\.php|wp-content(/|$)|wp-includes(/|$)|xmlrpc\.php|"
    r"phpmyadmin(/|$)|pma(/|$)|adminer(\.php)?|etc/passwd|etc/shadow|etc/hosts|"
    r"proc/self|windows/win\.ini|boot\.ini|vendor/phpunit|cgi-bin(/|$)|"
    r"actuator(/|$)|solr(/|$)|eval-stdin\.php)"
)

# Path traversal sequences (raw & decoded)
PATH_TRAVERSAL_RE = re.compile(
    r"(?i)(\.\./|\.\.\\|/\.\.|\\\.\.|%2e%2e|%252e%252e|\.\.;|\x00|%00)"
)

# SQL Injection signatures
SQLI_PAYLOAD_RE = re.compile(
    r"(?i)("
    r"\bunion\s+(all\s+)?select\b|"
    r"\bselect\s+.{1,80}?\bfrom\s+(camera_feeds|sqlite_master|information_schema|users|pg_catalog)\b|"
    r"\b(drop|alter|truncate)\s+(table|database)\b|"
    r"\binsert\s+into\s+\w+|"
    r"\bdelete\s+from\s+\w+|"
    r"\bupdate\s+\w+\s+set\b|"
    r"\bsqlite_master\b|\binformation_schema\b|"
    r"\b(sleep|benchmark|pg_sleep)\s*\(\s*\d+|"
    r"\bwaitfor\s+delay\b|"
    r"('|\")\s*(or|and)\s*('|\")?\d+('|\")?\s*=\s*('|\")?\d+|"
    r"('|\")\s*or\s+('|\")?[a-z0-9_]+('|\")?\s*=\s*('|\")?[a-z0-9_]+|"
    r";\s*(drop|select|insert|update|delete|attach|detach|pragma)\b|"
    r"/\*![0-9]*\s"
    r")"
)

# Cross-Site Scripting (XSS) & Dangerous URI signatures in query/path inputs
XSS_PAYLOAD_RE = re.compile(
    r"(?i)("
    r"<\s*script\b|"
    r"</\s*script\s*>|"
    r"javascript\s*:|"
    r"vbscript\s*:|"
    r"data\s*:\s*text/html|"
    r"\bon(error|load|mouseover|focus|click|animationstart|toggle)\s*=|"
    r"<\s*(iframe|object|embed|applet|meta|base|svg|math|form)\b"
    r")"
)

# Command Injection signatures in query/path inputs
CMD_INJECTION_RE = re.compile(
    r"(?i)("
    r"(;|\|\||&&|\|)\s*(cat|ls|id|whoami|uname|wget|curl|nc|netcat|bash|sh|zsh|python|perl|php|ruby|chmod|chown|rm)\b|"
    r"\$\([^)]{1,120}\)|"
    r"`[^`]{1,120}`"
    r")"
)

# Safe identifier pattern for feed_id and stream_name
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


def validate_safe_identifier(value: str, field_name: str = "identifier") -> str:
    """Ensure IDs and stream names cannot contain traversal, SQLi, or shell metacharacters."""
    if not value or not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    cleaned = value.strip()
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise HTTPException(status_code=400, detail=f"Path traversal blocked in {field_name}")
    if not SAFE_IDENTIFIER_RE.match(cleaned):
        raise HTTPException(status_code=400, detail=f"Malformed {field_name} format")
    return cleaned


def escape_sql_like(value: str, max_len: int = 120) -> str:
    """
    Sanitize user search/filter strings for SQL LIKE/ILIKE clauses.
    Rejects SQLi/XSS attack payloads, truncates length, and escapes %, _, and \\ wildcards.
    """
    if not value:
        return ""
    trimmed = value.strip()[:max_len]
    if SQLI_PAYLOAD_RE.search(trimmed) or XSS_PAYLOAD_RE.search(trimmed):
        raise HTTPException(status_code=400, detail="Malicious query pattern blocked by security filter")
    return (
        trimmed
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def sanitize_text_field(value: Optional[str], max_len: int = 500) -> str:
    """
    Strip HTML tags, script payloads, and control characters from untrusted text fields
    (such as Shodan banners, camera product names, organizations, or notes) to prevent Stored XSS.
    """
    if not value:
        return ""
    text = str(value)[:max_len]
    # Remove <script>...</script> and <style>...</style> blocks completely
    text = re.sub(r"(?is)<\s*(script|style|iframe|object|embed|svg)[^>]*>.*?<\s*/\s*\1\s*>", "", text)
    # Strip any remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove dangerous URI schemes or inline event handlers
    text = re.sub(r"(?i)(javascript\s*:|vbscript\s*:|data\s*:\s*text/html)", "", text)
    # Remove null bytes
    text = text.replace("\x00", "")
    return text.strip()


# =====================================================================
# 4. SSRF Protection & Camera Target Isolation
# =====================================================================

# Ports that must never be probed or proxied (internal services, DBs, SSH, MediaMTX Control API)
FORBIDDEN_TARGET_PORTS = {
    21, 22, 23, 25, 53, 69, 110, 111, 135, 137, 138, 139, 143, 161, 389, 445,
    512, 513, 514, 636, 873, 993, 995, 1080, 1433, 1521, 2049, 2375, 2376, 2379,
    3306, 3389, 4444, 5432, 5672, 5900, 5984, 6379, 6443, 7001, 8500, 9000,
    9042, 9092, 9200, 9300, 9997, 11211, 27017, 27018, 50000
}

ALLOWED_SCHEMES = {"http", "https", "rtsp", "rtsps"}
ALLOWED_DEMO_HOSTS = {"rtsp.stream", "images.unsplash.com"}


def validate_camera_ip(ip_str: str, port: Optional[int] = None) -> str:
    """
    Validate that a camera target IP is a legitimate IPv4/IPv6 address and not
    an SSRF target (such as Cloud Metadata 169.254.169.254, link-local, multicast,
    unspecified 0.0.0.0, or internal infrastructure ports).
    """
    if not ip_str or not isinstance(ip_str, str):
        raise HTTPException(status_code=400, detail="Missing camera IP address")

    host = ip_str.strip().strip("[]")
    if host.lower() in ALLOWED_DEMO_HOSTS:
        return host

    # Reject domain names (prevents DNS rebinding & internal hostname SSRF like localhost / metadata.google.internal)
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"SSRF Protection: Invalid camera IP address '{host}': hostnames and DNS rebinding targets are blocked."
        )

    # Block Cloud Metadata / Link-Local (169.254.0.0/16, fe80::/10), Multicast, Unspecified (0.0.0.0), Reserved
    if (
        ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
        or str(ip_obj).startswith("169.254.")
    ):
        raise HTTPException(
            status_code=403,
            detail=f"SSRF Protection: Access to restricted address range ({ip_obj}) is forbidden."
        )

    # Validate port if provided
    if port is not None:
        if not (1 <= int(port) <= 65535):
            raise HTTPException(status_code=400, detail="Port number out of valid range (1-65535)")
        if int(port) in FORBIDDEN_TARGET_PORTS:
            raise HTTPException(
                status_code=403,
                detail=f"SSRF Protection: Port {port} is restricted and cannot be accessed."
            )
        # Prevent loopback self-proxy into the FastAPI backend port (8000)
        if ip_obj.is_loopback and int(port) == int(settings.APP_PORT):
            raise HTTPException(
                status_code=403,
                detail="SSRF Protection: Loopback access to application server port is forbidden."
            )

    return str(ip_obj)


def validate_camera_url(url: str, allowed_schemes: Optional[set] = None) -> str:
    """
    Validate a full camera stream/portal URL against SSRF, Path Traversal, and Scheme Injection.
    """
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=400, detail="Empty target URL")

    cleaned = url.strip()
    if len(cleaned) > 2048 or "\x00" in cleaned or "\r" in cleaned or "\n" in cleaned:
        raise HTTPException(status_code=400, detail="Malformed or oversized URL")

    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    valid_schemes = allowed_schemes or ALLOWED_SCHEMES
    if scheme not in valid_schemes:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported or unsafe URL scheme '{scheme}'. Allowed: {sorted(valid_schemes)}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="URL missing valid host/IP")

    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid port in URL")

    if port is None:
        port = 443 if scheme in ("https", "rtsps") else (554 if scheme == "rtsp" else 80)

    validate_camera_ip(hostname, port)

    # Block path traversal inside camera URL path
    unquoted_path = unquote(unquote(parsed.path or ""))
    if ".." in unquoted_path or "\\" in unquoted_path:
        raise HTTPException(status_code=400, detail="Path traversal sequence blocked in target URL")

    return cleaned


# =====================================================================
# 5. Anti-Honeypot Portal Isolation & Stream Source Extractor
# =====================================================================

# Binary signatures of dangerous executable / script payloads disguised as camera streams
MALWARE_MAGIC_PREFIXES = (
    b"MZ",                  # Windows PE Executable / DLL
    b"\x7fELF",             # Linux ELF Executable
    b"PK\x03\x04",          # ZIP / JAR / APK Archive
    b"\xca\xfe\xba\xbe",    # Mach-O / Java Class
    b"\xfe\xed\xfa\xce",    # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",    # Mach-O 64-bit
    b"#!",                  # Shell / Script shebang
)

HTML_OR_SCRIPT_PREFIXES = (
    b"<!doctype",
    b"<html",
    b"<head",
    b"<body",
    b"<script",
    b"<iframe",
    b"<svg",
    b"<?xml",
    b"<object",
    b"<embed",
    b"<meta",
)

FORBIDDEN_STREAM_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "text/xml",
    "application/xml",
    "image/svg+xml",
    "application/x-msdownload",
    "application/x-executable",
    "application/x-sh",
    "application/wasm",
    "application/octet-stream",
)

# Regex patterns to find embedded camera stream paths inside raw HTML / JS config text
STREAM_URL_REGEX = re.compile(
    r"""(?ix)
    (?:src|href|data|url|stream|video|path|snapshot|mjpeg|cam_url)\s*[:=]\s*["']
    (
        (?:https?://[^"'<>\s]{4,250}|/[a-zA-Z0-9_./?&=%-]+|[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./?&=%-]+)
    )
    ["']
    """
)

DIRECT_MEDIA_PATH_REGEX = re.compile(
    r"""(?ix)
    ["']
    (
        (?:https?://[^"'<>\s]{4,250}|/?[a-zA-Z0-9_./-]*)
        (?:
            nphMotionJpeg|
            faststream\.jpg|
            axis-cgi/mjpg/video\.cgi|
            axis-cgi/jpg/image\.cgi|
            mjpg/video\.mjpg|
            cgi-bin/mjpg/video\.cgi|
            cgi-bin/snapshot\.cgi|
            cgi-bin/hi3510/snap\.cgi|
            SnapshotJPEG|
            ISAPI/Streaming/channels/\d+/picture|
            onvif-http/snapshot|
            webcapture\.jpg|
            videostream\.cgi|
            video\.cgi|
            snapshot\.cgi|
            image\.cgi|
            snapshot\.jpg|
            snap\.jpg|
            live\.jpg|
            mjpeg
        )
        [^"'<>\s]*
    )
    ["']
    """
)

EMBEDDED_RTSP_REGEX = re.compile(
    r"""(?i)["'](rtsps?://[a-zA-Z0-9_.:@-]+/[a-zA-Z0-9_./?&=%-]*)["']"""
)

IGNORED_STATIC_ASSET_EXTS = (".css", ".js", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".swf", ".jar", ".exe", ".dll", ".bat", ".sh")
IGNORED_ASSET_KEYWORDS = ("logo", "icon", "spacer", "blank", "button", "banner_bg", "favicon", "loading", "spinner", "pixel")


def inspect_stream_chunk_safety(first_chunk: bytes, content_type: str = "") -> Tuple[bool, str]:
    """
    Inspect the HTTP Content-Type and the magic bytes of the first stream chunk
    from a camera to block cam-honeypot malware, executables, SVG scripts, or raw HTML.
    Returns (is_safe_media, reason).
    """
    ctype_lower = (content_type or "").lower()

    # 1. Check if content-type is explicitly HTML or script/executable
    for forbidden in FORBIDDEN_STREAM_CONTENT_TYPES:
        if forbidden in ctype_lower:
            # Exception: some cameras send multipart MJPEG or JPEG with generic content-type;
            # only allow application/octet-stream if magic bytes are strictly JPEG (\xff\xd8\xff)
            if forbidden == "application/octet-stream" and first_chunk.lstrip().startswith(b"\xff\xd8\xff"):
                break
            return False, f"Blocked non-media Content-Type '{content_type}'"

    if not first_chunk:
        return False, "Empty response from camera endpoint"

    stripped = first_chunk.lstrip()
    lower_head = stripped[:256].lower()

    # 2. Block binary executable / archive magic bytes (PE MZ, ELF, ZIP/JAR, Mach-O, Shebang)
    for magic in MALWARE_MAGIC_PREFIXES:
        if stripped.startswith(magic):
            return False, "Blocked executable/archive binary signature (Anti-Honeypot Malware Protection)"

    # 3. Block HTML, XML, SVG, or Script tags disguised with a fake image/jpeg Content-Type
    for html_prefix in HTML_OR_SCRIPT_PREFIXES:
        if lower_head.startswith(html_prefix):
            return False, "Blocked HTML/Script/SVG payload disguised as media stream"

    if b"<script" in lower_head or b"javascript:" in lower_head or b"onerror=" in lower_head:
        return False, "Blocked embedded script tag in media stream"

    return True, "Valid media stream"


def extract_stream_sources_from_html(html_text: str, base_url: str) -> Dict[str, Any]:
    """
    Statically parse a camera web portal's raw HTML source without executing any code.
    Extracts candidate video/MJPEG/JPEG stream URLs and RTSP links pointing to the same camera IP.
    Also produces a sanitized, de-armed summary of the portal.
    """
    parsed_base = urlparse(base_url)
    base_host = (parsed_base.hostname or "").lower()
    base_scheme = parsed_base.scheme or "http"
    base_port = parsed_base.port or (443 if base_scheme == "https" else 80)

    candidates: List[str] = []
    rtsp_candidates: List[str] = []

    def _add_http_candidate(raw_ref: str):
        if not raw_ref:
            return
        ref = raw_ref.strip().replace("&amp;", "&")
        if not ref or ref.startswith(("javascript:", "data:", "vbscript:", "mailto:", "#", "about:")):
            return
        full_url = urljoin(base_url, ref)
        p = urlparse(full_url)
        if p.scheme not in ("http", "https"):
            return
        # CRITICAL ISOLATION: Only allow stream sources hosted on the exact same camera IP!
        # Prevents a honeypot portal from redirecting our stream extractor to a 3rd-party malware host or SSRF target.
        if (p.hostname or "").lower() != base_host:
            return
        path_lower = (p.path or "").lower()
        if any(path_lower.endswith(ext) for ext in IGNORED_STATIC_ASSET_EXTS):
            return
        if any(kw in path_lower for kw in IGNORED_ASSET_KEYWORDS):
            return
        if full_url not in candidates:
            candidates.append(full_url)

    # 1. Extract high-confidence direct camera media paths via regex
    for m in DIRECT_MEDIA_PATH_REGEX.finditer(html_text):
        _add_http_candidate(m.group(1))

    # 2. Extract embedded RTSP URLs if present on the same camera IP
    for m in EMBEDDED_RTSP_REGEX.finditer(html_text):
        r_url = m.group(1).strip().replace("&amp;", "&")
        rp = urlparse(r_url)
        if (rp.hostname or "").lower() == base_host and r_url not in rtsp_candidates:
            rtsp_candidates.append(r_url)

    # 3. Parse HTML tags (<video>, <source>, <img>, <embed>) via regex / static scan
    for tag_m in re.finditer(r'(?i)<(img|video|source|embed|iframe)\b[^>]*?\b(src|data-src)\s*=\s*["\']([^"\']+)["\']', html_text):
        tag_name = tag_m.group(1).lower()
        attr_val = tag_m.group(3)
        # Prioritize video/source/embed or img tags that look like camera streams/snapshots
        attr_lower = attr_val.lower()
        if tag_name in ("video", "source", "embed") or any(
            k in attr_lower for k in (
                "mjpg", "mjpeg", "video", "stream", "snap", "shot", "jpg", "jpeg",
                "cgi", "cam", "live", "image", "picture", "channel", "faststream", "nph"
            )
        ):
            _add_http_candidate(attr_val)

    # 4. Also check generic JS config key-values that reference media endpoints
    for m in STREAM_URL_REGEX.finditer(html_text):
        val = m.group(1)
        val_lower = val.lower()
        if any(
            k in val_lower for k in (
                "mjpg", "mjpeg", "video", "stream", "snap", "jpg", "jpeg",
                "cgi", "live", "image", "picture", "channel", "faststream", "nph", ".m3u8"
            )
        ):
            _add_http_candidate(val)

    # Extract page <title> safely as plain text
    title_m = re.search(r"(?is)<\s*title[^>]*>(.*?)<\s*/\s*title\s*>", html_text)
    portal_title = sanitize_text_field(title_m.group(1), max_len=120) if title_m else f"Camera Portal ({base_host}:{base_port})"

    # Count scripts/iframes/applets that were neutralized
    blocked_scripts = len(re.findall(r"(?i)<\s*script\b", html_text))
    blocked_objects = len(re.findall(r"(?i)<\s*(iframe|object|embed|applet)\b", html_text))

    # Build a clean, escaped source preview (first 4000 chars) for safe read-only inspection
    raw_preview = html_text[:4000].replace("\x00", "")

    return {
        "portal_title": portal_title or f"Camera Portal ({base_host}:{base_port})",
        "http_candidates": candidates[:12],
        "rtsp_candidates": rtsp_candidates[:5],
        "blocked_scripts_count": blocked_scripts,
        "blocked_objects_count": blocked_objects,
        "raw_source_preview": raw_preview,
        "escaped_source_preview": html.escape(raw_preview),
    }


# =====================================================================
# 6. Unified Security & Anti-Bot WAF Middleware
# =====================================================================

class SecurityWAFMiddleware(BaseHTTPMiddleware):
    """
    FastAPI Security & Isolation Middleware:
    1. Blocks Path Traversal (../, %2e%2e, null bytes) & sensitive file probes (.env, .git, etc.)
    2. Blocks Automated Bot Scanners (sqlmap, nikto, nuclei, etc.)
    3. Blocks SQL Injection, XSS, and Command Injection payloads in URLs/query strings
    4. Enforces Sliding-Window Rate Limiting per IP
    5. Enforces CSRF Protection (Origin/Referer match, Content-Type enforcement, Double-Submit Cookie)
    6. Injects strict HTTP Security & Isolation Headers (CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy)
    """

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        raw_path_bytes = request.scope.get("raw_path", b"")
        raw_path = raw_path_bytes.decode("latin-1", errors="ignore")
        path = request.url.path
        decoded_path = unquote(unquote(raw_path or path))
        raw_query = request.url.query or ""
        decoded_query = unquote_plus(unquote_plus(raw_query))

        # 1. Block Path Traversal & Sensitive File Probes
        if (
            PATH_TRAVERSAL_RE.search(raw_path)
            or PATH_TRAVERSAL_RE.search(decoded_path)
            or PATH_TRAVERSAL_RE.search(raw_query)
            or PATH_TRAVERSAL_RE.search(decoded_query)
            or BOT_PROBE_PATHS_RE.search(decoded_path)
        ):
            logger.warning(f"[WAF] Blocked Path Traversal / Probe from {client_ip}: {raw_path}")
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden: Path traversal or restricted resource access blocked."}
            )

        # 2. Block Known Vulnerability Scanner User-Agents
        user_agent = request.headers.get("user-agent", "")
        if user_agent and BOT_SCANNER_UA_RE.search(user_agent):
            logger.warning(f"[WAF] Blocked automated scanner User-Agent from {client_ip}: {user_agent}")
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden: Automated scanner blocked."}
            )

        # 3. Rate Limiting Check
        if not rate_limiter.check_general_rate(client_ip):
            logger.warning(f"[WAF] Rate limit exceeded for {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests: Rate limit exceeded. Please slow down."}
            )

        # 4. Inspect Query Parameters & Path for SQLi, XSS, and Command Injection
        inspection_target = f"{decoded_path}?{decoded_query}" if decoded_query else decoded_path
        if (
            SQLI_PAYLOAD_RE.search(inspection_target)
            or XSS_PAYLOAD_RE.search(inspection_target)
            or CMD_INJECTION_RE.search(inspection_target)
        ):
            logger.warning(f"[WAF] Blocked malicious payload in request from {client_ip}: {inspection_target[:200]}")
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden: Malicious request pattern blocked by WAF."}
            )

        # 5. CSRF Protection for State-Changing Methods (POST, PUT, PATCH, DELETE)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            csrf_error = self._verify_csrf_protection(request)
            if csrf_error:
                logger.warning(f"[WAF] Blocked CSRF attempt from {client_ip} on {path}: {csrf_error}")
                return JSONResponse(
                    status_code=403,
                    content={"detail": f"Forbidden (CSRF Protection): {csrf_error}"}
                )

        response = await call_next(request)

        # 6. Issue CSRF Cookie on entry HTML pages (endpoint /api/security/csrf sets its own matching cookie)
        if request.method == "GET" and path in ("/", "/index.html") and "worldcams_csrf" not in request.cookies:
            csrf_token = generate_csrf_token()
            response.set_cookie(
                key="worldcams_csrf",
                value=csrf_token,
                httponly=False,  # Accessible to same-origin JS to populate X-CSRF-Token header
                samesite="strict",
                path="/"
            )

        # 7. Inject Hardened Security & Isolation Headers
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), autoplay=(self)"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        # Apply default Content-Security-Policy if endpoint didn't set a stricter one
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "img-src 'self' data: blob: https://*.openstreetmap.org https://images.unsplash.com; "
                "media-src 'self' blob: http: https:; "
                "frame-src 'self' http://localhost:8889 http://127.0.0.1:8889 http://*:8889; "
                "connect-src 'self' http://localhost:8889 http://127.0.0.1:8889 http://*:8889 ws: wss:; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )

        return response

    @staticmethod
    def _verify_csrf_protection(request: Request) -> Optional[str]:
        """
        Multi-layered CSRF defense for state-changing requests:
        - Layer 1: Origin / Referer Host matching
        - Layer 2: Reject cross-origin simple form Content-Types (application/x-www-form-urlencoded, multipart/form-data, text/plain)
        - Layer 3: Double-Submit Cookie verification when browser CSRF cookie is present
        """
        host_hdr = (
            request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or ""
        ).split(":")[0].strip().lower()

        allowed_local_hosts = {host_hdr, "localhost", "127.0.0.1", "test"}

        # Layer 1A: Check Origin header if present
        origin = request.headers.get("origin")
        if origin:
            if origin.strip().lower() == "null":
                return "Sandboxed 'null' Origin is not permitted for state-changing requests"
            origin_host = (urlparse(origin).hostname or "").strip().lower()
            if not origin_host or (origin_host != host_hdr and not (origin_host in allowed_local_hosts and host_hdr in allowed_local_hosts)):
                return f"Origin '{origin}' does not match application host '{host_hdr}'"

        # Layer 1B: Check Referer header if Origin is absent
        referer = request.headers.get("referer")
        if not origin and referer:
            ref_host = (urlparse(referer).hostname or "").strip().lower()
            if not ref_host or (ref_host != host_hdr and not (ref_host in allowed_local_hosts and host_hdr in allowed_local_hosts)):
                return f"Referer '{referer}' does not match application host '{host_hdr}'"

        # Layer 2: Block browser HTML <form> Content-Types on API mutations
        ctype = (request.headers.get("content-type") or "").lower()
        if any( ctype.startswith(form_ct) for form_ct in ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain") ):
            return f"Form Content-Type '{ctype}' is blocked on JSON API endpoints"

        # Layer 3: Double-Submit CSRF Cookie check (when browser session has worldcams_csrf cookie)
        csrf_cookie = request.cookies.get("worldcams_csrf")
        if csrf_cookie:
            csrf_header = request.headers.get("x-csrf-token")
            admin_header = request.headers.get("x-admin-token")
            # Allow if valid matching X-CSRF-Token OR valid X-Admin-Token is provided
            if csrf_header and hmac.compare_digest(csrf_cookie, csrf_header) and verify_csrf_token(csrf_header):
                return None
            if admin_header and verify_admin_token_constant_time(admin_header):
                return None
            return "Missing or invalid X-CSRF-Token header for active browser session"

        return None

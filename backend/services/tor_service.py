import asyncio
import hashlib
import ipaddress
import logging
import re
import secrets
import shutil
import socket
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from backend.config import BASE_DIR, settings

logger = logging.getLogger("worldcams.tor")


class TorSocksConnectError(ConnectionError):
    """Raised when Tor SOCKS5 CONNECT fails, preserving RFC 1928 reply code."""
    def __init__(self, message: str, reply_code: int = 0):
        super().__init__(message)
        self.reply_code = reply_code


class TorProxyService:
    """
    Optional Tor SOCKS5 Proxy & Circuit Rotation Controller.
    - Disabled by default (`TOR_ENABLED=False`); explicitly enabled by the user.
    - Fail-closed privacy guarantee: when enabled, NEVER falls back to direct connections
      if Tor SOCKS5 is unreachable.
    - Rotates circuits using Tor's native `IsolateSocksAuth` (unique per-circuit SOCKS5
      username/password credentials) combined with `SIGNAL NEWNYM` on Tor ControlPort.
    - Bridges both `httpx` HTTP/HTTPS streams and raw TCP/RTSP sockets (MediaMTX & prober).
    """

    def __init__(self):
        self.enabled: bool = bool(settings.TOR_ENABLED)
        self.auto_rotate: bool = bool(settings.TOR_AUTO_ROTATE)
        self.socks_host: str = settings.TOR_SOCKS_HOST
        self.socks_port: int = int(settings.TOR_SOCKS_PORT)
        self.control_port: int = int(settings.TOR_CONTROL_PORT)
        self.control_password: Optional[str] = settings.TOR_CONTROL_PASSWORD

        # Bypass loopback (127.0.0.1 / ::1 / localhost) so internal tests and MediaMTX
        # control traffic keep working unless explicitly overridden for SOCKS5 unit tests.
        self.bypass_loopback: bool = True
        self.force_proxy_loopback: bool = False

        # Circuit rotation state
        self.circuit_generation: int = 1
        self._circuit_salt: str = secrets.token_hex(6)
        self.current_circuit_id: str = f"tor-c1-{self._circuit_salt[:6]}"
        self.last_rotated_at: Optional[str] = None

        # Cached exit telemetry
        self.cached_exit_ip: Optional[str] = None
        self.cached_exit_country: Optional[str] = None
        self.cached_exit_isp: Optional[str] = None

        # Managed Tor daemon process & active RTSP TCP->SOCKS5 bridges
        self.process: Optional[subprocess.Popen] = None
        self._bridges: Dict[str, Dict[str, Any]] = {}
        self._bridge_lock = asyncio.Lock()

    @staticmethod
    def _extract_host(target_host_or_url: str) -> str:
        """Extract hostname/IP cleanly from either a bare IP/host or full URL."""
        raw = (target_host_or_url or "").strip()
        if not raw:
            return ""
        if "://" in raw:
            parsed = urlparse(raw)
            return (parsed.hostname or "").strip().strip("[]").lower()
        # Handle bare host, ip, ip:port, or [ipv6]:port
        if raw.startswith("[") and "]" in raw:
            return raw[1:raw.index("]")].strip().lower()
        # Bare IPv6 without brackets has >= 2 colons
        if raw.count(":") >= 2:
            return raw.lower()
        return raw.split("/")[0].split(":")[0].strip().lower()

    @staticmethod
    def _is_loopback(host: str) -> bool:
        """Return True if host is a loopback address."""
        clean = (host or "").strip().strip("[]").lower()
        if clean in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "test"):
            return True
        try:
            return ipaddress.ip_address(clean).is_loopback
        except ValueError:
            return False

    def should_route_via_tor(self, target_host_or_url: str) -> bool:
        """
        Determine whether connections to `target_host_or_url` must route through Tor SOCKS5.
        """
        if not self.enabled:
            return False
        host = self._extract_host(target_host_or_url)
        if self._is_loopback(host) and self.bypass_loopback and not self.force_proxy_loopback:
            return False
        return True

    def get_socks_credentials(self, circuit_key: Optional[str] = None) -> Tuple[str, str]:
        """
        Return SOCKS5 (username, password) scoped to the active circuit generation
        and optional circuit_key to trigger Tor `IsolateSocksAuth` stream isolation.
        """
        clean_key = re.sub(r"[^a-zA-Z0-9_-]", "", str(circuit_key or "default"))[:24] or "default"
        username = f"wc_{self.circuit_generation}_{clean_key}"
        password = self._circuit_salt
        return username, password

    def _build_httpx_proxy_url(self, circuit_key: Optional[str] = None) -> str:
        user, pwd = self.get_socks_credentials(circuit_key)
        return f"socks5://{user}:{pwd}@{self.socks_host}:{self.socks_port}"

    async def check_socks_alive(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: float = 1.2
    ) -> bool:
        """
        Verify SOCKS5 listener reachability with an RFC 1928 handshake.
        If default port 9050 is closed and no explicit port override was passed,
        also probes Tor Browser's default port 9150 and adopts it if active.
        """
        target_host = host or self.socks_host
        target_port = int(port or self.socks_port)

        async def _probe(h: str, p: int) -> bool:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(h, p), timeout=timeout)
                # Propose No-Auth (0x00) and Username/Password (0x02)
                writer.write(b"\x05\x02\x00\x02")
                await asyncio.wait_for(writer.drain(), timeout=timeout)
                reply = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
                return len(reply) == 2 and reply[0] == 5 and reply[1] in (0, 2)
            except Exception:
                return False
            finally:
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

        if await _probe(target_host, target_port):
            return True

        # Auto-detect Tor Browser SOCKS5 port (9150) only when using the default 9050 setting
        if port is None and target_port == 9050:
            if await _probe(target_host, 9150):
                logger.info("Detected active Tor Browser SOCKS5 listener on port 9150.")
                self.socks_port = 9150
                return True

        return False

    async def ensure_tor_daemon(self) -> bool:
        """
        Attempt to start a local managed Tor daemon if no SOCKS5 listener is active
        and the `tor` binary is installed on the host.
        """
        if await self.check_socks_alive():
            return True

        tor_bin = shutil.which("tor")
        if not tor_bin:
            return False

        if self.process and self.process.poll() is None:
            # Daemon is booting; wait briefly for SOCKS5 listener
            for _ in range(10):
                await asyncio.sleep(0.5)
                if await self.check_socks_alive(self.socks_host, self.socks_port, timeout=1.0):
                    return True
            return False

        data_dir = Path(BASE_DIR) / ".tor_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        torrc_path = data_dir / "torrc"
        torrc_content = (
            f"SocksPort {self.socks_host}:{self.socks_port} IsolateSocksAuth\n"
            f"ControlPort {self.control_port}\n"
            f"CookieAuthentication 1\n"
            f"DataDirectory {data_dir}\n"
        )
        torrc_path.write_text(torrc_content, encoding="utf-8")

        logger.info(f"Starting managed Tor daemon ({tor_bin}) on {self.socks_host}:{self.socks_port}...")
        try:
            self.process = subprocess.Popen(
                [tor_bin, "-f", str(torrc_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(10):
                await asyncio.sleep(0.5)
                if await self.check_socks_alive(self.socks_host, self.socks_port, timeout=1.0):
                    logger.info("Managed Tor SOCKS5 proxy is online.")
                    return True
        except Exception as e:
            logger.warning(f"Failed to start managed Tor daemon: {e}")

        return False

    async def _send_control_newnym(self) -> bool:
        """
        Send `SIGNAL NEWNYM` to Tor ControlPort (when active) to request fresh circuits.
        Also works gracefully if ControlPort is closed (`IsolateSocksAuth` handles rotation).
        """
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.socks_host, self.control_port),
                timeout=1.0
            )
            if self.control_password:
                escaped = self.control_password.replace("\\", "\\\\").replace('"', '\\"')
                writer.write(f'AUTHENTICATE "{escaped}"\r\n'.encode("utf-8"))
            else:
                writer.write(b"AUTHENTICATE\r\n")
            await asyncio.wait_for(writer.drain(), timeout=1.0)
            auth_reply = await asyncio.wait_for(reader.readline(), timeout=1.0)
            if not auth_reply.startswith(b"250"):
                return False

            writer.write(b"SIGNAL NEWNYM\r\n")
            await asyncio.wait_for(writer.drain(), timeout=1.0)
            newnym_reply = await asyncio.wait_for(reader.readline(), timeout=1.0)
            return newnym_reply.startswith(b"250")
        except Exception:
            return False
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    async def verify_exit_ip(self) -> Dict[str, Optional[str]]:
        """
        Query external Geo/IP echo through the active Tor SOCKS5 circuit to discover the Exit Node IP.
        """
        if not await self.check_socks_alive():
            return {"ip": None, "country": None, "isp": None}

        proxy_url = self._build_httpx_proxy_url("exit_check")
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=8.0, follow_redirects=False) as client:
                res = await client.get("http://ip-api.com/json")
                if res.status_code == 200:
                    data = res.json()
                    self.cached_exit_ip = data.get("query")
                    self.cached_exit_country = data.get("countryCode") or data.get("country")
                    self.cached_exit_isp = data.get("isp") or data.get("org")
                    return {
                        "ip": self.cached_exit_ip,
                        "country": self.cached_exit_country,
                        "isp": self.cached_exit_isp,
                    }
        except Exception as e:
            logger.debug(f"Tor exit IP verification notice: {e}")

        return {"ip": None, "country": None, "isp": None}

    async def rotate_circuit(
        self,
        reason: str = "manual",
        verify_ip: bool = False,
        send_newnym: bool = True,
    ) -> Dict[str, Any]:
        """
        Rotate to a new Tor circuit immediately:
        1. Increment `circuit_generation` and rotate `_circuit_salt` (triggers IsolateSocksAuth).
        2. Optionally send `SIGNAL NEWNYM` to Tor ControlPort (skipped on per-stream auto_rotate so
           Tor keeps its pre-built clean circuit pool warm instead of stalling 5-10s rebuilding).
        3. Clear cached exit IP and optionally query the new exit node IP.
        """
        self.circuit_generation += 1
        self._circuit_salt = secrets.token_hex(6)
        self.current_circuit_id = f"tor-c{self.circuit_generation}-{self._circuit_salt[:6]}"
        self.last_rotated_at = datetime.now(timezone.utc).isoformat()
        self.cached_exit_ip = None
        self.cached_exit_country = None
        self.cached_exit_isp = None

        await self.stop_all_bridges()
        newnym_sent = await self._send_control_newnym() if send_newnym else False
        logger.info(
            f"[TOR] Rotated circuit -> {self.current_circuit_id} "
            f"(reason={reason}, control_newnym={'ok' if newnym_sent else 'isolate-auth'})"
        )

        if verify_ip and self.enabled:
            await self.verify_exit_ip()

        return {
            "circuit_generation": self.circuit_generation,
            "circuit_id": self.current_circuit_id,
            "last_rotated_at": self.last_rotated_at,
            "control_newnym": newnym_sent,
            "exit_ip": self.cached_exit_ip,
            "exit_country": self.cached_exit_country,
            "exit_isp": self.cached_exit_isp,
        }

    async def _require_socks_ready(self) -> None:
        """Fail-closed check ensuring Tor SOCKS5 is alive before attempting any proxied camera connection."""
        if await self.check_socks_alive():
            return
        if self.socks_port in (9050, 9150) and await self.ensure_tor_daemon():
            return
        raise HTTPException(
            status_code=503,
            detail="Tor Proxy is enabled but currently unreachable (Fail-Closed Privacy Protection)."
        )

    async def create_http_client(
        self,
        target_host_or_url: str,
        verify: Any = False,
        timeout: float = 8.0,
        follow_redirects: bool = False,
        circuit_key: Optional[str] = None,
    ) -> httpx.AsyncClient:
        """
        Build an `httpx.AsyncClient` routed over Tor SOCKS5 when Tor is enabled for the target,
        or a direct client otherwise. Raises HTTP 503 (fail-closed) if Tor is enabled and unreachable.
        """
        if self.should_route_via_tor(target_host_or_url):
            await self._require_socks_ready()
            proxy_url = self._build_httpx_proxy_url(circuit_key or self._extract_host(target_host_or_url))
            return httpx.AsyncClient(
                verify=verify,
                timeout=max(float(timeout), 10.0),
                follow_redirects=follow_redirects,
                proxy=proxy_url,
            )

        return httpx.AsyncClient(
            verify=verify,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

    async def _open_socks5_once(
        self,
        ip: str,
        port: int,
        timeout: float,
        circuit_key: Optional[str] = None,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # Fail-closed connect to SOCKS5 proxy
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.socks_host, self.socks_port),
                timeout=max(timeout, 3.0),
            )
        except Exception:
            if self.socks_port in (9050, 9150) and await self.ensure_tor_daemon():
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.socks_host, self.socks_port),
                    timeout=max(timeout, 3.0),
                )
            else:
                raise HTTPException(
                    status_code=503,
                    detail="Tor Proxy is enabled but currently unreachable (Fail-Closed Privacy Protection)."
                )

        try:
            # RFC 1928 Greeting: support No-Auth (0x00) and Username/Password (0x02)
            writer.write(b"\x05\x02\x00\x02")
            await asyncio.wait_for(writer.drain(), timeout=timeout)
            ver, method = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
            if ver != 5 or method == 0xFF:
                raise TorSocksConnectError(f"SOCKS5 handshake rejected (ver={ver}, method={method})", reply_code=1)

            # RFC 1929 Username/Password Authentication (IsolateSocksAuth)
            if method == 0x02:
                user, pwd = self.get_socks_credentials(circuit_key or f"{ip}:{port}")
                u_bytes = user.encode("utf-8")[:255]
                p_bytes = pwd.encode("utf-8")[:255]
                auth_pkt = b"\x01" + bytes([len(u_bytes)]) + u_bytes + bytes([len(p_bytes)]) + p_bytes
                writer.write(auth_pkt)
                await asyncio.wait_for(writer.drain(), timeout=timeout)
                auth_ver, auth_status = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
                if auth_ver != 1 or auth_status != 0x00:
                    raise TorSocksConnectError(f"SOCKS5 authentication failed (status={auth_status})", reply_code=1)
            elif method != 0x00:
                raise TorSocksConnectError(f"Unsupported SOCKS5 auth method selected: {method}", reply_code=1)

            # SOCKS5 CONNECT command
            clean_host = self._extract_host(ip) or ip.strip().strip("[]")
            try:
                ip_obj = ipaddress.ip_address(clean_host)
                if ip_obj.version == 4:
                    addr_payload = b"\x01" + ip_obj.packed
                else:
                    addr_payload = b"\x04" + ip_obj.packed
            except ValueError:
                h_bytes = clean_host.encode("utf-8")[:255]
                addr_payload = b"\x03" + bytes([len(h_bytes)]) + h_bytes

            req_pkt = b"\x05\x01\x00" + addr_payload + struct.pack("!H", int(port))
            writer.write(req_pkt)
            await asyncio.wait_for(writer.drain(), timeout=timeout)

            rep_hdr = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
            r_ver, rep_code, _rsv, atyp = rep_hdr
            if r_ver != 5 or rep_code != 0x00:
                raise TorSocksConnectError(
                    f"SOCKS5 CONNECT to {clean_host}:{port} failed with reply code {rep_code}",
                    reply_code=int(rep_code),
                )

            # Consume bound address + bound port
            if atyp == 1:
                await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
            elif atyp == 3:
                alen = (await asyncio.wait_for(reader.readexactly(1), timeout=timeout))[0]
                await asyncio.wait_for(reader.readexactly(alen), timeout=timeout)
            elif atyp == 4:
                await asyncio.wait_for(reader.readexactly(16), timeout=timeout)
            await asyncio.wait_for(reader.readexactly(2), timeout=timeout)

            return reader, writer
        except Exception:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            raise

    async def open_connection(
        self,
        ip: str,
        port: int,
        timeout: float = 4.0,
        circuit_key: Optional[str] = None,
        retry_alt_exit: bool = True,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """
        Open a TCP connection to `(ip, port)`, tunneled via Tor SOCKS5 (RFC 1928 + RFC 1929)
        when Tor is enabled for the target. Fail-closed if SOCKS5 proxy is unreachable.
        If a Tor exit node rejects the port (reply 0x01/0x02/0x06), retries once on an isolated
        alternate circuit so Tor selects a different exit node.
        """
        if not self.should_route_via_tor(ip):
            return await asyncio.wait_for(asyncio.open_connection(ip, int(port)), timeout=timeout)

        eff_timeout = max(float(timeout), 10.0)
        base_key = circuit_key or f"{ip}:{port}"
        try:
            return await self._open_socks5_once(ip, port, eff_timeout, circuit_key=base_key)
        except HTTPException:
            raise
        except TorSocksConnectError as e:
            # 0x05 = target actively refused connection; 0x01/0x02/0x06 = Tor exit policy / circuit failure
            if retry_alt_exit and e.reply_code in (1, 2, 6):
                alt_key = f"{base_key}_e{secrets.token_hex(2)}"
                logger.debug(f"[TOR] Exit failure (code={e.reply_code}) for {ip}:{port}; retrying via {alt_key}")
                return await self._open_socks5_once(ip, port, eff_timeout, circuit_key=alt_key)
            raise

    @staticmethod
    def _rewrite_rtsp_c2r(
        data: bytes,
        local_variants: List[bytes],
        remote_host_port: bytes,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bytes:
        """
        Rewrite local bridge host:port in outgoing RTSP control requests to the remote camera host:port,
        leaving binary RTP/RTCP interleaved frames (`$`) untouched and recomputing Digest auth if present.
        """
        if not data or data[:1] == b"$":
            return data
        if not any(lv in data for lv in local_variants):
            return data

        has_digest = bool(re.search(rb"(?i)Authorization:\s*Digest\b", data))
        if has_digest and username is None:
            return data

        rewritten = data
        for lv in local_variants:
            if lv in rewritten:
                rewritten = rewritten.replace(lv, remote_host_port)

        if not has_digest:
            return rewritten

        text = rewritten.decode("latin-1")
        method_m = re.match(r"^([A-Z_]+)\s+", text)
        method = method_m.group(1) if method_m else "DESCRIBE"

        def _recompute_auth_line(match: re.Match) -> str:
            line = match.group(0)
            realm_m = re.search(r'realm="([^"]*)"', line, re.IGNORECASE)
            nonce_m = re.search(r'nonce="([^"]+)"', line, re.IGNORECASE)
            uri_m = re.search(r'uri="([^"]+)"', line, re.IGNORECASE)
            resp_m = re.search(r'response="([a-fA-F0-9]+)"', line, re.IGNORECASE)
            if not (realm_m and nonce_m and uri_m and resp_m):
                return line

            realm = realm_m.group(1)
            nonce = nonce_m.group(1)
            uri = uri_m.group(1)
            alg_m = re.search(r'algorithm=([^,\s"]+)', line, re.IGNORECASE)
            alg = (alg_m.group(1) if alg_m else "MD5").upper()
            hash_fn = hashlib.sha256 if alg.startswith("SHA-256") else hashlib.md5

            ha1 = hash_fn(f"{username}:{realm}:{password or ''}".encode("utf-8")).hexdigest()
            ha2 = hash_fn(f"{method}:{uri}".encode("utf-8")).hexdigest()

            qop_m = re.search(r'qop=([^,\s"]+|"[^"]+")', line, re.IGNORECASE)
            nc_m = re.search(r'nc=([^,\s"]+)', line, re.IGNORECASE)
            cnonce_m = re.search(r'cnonce="([^"]+)"', line, re.IGNORECASE)

            if qop_m and nc_m and cnonce_m:
                qop_val = qop_m.group(1).strip('"')
                new_resp = hash_fn(
                    f"{ha1}:{nonce}:{nc_m.group(1)}:{cnonce_m.group(1)}:{qop_val}:{ha2}".encode("utf-8")
                ).hexdigest()
            else:
                new_resp = hash_fn(f"{ha1}:{nonce}:{ha2}".encode("utf-8")).hexdigest()

            return re.sub(
                r'response="[a-fA-F0-9]+"',
                f'response="{new_resp}"',
                line,
                count=1,
                flags=re.IGNORECASE,
            )

        recomputed = re.sub(
            r"(?im)^Authorization:\s*Digest\b[^\r\n]*",
            _recompute_auth_line,
            text,
        )
        return recomputed.encode("latin-1")

    @staticmethod
    def _rewrite_rtsp_r2c(
        data: bytes,
        cam_ip: str,
        cam_port: int,
        local_host_port: bytes,
    ) -> bytes:
        """
        Keep RTSP 3xx redirect Location headers inside the local Tor bridge so MediaMTX
        cannot bypass Tor by chasing a remote camera's `Location: rtsp://<cam_ip>...` header.
        """
        if not data or not data.startswith(b"RTSP/") or b"Location:" not in data and b"location:" not in data:
            return data
        text = data.decode("latin-1")
        if not re.match(r"^RTSP/\d\.\d\s+30[12378]\b", text):
            return data
        local_str = local_host_port.decode("latin-1")
        remote_hp = f"{cam_ip}:{int(cam_port)}"
        def _rw_loc(match: re.Match) -> str:
            line = match.group(0)
            if remote_hp in line:
                return line.replace(remote_hp, local_str)
            if int(cam_port) == 554 and f"//{cam_ip}/" in line:
                return line.replace(f"//{cam_ip}/", f"//{local_str}/")
            return line
        rewritten = re.sub(r"(?im)^Location:\s*rtsps?://[^\r\n]+", _rw_loc, text)
        return rewritten.encode("latin-1")

    async def ensure_rtsp_bridge(
        self,
        stream_path: str,
        cam_ip: str,
        cam_port: int,
        circuit_key: Optional[str] = None,
        rtsp_username: Optional[str] = None,
        rtsp_password: Optional[str] = None,
    ) -> int:
        """
        Start (or reuse) a local loopback TCP-to-SOCKS5 RTSP bridge for MediaMTX so
        RTSP/TCP camera streams are pulled transparently over Tor without leaking the real IP.
        """
        clean_path = re.sub(r"[^a-zA-Z0-9_-]", "_", stream_path).strip("_") or "default"
        async with self._bridge_lock:
            existing = self._bridges.get(clean_path)
            if (
                existing
                and existing.get("cam_ip") == cam_ip
                and existing.get("cam_port") == int(cam_port)
                and existing.get("circuit_generation") == self.circuit_generation
                and existing.get("rtsp_username") == rtsp_username
                and existing.get("rtsp_password") == rtsp_password
                and existing.get("server")
                and existing["server"].is_serving()
            ):
                return int(existing["local_port"])

            if existing:
                await self._close_bridge_entry(existing)
                self._bridges.pop(clean_path, None)

            remote_host_port = f"[{cam_ip}]:{int(cam_port)}".encode("utf-8") if ":" in cam_ip else f"{cam_ip}:{int(cam_port)}".encode("utf-8")
            active_writers: set = set()
            holder: Dict[str, Any] = {"local_port": 0, "fail_count": 0}

            async def _handle_bridge_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
                remote_reader: Optional[asyncio.StreamReader] = None
                remote_writer: Optional[asyncio.StreamWriter] = None
                active_writers.add(client_writer)
                local_port_val = holder["local_port"]
                local_variants = [
                    f"127.0.0.1:{local_port_val}".encode("utf-8"),
                    f"localhost:{local_port_val}".encode("utf-8"),
                ]
                t_start = time.monotonic()
                base_ck = circuit_key or f"{cam_ip}:{cam_port}"
                eff_ck = base_ck if holder["fail_count"] == 0 else f"{base_ck}_t{holder['fail_count']}"
                try:
                    remote_reader, remote_writer = await self.open_connection(
                        cam_ip,
                        int(cam_port),
                        timeout=20.0,
                        circuit_key=eff_ck,
                    )
                    active_writers.add(remote_writer)

                    async def _c2r():
                        try:
                            while True:
                                data = await client_reader.read(65536)
                                if not data:
                                    break
                                if local_port_val:
                                    data = self._rewrite_rtsp_c2r(
                                        data,
                                        local_variants,
                                        remote_host_port,
                                        rtsp_username,
                                        rtsp_password,
                                    )
                                remote_writer.write(data)
                                await remote_writer.drain()
                        except Exception:
                            pass
                        finally:
                            try:
                                if remote_writer and not remote_writer.is_closing():
                                    remote_writer.close()
                            except Exception:
                                pass

                    async def _r2c():
                        try:
                            while True:
                                data = await remote_reader.read(65536)
                                if not data:
                                    break
                                if local_port_val and data.startswith(b"RTSP/"):
                                    data = self._rewrite_rtsp_r2c(
                                        data,
                                        cam_ip,
                                        int(cam_port),
                                        local_variants[0],
                                    )
                                client_writer.write(data)
                                await client_writer.drain()
                        except Exception:
                            pass
                        finally:
                            try:
                                if not client_writer.is_closing():
                                    client_writer.close()
                            except Exception:
                                pass

                    await asyncio.gather(_c2r(), _r2c(), return_exceptions=True)
                    if (time.monotonic() - t_start) < 8.0:
                        holder["fail_count"] += 1
                    else:
                        holder["fail_count"] = 0
                except Exception as e:
                    holder["fail_count"] += 1
                    logger.debug(f"Tor RTSP bridge tunnel error for {clean_path} -> {cam_ip}:{cam_port}: {e}")
                finally:
                    for w in (client_writer, remote_writer):
                        if w is not None:
                            active_writers.discard(w)
                            try:
                                w.close()
                            except Exception:
                                pass

            server = await asyncio.start_server(_handle_bridge_client, "127.0.0.1", 0)
            local_port = int(server.sockets[0].getsockname()[1])
            holder["local_port"] = local_port

            self._bridges[clean_path] = {
                "server": server,
                "active_writers": active_writers,
                "local_port": local_port,
                "cam_ip": cam_ip,
                "cam_port": int(cam_port),
                "circuit_generation": self.circuit_generation,
                "rtsp_username": rtsp_username,
                "rtsp_password": rtsp_password,
            }
            logger.info(
                f"[TOR BRIDGE] Started RTSP-over-SOCKS5 bridge 127.0.0.1:{local_port} "
                f"-> {cam_ip}:{cam_port} (circuit={self.current_circuit_id})"
            )
            return local_port

    async def _close_bridge_entry(self, entry: Dict[str, Any]) -> None:
        # Close active tunnel sockets first so Python 3.12+ Server.wait_closed() NEVER deadlocks
        # waiting on a live MediaMTX pull socket during camera switches or circuit rotation!
        for w in list(entry.get("active_writers") or ()):
            try:
                w.close()
            except Exception:
                pass
        srv = entry.get("server")
        if srv is not None:
            try:
                srv.close()
                if hasattr(srv, "close_clients"):
                    srv.close_clients()
                await asyncio.wait_for(srv.wait_closed(), timeout=1.5)
            except Exception:
                pass

    async def stop_rtsp_bridge(self, stream_path: str) -> None:
        clean_path = re.sub(r"[^a-zA-Z0-9_-]", "_", stream_path).strip("_")
        async with self._bridge_lock:
            entry = self._bridges.pop(clean_path, None)
            if entry:
                await self._close_bridge_entry(entry)

    async def stop_all_bridges(self) -> None:
        async with self._bridge_lock:
            entries = list(self._bridges.values())
            self._bridges.clear()
        for entry in entries:
            await self._close_bridge_entry(entry)

    async def shutdown(self) -> None:
        """Stop all active RTSP Tor bridges and any managed Tor daemon subprocess."""
        await self.stop_all_bridges()
        if self.process is not None:
            try:
                self.process.terminate()
                self.process = None
                logger.info("Managed Tor daemon stopped.")
            except Exception:
                pass

    async def update_config(
        self,
        enabled: bool,
        auto_rotate: Optional[bool] = None,
        socks_host: Optional[str] = None,
        socks_port: Optional[int] = None,
        control_port: Optional[int] = None,
        control_password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update Tor proxy settings at runtime."""
        was_enabled = self.enabled
        self.enabled = bool(enabled)
        if auto_rotate is not None:
            self.auto_rotate = bool(auto_rotate)
        if socks_host and socks_host.strip():
            # Restrict proxy host to valid IP or localhost to prevent unsafe configuration
            clean_h = socks_host.strip()
            if clean_h != "localhost":
                ipaddress.ip_address(clean_h.strip("[]"))
            self.socks_host = clean_h
        if socks_port is not None:
            p = int(socks_port)
            if not (1 <= p <= 65535):
                raise HTTPException(status_code=400, detail="Invalid Tor SOCKS5 port")
            self.socks_port = p
        if control_port is not None:
            cp = int(control_port)
            if not (1 <= cp <= 65535):
                raise HTTPException(status_code=400, detail="Invalid Tor ControlPort")
            self.control_port = cp
        if control_password is not None:
            self.control_password = control_password or None

        if was_enabled != self.enabled or not self.enabled:
            await self.stop_all_bridges()
            try:
                from backend.services.mediamtx_service import mediamtx_service
                await mediamtx_service.stop_all_streams()
            except Exception:
                pass

        if not self.enabled:
            self.cached_exit_ip = None
            self.cached_exit_country = None
            self.cached_exit_isp = None
        else:
            # If user enabled Tor on standard ports and no daemon is running yet, try managed launch if `tor` exists
            if self.socks_port in (9050, 9150) and not await self.check_socks_alive():
                await self.ensure_tor_daemon()

        return await self.get_status(verify_ip=False)

    async def get_status(self, verify_ip: bool = False) -> Dict[str, Any]:
        """Return live Tor proxy and circuit rotation status."""
        socks_alive = await self.check_socks_alive()
        tor_installed = bool(shutil.which("tor"))

        if verify_ip and self.enabled and socks_alive and not self.cached_exit_ip:
            await self.verify_exit_ip()

        if self.enabled:
            if socks_alive:
                msg = (
                    f"Tor Anonymizer ACTIVE on {self.socks_host}:{self.socks_port} "
                    f"(Circuit {self.current_circuit_id})"
                )
            elif tor_installed:
                msg = (
                    f"Tor enabled, but SOCKS5 proxy {self.socks_host}:{self.socks_port} is starting or offline. "
                    "Direct connections are blocked (Fail-Closed)."
                )
            else:
                msg = (
                    f"Tor enabled, but no SOCKS5 listener found on {self.socks_host}:{self.socks_port} "
                    "and `tor` binary is not installed. Start Tor service or Tor Browser, "
                    "or disable Tor mode for direct connections."
                )
        else:
            msg = (
                "Tor Proxy is OFF (Direct server connection active)."
                if not socks_alive
                else f"Tor Proxy is OFF (Tor SOCKS5 ready on {self.socks_host}:{self.socks_port})."
            )

        return {
            "enabled": self.enabled,
            "auto_rotate": self.auto_rotate,
            "socks_host": self.socks_host,
            "socks_port": self.socks_port,
            "control_port": self.control_port,
            "socks_alive": socks_alive,
            "tor_binary_installed": tor_installed,
            "circuit_generation": self.circuit_generation,
            "circuit_id": self.current_circuit_id,
            "last_rotated_at": self.last_rotated_at,
            "exit_ip": self.cached_exit_ip,
            "exit_country": self.cached_exit_country,
            "exit_isp": self.cached_exit_isp,
            "active_bridges": len(self._bridges),
            "message": msg,
        }


tor_service = TorProxyService()

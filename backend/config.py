import sys
from typing import Optional
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Shodan API Key
    SHODAN_API_KEY: Optional[str] = None

    # Admin Security
    ADMIN_TOKEN: str = "worldcams-admin-2026"

    # Application Server
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = True

    # MediaMTX Streaming Server
    MEDIAMTX_PATH: str = str(BASE_DIR / "bin" / ("mediamtx.exe" if sys.platform == "win32" else "mediamtx"))
    MEDIAMTX_CONFIG: str = str(BASE_DIR / "mediamtx.yml")
    MEDIAMTX_API_URL: str = "http://localhost:9997"
    MEDIAMTX_HLS_URL: str = "http://localhost:8888"
    MEDIAMTX_WHEP_URL: str = "http://localhost:8889"
    MEDIAMTX_RTSP_PORT: int = 8554
    MEDIAMTX_PUBLIC_HOST: Optional[str] = None

    @property
    def effective_hls_url(self) -> str:
        if self.MEDIAMTX_PUBLIC_HOST:
            return f"http://{self.MEDIAMTX_PUBLIC_HOST}:8888"
        return self.MEDIAMTX_HLS_URL

    @property
    def effective_whep_url(self) -> str:
        if self.MEDIAMTX_PUBLIC_HOST:
            return f"http://{self.MEDIAMTX_PUBLIC_HOST}:8889"
        return self.MEDIAMTX_WHEP_URL

    # Database & Data Paths
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR.as_posix()}/world_cams.db"
    SAMPLE_DATA_PATH: str = str(BASE_DIR / "data" / "sample_feeds.json")
    STATIC_DIR: str = str(BASE_DIR / "frontend")

settings = Settings()

import ssl

def get_legacy_ssl_context() -> ssl.SSLContext:
    """
    Creates an SSL context that allows connecting to legacy surveillance cameras
    running outdated firmware with TLS 1.0, TLS 1.1, SSLv3, or legacy ciphers/DH parameters.
    Fixes SSL_ERROR_UNSUPPORTED_VERSION and ERR_SSL_VERSION_OR_CIPHER_MISMATCH.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 1. Enable lowest supported TLS/SSL versions (TLS 1.0, 1.1, 1.2, 1.3)
    try:
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    except Exception:
        try:
            ctx.minimum_version = getattr(ssl.TLSVersion, "TLSv1", None) or ssl.TLSVersion.TLSv1_2
        except Exception:
            pass

    # 2. Lower OpenSSL SECLEVEL to 0 to permit legacy keys and ciphers (3DES, RC4, weak DH)
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except Exception:
        try:
            ctx.set_ciphers("ALL:!aNULL:!eNULL:@SECLEVEL=0")
        except Exception:
            try:
                ctx.set_ciphers("ALL:!aNULL:!eNULL")
            except Exception:
                pass

    return ctx


import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional, Dict, Any

from sqlalchemy import (
    Column, String, Integer, Float, Text, Boolean, DateTime, select, func, or_, and_
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

from backend.config import settings

logger = logging.getLogger("worldcams.database")

Base = declarative_base()

class CameraFeedModel(Base):
    __tablename__ = "camera_feeds"

    id = Column(String(64), primary_key=True, index=True)
    ip = Column(String(45), index=True, nullable=False)
    port = Column(Integer, default=554, index=True)
    country_code = Column(String(8), index=True, default="XX")
    country_name = Column(String(128), default="Unknown")
    city = Column(String(128), nullable=True)
    latitude = Column(Float, default=0.0)
    longitude = Column(Float, default=0.0)
    org = Column(String(255), index=True, default="Unknown")
    isp = Column(String(255), nullable=True)
    asn = Column(String(64), nullable=True)
    product = Column(String(255), index=True, default="Generic RTSP IP Camera")
    rtsp_url = Column(Text, nullable=False)
    stream_path = Column(String(128), unique=True, index=True, nullable=False)
    auth_status = Column(String(64), index=True, default="unauthenticated")
    risk_level = Column(String(32), index=True, default="high")
    banner = Column(Text, nullable=True)
    last_seen = Column(String(64), nullable=True)
    tags_json = Column(Text, default="[]")
    screenshot_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    is_favorite = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        tags = []
        try:
            if self.tags_json:
                tags = json.loads(self.tags_json)
        except Exception:
            tags = []

        return {
            "id": self.id,
            "ip": self.ip,
            "port": self.port,
            "country_code": self.country_code,
            "country_name": self.country_name,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "org": self.org,
            "isp": self.isp,
            "asn": self.asn,
            "product": self.product,
            "rtsp_url": self.rtsp_url,
            "stream_path": self.stream_path,
            "auth_status": self.auth_status,
            "risk_level": self.risk_level,
            "banner": self.banner,
            "last_seen": self.last_seen,
            "tags": tags,
            "screenshot_url": self.screenshot_url,
            "notes": self.notes,
            "is_favorite": self.is_favorite,
            "hls_url": f"{settings.MEDIAMTX_HLS_URL}/{self.stream_path}",
            "webrtc_url": f"{settings.MEDIAMTX_WHEP_URL}/{self.stream_path}"
        }

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    """Create database schema and populate with initial sample feeds if empty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Check if feeds table is empty, then seed from sample_feeds.json
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count(CameraFeedModel.id)))
        count = result.scalar()
        if count == 0:
            sample_path = Path(settings.SAMPLE_DATA_PATH)
            if sample_path.exists():
                logger.info(f"Seeding database with sample feeds from {sample_path}...")
                with open(sample_path, "r", encoding="utf-8") as f:
                    samples = json.load(f)
                    for item in samples:
                        feed = CameraFeedModel(
                            id=item["id"],
                            ip=item["ip"],
                            port=item.get("port", 554),
                            country_code=item.get("country_code", "XX"),
                            country_name=item.get("country_name", "Unknown"),
                            city=item.get("city"),
                            latitude=item.get("latitude", 0.0),
                            longitude=item.get("longitude", 0.0),
                            org=item.get("org", "Unknown"),
                            isp=item.get("isp"),
                            asn=item.get("asn"),
                            product=item.get("product", "Generic RTSP IP Camera"),
                            rtsp_url=item["rtsp_url"],
                            stream_path=item["stream_path"],
                            auth_status=item.get("auth_status", "unauthenticated"),
                            risk_level=item.get("risk_level", "high"),
                            banner=item.get("banner"),
                            last_seen=item.get("last_seen"),
                            tags_json=json.dumps(item.get("tags", [])),
                            screenshot_url=item.get("screenshot_url"),
                            notes=item.get("notes"),
                            is_favorite=item.get("is_favorite", False)
                        )
                        session.add(feed)
                    await session.commit()
                logger.info(f"Successfully seeded {len(samples)} camera feeds.")

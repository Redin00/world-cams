from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class CameraFeedBase(BaseModel):
    ip: str
    port: int = 554
    country_code: str = "XX"
    country_name: str = "Unknown"
    city: Optional[str] = None
    latitude: float = 0.0
    longitude: float = 0.0
    org: str = "Unknown"
    isp: Optional[str] = None
    asn: Optional[str] = None
    product: str = "Generic RTSP IP Camera"
    rtsp_url: str
    stream_path: str
    auth_status: str = "unauthenticated"  # unauthenticated, default_creds, weak_auth, verified_public
    risk_level: str = "high"             # critical, high, medium, low, info
    banner: Optional[str] = None
    last_seen: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    screenshot_url: Optional[str] = None
    notes: Optional[str] = None
    is_favorite: bool = False

class CameraFeedCreate(CameraFeedBase):
    pass

class CameraFeedResponse(CameraFeedBase):
    id: str
    hls_url: Optional[str] = None
    webrtc_url: Optional[str] = None

class CameraFeedUpdate(BaseModel):
    is_favorite: Optional[bool] = None
    notes: Optional[str] = None
    rtsp_url: Optional[str] = None

class BatchDeleteRequest(BaseModel):
    feed_ids: List[str] = Field(default_factory=list)

class FilterQuery(BaseModel):
    country: Optional[str] = None
    org: Optional[str] = None
    port: Optional[int] = None
    product: Optional[str] = None
    risk_level: Optional[str] = None
    auth_status: Optional[str] = None
    search: Optional[str] = None
    is_favorite: Optional[bool] = None
    limit: int = 1000
    offset: int = 0

class FacetItem(BaseModel):
    name: str
    code: Optional[str] = None
    count: int

class FacetSummary(BaseModel):
    countries: List[FacetItem]
    orgs: List[FacetItem]
    ports: List[FacetItem]
    products: List[FacetItem]
    risk_levels: List[FacetItem]
    total_cameras: int
    unauthenticated_count: int

class StreamStartRequest(BaseModel):
    feed_id: Optional[str] = None
    rtsp_url: Optional[str] = None
    stream_name: Optional[str] = None

class StreamResponse(BaseModel):
    stream_path: str
    rtsp_source: str
    webrtc_url: str
    webrtc_whep_url: str
    hls_url: str
    hls_manifest_url: str
    status: str
    message: Optional[str] = None
    stream_type: Optional[str] = None
    web_url: Optional[str] = None

class ResolveRtspRequest(BaseModel):
    feed_id: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = 554
    product: Optional[str] = ""
    current_url: Optional[str] = ""

class ResolveRtspResponse(BaseModel):
    resolved: bool
    protocol: str
    stream_url: str
    vendor: str
    tested_count: int
    auth: Optional[str] = None
    message: str
    feed_id: Optional[str] = None

class ShodanSearchQuery(BaseModel):
    query: str = 'port:554 has_screenshot:true'
    limit: int = 8000
    import_to_db: bool = True
    mode: str = 'auto'  # 'auto', 'live', 'demo'

class ShodanKeyRequest(BaseModel):
    api_key: str

class ChecklistItem(BaseModel):
    id: str
    title: str
    description: str
    category: str
    command_or_example: Optional[str] = None
    severity: str

class EducationArticle(BaseModel):
    id: str
    title: str
    category: str
    summary: str
    content: str
    checklists: List[ChecklistItem] = Field(default_factory=list)

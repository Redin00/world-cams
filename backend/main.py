import os
import html
import json
import logging
import httpx
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, urljoin, quote

from fastapi import FastAPI, Depends, HTTPException, Query, Header, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response, HTMLResponse, JSONResponse
from sqlalchemy import select, func, or_, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings, get_legacy_ssl_context
from backend.database import get_db, init_db, CameraFeedModel
from backend.models import (
    CameraFeedResponse, CameraFeedCreate, CameraFeedUpdate, BatchDeleteRequest,
    FilterQuery, FacetSummary, FacetItem,
    StreamStartRequest, StreamResponse, ResolveRtspRequest, ResolveRtspResponse,
    RetryCredentialsRequest, RetryCredentialsResponse,
    ShodanSearchQuery, ShodanKeyRequest,
    EducationArticle
)
from backend.security import (
    SecurityWAFMiddleware,
    generate_csrf_token,
    verify_admin_token_constant_time,
    rate_limiter,
    validate_safe_identifier,
    escape_sql_like,
    sanitize_text_field,
    validate_camera_ip,
    validate_camera_url,
    inspect_stream_chunk_safety,
    extract_stream_sources_from_html,
)
from backend.services.mediamtx_service import mediamtx_service
from backend.services.shodan_service import shodan_service
from backend.services.education_service import get_education_articles, get_checklist_items
from backend.services.rtsp_resolver import rtsp_resolver

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("worldcams.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and start MediaMTX
    logger.info("Initializing database...")
    await init_db()

    logger.info("Starting MediaMTX streaming bridge...")
    await mediamtx_service.start()

    yield

    # Shutdown: clean up background processes
    logger.info("Shutting down MediaMTX...")
    mediamtx_service.stop()

app = FastAPI(
    title="WorldCams - Exposed Camera Intelligence & Awareness",
    description="OSINT and Security Awareness Platform for Indexing Exposed RTSP Camera Feeds via Shodan and MediaMTX",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware (restricted to same-origin / local deployment; cross-site credentials disabled)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.APP_PORT}",
        f"http://127.0.0.1:{settings.APP_PORT}",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Token", "X-CSRF-Token", "X-Requested-With"],
)

# WAF, Anti-Bot, Path Traversal, SQLi/XSS & CSRF Protection Middleware
app.add_middleware(SecurityWAFMiddleware)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.endswith((".js", ".css", ".html", "/")) or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api/security/csrf")
async def get_csrf_token_endpoint(request: Request):
    """Issue a signed CSRF token and report active security isolation status."""
    token = request.cookies.get("worldcams_csrf") or generate_csrf_token()
    resp = JSONResponse({
        "csrf_token": token,
        "portal_isolation": "strict_no_exec",
        "waf_protection": "enabled"
    })
    resp.set_cookie(
        key="worldcams_csrf",
        value=token,
        httponly=False,
        samesite="strict",
        path="/"
    )
    return resp


# ==========================================
# Camera Feeds Endpoints
# ==========================================

@app.get("/api/feeds", response_model=Dict[str, Any])
async def list_feeds(
    country: Optional[str] = Query(None, max_length=8, description="Country code (e.g. US, DE)"),
    org: Optional[str] = Query(None, max_length=120, description="Organization / ISP name"),
    port: Optional[int] = Query(None, ge=1, le=65535, description="Port number (e.g. 554, 8554)"),
    product: Optional[str] = Query(None, max_length=120, description="Product / Vendor name"),
    risk_level: Optional[str] = Query(None, max_length=32, description="Risk level (critical, high, medium, low)"),
    auth_status: Optional[str] = Query(None, max_length=64, description="Auth status (unauthenticated, default_creds, etc.)"),
    search: Optional[str] = Query(None, max_length=120, description="Full-text search keyword"),
    is_favorite: Optional[bool] = Query(None, description="Filter favorites"),
    limit: int = Query(25000, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    # Ensure all cameras load even if an older cached browser sends limit=100
    if limit < 1000:
        limit = 25000

    query = select(CameraFeedModel)
    conditions = []

    if country:
        safe_country = validate_safe_identifier(country, "country").upper()
        conditions.append(CameraFeedModel.country_code == safe_country)
    if org:
        safe_org = escape_sql_like(org)
        conditions.append(CameraFeedModel.org.ilike(f"%{safe_org}%", escape="\\"))
    if port:
        conditions.append(CameraFeedModel.port == port)
    if product:
        safe_product = escape_sql_like(product)
        conditions.append(CameraFeedModel.product.ilike(f"%{safe_product}%", escape="\\"))
    if risk_level:
        safe_risk = validate_safe_identifier(risk_level, "risk_level").lower()
        conditions.append(CameraFeedModel.risk_level == safe_risk)
    if auth_status:
        safe_auth = validate_safe_identifier(auth_status, "auth_status").lower()
        conditions.append(CameraFeedModel.auth_status == safe_auth)
    if is_favorite is not None:
        conditions.append(CameraFeedModel.is_favorite == is_favorite)

    if search:
        safe_search = escape_sql_like(search)
        search_filter = or_(
            CameraFeedModel.ip.ilike(f"%{safe_search}%", escape="\\"),
            CameraFeedModel.city.ilike(f"%{safe_search}%", escape="\\"),
            CameraFeedModel.country_name.ilike(f"%{safe_search}%", escape="\\"),
            CameraFeedModel.org.ilike(f"%{safe_search}%", escape="\\"),
            CameraFeedModel.product.ilike(f"%{safe_search}%", escape="\\"),
            CameraFeedModel.banner.ilike(f"%{safe_search}%", escape="\\"),
            CameraFeedModel.notes.ilike(f"%{safe_search}%", escape="\\")
        )
        conditions.append(search_filter)

    if conditions:
        query = query.where(and_(*conditions))

    # Total count for filtered query
    count_query = select(func.count(CameraFeedModel.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total_res = await db.execute(count_query)
    total = total_res.scalar() or 0

    # Pagination
    query = query.offset(offset).limit(limit)
    res = await db.execute(query)
    records = res.scalars().all()

    items = [record.to_dict() for record in records]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

@app.get("/api/feeds/random")
async def get_random_feed(db: AsyncSession = Depends(get_db)):
    """Return a single random camera feed from the entire database."""
    query = select(CameraFeedModel).order_by(func.random()).limit(1)
    res = await db.execute(query)
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="No camera feeds available in database")
    return record.to_dict()

@app.get("/api/feeds/{feed_id}")
async def get_feed(feed_id: str, db: AsyncSession = Depends(get_db)):
    safe_id = validate_safe_identifier(feed_id, "feed_id")
    res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_id))
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Camera feed not found")
    return record.to_dict()

@app.patch("/api/feeds/{feed_id}")
async def update_feed(feed_id: str, payload: CameraFeedUpdate, db: AsyncSession = Depends(get_db)):
    safe_id = validate_safe_identifier(feed_id, "feed_id")
    res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_id))
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Camera feed not found")

    if payload.is_favorite is not None:
        record.is_favorite = payload.is_favorite
    if payload.notes is not None:
        record.notes = sanitize_text_field(payload.notes, max_len=1000)
    if payload.rtsp_url is not None:
        record.rtsp_url = validate_camera_url(payload.rtsp_url)

    await db.commit()
    await db.refresh(record)
    return record.to_dict()

def check_admin_token(x_admin_token: Optional[str], request: Optional[Request] = None):
    """Validate administrator access token with constant-time comparison and brute-force lockout."""
    client_ip = (request.client.host if (request and request.client) else "local")
    if rate_limiter.is_auth_locked_out(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many failed administrator authentication attempts. Try again in 60 seconds."
        )
    if not verify_admin_token_constant_time(x_admin_token):
        rate_limiter.record_auth_failure(client_ip)
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Valid Administrator Access Token (X-Admin-Token) required."
        )
    rate_limiter.clear_auth_failures(client_ip)

@app.post("/api/admin/verify")
async def verify_admin(request: Request, x_admin_token: Optional[str] = Header(None)):
    """Verify administrator access key."""
    check_admin_token(x_admin_token, request)
    return {"status": "authorized", "valid": True, "message": "Admin privileges verified"}

@app.delete("/api/feeds/{feed_id}")
async def delete_feed(
    feed_id: str,
    request: Request,
    x_admin_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a camera feed from the database (Requires Admin Token).
    """
    check_admin_token(x_admin_token, request)
    safe_id = validate_safe_identifier(feed_id, "feed_id")

    res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_id))
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Camera feed not found")

    cam_identifier = f"{record.ip}:{record.port}"
    await db.delete(record)
    await db.commit()
    logger.info(f"Admin deleted camera feed {safe_id} [{cam_identifier}].")
    return {
        "status": "deleted",
        "feed_id": safe_id,
        "message": f"Camera feed {cam_identifier} successfully removed from database."
    }

@app.post("/api/feeds/batch-delete")
async def batch_delete_feeds(
    payload: BatchDeleteRequest,
    request: Request,
    x_admin_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove multiple camera feeds from the database at once (Requires Admin Token).
    """
    check_admin_token(x_admin_token, request)

    if not payload.feed_ids:
        return {"status": "success", "deleted_count": 0, "message": "No feed IDs provided"}

    safe_ids = [validate_safe_identifier(fid, "feed_id") for fid in payload.feed_ids[:5000]]
    stmt = delete(CameraFeedModel).where(CameraFeedModel.id.in_(safe_ids))
    res = await db.execute(stmt)
    await db.commit()
    deleted_count = res.rowcount or 0
    logger.info(f"Admin batch-deleted {deleted_count} camera feeds.")
    return {
        "status": "success",
        "deleted_count": deleted_count,
        "message": f"Successfully deleted {deleted_count} camera feeds from database."
    }

# ==========================================
# Facets & Filters Data
# ==========================================

@app.get("/api/facets", response_model=FacetSummary)
async def get_facets(db: AsyncSession = Depends(get_db)):
    """Return aggregated facet statistics for dynamic filters."""
    # Country facets
    c_query = select(CameraFeedModel.country_name, CameraFeedModel.country_code, func.count(CameraFeedModel.id))\
        .group_by(CameraFeedModel.country_name, CameraFeedModel.country_code)\
        .order_by(func.count(CameraFeedModel.id).desc()).limit(30)
    c_res = await db.execute(c_query)
    countries = [
        FacetItem(name=r[0] or "Unknown", code=r[1], count=r[2])
        for r in c_res.all()
    ]

    # Org facets
    o_query = select(CameraFeedModel.org, func.count(CameraFeedModel.id))\
        .group_by(CameraFeedModel.org)\
        .order_by(func.count(CameraFeedModel.id).desc()).limit(20)
    o_res = await db.execute(o_query)
    orgs = [FacetItem(name=r[0] or "Unknown", count=r[1]) for r in o_res.all()]

    # Port facets
    p_query = select(CameraFeedModel.port, func.count(CameraFeedModel.id))\
        .group_by(CameraFeedModel.port)\
        .order_by(func.count(CameraFeedModel.id).desc()).limit(15)
    p_res = await db.execute(p_query)
    ports = [FacetItem(name=str(r[0]), count=r[1]) for r in p_res.all()]

    # Product facets
    prod_query = select(CameraFeedModel.product, func.count(CameraFeedModel.id))\
        .group_by(CameraFeedModel.product)\
        .order_by(func.count(CameraFeedModel.id).desc()).limit(20)
    prod_res = await db.execute(prod_query)
    products = [FacetItem(name=r[0] or "Generic RTSP", count=r[1]) for r in prod_res.all()]

    # Risk level facets
    risk_query = select(CameraFeedModel.risk_level, func.count(CameraFeedModel.id))\
        .group_by(CameraFeedModel.risk_level)\
        .order_by(func.count(CameraFeedModel.id).desc())
    risk_res = await db.execute(risk_query)
    risks = [FacetItem(name=r[0] or "unknown", count=r[1]) for r in risk_res.all()]

    # Total, unauthenticated, and favorites count
    total_res = await db.execute(select(func.count(CameraFeedModel.id)))
    total = total_res.scalar() or 0

    unauth_res = await db.execute(
        select(func.count(CameraFeedModel.id)).where(CameraFeedModel.auth_status == "unauthenticated")
    )
    unauth_count = unauth_res.scalar() or 0

    fav_res = await db.execute(
        select(func.count(CameraFeedModel.id)).where(CameraFeedModel.is_favorite == True)
    )
    fav_count = fav_res.scalar() or 0

    return FacetSummary(
        countries=countries,
        orgs=orgs,
        ports=ports,
        products=products,
        risk_levels=risks,
        total_cameras=total,
        unauthenticated_count=unauth_count,
        favorites_count=fav_count
    )

# ==========================================
# MediaMTX Streaming Controller
# ==========================================

def _clean_http_url(u: Optional[str]) -> str:
    if not u:
        return ""
    return u.replace("&amp;", "&").strip()


def _is_specific_http_stream_url(u: Optional[str], port: int) -> bool:
    if not u or "unsplash" in u:
        return False
    cleaned = _clean_http_url(u).rstrip("/")
    if cleaned.endswith(f":{port}") or cleaned.endswith("/live") or cleaned.endswith("/onvif1"):
        return False
    return cleaned.startswith(("http://", "https://"))


@app.post("/api/streams/play", response_model=StreamResponse)
async def play_stream(req: StreamStartRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    On-demand RTSP / HTTP streaming proxy controller.
    Automatically resolves working RTSP/HTTP subpaths for camera feeds,
    extracts media sources from isolated web portals without executing code,
    routes WebRTC via MediaMTX, and handles direct HTTP Web camera streams.
    """
    stream_path = validate_safe_identifier(req.stream_name, "stream_name") if req.stream_name else None
    rtsp_url = validate_camera_url(req.rtsp_url) if req.rtsp_url else None
    feed = None

    if req.feed_id:
        safe_feed_id = validate_safe_identifier(req.feed_id, "feed_id")
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_feed_id))
        feed = res.scalar_one_or_none()
        if not feed:
            raise HTTPException(status_code=404, detail="Feed ID not found")
        rtsp_url = feed.rtsp_url
        stream_path = feed.stream_path

        # ---------------------------------------------------------------------
        # Case A: Camera on Web Port (443, 80, 8080, 8050, 8000) or HTTP/HTTPS URL
        # ---------------------------------------------------------------------
        if feed.port in (443, 80, 8080, 8050, 8000) or (rtsp_url and rtsp_url.startswith(("http://", "https://"))):
            scheme = "https" if feed.port == 443 or (rtsp_url and rtsp_url.startswith("https://")) else "http"
            has_valid_stream = _is_specific_http_stream_url(feed.screenshot_url, feed.port) or _is_specific_http_stream_url(rtsp_url, feed.port)

            if has_valid_stream:
                clean_shot = _clean_http_url(feed.screenshot_url) if _is_specific_http_stream_url(feed.screenshot_url, feed.port) else None
                clean_rtsp = _clean_http_url(rtsp_url) if _is_specific_http_stream_url(rtsp_url, feed.port) else None
                effective_stream = clean_shot or clean_rtsp
                if effective_stream and effective_stream != feed.rtsp_url:
                    feed.rtsp_url = effective_stream
                    if clean_shot and clean_shot != feed.screenshot_url:
                        feed.screenshot_url = clean_shot
                    await db.commit()
                rtsp_url = effective_stream or rtsp_url

            # Auto-resolve live MJPEG video or JPEG snapshot subpath (or extract from portal HTML)
            if not has_valid_stream:
                product_hint = f"{feed.product or ''} {feed.banner or ''}".strip()
                resolve_res = await rtsp_resolver.resolve_camera(feed.ip, feed.port, product_hint, rtsp_url)
                if resolve_res.get("resolved"):
                    stype = resolve_res.get("stream_type", "mjpeg")
                    resolved_url = _clean_http_url(resolve_res["stream_url"])
                    if stype in ("mjpeg", "jpeg") or resolve_res.get("protocol") in ("http", "https"):
                        feed.rtsp_url = resolved_url
                        feed.screenshot_url = resolved_url
                        feed.notes = f"[Auto-Resolved {resolve_res.get('vendor','').upper()}] {resolve_res.get('message','')}"
                        await db.commit()
                        await db.refresh(feed)
                        return StreamResponse(
                            stream_path=feed.stream_path,
                            rtsp_source=resolved_url,
                            webrtc_url="",
                            webrtc_whep_url="",
                            hls_url="",
                            hls_manifest_url="",
                            status="http_stream",
                            stream_type=stype,
                            web_url=f"{scheme}://{feed.ip}:{feed.port}/",
                            message=resolve_res.get("message", f"{scheme.upper()} Live Stream active")
                        )
                    elif stype == "web_ui":
                        return StreamResponse(
                            stream_path=feed.stream_path,
                            rtsp_source=resolved_url,
                            webrtc_url="",
                            webrtc_whep_url="",
                            hls_url="",
                            hls_manifest_url="",
                            status="web_ui",
                            stream_type="web_ui",
                            web_url=f"{scheme}://{feed.ip}:{feed.port}/",
                            message=resolve_res.get("message", "Isolated Camera Web Portal (Code Execution Blocked)")
                        )

            genuine_snapshot = _clean_http_url(feed.screenshot_url) if _is_specific_http_stream_url(feed.screenshot_url, feed.port) else None
            genuine_rtsp = _clean_http_url(rtsp_url) if _is_specific_http_stream_url(rtsp_url, feed.port) else None
            target_stream = genuine_snapshot or genuine_rtsp or f"{scheme}://{feed.ip}:{feed.port}/"

            # If target_stream is generic root, probe root in isolated mode to extract stream source or report isolated portal
            if target_stream.rstrip("/").endswith(f":{feed.port}"):
                schemes_to_try = [scheme]
                if scheme == "https" and feed.port == 443:
                    schemes_to_try.append("http")
                for s in schemes_to_try:
                    verify_arg = get_legacy_ssl_context() if s == "https" else False
                    try:
                        async with httpx.AsyncClient(timeout=2.0, verify=verify_arg, follow_redirects=False) as client:
                            portal_root = f"{s}://{feed.ip}:{feed.port}/"
                            r_root = await client.get(portal_root)
                            if r_root.status_code in (200, 301, 302, 401):
                                proto_note = "HTTPS" if s == "https" else "Plaintext HTTP"
                                # Attempt static stream source extraction from portal HTML
                                if r_root.status_code == 200 and r_root.text:
                                    extracted = extract_stream_sources_from_html(r_root.text[:262144], portal_root)
                                    for cand_http in extracted.get("http_candidates", []):
                                        cp = urlparse(cand_http)
                                        c_path = (cp.path or "/") + (f"?{cp.query}" if cp.query else "")
                                        ok_c, resolved_c, stype_c = await rtsp_resolver.probe_http_path(
                                            feed.ip, feed.port, c_path, timeout=1.5, is_https=(s == "https")
                                        )
                                        if ok_c:
                                            feed.rtsp_url = resolved_c
                                            feed.screenshot_url = resolved_c
                                            await db.commit()
                                            return StreamResponse(
                                                stream_path=feed.stream_path,
                                                rtsp_source=resolved_c,
                                                webrtc_url="",
                                                webrtc_whep_url="",
                                                hls_url="",
                                                hls_manifest_url="",
                                                status="http_stream",
                                                stream_type=stype_c,
                                                web_url=portal_root,
                                                message=f"Extracted {stype_c.upper()} stream from isolated web portal ({proto_note})"
                                            )
                                return StreamResponse(
                                    stream_path=feed.stream_path,
                                    rtsp_source=portal_root,
                                    webrtc_url="",
                                    webrtc_whep_url="",
                                    hls_url="",
                                    hls_manifest_url="",
                                    status="web_ui",
                                    stream_type="web_ui",
                                    web_url=portal_root,
                                    message=f"Isolated Camera Web Portal on port {feed.port} ({proto_note}) — Code Execution Blocked"
                                )
                    except Exception as e:
                        err_str = str(e).lower()
                        if s == "https" and any(k in err_str for k in ["ssl", "record", "version", "handshake", "connect"]):
                            continue
                        break

                return StreamResponse(
                    stream_path=feed.stream_path,
                    rtsp_source=target_stream,
                    webrtc_url="",
                    webrtc_whep_url="",
                    hls_url="",
                    hls_manifest_url="",
                    status="offline",
                    stream_type="offline",
                    web_url=f"{scheme}://{feed.ip}:{feed.port}/",
                    message=f"Port {feed.port} unreachable or camera offline"
                )

            inferred_type = "mjpeg" if any(k in target_stream.lower() for k in ("mjpg", "mjpeg", "nphmotionjpeg", "video.cgi", "faststream", "videostream.cgi")) else "jpeg"
            return StreamResponse(
                stream_path=feed.stream_path,
                rtsp_source=target_stream,
                webrtc_url="",
                webrtc_whep_url="",
                hls_url="",
                hls_manifest_url="",
                status="http_stream",
                stream_type=inferred_type,
                web_url=f"{scheme}://{feed.ip}:{feed.port}/",
                message=f"{scheme.upper()} Live Stream / Snapshot endpoint ready"
            )

        # ---------------------------------------------------------------------
        # Case B: Camera on Native RTSP Port (554, 8554, etc.)
        # Verify current path; if non-working or dummy '/live', perform continuous candidate probing
        # ---------------------------------------------------------------------
        parsed_u = urlparse(rtsp_url) if rtsp_url else None
        parsed_p = (parsed_u.path + (f"?{parsed_u.query}" if parsed_u.query else "")) if parsed_u else ""
        existing_creds = (parsed_u.username, parsed_u.password or "") if (parsed_u and parsed_u.username) else None
        product_hint = f"{feed.product or ''} {feed.banner or ''}".strip()
        current_works = False
        if rtsp_url and parsed_p and parsed_p not in ("/", "/live", "/onvif1"):
            is_ok, code, target_url, realm, nonce = await rtsp_resolver.probe_rtsp_path(
                feed.ip, feed.port, parsed_p, creds=existing_creds, timeout=0.8
            )
            if is_ok:
                rtsp_url = target_url
                current_works = True
            elif code == 401:
                # Camera requires authentication: retry with default password list based on vendor
                retry_ok, auth_url, used_cred = await rtsp_resolver.retry_rtsp_with_default_creds(
                    ip=feed.ip,
                    port=feed.port,
                    path=parsed_p,
                    product=product_hint,
                    realm=realm,
                    nonce=nonce,
                    timeout=1.0
                )
                if retry_ok and used_cred:
                    rtsp_url = auth_url
                    feed.rtsp_url = auth_url
                    feed.auth_status = "default_creds"
                    feed.notes = f"[Auto-Auth {feed.product or 'Vendor'}] Authenticated with default credentials ({used_cred[0]}:{used_cred[1]})"
                    await db.commit()
                    await db.refresh(feed)
                    current_works = True

        if not current_works:
            resolve_res = await rtsp_resolver.resolve_camera(feed.ip, feed.port, product_hint, rtsp_url, continuous=True)
            if resolve_res.get("resolved"):
                if resolve_res.get("protocol") == "rtsp":
                    rtsp_url = resolve_res["stream_url"]
                    feed.rtsp_url = rtsp_url
                    feed.notes = f"[Auto-Resolved {resolve_res.get('vendor','').upper()}] {resolve_res.get('message','')}"
                    if resolve_res.get("auth") and resolve_res["auth"] != "unauthenticated":
                        feed.auth_status = "default_creds"
                    await db.commit()
                    await db.refresh(feed)
                elif resolve_res.get("protocol") in ("http", "https"):
                    feed.screenshot_url = resolve_res["stream_url"]
                    feed.rtsp_url = resolve_res["stream_url"]
                    await db.commit()
                    return StreamResponse(
                        stream_path=feed.stream_path,
                        rtsp_source=resolve_res["stream_url"],
                        webrtc_url="",
                        webrtc_whep_url="",
                        hls_url="",
                        hls_manifest_url="",
                        status="http_stream",
                        message=resolve_res.get("message", "HTTP Stream resolved")
                    )

    if not rtsp_url:
        raise HTTPException(status_code=400, detail="Missing rtsp_url or feed_id")

    if not stream_path:
        stream_path = f"stream_{abs(hash(rtsp_url)) % 1000000}"

    route_info = await mediamtx_service.register_stream(stream_path, rtsp_url)

    # Dynamic host resolution for external VPS/remote clients:
    # If MEDIAMTX_PUBLIC_HOST is not configured, dynamically rewrite any
    # 'localhost' or '127.0.0.1' with the host IP/domain used by the client.
    if not settings.MEDIAMTX_PUBLIC_HOST:
        client_host = request.headers.get("x-forwarded-host") or request.headers.get("host", "").split(":")[0] or ""
        if client_host and client_host not in ("localhost", "127.0.0.1", "0.0.0.0"):
            for k in ("webrtc_url", "webrtc_whep_url", "hls_url", "hls_manifest_url"):
                if route_info.get(k):
                    route_info[k] = (
                        route_info[k]
                        .replace("http://localhost:", f"http://{client_host}:")
                        .replace("http://127.0.0.1:", f"http://{client_host}:")
                    )

    return StreamResponse(**route_info)


@app.get("/api/proxy/stream")
async def proxy_camera_stream(
    feed_id: Optional[str] = Query(None, max_length=128),
    url: Optional[str] = Query(None, max_length=2048),
    ip: Optional[str] = Query(None, max_length=64),
    port: Optional[int] = Query(None, ge=1, le=65535),
    db: AsyncSession = Depends(get_db)
):
    """
    Isolated Media Stream Proxy:
    - Validates target against SSRF and forbidden ports.
    - Never forwards HTML, JavaScript, SVG, or executable binaries to the client (Anti-Honeypot Protection).
    - If the camera responds with a Web Portal HTML page, automatically parses the HTML server-side
      to extract the embedded MJPEG/JPEG stream source and transmits only verified binary frames.
    """
    target_url = _clean_http_url(url) if url else None
    if target_url:
        target_url = validate_camera_url(target_url, allowed_schemes={"http", "https"})

    feed = None
    if feed_id:
        safe_feed_id = validate_safe_identifier(feed_id, "feed_id")
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_feed_id))
        feed = res.scalar_one_or_none()
        if feed:
            validate_camera_ip(feed.ip, feed.port)
            scheme = "https" if feed.port == 443 or (feed.rtsp_url and feed.rtsp_url.startswith("https://")) else "http"
            if _is_specific_http_stream_url(feed.screenshot_url, feed.port):
                target_url = _clean_http_url(feed.screenshot_url)
            elif _is_specific_http_stream_url(feed.rtsp_url, feed.port):
                target_url = _clean_http_url(feed.rtsp_url)
            else:
                # Auto-resolve live MJPEG or snapshot endpoint on the fly
                product_hint = f"{feed.product or ''} {feed.banner or ''}".strip()
                resolved = await rtsp_resolver.resolve_camera(feed.ip, feed.port, product_hint, feed.rtsp_url)
                if resolved.get("resolved") and resolved.get("stream_type") in ("mjpeg", "jpeg"):
                    target_url = _clean_http_url(resolved["stream_url"])
                    feed.screenshot_url = target_url
                    feed.rtsp_url = target_url
                    await db.commit()
                else:
                    target_url = f"{scheme}://{feed.ip}:{feed.port}/"
    elif not target_url and ip and port:
        safe_ip = validate_camera_ip(ip, port)
        scheme = "https" if port == 443 else "http"
        resolved = await rtsp_resolver.resolve_camera(safe_ip, port)
        if resolved.get("resolved") and resolved.get("stream_type") in ("mjpeg", "jpeg"):
            target_url = _clean_http_url(resolved["stream_url"])
        else:
            target_url = f"{scheme}://{safe_ip}:{port}/"

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing target stream URL or feed_id")

    # Validate final target_url against SSRF
    target_url = validate_camera_url(target_url, allowed_schemes={"http", "https"})

    # Replace dynamic Mobotix/Axis COUNTER token with current timestamp so fresh frames are served
    if "COUNTER" in target_url:
        import time
        target_url = target_url.replace("COUNTER", str(int(time.time() * 1000)))

    candidates = [target_url]
    if target_url.startswith("https://") and (":443" in target_url or (feed_id and feed and feed.port == 443)):
        candidates.append(target_url.replace("https://", "http://", 1))

    async def _open_safe_camera_stream(cand_url: str):
        """Connect to camera URL with same-IP redirect validation (max 2 hops)."""
        verify_arg = get_legacy_ssl_context() if cand_url.startswith("https://") else False
        client_inst = httpx.AsyncClient(verify=verify_arg, timeout=10.0, follow_redirects=False)
        curr_url = cand_url
        orig_host = (urlparse(cand_url).hostname or "").lower()
        try:
            for _ in range(3):
                req_obj = client_inst.build_request("GET", curr_url)
                resp_obj = await client_inst.send(req_obj, stream=True)
                if resp_obj.status_code in (301, 302, 303, 307, 308):
                    loc = resp_obj.headers.get("location", "")
                    await resp_obj.aclose()
                    if not loc:
                        break
                    next_url = urljoin(curr_url, loc)
                    next_parsed = urlparse(next_url)
                    if next_parsed.scheme not in ("http", "https") or (next_parsed.hostname or "").lower() != orig_host:
                        raise HTTPException(status_code=403, detail="SSRF Protection: Cross-host redirect from camera blocked")
                    curr_url = next_url
                    continue
                return client_inst, resp_obj, curr_url
            await client_inst.aclose()
            return None, None, curr_url
        except Exception:
            await client_inst.aclose()
            raise

    r = None
    client = None
    last_err = None

    for cand in candidates:
        try:
            client, r, target_url = await _open_safe_camera_stream(cand)
            if r is not None:
                break
        except HTTPException:
            raise
        except Exception as e:
            last_err = e
            err_msg = str(e).lower()
            if cand.startswith("https://") and any(k in err_msg for k in ["ssl", "record", "version", "handshake", "connect"]):
                logger.info(f"Port 443 SSL error on {cand} ({e}). Retrying with plaintext HTTP fallback...")
                continue
            break

    if not r or not client:
        logger.warning(f"Proxy stream error for {target_url}: {last_err}")
        raise HTTPException(status_code=502, detail=f"Failed to connect to camera endpoint: {str(last_err)}")

    try:
        ctype = r.headers.get("content-type", "image/jpeg")
        aiter = r.aiter_bytes()
        first_chunk = b""
        try:
            async for ch in aiter:
                if ch:
                    first_chunk = ch
                    break
        except Exception:
            first_chunk = b""

        # Check if upstream returned an HTML Web Portal instead of a binary media stream
        stripped_head = first_chunk.lstrip()[:128].lower()
        is_html_portal = (
            "text/html" in ctype.lower()
            or "application/xhtml" in ctype.lower()
            or stripped_head.startswith((b"<!doctype", b"<html", b"<head", b"<body", b"<script", b"<iframe"))
        )

        if is_html_portal:
            # Read up to 256 KB of the HTML portal server-side, close connection, and extract embedded stream source!
            html_bytes = bytearray(first_chunk[:262144])
            try:
                async for ch in aiter:
                    if len(html_bytes) + len(ch) > 262144:
                        html_bytes.extend(ch[: 262144 - len(html_bytes)])
                        break
                    html_bytes.extend(ch)
            except Exception:
                pass
            finally:
                await r.aclose()
                await client.aclose()

            html_text = html_bytes.decode("utf-8", errors="ignore")
            extracted = extract_stream_sources_from_html(html_text, target_url)

            # Try each extracted candidate stream URL on the same camera IP
            for cand_media_url in extracted.get("http_candidates", []):
                try:
                    c_client, c_resp, eff_media_url = await _open_safe_camera_stream(cand_media_url)
                    if not c_resp or not c_client:
                        continue
                    c_ctype = c_resp.headers.get("content-type", "image/jpeg")
                    c_iter = c_resp.aiter_bytes()
                    c_first = b""
                    async for ch in c_iter:
                        if ch:
                            c_first = ch
                            break
                    is_ok_media, _ = inspect_stream_chunk_safety(c_first, c_ctype)
                    if is_ok_media and c_resp.status_code == 200:
                        if feed:
                            feed.rtsp_url = eff_media_url
                            feed.screenshot_url = eff_media_url
                            await db.commit()
                        r = c_resp
                        client = c_client
                        ctype = c_ctype
                        first_chunk = c_first
                        aiter = c_iter
                        target_url = eff_media_url
                        is_html_portal = False
                        logger.info(f"[Portal Extractor] Extracted & verified live stream from web portal: {eff_media_url}")
                        break
                    else:
                        await c_resp.aclose()
                        await c_client.aclose()
                except Exception:
                    continue

            if is_html_portal:
                # Never forward HTML/JS to the browser!
                return JSONResponse(
                    status_code=415,
                    content={
                        "status": "isolated_web_portal",
                        "code_execution_blocked": True,
                        "portal_title": extracted.get("portal_title", "Camera Web Portal"),
                        "blocked_scripts_count": extracted.get("blocked_scripts_count", 0),
                        "detail": "Endpoint returned an HTML web portal with no unauthenticated video stream. Remote code execution blocked."
                    },
                    headers={
                        "X-Stream-Type": "web_ui",
                        "X-Camera-Status": "isolated_web_portal",
                        "X-Content-Type-Options": "nosniff",
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                    }
                )

        # Verify binary media safety (blocks PE MZ, ELF, ZIP, SVG, HTML/Script honeypots)
        is_safe, safety_reason = inspect_stream_chunk_safety(first_chunk, ctype)
        if not is_safe:
            await r.aclose()
            await client.aclose()
            logger.warning(f"[HONEYPOT PROTECTION] Blocked unsafe stream payload from {target_url}: {safety_reason}")
            return JSONResponse(
                status_code=415,
                content={
                    "status": "blocked_unsafe_payload",
                    "code_execution_blocked": True,
                    "detail": safety_reason
                },
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                }
            )

        # Force safe media Content-Type if camera sent generic header
        safe_media_type = ctype
        if "multipart/x-mixed-replace" not in ctype.lower() and not any(
            m in ctype.lower() for m in ("image/jpeg", "image/png", "image/gif", "image/webp", "video/")
        ):
            safe_media_type = "image/jpeg"

        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-Extracted-Stream-Source": target_url,
            "Content-Disposition": 'inline; filename="camera_stream.jpg"',
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self' data:; media-src 'self'; "
                "script-src 'none'; style-src 'none'; object-src 'none'; sandbox"
            ),
        }

        async def stream_generator():
            max_bytes = 64 * 1024 * 1024  # 64 MB safety cap per stream session
            sent = len(first_chunk)
            try:
                if first_chunk:
                    yield first_chunk
                async for chunk in aiter:
                    sent += len(chunk)
                    if sent > max_bytes:
                        break
                    yield chunk
            except Exception:
                pass
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            status_code=r.status_code,
            media_type=safe_media_type,
            headers=headers
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Proxy stream processing error for {target_url}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to stream camera endpoint: {str(e)}")


@app.get("/api/proxy/web")
async def proxy_web_console(
    feed_id: Optional[str] = Query(None, max_length=128),
    url: Optional[str] = Query(None, max_length=2048),
    format: Optional[str] = Query(None, max_length=16),
    db: AsyncSession = Depends(get_db)
):
    """
    Isolated Camera Web Portal Source Extractor & Inspector (Anti-Honeypot Protection):
    NEVER executes or renders raw HTML, JavaScript, iframes, or plugins from the remote camera.
    Instead, fetches the portal source server-side, extracts any embedded video/snapshot stream URLs
    for direct screen transmission, and presents a sanitized, read-only source inspection report.
    """
    target_url = _clean_http_url(url) if url else None
    if target_url:
        target_url = validate_camera_url(target_url, allowed_schemes={"http", "https"})

    feed = None
    if feed_id:
        safe_feed_id = validate_safe_identifier(feed_id, "feed_id")
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_feed_id))
        feed = res.scalar_one_or_none()
        if feed:
            validate_camera_ip(feed.ip, feed.port)
            scheme = "https" if feed.port == 443 or (feed.rtsp_url and feed.rtsp_url.startswith("https://")) else "http"
            target_url = f"{scheme}://{feed.ip}:{feed.port}/"

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing target URL or feed_id")

    target_url = validate_camera_url(target_url, allowed_schemes={"http", "https"})

    candidates = [target_url]
    if target_url.startswith("https://") and (":443" in target_url or (feed_id and feed and feed.port == 443)):
        candidates.append(target_url.replace("https://", "http://", 1))

    resp_status = 0
    resp_headers: Dict[str, str] = {}
    html_text = ""
    effective_url = target_url
    last_err = None

    for cand in candidates:
        verify_arg = get_legacy_ssl_context() if cand.startswith("https://") else False
        orig_host = (urlparse(cand).hostname or "").lower()
        try:
            async with httpx.AsyncClient(verify=verify_arg, timeout=6.0, follow_redirects=False) as client:
                curr = cand
                for _ in range(3):
                    async with client.stream("GET", curr) as r:
                        if r.status_code in (301, 302, 303, 307, 308):
                            loc = r.headers.get("location", "")
                            if loc:
                                nxt = urljoin(curr, loc)
                                np = urlparse(nxt)
                                if np.scheme in ("http", "https") and (np.hostname or "").lower() == orig_host:
                                    curr = nxt
                                    continue
                        resp_status = r.status_code
                        resp_headers = dict(r.headers)
                        buf = bytearray()
                        async for ch in r.aiter_bytes():
                            if len(buf) + len(ch) > 262144:
                                buf.extend(ch[: 262144 - len(buf)])
                                break
                            buf.extend(ch)
                        html_text = buf.decode("utf-8", errors="ignore")
                        effective_url = curr
                        break
                if resp_status > 0:
                    break
        except Exception as e:
            last_err = e
            err_msg = str(e).lower()
            if cand.startswith("https://") and any(k in err_msg for k in ["ssl", "record", "version", "handshake", "connect"]):
                logger.info(f"Port 443 Web portal SSL error for {cand} ({e}). Retrying with plaintext HTTP fallback...")
                continue
            break

    strict_isolation_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
            "script-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'; sandbox"
        )
    }

    if resp_status == 0:
        if format == "json":
            return JSONResponse(
                status_code=502,
                content={
                    "isolated": True,
                    "code_execution_blocked": True,
                    "reachable": False,
                    "target_url": effective_url,
                    "detail": f"Camera web portal unreachable: {str(last_err)}"
                },
                headers=strict_isolation_headers
            )
        safe_err = html.escape(str(last_err or "Connection timed out"))
        safe_target = html.escape(effective_url)
        return HTMLResponse(
            content=(
                "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Isolated Portal Gateway</title></head>"
                "<body style='background:#0b0f19;color:#94a3b8;font-family:monospace;padding:24px;text-align:center;'>"
                "<h3 style='color:#f87171;'>🛡️ Isolated Portal Gateway — Target Unreachable</h3>"
                f"<p>Target: <code>{safe_target}</code></p>"
                f"<p>Error: <code>{safe_err}</code></p>"
                "</body></html>"
            ),
            status_code=200,
            headers=strict_isolation_headers
        )

    # Statically parse the HTML source to extract candidate stream URLs without executing any code
    extracted = extract_stream_sources_from_html(html_text, effective_url)
    parsed_eff = urlparse(effective_url)
    cam_ip = parsed_eff.hostname or (feed.ip if feed else "")
    cam_port = parsed_eff.port or (feed.port if feed else (443 if parsed_eff.scheme == "https" else 80))
    is_https = (parsed_eff.scheme == "https")

    verified_stream_url = None
    verified_stream_type = None

    for cand_http in extracted.get("http_candidates", []):
        cp = urlparse(cand_http)
        c_path = (cp.path or "/") + (f"?{cp.query}" if cp.query else "")
        ok_c, resolved_c, stype_c = await rtsp_resolver.probe_http_path(
            cam_ip, cam_port, c_path, timeout=1.5, is_https=is_https
        )
        if ok_c:
            verified_stream_url = resolved_c
            verified_stream_type = stype_c
            if feed:
                feed.rtsp_url = resolved_c
                feed.screenshot_url = resolved_c
                await db.commit()
            break

    server_hdr = sanitize_text_field(resp_headers.get("server", "Unknown"), max_len=120)
    auth_hdr = sanitize_text_field(resp_headers.get("www-authenticate", "None"), max_len=120)

    if format == "json":
        return JSONResponse(
            status_code=200,
            content={
                "isolated": True,
                "code_execution_blocked": True,
                "reachable": True,
                "target_url": effective_url,
                "status_code": resp_status,
                "portal_title": extracted["portal_title"],
                "server_header": server_hdr,
                "auth_header": auth_hdr,
                "extracted_stream_url": verified_stream_url,
                "extracted_stream_type": verified_stream_type,
                "http_candidates": extracted["http_candidates"],
                "rtsp_candidates": extracted["rtsp_candidates"],
                "blocked_scripts_count": extracted["blocked_scripts_count"],
                "blocked_objects_count": extracted["blocked_objects_count"],
                "source_preview": extracted["raw_source_preview"],
            },
            headers=strict_isolation_headers
        )

    # Render a completely static, zero-JS Isolated Source & Stream Inspector HTML view
    safe_title = html.escape(extracted["portal_title"])
    safe_url = html.escape(effective_url)
    safe_server = html.escape(server_hdr)
    safe_auth = html.escape(auth_hdr)
    escaped_source = extracted["escaped_source_preview"] or "&lt;!-- Empty response body --&gt;"
    blocked_scripts = extracted["blocked_scripts_count"]
    blocked_objs = extracted["blocked_objects_count"]

    stream_section_html = ""
    if verified_stream_url:
        proxied_img_src = f"/api/proxy/stream?url={quote(verified_stream_url, safe='')}"
        safe_v_url = html.escape(verified_stream_url)
        stream_section_html = (
            "<div style='background:#111827;border:1px solid #10b981;border-radius:8px;padding:16px;margin-bottom:18px;'>"
            "<div style='color:#10b981;font-weight:700;margin-bottom:8px;'>✓ EXTRACTED LIVE MEDIA STREAM FROM PORTAL</div>"
            f"<div style='font-size:12px;color:#94a3b8;margin-bottom:10px;'>Source URL: <code>{safe_v_url}</code></div>"
            f"<img src='{proxied_img_src}' alt='Extracted Stream' style='max-width:100%;max-height:420px;border-radius:6px;border:1px solid #1e293b;display:block;margin:0 auto;'>"
            "</div>"
        )
    else:
        stream_section_html = (
            "<div style='background:#111827;border:1px solid #334155;border-radius:8px;padding:14px;margin-bottom:18px;color:#cbd5e1;font-size:13px;'>"
            "No unauthenticated direct video stream was found embedded in the portal HTML (authentication required by device firmware)."
            "</div>"
        )

    inspector_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Isolated Portal Inspector // {safe_title}</title>
</head>
<body style="background:#0b0f19;color:#e2e8f0;font-family:'Fira Code',monospace,sans-serif;padding:20px;margin:0;line-height:1.5;">
  <div style="max-width:960px;margin:0 auto;">
    <div style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.4);color:#34d399;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:13px;">
      <strong>🛡️ ANTI-HONEYPOT ISOLATION ACTIVE:</strong> Remote HTML rendering, JavaScript (<code>{blocked_scripts}</code> scripts), and embedded objects/iframes (<code>{blocked_objs}</code>) have been stripped and blocked from executing.
    </div>
    <h2 style="margin:0 0 8px 0;font-size:18px;color:#f8fafc;">{safe_title}</h2>
    <div style="font-size:12px;color:#94a3b8;margin-bottom:16px;">
      <span>Endpoint: <code>{safe_url}</code></span> &bull;
      <span>HTTP Status: <code>{resp_status}</code></span> &bull;
      <span>Server: <code>{safe_server}</code></span> &bull;
      <span>Auth Header: <code>{safe_auth}</code></span>
    </div>
    {stream_section_html}
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:14px;">
      <div style="font-size:12px;font-weight:700;color:#38bdf8;margin-bottom:8px;">SANITIZED PORTAL SOURCE CODE (READ-ONLY TEXT):</div>
      <pre style="margin:0;white-space:pre-wrap;word-break:break-all;font-size:11px;color:#94a3b8;max-height:380px;overflow-y:auto;"><code>{escaped_source}</code></pre>
    </div>
  </div>
</body>
</html>"""

    return HTMLResponse(content=inspector_html, status_code=200, headers=strict_isolation_headers)


@app.get("/api/streams/active")
async def get_active_streams():
    """Retrieve list of currently registered streaming paths from MediaMTX."""
    paths = await mediamtx_service.list_active_paths()
    return {"active_paths": paths}

@app.get("/api/streams/status/{stream_name}")
async def get_stream_status(stream_name: str):
    """Real-time streaming transmission telemetry and health check."""
    safe_name = validate_safe_identifier(stream_name, "stream_name")
    status_info = await mediamtx_service.get_path_status(safe_name)
    return status_info

@app.post("/api/streams/stop")
async def stop_stream_route(payload: Dict[str, Any]):
    """Terminate and release a camera stream from MediaMTX."""
    raw_name = payload.get("stream_name", "")
    stream_name = validate_safe_identifier(str(raw_name), "stream_name") if raw_name else ""
    if stream_name:
        await mediamtx_service.stop_stream(stream_name)
    return {"status": "stopped", "stream_name": stream_name}

@app.post("/api/streams/resolve-rtsp", response_model=ResolveRtspResponse)
async def resolve_camera_rtsp(
    payload: ResolveRtspRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Vendor RTSP Profile & Resolver Engine:
    Probes vendor-specific subpaths and default credentials using fast socket DESCRIBE
    to discover working RTSP / HTTP video streams.
    """
    feed = None
    ip = payload.ip
    port = payload.port or 554
    product = sanitize_text_field(payload.product or "", max_len=200)
    current_url = payload.current_url or ""

    if payload.feed_id:
        safe_feed_id = validate_safe_identifier(payload.feed_id, "feed_id")
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_feed_id))
        feed = res.scalar_one_or_none()
        if not feed:
            raise HTTPException(status_code=404, detail="Feed ID not found")
        ip = feed.ip
        port = feed.port
        product = f"{product or feed.product or ''} {feed.banner or ''}".strip()
        current_url = payload.current_url or feed.rtsp_url or ""

    if not ip:
        raise HTTPException(status_code=400, detail="Missing target IP address or feed_id")

    ip = validate_camera_ip(ip, port)
    if current_url:
        current_url = validate_camera_url(current_url)

    resolve_result = await rtsp_resolver.resolve_camera(
        ip=ip,
        port=port,
        product=product,
        current_url=current_url
    )

    if feed and resolve_result.get("resolved"):
        new_url = resolve_result["stream_url"]
        feed.rtsp_url = new_url
        if resolve_result.get("protocol") in ("http", "https"):
            feed.screenshot_url = new_url
        note_text = f"Auto-Resolved: {resolve_result.get('vendor', '').upper()} stream ({resolve_result.get('protocol')}). {resolve_result.get('message')}"
        feed.notes = note_text
        if resolve_result.get("auth") and resolve_result["auth"] != "unauthenticated":
            feed.auth_status = "default_creds"
        await db.commit()
        await db.refresh(feed)

    return ResolveRtspResponse(
        feed_id=payload.feed_id,
        resolved=resolve_result.get("resolved", False),
        protocol=resolve_result.get("protocol", "unknown"),
        stream_url=resolve_result.get("stream_url", current_url),
        vendor=resolve_result.get("vendor", "generic"),
        tested_count=resolve_result.get("tested_count", 0),
        auth=resolve_result.get("auth"),
        message=resolve_result.get("message", "Resolution completed.")
    )

@app.post("/api/streams/retry-default-creds", response_model=RetryCredentialsResponse)
async def retry_default_creds_route(
    payload: RetryCredentialsRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Retry connecting to RTSP stream with default credentials sourced from
    the vendor profiles in rtsp_resolver (VENDOR_RTSP_PROFILES).
    """
    feed = None
    ip = payload.ip
    port = payload.port or 554
    product = sanitize_text_field(payload.product or "", max_len=200)
    path = payload.path or ""

    if not path and payload.current_url:
        valid_curr = validate_camera_url(payload.current_url)
        parsed_curr = urlparse(valid_curr)
        path = parsed_curr.path + (f"?{parsed_curr.query}" if parsed_curr.query else "")

    if payload.feed_id:
        safe_feed_id = validate_safe_identifier(payload.feed_id, "feed_id")
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == safe_feed_id))
        feed = res.scalar_one_or_none()
        if not feed:
            raise HTTPException(status_code=404, detail="Feed ID not found")
        ip = feed.ip
        port = feed.port
        product = f"{product or feed.product or ''} {feed.banner or ''}".strip()
        if not path and feed.rtsp_url:
            parsed_feed_url = urlparse(feed.rtsp_url)
            path = parsed_feed_url.path + (f"?{parsed_feed_url.query}" if parsed_feed_url.query else "")

    if not ip:
        raise HTTPException(status_code=400, detail="Missing target IP address or feed_id")

    ip = validate_camera_ip(ip, port)

    vendor_key = rtsp_resolver.detect_vendor_key(product)
    retry_ok, auth_url, used_cred = await rtsp_resolver.retry_rtsp_with_default_creds(
        ip=ip,
        port=port,
        path=path,
        product=product
    )

    if feed and retry_ok and used_cred:
        feed.rtsp_url = auth_url
        feed.auth_status = "default_creds"
        feed.notes = f"[Auto-Auth {vendor_key.upper()}] Authenticated with default credentials ({used_cred[0]}:{used_cred[1]})"
        await db.commit()
        await db.refresh(feed)

    creds_tried = rtsp_resolver.get_vendor_credentials(product)
    return RetryCredentialsResponse(
        feed_id=payload.feed_id,
        resolved=retry_ok,
        stream_url=auth_url if retry_ok else (feed.rtsp_url if feed else f"rtsp://{ip}:{port}{path}"),
        vendor=vendor_key,
        username=used_cred[0] if (retry_ok and used_cred) else None,
        auth_status="default_creds" if retry_ok else "unauthenticated",
        tested_count=len(creds_tried),
        message=f"Authenticated with {used_cred[0]}:{used_cred[1]}" if (retry_ok and used_cred) else "No default credentials matched"
    )

# ==========================================
# Shodan API Integration
# ==========================================

@app.get("/api/shodan/info")
async def get_shodan_info():
    """Retrieve Shodan API connection status and plan info."""
    return await shodan_service.get_account_info()

@app.post("/api/shodan/key")
async def update_shodan_key(
    payload: ShodanKeyRequest,
    request: Request,
    x_admin_token: Optional[str] = Header(None)
):
    """Set or update the Shodan API key dynamically (Admin Only)."""
    check_admin_token(x_admin_token, request)
    shodan_service.set_api_key(payload.api_key)
    account_info = await shodan_service.get_account_info()
    return {
        "status": "updated",
        "shodan": account_info
    }

@app.post("/api/shodan/search")
async def search_shodan(
    payload: ShodanSearchQuery,
    request: Request,
    x_admin_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Search exposed camera directories & Shodan. (Admin Only)
    If import_to_db is True, newly discovered feeds are cached into SQLite.
    """
    check_admin_token(x_admin_token, request)
    results = await shodan_service.search(
        query=sanitize_text_field(payload.query, max_len=250),
        limit=max(1, min(payload.limit, 10000)),
        mode=payload.mode
    )

    imported_count = 0
    if payload.import_to_db and "results" in results and results["results"]:
        # Query all existing IDs in 1 single fast query for O(1) set lookup
        existing_res = await db.execute(select(CameraFeedModel.id))
        existing_ids = set(existing_res.scalars().all())

        new_feeds = []
        for item in results["results"]:
            fid = sanitize_text_field(item.get("id", ""), max_len=64)
            if fid and fid not in existing_ids:
                existing_ids.add(fid)
                feed = CameraFeedModel(
                    id=fid,
                    ip=sanitize_text_field(item.get("ip", ""), max_len=45),
                    port=int(item.get("port", 554)),
                    country_code=sanitize_text_field(item.get("country_code", "XX"), max_len=8),
                    country_name=sanitize_text_field(item.get("country_name", "Unknown"), max_len=128),
                    city=sanitize_text_field(item.get("city"), max_len=128) if item.get("city") else None,
                    latitude=float(item.get("latitude") or 0.0),
                    longitude=float(item.get("longitude") or 0.0),
                    org=sanitize_text_field(item.get("org", "Unknown"), max_len=255),
                    isp=sanitize_text_field(item.get("isp"), max_len=255) if item.get("isp") else None,
                    asn=sanitize_text_field(item.get("asn"), max_len=64) if item.get("asn") else None,
                    product=sanitize_text_field(item.get("product", "Generic RTSP IP Camera"), max_len=255),
                    rtsp_url=sanitize_text_field(item.get("rtsp_url", ""), max_len=500),
                    stream_path=sanitize_text_field(item.get("stream_path", ""), max_len=128),
                    auth_status=sanitize_text_field(item.get("auth_status", "unauthenticated"), max_len=64),
                    risk_level=sanitize_text_field(item.get("risk_level", "high"), max_len=32),
                    banner=item.get("banner", "")[:2000] if item.get("banner") else None,
                    last_seen=sanitize_text_field(item.get("last_seen"), max_len=64) if item.get("last_seen") else None,
                    tags_json=json.dumps([sanitize_text_field(str(t), max_len=40) for t in item.get("tags", [])[:15]]),
                    screenshot_url=sanitize_text_field(item.get("screenshot_url"), max_len=500) if item.get("screenshot_url") else None,
                    notes=sanitize_text_field(item.get("notes"), max_len=500) if item.get("notes") else None,
                    is_favorite=False
                )
                new_feeds.append(feed)

        # Batch insert in chunks of 1000 for rapid atomic commits
        if new_feeds:
            for i in range(0, len(new_feeds), 1000):
                db.add_all(new_feeds[i:i + 1000])
                await db.commit()
            imported_count = len(new_feeds)
            logger.info(f"Successfully batch-imported {imported_count} new feeds into database.")

    results["imported_to_db"] = imported_count
    return results

# ==========================================
# Awareness & Education Content
# ==========================================

@app.get("/api/education/articles")
async def get_articles():
    """Retrieve educational articles on IoT camera exposure and threat landscape."""
    return get_education_articles()

@app.get("/api/education/checklist")
async def get_checklist():
    """Retrieve interactive hardening checklist."""
    return get_checklist_items()

# ==========================================
# Health Check
# ==========================================

@app.get("/api/health")
async def health_check():
    mtx_alive = await mediamtx_service.is_running()
    return {
        "status": "healthy",
        "backend": "online",
        "mediamtx_alive": mtx_alive,
        "shodan_configured": bool(shodan_service.api_key)
    }

# ==========================================
# Mount Static Frontend
# ==========================================

static_path = settings.STATIC_DIR
if os.path.exists(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="frontend")

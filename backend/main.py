import os
import json
import logging
import httpx
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Query, Header, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response, HTMLResponse
from sqlalchemy import select, func, or_, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings, get_legacy_ssl_context
from backend.database import get_db, init_db, CameraFeedModel
from backend.models import (
    CameraFeedResponse, CameraFeedCreate, CameraFeedUpdate, BatchDeleteRequest,
    FilterQuery, FacetSummary, FacetItem,
    StreamStartRequest, StreamResponse, ResolveRtspRequest, ResolveRtspResponse,
    ShodanSearchQuery, ShodanKeyRequest,
    EducationArticle
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

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.endswith((".js", ".css", ".html", "/")) or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ==========================================
# Camera Feeds Endpoints
# ==========================================

@app.get("/api/feeds", response_model=Dict[str, Any])
async def list_feeds(
    country: Optional[str] = Query(None, description="Country code (e.g. US, DE)"),
    org: Optional[str] = Query(None, description="Organization / ISP name"),
    port: Optional[int] = Query(None, description="Port number (e.g. 554, 8554)"),
    product: Optional[str] = Query(None, description="Product / Vendor name"),
    risk_level: Optional[str] = Query(None, description="Risk level (critical, high, medium, low)"),
    auth_status: Optional[str] = Query(None, description="Auth status (unauthenticated, default_creds, etc.)"),
    search: Optional[str] = Query(None, description="Full-text search keyword"),
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
        conditions.append(CameraFeedModel.country_code == country.upper())
    if org:
        conditions.append(CameraFeedModel.org.ilike(f"%{org}%"))
    if port:
        conditions.append(CameraFeedModel.port == port)
    if product:
        conditions.append(CameraFeedModel.product.ilike(f"%{product}%"))
    if risk_level:
        conditions.append(CameraFeedModel.risk_level == risk_level.lower())
    if auth_status:
        conditions.append(CameraFeedModel.auth_status == auth_status.lower())
    if is_favorite is not None:
        conditions.append(CameraFeedModel.is_favorite == is_favorite)

    if search:
        search_filter = or_(
            CameraFeedModel.ip.ilike(f"%{search}%"),
            CameraFeedModel.city.ilike(f"%{search}%"),
            CameraFeedModel.country_name.ilike(f"%{search}%"),
            CameraFeedModel.org.ilike(f"%{search}%"),
            CameraFeedModel.product.ilike(f"%{search}%"),
            CameraFeedModel.banner.ilike(f"%{search}%"),
            CameraFeedModel.notes.ilike(f"%{search}%")
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

@app.get("/api/feeds/{feed_id}")
async def get_feed(feed_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == feed_id))
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Camera feed not found")
    return record.to_dict()

@app.patch("/api/feeds/{feed_id}")
async def update_feed(feed_id: str, payload: CameraFeedUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == feed_id))
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Camera feed not found")

    if payload.is_favorite is not None:
        record.is_favorite = payload.is_favorite
    if payload.notes is not None:
        record.notes = payload.notes
    if payload.rtsp_url is not None:
        record.rtsp_url = payload.rtsp_url

    await db.commit()
    await db.refresh(record)
    return record.to_dict()

def check_admin_token(x_admin_token: Optional[str]):
    """Validate administrator access token."""
    if not x_admin_token or x_admin_token.strip() != settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Valid Administrator Access Token (X-Admin-Token) required."
        )

@app.post("/api/admin/verify")
async def verify_admin(x_admin_token: Optional[str] = Header(None)):
    """Verify administrator access key."""
    check_admin_token(x_admin_token)
    return {"status": "authorized", "valid": True, "message": "Admin privileges verified"}

@app.delete("/api/feeds/{feed_id}")
async def delete_feed(
    feed_id: str,
    x_admin_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a camera feed from the database (Requires Admin Token).
    """
    check_admin_token(x_admin_token)

    res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == feed_id))
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Camera feed not found")

    cam_identifier = f"{record.ip}:{record.port}"
    await db.delete(record)
    await db.commit()
    logger.info(f"Admin deleted camera feed {feed_id} [{cam_identifier}].")
    return {
        "status": "deleted",
        "feed_id": feed_id,
        "message": f"Camera feed {cam_identifier} successfully removed from database."
    }

@app.post("/api/feeds/batch-delete")
async def batch_delete_feeds(
    payload: BatchDeleteRequest,
    x_admin_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove multiple camera feeds from the database at once (Requires Admin Token).
    """
    check_admin_token(x_admin_token)

    if not payload.feed_ids:
        return {"status": "success", "deleted_count": 0, "message": "No feed IDs provided"}

    stmt = delete(CameraFeedModel).where(CameraFeedModel.id.in_(payload.feed_ids))
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

    # Total and unauthenticated count
    total_res = await db.execute(select(func.count(CameraFeedModel.id)))
    total = total_res.scalar() or 0

    unauth_res = await db.execute(
        select(func.count(CameraFeedModel.id)).where(CameraFeedModel.auth_status == "unauthenticated")
    )
    unauth_count = unauth_res.scalar() or 0

    return FacetSummary(
        countries=countries,
        orgs=orgs,
        ports=ports,
        products=products,
        risk_levels=risks,
        total_cameras=total,
        unauthenticated_count=unauth_count
    )

# ==========================================
# MediaMTX Streaming Controller
# ==========================================

@app.post("/api/streams/play", response_model=StreamResponse)
async def play_stream(req: StreamStartRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    On-demand RTSP / HTTP streaming proxy controller.
    Automatically resolves working RTSP/HTTP subpaths for camera feeds,
    routes WebRTC via MediaMTX, and handles direct HTTP Web camera streams.
    """
    stream_path = req.stream_name
    rtsp_url = req.rtsp_url
    feed = None

    if req.feed_id:
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == req.feed_id))
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
            has_valid_stream = (
                feed.screenshot_url
                and "unsplash" not in feed.screenshot_url
                and not feed.screenshot_url.rstrip("/").endswith(f":{feed.port}")
                and "/live" not in (feed.screenshot_url or "")
            )

            # Auto-resolve live MJPEG video or JPEG snapshot subpath if not already established
            if not has_valid_stream:
                resolve_res = await rtsp_resolver.resolve_camera(feed.ip, feed.port, feed.product or "", rtsp_url)
                if resolve_res.get("resolved"):
                    stype = resolve_res.get("stream_type", "mjpeg")
                    if stype in ("mjpeg", "jpeg") or resolve_res.get("protocol") in ("http", "https"):
                        feed.rtsp_url = resolve_res["stream_url"]
                        feed.screenshot_url = resolve_res["stream_url"]
                        feed.notes = f"[Auto-Resolved {resolve_res.get('vendor','').upper()}] {resolve_res.get('message','')}"
                        await db.commit()
                        await db.refresh(feed)
                        return StreamResponse(
                            stream_path=feed.stream_path,
                            rtsp_source=resolve_res["stream_url"],
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
                            rtsp_source=resolve_res["stream_url"],
                            webrtc_url="",
                            webrtc_whep_url="",
                            hls_url="",
                            hls_manifest_url="",
                            status="web_ui",
                            stream_type="web_ui",
                            web_url=f"{scheme}://{feed.ip}:{feed.port}/",
                            message=resolve_res.get("message", "Active Camera Web Interface")
                        )

            genuine_snapshot = feed.screenshot_url if (feed.screenshot_url and "unsplash" not in feed.screenshot_url and not feed.screenshot_url.rstrip("/").endswith(f":{feed.port}")) else None
            target_stream = genuine_snapshot or (rtsp_url if rtsp_url and not rtsp_url.rstrip("/").endswith(f":{feed.port}") else f"{scheme}://{feed.ip}:{feed.port}/")

            # If target_stream is generic root, probe root to verify if active web console
            if target_stream.rstrip("/").endswith(f":{feed.port}"):
                schemes_to_try = [scheme]
                if scheme == "https" and feed.port == 443:
                    schemes_to_try.append("http")
                for s in schemes_to_try:
                    verify_arg = get_legacy_ssl_context() if s == "https" else False
                    try:
                        async with httpx.AsyncClient(timeout=2.0, verify=verify_arg, follow_redirects=True) as client:
                            r_root = await client.get(f"{s}://{feed.ip}:{feed.port}/")
                            if r_root.status_code in (200, 301, 302, 401):
                                proto_note = "HTTPS" if s == "https" else "Plaintext HTTP"
                                return StreamResponse(
                                    stream_path=feed.stream_path,
                                    rtsp_source=f"{s}://{feed.ip}:{feed.port}/",
                                    webrtc_url="",
                                    webrtc_whep_url="",
                                    hls_url="",
                                    hls_manifest_url="",
                                    status="web_ui",
                                    stream_type="web_ui",
                                    web_url=f"{s}://{feed.ip}:{feed.port}/",
                                    message=f"Active Camera Web Console on port {feed.port} ({proto_note})"
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
                status="http_stream",
                stream_type="mjpeg",
                web_url=f"{scheme}://{feed.ip}:{feed.port}/",
                message=f"{scheme.upper()} Live Stream / Snapshot endpoint ready"
            )

        # ---------------------------------------------------------------------
        # Case B: Camera on Native RTSP Port (554, 8554, etc.)
        # Verify current path; if non-working or dummy '/live', perform continuous candidate probing
        # ---------------------------------------------------------------------
        parsed_p = urlparse(rtsp_url).path if rtsp_url else ""
        current_works = False
        if rtsp_url and parsed_p and parsed_p not in ("/", "/live"):
            is_ok, _, _, _, _ = await rtsp_resolver.probe_rtsp_path(feed.ip, feed.port, parsed_p, timeout=0.8)
            current_works = is_ok

        if not current_works:
            resolve_res = await rtsp_resolver.resolve_camera(feed.ip, feed.port, feed.product or "", rtsp_url, continuous=True)
            if resolve_res.get("resolved"):
                if resolve_res.get("protocol") == "rtsp":
                    rtsp_url = resolve_res["stream_url"]
                    feed.rtsp_url = rtsp_url
                    feed.notes = f"[Auto-Resolved {resolve_res.get('vendor','').upper()}] {resolve_res.get('message','')}"
                    if resolve_res.get("auth") and resolve_res["auth"] != "unauthenticated":
                        feed.auth_status = "default_creds"
                    await db.commit()
                    await db.refresh(feed)
                elif resolve_res.get("protocol") == "http":
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
    feed_id: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    port: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Proxies live HTTP MJPEG video streams or JPEG snapshots directly to the frontend.
    Handles persistent multipart video streams and eliminates CORS / mixed-content blocks.
    """
    target_url = url
    if feed_id:
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == feed_id))
        feed = res.scalar_one_or_none()
        if feed:
            scheme = "https" if feed.port == 443 or (feed.rtsp_url and feed.rtsp_url.startswith("https://")) else "http"
            is_valid_specific = (
                feed.screenshot_url
                and "unsplash" not in feed.screenshot_url
                and not feed.screenshot_url.rstrip("/").endswith(f":{feed.port}")
            )
            if is_valid_specific:
                target_url = feed.screenshot_url
            elif feed.rtsp_url.startswith(("http://", "https://")) and not feed.rtsp_url.rstrip("/").endswith(f":{feed.port}"):
                target_url = feed.rtsp_url
            else:
                # Auto-resolve live MJPEG or snapshot endpoint on the fly
                resolved = await rtsp_resolver.resolve_camera(feed.ip, feed.port, feed.product or "", feed.rtsp_url)
                if resolved.get("resolved") and resolved.get("stream_type") in ("mjpeg", "jpeg"):
                    target_url = resolved["stream_url"]
                    feed.screenshot_url = target_url
                    feed.rtsp_url = target_url
                    await db.commit()
                else:
                    target_url = f"{scheme}://{feed.ip}:{feed.port}/"
    elif not target_url and ip and port:
        scheme = "https" if port == 443 else "http"
        resolved = await rtsp_resolver.resolve_camera(ip, port)
        if resolved.get("resolved") and resolved.get("stream_type") in ("mjpeg", "jpeg"):
            target_url = resolved["stream_url"]
        else:
            target_url = f"{scheme}://{ip}:{port}/"

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing target stream URL or feed_id")

    candidates = [target_url]
    if target_url.startswith("https://") and (":443" in target_url or (feed_id and feed and feed.port == 443)):
        candidates.append(target_url.replace("https://", "http://", 1))

    r = None
    client = None
    last_err = None

    for cand in candidates:
        verify_arg = get_legacy_ssl_context() if cand.startswith("https://") else False
        try:
            client = httpx.AsyncClient(verify=verify_arg, timeout=10.0, follow_redirects=True)
            req = client.build_request("GET", cand)
            r = await client.send(req, stream=True)
            target_url = cand
            break
        except Exception as e:
            if client:
                await client.aclose()
            last_err = e
            err_msg = str(e).lower()
            if cand.startswith("https://") and any(k in err_msg for k in ["ssl", "record", "version", "handshake", "connect"]):
                logger.info(f"Port 443 SSL error on {cand} ({e}). Retrying with plaintext HTTP fallback...")
                continue
            break

    if not r:
        logger.warning(f"Proxy stream error for {target_url}: {last_err}")
        raise HTTPException(status_code=502, detail=f"Failed to connect to camera endpoint: {str(last_err)}")

    try:
        ctype = r.headers.get("content-type", "image/jpeg")
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Access-Control-Allow-Origin": "*"
        }
        if "text/html" in ctype.lower():
            headers["X-Stream-Type"] = "web_ui"
            headers["X-Camera-Status"] = "active_web_console"

        async def stream_generator():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            except Exception:
                pass
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            status_code=r.status_code,
            media_type=ctype,
            headers=headers
        )
    except Exception as e:
        logger.warning(f"Proxy stream processing error for {target_url}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to stream camera endpoint: {str(e)}")

@app.get("/api/proxy/web")
async def proxy_web_console(
    feed_id: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Proxies Camera Web UI HTML consoles, stripping X-Frame-Options and CSP,
    injecting <base href="..."> so embedded iframes render the camera interface directly.
    """
    target_url = url
    if feed_id:
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == feed_id))
        feed = res.scalar_one_or_none()
        if feed:
            scheme = "https" if feed.port == 443 or (feed.rtsp_url and feed.rtsp_url.startswith("https://")) else "http"
            target_url = f"{scheme}://{feed.ip}:{feed.port}/"

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing target URL or feed_id")

    candidates = [target_url]
    if target_url.startswith("https://") and (":443" in target_url or (feed_id and feed and feed.port == 443)):
        candidates.append(target_url.replace("https://", "http://", 1))

    resp = None
    effective_url = target_url
    last_err = None

    for cand in candidates:
        verify_arg = get_legacy_ssl_context() if cand.startswith("https://") else False
        try:
            async with httpx.AsyncClient(verify=verify_arg, timeout=6.0, follow_redirects=True) as client:
                resp = await client.get(cand)
                effective_url = cand
                break
        except Exception as e:
            last_err = e
            err_msg = str(e).lower()
            if cand.startswith("https://") and any(k in err_msg for k in ["ssl", "record", "version", "handshake", "connect"]):
                logger.info(f"Port 443 Web console SSL error for {cand} ({e}). Retrying with plaintext HTTP fallback...")
                continue
            break

    if not resp:
        logger.warning(f"Web console proxy error for {target_url}: {last_err}")
        http_fallback_url = target_url.replace("https://", "http://", 1) if target_url.startswith("https://") else target_url
        return HTMLResponse(
            content=f"<html><body style='background:#0b0f19;color:#94a3b8;font-family:sans-serif;padding:20px;text-align:center;'>"
                    f"<h3 style='color:#f87171;'>Web Console Gateway Notice</h3>"
                    f"<p>Could not proxy target interface: {last_err}</p>"
                    f"<p>If the camera rejected HTTPS (SSL_ERROR_RX_RECORD_TOO_LONG), it may only support plaintext HTTP:</p>"
                    f"<p><a href='{http_fallback_url}' target='_blank' style='color:#38bdf8;text-decoration:underline;'>Open via Plaintext HTTP: {http_fallback_url}</a></p>"
                    f"</body></html>",
            status_code=200
        )

    try:
        content = resp.text
        base_href = str(resp.url)
        base_tag = f'<base href="{base_href}">'
        if "<head>" in content:
            content = content.replace("<head>", f"<head>{base_tag}", 1)
        elif "<HEAD>" in content:
            content = content.replace("<HEAD>", f"<HEAD>{base_tag}", 1)
        else:
            content = base_tag + content

        headers = {
            "Content-Type": resp.headers.get("content-type", "text/html"),
            "Cache-Control": "no-cache, no-store",
            "Access-Control-Allow-Origin": "*"
        }
        return HTMLResponse(content=content, status_code=resp.status_code, headers=headers)
    except Exception as e:
        logger.warning(f"Web console parsing error: {e}")
        return HTMLResponse(content=resp.text, status_code=resp.status_code)

@app.get("/api/streams/active")
async def get_active_streams():
    """Retrieve list of currently registered streaming paths from MediaMTX."""
    paths = await mediamtx_service.list_active_paths()
    return {"active_paths": paths}

@app.get("/api/streams/status/{stream_name}")
async def get_stream_status(stream_name: str):
    """Real-time streaming transmission telemetry and health check."""
    status_info = await mediamtx_service.get_path_status(stream_name)
    return status_info

@app.post("/api/streams/stop")
async def stop_stream_route(payload: Dict[str, Any]):
    """Terminate and release a camera stream from MediaMTX."""
    stream_name = payload.get("stream_name", "")
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
    product = payload.product or ""
    current_url = payload.current_url or ""

    if payload.feed_id:
        res = await db.execute(select(CameraFeedModel).where(CameraFeedModel.id == payload.feed_id))
        feed = res.scalar_one_or_none()
        if not feed:
            raise HTTPException(status_code=404, detail="Feed ID not found")
        ip = feed.ip
        port = feed.port
        product = feed.product or ""
        current_url = feed.rtsp_url or ""

    if not ip:
        raise HTTPException(status_code=400, detail="Missing target IP address or feed_id")

    resolve_result = await rtsp_resolver.resolve_camera(
        ip=ip,
        port=port,
        product=product,
        current_url=current_url
    )

    if feed and resolve_result.get("resolved"):
        new_url = resolve_result["stream_url"]
        feed.rtsp_url = new_url
        if resolve_result.get("protocol") == "http":
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
    x_admin_token: Optional[str] = Header(None)
):
    """Set or update the Shodan API key dynamically (Admin Only)."""
    check_admin_token(x_admin_token)
    shodan_service.set_api_key(payload.api_key)
    account_info = await shodan_service.get_account_info()
    return {
        "status": "updated",
        "shodan": account_info
    }

@app.post("/api/shodan/search")
async def search_shodan(
    payload: ShodanSearchQuery,
    x_admin_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Search exposed camera directories & Shodan. (Admin Only)
    If import_to_db is True, newly discovered feeds are cached into SQLite.
    """
    check_admin_token(x_admin_token)
    results = await shodan_service.search(
        query=payload.query,
        limit=payload.limit,
        mode=payload.mode
    )

    imported_count = 0
    if payload.import_to_db and "results" in results and results["results"]:
        # Query all existing IDs in 1 single fast query for O(1) set lookup
        existing_res = await db.execute(select(CameraFeedModel.id))
        existing_ids = set(existing_res.scalars().all())

        new_feeds = []
        for item in results["results"]:
            fid = item["id"]
            if fid not in existing_ids:
                existing_ids.add(fid)
                feed = CameraFeedModel(
                    id=fid,
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

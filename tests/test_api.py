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

        # 3b. Favorites filter & toggle test
        print("[*] Testing Favorites toggle & filtering...")
        sample_feed = all_feeds["items"][0]
        feed_id = sample_feed["id"]
        # Mark as favorite
        fav_patch = await client.patch(f"/api/feeds/{feed_id}", json={"is_favorite": True})
        assert fav_patch.status_code == 200
        assert fav_patch.json()["is_favorite"] is True

        # Query favorites
        fav_res = await client.get("/api/feeds?is_favorite=true")
        assert fav_res.status_code == 200
        fav_items = fav_res.json()["items"]
        assert any(f["id"] == feed_id for f in fav_items), "Favorited feed not found in /api/feeds?is_favorite=true"

        # Check facets updated
        facets_updated = (await client.get("/api/facets")).json()
        assert facets_updated["favorites_count"] >= 1

        # Unmark favorite
        unfav_patch = await client.patch(f"/api/feeds/{feed_id}", json={"is_favorite": False})
        assert unfav_patch.status_code == 200
        assert unfav_patch.json()["is_favorite"] is False
        print(f"    -> Successfully verified favorite toggle, DB persistence, and /api/feeds?is_favorite=true filter.")

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

    print("\n[✓] ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_full_pipeline())


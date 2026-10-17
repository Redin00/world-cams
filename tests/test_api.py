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

        # 3. Facets test
        print("[*] Testing /api/facets...")
        res = await client.get("/api/facets")
        assert res.status_code == 200
        facets = res.json()
        print(f"    -> Countries in facets: {len(facets['countries'])}, Products: {len(facets['products'])}")
        assert len(facets["countries"]) > 0

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

    print("\n[✓] ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_full_pipeline())


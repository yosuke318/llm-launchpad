"""LAN 内のほかの PC から使うときの、合言葉と入れる場所のテスト"""

import ipaddress

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui import access
from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.runner import Runner

TOKEN = "correct-horse-battery-staple"
LAN = ("192.168.1.20", 50000)
LOCAL = ("127.0.0.1", 50000)
INTERNET = ("203.0.113.5", 50000)


def make(tmp_path, client=LOCAL, token=TOKEN, **kw):
    app = create_app(Runner(str(tmp_path)), web_dist=None, access_token=token, login_failure_delay_sec=0, **kw)
    return TestClient(app, client=client)


def test_without_token_only_this_pc_can_use_the_api(tmp_path):
    with make(tmp_path, client=LOCAL, token=None) as c:
        assert c.get("/api/health").status_code == 200, "今までどおり、この PC からは合言葉なしで使える"
        assert c.get("/api/auth").json() == {"required": False, "authenticated": True}
    with make(tmp_path, client=LAN, token=None) as c:
        res = c.get("/api/health", headers={"Accept-Language": "en"})
        assert res.status_code == 403 and "LAUNCHPAD_ACCESS_TOKEN" in res.json()["detail"]
        assert c.get("/api/auth").json() == {"required": False, "authenticated": False}


@pytest.mark.parametrize("client", [LAN, LOCAL], ids=["lan", "this-pc"])
def test_with_token_every_api_needs_login(tmp_path, client):
    with make(tmp_path, client=client) as c:
        res = c.get("/api/health")
        assert res.status_code == 401 and res.json()["detail"] == "ログインしてください"
        assert c.get("/api/auth").json() == {"required": True, "authenticated": False}

        assert c.post("/api/auth/login", json={"token": "wrong-token-wrong-token"}).status_code == 401
        res = c.post("/api/auth/login", json={"token": TOKEN})
        assert res.status_code == 200
        cookie = res.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie

        assert c.get("/api/health").status_code == 200, "Cookie で通る(SSE や CSV の保存も同じ)"
        assert c.get("/api/auth").json() == {"required": True, "authenticated": True}

        c.post("/api/auth/logout")
        assert c.get("/api/health").status_code == 401


def test_bearer_header_also_works(tmp_path):
    with make(tmp_path, client=LAN) as c:
        assert c.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert c.get("/api/health", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_addresses_outside_the_lan_are_refused_even_with_the_token(tmp_path):
    with make(tmp_path, client=INTERNET) as c:
        assert c.post("/api/auth/login", json={"token": TOKEN}).status_code == 403
        assert c.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 403


def test_allowed_cidrs_can_be_narrowed(tmp_path):
    with make(tmp_path, client=LAN, allowed_cidrs="10.0.0.0/8") as c:
        assert c.get("/api/auth").status_code == 403
    with make(tmp_path, client=LOCAL, allowed_cidrs="10.0.0.0/8") as c:
        assert c.get("/api/auth").status_code == 200, "この PC からはいつでも入れる"


def test_login_without_token_configured(tmp_path):
    with make(tmp_path, token=None) as c:
        assert c.post("/api/auth/login", json={"token": TOKEN}).status_code == 400


def test_short_token_and_bad_cidr_are_rejected_at_startup(tmp_path):
    with pytest.raises(access.AccessConfigError):
        make(tmp_path, token="short")
    with pytest.raises(access.AccessConfigError):
        make(tmp_path, allowed_cidrs="192.168.1.0/33")


def test_static_files_are_served_without_login(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>x</title>")
    app = create_app(Runner(str(tmp_path)), web_dist=str(dist), access_token=TOKEN)
    with TestClient(app, client=LAN) as c:
        assert c.get("/").status_code == 200, "ログインの画面を出すため、画面のファイルは合言葉なしで配る"


@pytest.mark.parametrize("host, allowed", [
    ("192.168.0.5", True), ("10.1.2.3", True), ("172.20.0.1", True), ("172.32.0.1", False),
    ("8.8.8.8", False), ("::ffff:192.168.0.5", True), ("fe80::1%eth0", True), ("fd00::1", True), ("2001:db8::1", False),
])
def test_default_allowed_networks(host, allowed):
    networks = access.parse_cidrs(None)
    assert access.is_allowed(access.client_address(host), networks) is allowed


def test_ipv4_mapped_loopback_is_local():
    assert access.is_loopback(ipaddress.ip_address("::ffff:127.0.0.1"))

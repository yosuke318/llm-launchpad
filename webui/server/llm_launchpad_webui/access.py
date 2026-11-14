"""LAN 内のほかの PC から画面を使えるようにするときの、入れる人と場所の決まり。

画面からは料金のかかるインスタンスを起動できるので、次の2つで守る。

    合言葉(LAUNCHPAD_ACCESS_TOKEN): 決めると、/api はどこからでも(この PC からも)合言葉が要る。
        画面のログインで合言葉を送ると、HttpOnly の Cookie に入れて返す。SSE や CSV の保存も Cookie で通る。
        決めていなければ、この PC(ループバック)からだけ使える。ほかから来たら 403 にする
    入れる場所(LAUNCHPAD_ALLOWED_CIDRS): 既定はプライベートアドレスとループバックだけ。
        ルーターのポート開放などでインターネットから届いても入れない

/api 以外(ビルドした画面の HTML、JS、CSS)は合言葉なしで配る。ログインの画面を出すため。
"""

import hmac
import ipaddress

COOKIE_NAME = "launchpad_token"
# Cookie を覚えておく長さ(30日)
COOKIE_MAX_AGE_SEC = 30 * 24 * 3600
MIN_TOKEN_LENGTH = 16

DEFAULT_ALLOWED_CIDRS = (
    "127.0.0.0/8", "::1/128",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    # IPv6 のユニークローカルとリンクローカル
    "fc00::/7", "fe80::/10",
)

# 合言葉なしで使えるパス。ログインの状態を見る、ログインする、ログアウトする
PUBLIC_PATHS = {"/api/auth", "/api/auth/login", "/api/auth/logout"}


class AccessConfigError(ValueError):
    pass


def parse_cidrs(text):
    """カンマ区切りの CIDR。空なら既定"""
    items = [s.strip() for s in (text or "").split(",") if s.strip()] or list(DEFAULT_ALLOWED_CIDRS)
    try:
        return [ipaddress.ip_network(s, strict=False) for s in items]
    except ValueError as e:
        raise AccessConfigError(f"LAUNCHPAD_ALLOWED_CIDRS を読めません: {e}") from e


def check_token(token):
    """短い合言葉は総当たりで当てられるので、起動のときに断る"""
    if token is not None and len(token) < MIN_TOKEN_LENGTH:
        raise AccessConfigError(
            f"LAUNCHPAD_ACCESS_TOKEN は {MIN_TOKEN_LENGTH} 文字以上にしてください"
            '(例: python -c "import secrets; print(secrets.token_urlsafe(24))")'
        )
    return token or None


def client_address(host):
    """リクエストの送り元。読めなければ None(テストクライアントの "testclient" など)"""
    try:
        return ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return None


def is_loopback(address):
    if address is None:
        return False
    # IPv4 を埋め込んだ IPv6(::ffff:127.0.0.1)も同じに扱う
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def is_allowed(address, networks):
    if address is None:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    address = mapped or address
    return any(address.version == n.version and address in n for n in networks)


def token_matches(expected, given):
    """時間の差で合言葉を探られないよう、同じ時間で比べる"""
    return bool(expected) and bool(given) and hmac.compare_digest(expected.encode(), given.encode())


def bearer(header):
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def decide(path, host, token, networks, cookie, authorization):
    """入れてよいか。入れてよければ None、だめなら (ステータス, 文言の key)"""
    if not path.startswith("/api"):
        return None
    address = client_address(host)
    # テストクライアントのように送り元が分からないときは、この PC から来たものとして扱う
    local = host in (None, "testclient") or is_loopback(address)
    if not local and not is_allowed(address, networks):
        return 403, "access.address_not_allowed"
    if path in PUBLIC_PATHS:
        return None
    if token is None:
        return None if local else (403, "access.token_not_set")
    if token_matches(token, cookie) or token_matches(token, bearer(authorization)):
        return None
    return 401, "access.login_required"


def authenticated(host, token, cookie, authorization):
    if token is None:
        address = client_address(host)
        return host in (None, "testclient") or is_loopback(address)
    return token_matches(token, cookie) or token_matches(token, bearer(authorization))

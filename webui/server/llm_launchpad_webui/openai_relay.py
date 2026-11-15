"""OpenAI 互換 API の中継。LAN 内のほかの PC の VSCode(Cline や Continue)から、立ち上げた環境の Ollama を使えるようにする。

    VSCode → WebUI の /api/environments/<環境ID>/openai/v1/... → サーバーの PC の localhost:11434(SSM のポートフォワード)
           → GPU インスタンスの Ollama の /v1/...

/api の下に置くので、ほかの /api と同じく合言葉(Authorization: Bearer)と、入れる場所の決まり(access.py)で守る。
VSCode の PC に AWS の認証情報は要らない。ポートフォワードは、チャットと同じくサーバーの PC で動かしておく。

送り先は terraform output の ollama_endpoint。応答はストリーミング(SSE)も、届いた分からそのまま流す。
決めたパスだけを中継し、Authorization などのヘッダーは Ollama に渡さない。
"""

import urllib.error
import urllib.request

# 中継するパスとメソッド(/v1 の後ろ)。Cline や Continue が使うもの
ALLOWED = {
    ("GET", "models"),
    ("POST", "chat/completions"),
    ("POST", "completions"),
    ("POST", "embeddings"),
}

# 大きいモデルは、最初の応答で GPU への読み込みに数分かかる。ストリーミング中は、チャンクの間隔に効く
TIMEOUT_SEC = 600
# エージェントは多くのファイルを一度に送るので、余裕を持たせる
MAX_BODY_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 16 * 1024


class RelayUnreachable(Exception):
    """送り先に接続できなかった(ポートフォワードが動いていないなど)"""


def allowed(method, path):
    return (method.upper(), path.strip("/")) in ALLOWED


def open_upstream(endpoint, method, path, body, content_type, timeout=TIMEOUT_SEC):
    """Ollama の /v1/<path> に送り、開いた応答を返す。Ollama が 4xx/5xx を返したときも、その応答を返す(本文をそのまま渡すため)"""
    url = f"{endpoint.rstrip('/')}/v1/{path.strip('/')}"
    headers = {"Accept": "*/*"}
    if body:
        headers["Content-Type"] = content_type or "application/json"
    req = urllib.request.Request(url, data=body or None, headers=headers, method=method.upper())
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise RelayUnreachable(str(getattr(e, "reason", e))) from e


def iter_body(res, chunk=CHUNK_BYTES):
    """届いた分からすぐに返す(read1 は、たまるのを待たない)。読み終わるか、画面側が切ったら閉じる。
    閉じると Ollama は生成をやめる"""
    try:
        while True:
            data = res.read1(chunk) if hasattr(res, "read1") else res.read(chunk)
            if not data:
                return
            yield data
    finally:
        res.close()


def error_body(message, kind):
    """OpenAI の SDK や Cline、Continue が読める形のエラー。FastAPI の形(detail)も入れておく"""
    return {"error": {"message": message, "type": kind}, "detail": message}

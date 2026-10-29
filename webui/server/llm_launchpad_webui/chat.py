"""簡単なチャット。Ollama の /api/chat に送り、応答と、出力の tok/s を返す。

送り先は terraform output の ollama_endpoint。SSM で接続する環境(access_mode=ssm)では http://localhost:11434 なので、
サーバーを動かしている端末で ssm_port_forward_command を動かしておく。
"""

import json
import time
import urllib.error
import urllib.request

# 大きいモデルは、最初の応答で GPU への読み込みに数分かかる
TIMEOUT_SEC = 600


class ChatError(Exception):
    pass


class ChatUnreachable(ChatError):
    """送り先に接続できなかった(ポートフォワードが動いていないなど)"""


def chat_once(endpoint, model, messages, timeout=TIMEOUT_SEC):
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(endpoint.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            result = json.load(res)
    except urllib.error.HTTPError as e:
        raise ChatError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise ChatUnreachable(str(getattr(e, "reason", e))) from e
    except json.JSONDecodeError as e:
        raise ChatError(str(e)) from e
    eval_sec = result.get("eval_duration", 0) / 1e9
    return {
        "message": result.get("message", {"role": "assistant", "content": ""}),
        "total_sec": round(time.monotonic() - started, 2),
        "output_tokens": result.get("eval_count"),
        "output_tokens_per_sec": round(result["eval_count"] / eval_sec, 1) if eval_sec and result.get("eval_count") else None,
    }

"""一覧(catalog/models.yaml)にないモデルを、Ollama のタグか Hugging Face のリポジトリ名から調べて、モデル定義と同じ項目にする。

Ollama の registry(registry.ollama.ai と hf.co)から manifest を取り、モデルの層(GGUF)の大きさを配布サイズにする。
層の数や KV ヘッド数などは、GGUF の先頭にあるメタデータを読む。重みは数GBあるので、メタデータの分だけ取って読むのをやめる。

    qwen3:8b                              → registry.ollama.ai/library/qwen3 の 8b
    someone/model:tag                     → registry.ollama.ai/someone/model の tag
    hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M
                                          → hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF の Q4_K_M(huggingface.co の URL も受け付ける)

ollama pull に渡す名前(モデル定義のキー)は、タグを省いたときも :latest を付けた形にそろえる。
"""

import json
import os
import re
import struct
import threading
import urllib.error
import urllib.request

from .messages import MessageError

TIMEOUT_SEC = 20
# GGUF のメタデータを読む上限。トークナイザーの語彙で数MBになるので、余裕を見る
METADATA_LIMIT_BYTES = 64 * 1024 * 1024
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
# 配布サイズに入れる層。画像を読むモデルは、重みとは別に projector を GPU に載せる
WEIGHT_MEDIA_TYPES = ("application/vnd.ollama.image.model", "application/vnd.ollama.image.projector")

_NAME = r"[a-z0-9][a-z0-9._-]*"
# Ollama の名前空間(ユーザー名)には . がない。hf.co/xxx のようにホスト名を書いたものを、名前空間と取り違えないため
OLLAMA_REF = re.compile(rf"^(?:([a-z0-9][a-z0-9_-]*)/)?({_NAME})(?::({_NAME}))?$")
HF_REF = re.compile(rf"^hf\.co/({_NAME})/({_NAME})(?::({_NAME}))?$", re.IGNORECASE)

# GGUF の general.file_type(llama.cpp の llama_ftype)。Ollama の config に量子化がないときに使う
FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M",
    13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS",
    21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
    29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4",
}


class ModelLookupError(MessageError):
    pass


def parse_ref(text):
    """入力を (ollama pull に渡す名前, registry の URL の先頭, タグ, 提供元) にする。形が合わなければ ModelLookupError"""
    ref = text.strip()
    ref = re.sub(r"^https?://", "", ref).rstrip("/")
    ref = re.sub(r"^(?:www\.)?huggingface\.co/", "hf.co/", ref, flags=re.IGNORECASE)
    if m := HF_REF.match(ref):
        user, repo, tag = m.group(1), m.group(2), m.group(3) or "latest"
        return f"hf.co/{user}/{repo}:{tag}", f"https://hf.co/v2/{user}/{repo}", tag, "Hugging Face"
    ref = ref.lower()
    if m := OLLAMA_REF.match(ref):
        namespace, name, tag = m.group(1) or "library", m.group(2), m.group(3) or "latest"
        pull_name = name if namespace == "library" else f"{namespace}/{name}"
        return f"{pull_name}:{tag}", f"https://registry.ollama.ai/v2/{namespace}/{name}", tag, "Ollama"
    raise ModelLookupError("model_lookup.invalid_ref")


def http_open(url, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=TIMEOUT_SEC)


def lookup(text, opener=http_open):
    """入力したモデルを調べて (ollama pull に渡す名前, モデル定義) を返す"""
    tag, base, version, vendor = parse_ref(text)
    try:
        with opener(f"{base}/manifests/{version}", {"Accept": MANIFEST_ACCEPT}) as res:
            manifest = json.load(res)
        layers = [layer for layer in manifest.get("layers", []) if layer.get("mediaType") in WEIGHT_MEDIA_TYPES]
        model_layer = next((layer for layer in layers if layer["mediaType"] == WEIGHT_MEDIA_TYPES[0]), None)
        if model_layer is None:
            raise ModelLookupError("model_lookup.not_gguf", model=tag)
        with opener(f"{base}/blobs/{manifest['config']['digest']}") as res:
            config = json.load(res)
        with opener(f"{base}/blobs/{model_layer['digest']}", {"Range": f"bytes=0-{METADATA_LIMIT_BYTES - 1}"}) as res:
            metadata = read_gguf_metadata(res)
    except urllib.error.HTTPError as e:
        # hf.co は、ないリポジトリに 401 を返す
        if e.code in (401, 404):
            raise ModelLookupError("model_lookup.not_found", model=tag) from e
        raise ModelLookupError("model_lookup.failed", model=tag, error=e) from e
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError, KeyError) as e:
        raise ModelLookupError("model_lookup.failed", model=tag, error=e) from e

    return tag, definition(tag, vendor, sum(layer["size"] for layer in layers), config, metadata)


def definition(tag, vendor, file_size_bytes, config, metadata):
    """manifest の大きさ、Ollama の config、GGUF のメタデータから、モデル定義(catalog/models.yaml の項目)を作る"""
    arch = metadata.get("general.architecture")

    def arch_value(key):
        return metadata.get(f"{arch}.{key}")

    layers = arch_value("block_count")
    max_context = arch_value("context_length")
    heads = arch_value("attention.head_count")
    kv_heads = arch_value("attention.head_count_kv")
    if kv_heads is None:
        kv_heads = heads
    # Mamba などを混ぜたモデルは、層ごとの KV ヘッド数の配列になっていて、KVキャッシュを持たない層は 0
    attention_layers = None
    if isinstance(kv_heads, list):
        attention_layers = sum(1 for h in kv_heads if h)
        kv_heads = max(kv_heads, default=0)
    if isinstance(heads, list):
        heads = max(heads, default=0)
    head_dim = arch_value("attention.key_length")
    if head_dim is None and heads and arch_value("embedding_length"):
        head_dim = arch_value("embedding_length") // heads
    if not all(isinstance(v, int) and v > 0 for v in (layers, max_context, kv_heads, head_dim)):
        raise ModelLookupError("model_lookup.unsupported", model=tag, architecture=arch or "-")

    quantization = config.get("file_type")
    if not quantization or quantization == "unknown":
        quantization = FILE_TYPES.get(metadata.get("general.file_type"), "")
    result = {
        "vendor": vendor,
        "name": metadata.get("general.name") or tag,
        "params_b": params_b(config.get("model_type") or (config.get("config") or {}).get("parameters"), metadata),
        "quantization": quantization,
        "file_size_gb": round(file_size_bytes / 1e9, 2),
        "max_context": max_context,
        "layers": layers,
        "kv_heads": kv_heads,
        "head_dim": head_dim,
        "license": metadata.get("general.license") or "",
    }
    if attention_layers is not None and attention_layers != layers:
        result["attention_layers"] = attention_layers
    return result


def params_b(model_type, metadata):
    """パラメータ数(B)。Ollama の config の "8.19B" や "751.63M"、なければ GGUF の general.size_label("8B")から"""
    for text in (model_type, metadata.get("general.size_label")):
        m = re.fullmatch(r"(?:\d+x)?([\d.]+)\s*([BMK])", str(text or "").strip(), re.IGNORECASE)
        if m:
            return round(float(m.group(1)) * {"B": 1, "M": 1e-3, "K": 1e-6}[m.group(2).upper()], 2)
    return 0


# GGUF の値の型
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class _Reader:
    def __init__(self, f, limit):
        self.f = f
        self.left = limit

    def take(self, n):
        self.left -= n
        if self.left < 0:
            raise ValueError("GGUF のメタデータが大きすぎます")
        data = b""
        while len(data) < n:
            chunk = self.f.read(n - len(data))
            if not chunk:
                raise ValueError("GGUF のメタデータが途中で終わっています")
            data += chunk
        return data

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        return self.take(self.unpack("<Q")).decode("utf-8", errors="replace")

    def value(self, typ):
        if typ in _SCALARS:
            return self.unpack(_SCALARS[typ])
        if typ == _STRING:
            return self.string()
        if typ == _ARRAY:
            item_type, count = self.unpack("<I"), self.unpack("<Q")
            if item_type in _SCALARS:
                fmt = _SCALARS[item_type]
                return list(struct.unpack(f"<{count}{fmt[1]}", self.take(struct.calcsize(fmt) * count)))
            return [self.value(item_type) for _ in range(count)]
        raise ValueError(f"GGUF の値の型 {typ} は読めません")


def read_gguf_metadata(f, limit=METADATA_LIMIT_BYTES):
    """GGUF(v2 以降)のメタデータ。トークナイザー(tokenizer.*)に入ったところで、要る値がそろっていれば読むのをやめる"""
    r = _Reader(f, limit)
    if r.take(4) != b"GGUF":
        raise ValueError("GGUF ではありません")
    if r.unpack("<I") < 2:
        raise ValueError("GGUF v1 は読めません")
    r.unpack("<Q")  # テンソルの数
    count = r.unpack("<Q")
    metadata = {}
    for _ in range(count):
        key = r.string()
        if key.startswith("tokenizer.") and _has_required(metadata):
            break
        metadata[key] = r.value(r.unpack("<I"))
    return metadata


def _has_required(metadata):
    arch = metadata.get("general.architecture")
    return arch is not None and all(f"{arch}.{k}" in metadata for k in ("block_count", "context_length", "attention.head_count"))


# モデル定義の項目。起動するときに、このうち値のあるものを tfvars の model_definition に書く(terraform/variables.tf と同じ)
DEFINITION_FIELDS = ("vendor", "name", "params_b", "quantization", "file_size_gb", "max_context", "layers",
                     "attention_layers", "kv_heads", "head_dim", "license")


class AddedModels:
    """画面で追加した、一覧にないモデル。キーは ollama pull に渡す名前、値はモデル定義(JSON のファイルに置く)"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def all(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _write(self, models):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def add(self, tag, definition):
        with self._lock:
            self._write({**self.all(), tag: definition})

    def remove(self, tag):
        """消したら True。なければ False"""
        with self._lock:
            models = self.all()
            if models.pop(tag, None) is None:
                return False
            self._write(models)
        return True

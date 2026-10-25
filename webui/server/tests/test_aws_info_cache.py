"""AwsInfo のキャッシュのテスト。AWS には接続せず、時刻を差し替える"""

import pytest

from llm_launchpad_webui import aws_info as mod
from llm_launchpad_webui.aws_info import AwsInfo


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(mod.time, "monotonic", c)
    return c


@pytest.fixture
def info():
    # セッションは使わないので、何でもよい
    return AwsInfo(session=object(), ttl_sec=3600)


def counter():
    calls = []

    def fetch():
        calls.append(1)
        return len(calls)

    return fetch, calls


def test_reuses_until_ttl(info, clock):
    fetch, calls = counter()
    assert info._cached(("a",), fetch) == 1
    clock.now += 3599
    assert info._cached(("a",), fetch) == 1, "期限内なら問い合わせ直さない"
    clock.now += 1
    assert info._cached(("a",), fetch) == 2, "期限が来たら問い合わせ直す"
    assert len(calls) == 2


def test_per_key_ttl(info, clock):
    fetch, calls = counter()
    info._cached(("spot",), fetch, ttl_sec=600)
    clock.now += 599
    info._cached(("spot",), fetch, ttl_sec=600)
    assert len(calls) == 1
    clock.now += 1
    info._cached(("spot",), fetch, ttl_sec=600)
    assert len(calls) == 2, "キーごとの期限(10分)で問い合わせ直す"


def test_expired_entries_are_removed(info, clock):
    """短い期限のキーを何度も問い合わせても、期限切れのエントリはたまらない"""
    for _ in range(100):
        info._cached(("model_store", "ap-northeast-1"), lambda: {}, ttl_sec=60)
        info._cached(("other", "x"), lambda: {}, ttl_sec=60)
        clock.now += 61
    assert len(info._cache) <= 2


def test_long_ttl_entries_survive_pruning(info, clock):
    info._cached(("regions",), lambda: ["ap-northeast-1"])
    clock.now += 61
    info._cached(("model_store", "us-east-1"), lambda: {}, ttl_sec=60)
    assert ("regions",) in info._cache, "期限が残っているものは消さない"

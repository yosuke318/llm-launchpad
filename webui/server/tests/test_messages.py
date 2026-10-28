"""サーバーが返す文言の日本語と英語のテスト"""

import re
import string

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.messages import LANGUAGES, MESSAGES, MessageError, language, parse_accept_language, t
from llm_launchpad_webui.runner import Runner

JAPANESE = re.compile(r"[ぁ-んァ-ン一-龥]")


def fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_every_message_has_both_languages_with_same_fields(key):
    m = MESSAGES[key]
    assert set(m) == set(LANGUAGES)
    assert fields(m["ja"]) == fields(m["en"]), "入れる値は両方の言語で同じ"
    assert not JAPANESE.search(m["en"]), "英語の文言に日本語が残っていない"


@pytest.mark.parametrize("header, expected", [
    (None, "ja"), ("", "ja"), ("ja", "ja"), ("en", "en"), ("en-US,en;q=0.9", "en"), ("fr", "ja"), ("ja,en;q=0.8", "ja"),
])
def test_parse_accept_language(header, expected):
    assert parse_accept_language(header) == expected


def test_error_is_rendered_in_the_language_when_turned_into_text():
    e = AwsInfoError("aws.fetch_failed", what="regions", error="AccessDenied")
    assert str(e) == "regions を取れませんでした: AccessDenied"
    token = language.set("en")
    try:
        assert str(e) == "Could not get regions: AccessDenied"
        assert t("output.read_failed", error=e) == "Could not read terraform output: Could not get regions: AccessDenied", "入れ子のエラーも同じ言語"
    finally:
        language.reset(token)


def test_unknown_key_is_an_error():
    with pytest.raises(KeyError):
        MessageError("nothing.here")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Runner(str(tmp_path)), web_dist=None)) as c:
        yield c


def test_api_errors_follow_accept_language(client):
    path = "/api/environments/nothing/progress"
    assert client.get(path).json()["detail"] == "環境 nothing を起動した記録がありません"
    assert client.get(path, headers={"Accept-Language": "en-US,en;q=0.9"}).json()["detail"] == (
        "There is no record of launching environment nothing"
    )
    res = client.post("/api/launch", json={"workspace": "default", "variables": {"model": "nothing"}}, headers={"Accept-Language": "en"})
    assert res.json()["detail"] == 'model "nothing" is not in the model definitions'

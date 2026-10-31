"""OAuth 1.0a with zotero.org (gamma/zotero_oauth.py): the signature
against the published RFC 5849 / OAuth Core vectors, the Authorization
header, and the three steps through the fake Zotero, ending with an API
key the fake accepts."""

import urllib.parse

import pytest
from zotero_fake import GOOD_VERIFIER, REQUEST_SECRET, REQUEST_TOKEN, FakeZotero

from gamma import config, zotero_oauth
from gamma.zotero_web import ZoteroAuthError, ZoteroClient

CALLBACK = "https://gamma.example/api/zotero/oauth/callback?state=s1"


@pytest.fixture
def fake(monkeypatch):
    return FakeZotero().install(monkeypatch)


def test_the_base_string_is_rfc_5849s_example():
    """RFC 5849 §3.4.1.1: query and form parameters merged, a repeated
    name kept twice, everything encoded twice over."""
    params = {"oauth_consumer_key": "9djdj82h48djs9d2", "oauth_token": "kkk9d7dh3k39sjv7",
              "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": "137131201", "oauth_nonce": "7d8f3e4a",
              "oauth_signature": "left out", "c2": "", "a3": "2 q"}
    assert zotero_oauth.base_string("POST", "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b", params) == (
        "POST&http%3A%2F%2Fexample.com%2Frequest&a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D%26"
        "c%2540%3D%26c2%3D%26oauth_consumer_key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a%26"
        "oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D137131201%26oauth_token%3Dkkk9d7dh3k39sjv7")


def test_the_base_uri_drops_a_default_port_and_keeps_another():
    assert zotero_oauth.base_string("get", "HTTPS://WWW.Zotero.org:443/oauth/request", {}).startswith(
        "GET&https%3A%2F%2Fwww.zotero.org%2Foauth%2Frequest&")
    assert "%3A8080%2F" in zotero_oauth.base_string("POST", "http://127.0.0.1:8080/oauth/request", {})


def test_the_signature_matches_the_published_examples():
    secrets = ("kd94hf93k423kf44", "pfkkdhi9sl3r4s00")
    common = {"oauth_consumer_key": "dpf43f3p2l4k3l03", "oauth_token": "nnch734d00sl2jdk",
              "oauth_signature_method": "HMAC-SHA1"}
    # RFC 5849 §1.2
    assert zotero_oauth.signature(
        "GET", "http://photos.example.net/photos?file=vacation.jpg&size=original",
        {**common, "oauth_timestamp": "137131202", "oauth_nonce": "chapoH"}, *secrets) == "MdpQcU8iPSUjWoN/UDMsK2sui9I="
    # OAuth Core 1.0, Appendix A.5
    assert zotero_oauth.signature(
        "GET", "http://photos.example.net/photos",
        {**common, "oauth_timestamp": "1191242096", "oauth_nonce": "kllo9940pd9333jh", "oauth_version": "1.0",
         "file": "vacation.jpg", "size": "original"}, *secrets) == "tR3+Ty81lMeYAr/Fid0kMTYa/WM="


def test_the_authorization_header_carries_the_encoded_parameters_and_signature():
    oauth = {"oauth_consumer_key": "ck", "oauth_callback": CALLBACK, "oauth_nonce": "n",
             "oauth_timestamp": "1", "oauth_signature_method": "HMAC-SHA1"}
    header = zotero_oauth.authorization_header("POST", "https://www.zotero.org/oauth/request", oauth, "cs")
    assert header.startswith("OAuth oauth_consumer_key=\"ck\", ")
    fields = dict(part.split("=", 1) for part in header[len("OAuth "):].split(", "))
    assert fields["oauth_callback"] == '"' + urllib.parse.quote(CALLBACK, safe="") + '"'
    expected = zotero_oauth.signature("POST", "https://www.zotero.org/oauth/request", oauth, "cs")
    assert urllib.parse.unquote(fields["oauth_signature"].strip('"')) == expected
    # Form fields are signed but stay out of the header.
    with_form = zotero_oauth.authorization_header("POST", "https://www.zotero.org/x", oauth, "cs", extra={"a": "1"})
    names = [part.split("=", 1)[0] for part in with_form[len("OAuth "):].split(", ")]
    assert names == [*oauth, "oauth_signature"]
    assert with_form != zotero_oauth.authorization_header("POST", "https://www.zotero.org/x", oauth, "cs")


def _verify(step, consumer_secret, token_secret=""):
    """The fake recorded the step's Authorization fields: recompute its
    signature over them."""
    params = dict(step["params"])
    sent = params.pop("oauth_signature")
    assert zotero_oauth.signature("POST", step["url"], params, consumer_secret, token_secret) == sent


def test_connecting_through_the_three_steps_yields_a_working_api_key(fake):
    token, secret = zotero_oauth.request_token("ck", "cs", CALLBACK)
    assert (token, secret) == (REQUEST_TOKEN, REQUEST_SECRET)
    step = fake.oauth[-1]
    assert step["url"] == "https://www.zotero.test/oauth/request"
    assert step["params"]["oauth_callback"] == CALLBACK and step["params"]["oauth_consumer_key"] == "ck"
    _verify(step, "cs")

    url = zotero_oauth.authorize_url(token, name="Gamma on my laptop")
    parts = urllib.parse.urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.zotero.test/oauth/authorize"
    assert dict(urllib.parse.parse_qsl(parts.query)) == {
        "oauth_token": REQUEST_TOKEN, "library_access": "1", "notes_access": "1", "write_access": "1",
        "all_groups": "write", "name": "Gamma on my laptop"}
    # The fake approves at once and sends the browser back with a verifier.
    approval = fake.handle("GET", url, {}, b"")
    assert approval.status == 302
    back = urllib.parse.urlsplit(approval.headers["Location"])
    assert back.query.startswith("state=s1&")
    verifier = dict(urllib.parse.parse_qsl(back.query))["oauth_verifier"]

    account = zotero_oauth.access_token("ck", "cs", token, secret, verifier)
    assert account["user_id"] == 123 and account["username"] == "tim"
    _verify(fake.oauth[-1], "cs", secret)
    assert fake.oauth[-1]["params"]["oauth_verifier"] == GOOD_VERIFIER
    assert ZoteroClient(account["api_key"]).key_info()["userID"] == 123


def test_a_wrong_verifier_is_refused(fake):
    with pytest.raises(ZoteroAuthError) as refused:
        zotero_oauth.access_token("ck", "cs", REQUEST_TOKEN, REQUEST_SECRET, "forged")
    assert refused.value.status == 401
    assert str(refused.value).startswith("Zotero refused the connection") and "start again" in str(refused.value)


def test_zoteros_addresses_and_this_servers_oauth_app_come_from_the_environment(monkeypatch):
    for name in ("GAMMA_ZOTERO_API_BASE", "GAMMA_ZOTERO_WEB_BASE", "GAMMA_ZOTERO_STREAM_URL",
                 "GAMMA_ZOTERO_CLIENT_KEY", "GAMMA_ZOTERO_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert config.zotero_api_base() == "https://api.zotero.org"
    assert config.zotero_web_base() == "https://www.zotero.org"
    assert config.zotero_stream_url() == "wss://stream.zotero.org"
    assert config.zotero_client() == ("", "")
    monkeypatch.setenv("GAMMA_ZOTERO_API_BASE", " http://127.0.0.1:9999/ ")
    monkeypatch.setenv("GAMMA_ZOTERO_WEB_BASE", "http://127.0.0.1:9999/")
    monkeypatch.setenv("GAMMA_ZOTERO_STREAM_URL", " ws://127.0.0.1:9998/ ")
    assert config.zotero_api_base() == config.zotero_web_base() == "http://127.0.0.1:9999"
    assert config.zotero_stream_url() == "ws://127.0.0.1:9998"
    monkeypatch.setenv("GAMMA_ZOTERO_CLIENT_KEY", "ck")
    assert config.zotero_client() == ("", "")  # half an app is none
    monkeypatch.setenv("GAMMA_ZOTERO_CLIENT_SECRET", "cs")
    assert config.zotero_client() == ("ck", "cs")

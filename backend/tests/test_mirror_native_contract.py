"""The native port's shared, language-neutral reconciliation examples.

These small fixtures are a compatibility floor, not proof of complete engine
equivalence. Native XCTest additionally exercises durable checkpoint recovery.
"""
import json
from pathlib import Path

from gamma.storage import content_digest
from gamma.textmerge import merge


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/shared/mirror-native-v1.json"


def test_native_fixture_is_the_same_packaged_resource():
    packaged = ROOT / "ipad/GammaCoreTests/Fixtures/mirror-native-v1.json"
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(packaged.read_text(encoding="utf-8"))


def test_native_text_reconciliation_contract():
    for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["text"]:
        assert merge(case["base"], case["ours"], case["theirs"]) == (case["result"], case["clean"]), case["name"]


def test_native_asset_digest_contract():
    for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["assets"]:
        assert content_digest(case["text"].encode("utf-8")) == case["sha24"]

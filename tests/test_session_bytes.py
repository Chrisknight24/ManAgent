"""La sauvegarde session tient avec des images (octets en base64)."""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.discovery.asset_registry import AssetRegistry, _safe_jsonable, _restore_jsonable
from core.discovery.data_asset import BinaryDataAsset, ToolOutputDataAsset

_JPEG = b"\xff\xd8\xff" + b"0" * 64


def _image_asset():
    from core.discovery.data_asset import AssetMetadata
    return BinaryDataAsset(
        target_id="m_abcd_step_1_outil_image",
        metadata={},
        asset_meta=AssetMetadata(
            uri="", data_type="outputs", name="image.jpg",
            size_bytes=len(_JPEG), mime_type="image/jpeg",
        ),
        filename="image.jpg",
        raw_bytes=_JPEG,
    )


def test_octets_marques_puis_restaures():
    marked = _safe_jsonable({"raw_bytes": _JPEG, "nested": [b"ab"]})
    assert marked["raw_bytes"]["__bytes_base64__"] == base64.b64encode(_JPEG).decode()
    back = _restore_jsonable(marked)
    assert back["raw_bytes"] == _JPEG
    assert back["nested"] == [b"ab"]


def test_registre_aller_retour_json():
    reg = AssetRegistry(session_id="s1")
    uri = reg.register_asset(_image_asset(), scheme="outputs")
    text = ToolOutputDataAsset.create(
        step_id="step_1", tool_name="outil", raw_output="ok", session_id="s1")
    reg.register_asset(text, scheme="outputs")
    exported = reg.to_dict()
    dumped = json.dumps(exported, ensure_ascii=False)
    assert "image.jpg" in dumped
    reg2 = AssetRegistry(session_id="s1")
    reg2.load_from_dict(json.loads(dumped))
    got = reg2.get_asset_by_uri(uri)
    assert got is not None
    assert got.raw_bytes == _JPEG
    assert got.asset_meta.mime_type == "image/jpeg"

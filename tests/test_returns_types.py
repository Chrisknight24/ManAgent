"""Sorties typées du manifeste : l'hôte étiquette (MIME), nous normalisons."""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.discovery.data_asset import (
    BinaryDataAsset,
    extract_typed_payloads,
    is_media_mime,
)
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import (
    find_direct_perception_calls,
    repair_direct_perception_calls,
)
from providers.base_provider import BaseProvider

_FAKE_JPEG = base64.b64encode(b"\xff\xd8\xff fake jpeg bytes").decode("utf-8")
_DECL = [{"field": "image_base64", "asset": "image/jpeg", "description": "Photo."}]


def test_mime_sain():
    assert is_media_mime("image/jpeg") is True
    assert is_media_mime("image") is True
    assert is_media_mime("application/pdf") is True
    assert is_media_mime("text/plain") is False
    assert is_media_mime("application/json") is False
    assert is_media_mime("") is False


def test_image_declaree_extraite():
    data = {"image_base64": _FAKE_JPEG, "cadre": {"x": 1}}
    out = extract_typed_payloads(data, _DECL, target_prefix="m_abcd_step_1_outil")
    assert len(out) == 1
    asset = out[0]
    assert isinstance(asset, BinaryDataAsset)
    assert asset.raw_bytes == base64.b64decode(_FAKE_JPEG)
    assert asset.asset_meta.mime_type == "image/jpeg"
    assert asset.filename.endswith(".jpg")
    assert "image" in asset.dump_data()


def test_champ_manquant_ignore():
    assert extract_typed_payloads({"autre": 1}, _DECL) == []


def test_base64_invalide_ignore():
    assert extract_typed_payloads({"image_base64": "pas du base64 !!!"}, _DECL) == []


def test_mime_texte_reste_inline():
    decl = [{"field": "cadre", "asset": "application/json"}]
    assert extract_typed_payloads({"cadre": {"x": 1}}, decl) == []


def test_sans_declaration_rien():
    assert extract_typed_payloads({"image_base64": _FAKE_JPEG}, []) == []
    assert extract_typed_payloads({"image_base64": _FAKE_JPEG}, None) == []


def test_image_generique_sniffe_png():
    import struct
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode("utf-8")
    out = extract_typed_payloads(
        {"photo": png}, [{"field": "photo", "asset": "image"}])
    assert len(out) == 1
    assert out[0].asset_meta.mime_type == "image/png"
    assert out[0].filename.endswith(".png")


def test_image_generique_indechiffrable_ignoree():
    blob = base64.b64encode(b"ceci n est pas une image du tout......").decode("utf-8")
    out = extract_typed_payloads(
        {"photo": blob}, [{"field": "photo", "asset": "image"}])
    assert out == []


def test_mime_declare_corrige_par_contenu():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode("utf-8")
    out = extract_typed_payloads(
        {"photo": png}, [{"field": "photo", "asset": "image/jpeg"}])
    assert len(out) == 1
    assert out[0].asset_meta.mime_type == "image/png"


def test_data_uri_acceptee():
    data = {"image_base64": "data:image/jpeg;base64," + _FAKE_JPEG}
    out = extract_typed_payloads(data, _DECL)
    assert len(out) == 1


def test_provider_prend_octets_bruts():
    data = {"image_base64": _FAKE_JPEG}
    out = extract_typed_payloads(data, _DECL)
    norm = BaseProvider.extract_normalized_media_assets(out)
    assert len(norm) == 1
    assert norm[0]["is_image"] is True
    assert norm[0]["mime_type"] == "image/jpeg"
    assert norm[0]["base64_data"] == _FAKE_JPEG


def _perceive_step(tool="capturer_image"):
    return PlanStep(
        id="s1", description="capturer", type=StepType.TOOL_CALL,
        tool_name=tool, tool_args_json="{}",
        expected_result="true",
    )


def test_capture_image_non_reecrite():
    plan = Plan(goal="g", steps=[_perceive_step()])
    returns = {"capturer_image": _DECL}
    assert find_direct_perception_calls(plan, {"capturer_image"}, returns) == []
    assert repair_direct_perception_calls(plan, {"capturer_image"}, returns) == []
    assert plan.steps[0].tool_name == "capturer_image"


def test_sans_declaration_reecrite_comme_avant():
    plan = Plan(goal="g", steps=[_perceive_step("balayer")])
    assert find_direct_perception_calls(plan, {"balayer"}) == ["s1"]
    assert repair_direct_perception_calls(plan, {"balayer"}) == ["s1"]
    assert plan.steps[0].tool_name == "perceive_understand"

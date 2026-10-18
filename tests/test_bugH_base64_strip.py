"""BUG H (P0) — fuite des charges binaires (image_base64) dans les prompts.

Les sorties d'outils (ex : vision) contiennent un champ `image_base64`
(JPEG ~150 Ko). Elles étaient sérialisées verbatim dans la trace d'exécution,
qui alimente le prompt du planner (« Already tried + errors ») et le prompt du
presentator (« Contexte d'exécution »). D'où des prompts de ~190 Ko.

`redact_binary_payloads` remplace ces charges par un marqueur court, sans
toucher au reste (le binaire reste dispo via Progressive Disclosure / assets).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_sanitizer import redact_binary_payloads

JPEG_B64 = "/9j/" + ("A" * 2000)


def _vision_like():
    return {
        "cols": 14,
        "delivered_size": "1280x720",
        "dpi_scale": 1,
        "frame_id": "img_0002",
        "full_size": "1366x768",
        "image_base64": JPEG_B64,
        "image_scale": 0.937,
        "mime": "image/jpeg",
        "preview_path": "C:/tmp/frames/img_0002.png",
        "rows": 8,
        "tile_px": 100,
    }


def test_cle_image_base64_remplacee():
    out = redact_binary_payloads(_vision_like())
    assert out["image_base64"] == "[binaire omis : {} car.]".format(len(JPEG_B64))
    assert "image_base64" in out


def test_aucune_trace_jpeg_dans_le_json_serialise():
    dumped = json.dumps(redact_binary_payloads(_vision_like()), ensure_ascii=False)
    assert "/9j/" not in dumped
    assert len(dumped) < 1000


def test_champs_non_binaires_preserves():
    out = redact_binary_payloads(_vision_like())
    assert out["cols"] == 14
    assert out["frame_id"] == "img_0002"
    assert out["image_scale"] == 0.937
    assert out["preview_path"] == "C:/tmp/frames/img_0002.png"


def test_recursion_dict_et_liste():
    payload = {"step": {"result": {"image_base64": JPEG_B64}}, "items": [{"base64": JPEG_B64}]}
    out = redact_binary_payloads(payload)
    assert out["step"]["result"]["image_base64"].startswith("[binaire omis")
    assert out["items"][0]["base64"].startswith("[binaire omis")


def test_data_uri_dans_une_chaine():
    s = "avant data:image/png;base64," + ("B" * 1500) + " apres"
    out = redact_binary_payloads(s)
    assert "base64," not in out
    assert out.startswith("avant ")
    assert out.endswith(" apres")


def test_blob_base64_nu_tres_long():
    out = redact_binary_payloads("C" * 4000)
    assert out.startswith("[binaire omis")


def test_petite_valeur_base64_conservee():
    out = redact_binary_payloads({"image_base64": "/9j/short"})
    assert out["image_base64"] == "/9j/short"


def test_texte_normal_intact():
    payload = {"description": "clic droit sur le fichier apollo", "point": [250, 250]}
    assert redact_binary_payloads(payload) == payload

"""Cycle de vie skills : import fantome, disjoncteur dyn, cles requises, P4 bruyant."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skills.models import (
    SkillManifest, SkillVersion, SkillState, ExecutionEnvironment, Checkpoint,
    TrustProfile, FailureClass,
)
from core.skills.registry import SkillRegistry


def _manifest(skill_id="skill.test.ouvrir", risk="standard", required_keys=None):
    return SkillManifest(
        skill_id=skill_id,
        namespace="test",
        name="Test Ouvrir",
        description="Ouvre un truc.",
        parameters_schema={"type": "object", "properties": {}},
        signature_hashes=["sig:ouvrir:truc"],
        environment=ExecutionEnvironment(requirements={}, required_env_keys=required_keys or []),
        checkpoints=[Checkpoint(checkpoint_id="cp1", name="Ouvert")],
        risk_level=risk,
    )


def _reg(tmp_path):
    return SkillRegistry(db_path=str(tmp_path / "skills_test.db"))


def test_imported_production_enters_shadow_without_pointer(tmp_path):
    reg = _reg(tmp_path)
    reg.register_draft_skill(_manifest(), flow_payload_ref="p", payload_content="{}",
                             initial_state=SkillState.PRODUCTION)
    from core.skills.models import SkillPackage
    pkg = reg.export_package("skill.test.ouvrir")
    reg.clear_all_skills()
    assert reg.import_package(pkg) is True
    man, ver = reg.get_active_skill("skill.test.ouvrir")
    assert man is not None and man.current_production_version is None  # pointeur neutralisé
    pkg2 = reg.export_package("skill.test.ouvrir")
    assert pkg2.versions[0].state == SkillState.SHADOW
    assert ver is None or ver.state != SkillState.PRODUCTION


def test_custom_breaker_quarantines_early(tmp_path):
    reg = _reg(tmp_path)
    reg.register_draft_skill(_manifest(), flow_payload_ref="p", payload_content="{}",
                             initial_state=SkillState.PRODUCTION)
    reg.transition_state("skill.test.ouvrir", 1, SkillState.PRODUCTION, reason="t")
    tp = reg.record_run_metric("skill.test.ouvrir", 1, success=False, breaker_max=1)
    assert tp.consecutive_failures == 1
    man, ver = reg.get_active_skill("skill.test.ouvrir")
    assert ver is None  # quarantaine immédiate, pointeur vidé


def test_default_breaker_still_three(tmp_path):
    reg = _reg(tmp_path)
    reg.register_draft_skill(_manifest(), flow_payload_ref="p", payload_content="{}",
                             initial_state=SkillState.PRODUCTION)
    reg.transition_state("skill.test.ouvrir", 1, SkillState.PRODUCTION, reason="t")
    reg.record_run_metric("skill.test.ouvrir", 1, success=False)
    reg.record_run_metric("skill.test.ouvrir", 1, success=False)
    man, ver = reg.get_active_skill("skill.test.ouvrir")
    assert ver is not None and ver.version == 1


def test_count_repairs_counts_repaired_versions(tmp_path):
    reg = _reg(tmp_path)
    reg.register_draft_skill(_manifest(), flow_payload_ref="p", payload_content="{}")
    assert reg.count_repairs("skill.test.ouvrir") == 0
    reg.create_repair_version(skill_id="skill.test.ouvrir", parent_version=1,
                              flow_payload_ref="p2", payload_content="{}")
    assert reg.count_repairs("skill.test.ouvrir") == 1


def test_required_env_keys_block_incompatible_host(tmp_path):
    reg = _reg(tmp_path)
    reg.register_draft_skill(
        _manifest(required_keys=["session_auth"]),
        flow_payload_ref="p", payload_content="{}",
        initial_state=SkillState.PRODUCTION)
    reg.transition_state("skill.test.ouvrir", 1, SkillState.PRODUCTION, reason="t")
    cands = reg.find_candidates_by_signatures(
        ["sig:ouvrir:truc"], host_environment={"os": "windows"}, only_production=True)
    assert cands == []
    cands = reg.find_candidates_by_signatures(
        ["sig:ouvrir:truc"], host_environment={"os": "windows", "session_auth": "tok"},
        only_production=True)
    assert len(cands) == 1


def test_promotion_moves_pointer(tmp_path):
    reg = _reg(tmp_path)
    reg.register_draft_skill(_manifest(), flow_payload_ref="p", payload_content="{}")
    assert reg.transition_state("skill.test.ouvrir", 1, SkillState.PRODUCTION, reason="t") is True
    man, ver = reg.get_active_skill("skill.test.ouvrir")
    assert ver is not None and ver.version == 1 and ver.state == SkillState.PRODUCTION


def test_host_without_flow_engine_fails_loudly(tmp_path):
    import asyncio
    from core.skills.engine import SkillExecutionEngine
    reg = _reg(tmp_path)
    reg.register_draft_skill(_manifest(), flow_payload_ref="p", payload_content="{}")
    reg.transition_state("skill.test.ouvrir", 1, SkillState.PRODUCTION, reason="t")
    manifest = reg.get_skill("skill.test.ouvrir")
    _, version = reg.get_active_skill("skill.test.ouvrir")
    engine = SkillExecutionEngine(registry=reg)

    async def fake_host(ref, params):
        return {"success": False, "failure_class": "HOST_CAPABILITY_ERROR",
                "error_message": "pas de moteur"}

    res = asyncio.run(engine.execute_skill(
        manifest=manifest, version=version, parameters={},
        host_executor=fake_host, mission_id="m1"))
    assert res["success"] is False
    assert res["breakout_report"].failure_class == FailureClass.HOST_CAPABILITY_ERROR

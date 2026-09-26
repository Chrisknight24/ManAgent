"""Panneau central : analyse triée + skills résumés sans doublons."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.build_observability_report import attach_llm_calls_by_mission, _analysis_reports_absence


def _episode(mid="m1"):
    return {
        "mission_id": mid,
        "session_id": "s1",
        "execution_tree": {
            "solver_id": mid,
            "attempts": [
                {
                    "attempt_number": 1,
                    "started_at": "2026-09-26T14:00:00+00:00",
                    "ended_at": "2026-09-26T15:00:00+00:00",
                    "nodes": [{"step_id": "step_1"}, {"step_id": "step_2"}],
                }
            ],
        },
        "_skill_lifecycle": [
            {"event": "skill_lifecycle", "mission_id": mid, "solver_id": "sub1",
             "skill_id": "desktop.select.sunglasses", "consecutive_count": 1,
             "threshold": 2, "is_success": True},
            {"event": "skill_lifecycle", "mission_id": mid, "solver_id": "sub1",
             "skill_id": "desktop.select.sunglasses", "consecutive_count": 2,
             "threshold": 2, "is_success": True},
            {"event": "skill_lifecycle", "mission_id": mid, "solver_id": "sub2",
             "skill_id": "desktop.launch.chrome", "consecutive_count": 1,
             "threshold": 2, "is_success": True},
        ],
    }


def test_analysis_calls_sorted_with_found_flags():
    ep = _episode()
    calls = [
        {"tag": "llm_analyze_data", "mission_id": "m1", "solver_id": "m1",
         "step_id": "step_2", "attempt_number": 1,
         "ts": "2026-09-26T14:20:00+00:00",
         "response": {"data": "Found 3 items e_1 e_2 e_3"}},
        {"tag": "perceive_understand", "mission_id": "m1", "solver_id": "m1",
         "step_id": "step_1", "attempt_number": 1,
         "ts": "2026-09-26T14:10:00+00:00",
         "response": {"data": "Aucun filtre visible sur l'écran"}},
    ]
    attach_llm_calls_by_mission([ep], calls, [])
    analysis = ep.get("_analysis_calls", [])
    assert len(analysis) == 2
    assert analysis[0]["step_id"] == "step_1"
    assert analysis[0]["found"] is False
    assert analysis[1]["step_id"] == "step_2"
    assert analysis[1]["found"] is True


def test_skills_summary_grouped_no_dup_logic():
    ep = _episode()
    attach_llm_calls_by_mission([ep], [], [])
    summary = ep.get("_skills_summary", [])
    assert len(summary) == 2
    by_id = {g["skill_id"]: g for g in summary}
    assert by_id["desktop.select.sunglasses"]["events"] == 2
    assert by_id["desktop.select.sunglasses"]["last_count"] == 2
    assert by_id["desktop.launch.chrome"]["events"] == 1


def test_absence_helper():
    assert _analysis_reports_absence("Aucun filtre visible") is True
    assert _analysis_reports_absence("Found 3 sunglasses") is False

#!/usr/bin/env python3
"""Analyse compacte d'un rejeu : la chaine d'ancrage des cibles.

Lit un (ou plusieurs) sink JSONL d'evenements et imprime, par tentative puis
par etape, la chaine `selection -> gate -> liaison -> action`, avec un verdict
explicite par etape. L'objectif est qu'un rejeu se lise en une page, sans
fouiller le log brut.

Aucun nom d'hote, d'outil ou d'application n'est code en dur : seuls les noms
d'evenements du cerveau sont connus (`target.*`, `alignment_failure`).

Usage :
    python tools/analyze_replay.py
    python tools/analyze_replay.py chemin/events.jsonl
    python tools/analyze_replay.py --mission-id 0f7c... --json
    python tools/analyze_replay.py --strict     # code 1 si lien sans preuve

Ne depend que de la stdlib.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_EVENTS = os.path.join("observability", "events.jsonl")

TARGET_EVENTS = (
    "target.selected",
    "target.bound",
    "target.gate",
    "target.verification",
    "target.resolve_failed",
    "target.refreshed",
    "target.invalidated",
    "target.purged",
    "alignment_failure",
)

# Evenements qui comptent pour la lecture d'une etape d'ancrage.
STEP_EVENTS = TARGET_EVENTS + ("attempt_started", "attempt_failed")

TRUNCATION_HINTS = ("tronqu", "...", "incomplet")


# ---------------------------------------------------------------------------
# Lecture tolerante
# ---------------------------------------------------------------------------


def read_events(paths: Iterable[str]) -> List[Dict[str, Any]]:
    """Charge les evenements JSONL, en ignorant les lignes illisibles.

    Tolerant par construction : un log peut etre tronque par une coupure de
    process, et l'analyse ne doit pas echouer pour autant.
    """
    events: List[Dict[str, Any]] = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("event"):
                    events.append(record)
    events.sort(key=lambda item: str(item.get("ts") or ""))
    return events


# ---------------------------------------------------------------------------
# Regroupement
# ---------------------------------------------------------------------------


def _attempt_key(event: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(event.get("mission_id") or ""),
        str(event.get("solver_id") or ""),
        str(event.get("attempt_number") or ""),
    )


def split_attempts(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Regroupe par (mission, solver, tentative) en conservant l'ordre."""
    ordered: List[Tuple[Tuple[str, str, str], List[Dict[str, Any]]]] = []
    index: Dict[Tuple[str, str, str], int] = {}
    for event in events:
        if event.get("event") not in STEP_EVENTS:
            continue
        key = _attempt_key(event)
        if not any(key):
            continue
        if key not in index:
            index[key] = len(ordered)
            ordered.append((key, []))
        ordered[index[key]][1].append(event)
    return [
        {"mission_id": key[0], "solver_id": key[1], "attempt": key[2], "events": items}
        for key, items in ordered
    ]


def _step_of(event: Dict[str, Any]) -> str:
    for name in ("step_id", "step", "node_id"):
        value = event.get(name)
        if value:
            return str(value)
    return ""


def split_steps(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Regroupe les evenements d'une tentative par etape (ordre conserve)."""
    steps: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    for event in events:
        name = _step_of(event) or "?"
        if name not in index:
            index[name] = len(steps)
            steps.append({"step_id": name, "events": []})
        steps[index[name]]["events"].append(event)
    return steps


# ---------------------------------------------------------------------------
# Lecture d'une etape
# ---------------------------------------------------------------------------


def _looks_truncated(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in TRUNCATION_HINTS)


def read_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """Resume une etape : ce qui a ete produit, refuse, lie.

    Le verdict est deterministe et ne juge jamais la justesse metier : il dit
    seulement si l'etape a respecte la regle « preuve positive avant liaison ».
    """
    gates: List[Dict[str, Any]] = []
    verifications: List[Dict[str, Any]] = []
    bounds: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    selections: List[Dict[str, Any]] = []
    for event in step["events"]:
        name = event.get("event")
        if name == "target.gate":
            gates.append(event)
        elif name == "target.verification":
            verifications.append(event)
        elif name == "target.bound":
            bounds.append(event)
        elif name in ("target.resolve_failed", "alignment_failure"):
            failures.append(event)
        elif name == "target.selected":
            selections.append(event)

    proven = [gate for gate in gates if gate.get("status") == "proven"]
    refused = [
        gate
        for gate in gates
        if gate.get("status") in ("unproven", "contradicted")
    ]
    flags: List[str] = []

    if bounds and not gates:
        flags.append("GATE ABSENT")
    if bounds and gates and not proven:
        flags.append("LIEN SANS PREUVE")
    if refused and not bounds:
        flags.append("REFUS PROPRE")
    if any(
        _looks_truncated(str(gate.get("reason") or "")) for gate in refused
    ):
        flags.append("PREUVE TRONQUEE ?")
    if len(failures) > 1:
        flags.append("BOUCLE")

    return {
        "step_id": step["step_id"],
        "selections": selections,
        "gates": gates,
        "verifications": verifications,
        "bounds": bounds,
        "failures": failures,
        "proven": len(proven),
        "refused": len(refused),
        "flags": [flag for flag in flags if flag],
    }


def read_attempt(attempt: Dict[str, Any]) -> Dict[str, Any]:
    steps = [read_step(step) for step in split_steps(attempt["events"])]
    steps = [
        step
        for step in steps
        if step["gates"] or step["bounds"] or step["failures"] or step["selections"]
    ]
    flags = sorted({flag for step in steps for flag in step["flags"]})
    return {**attempt, "steps": steps, "flags": flags}


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------


def _short(text: Any, limit: int = 90) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _gate_line(gate: Dict[str, Any]) -> str:
    status = str(gate.get("status") or "?")
    verdict = str(gate.get("verdict") or "")
    label = str(gate.get("local_name") or "")
    reason = _short(gate.get("reason"), 90)
    parts = [f"gate={status}"]
    if verdict:
        parts.append(f"verdict={verdict}")
    if label:
        parts.append(f"cible={label}")
    if reason:
        parts.append(f"« {reason} »")
    return " · ".join(parts)


def render_text(attempt: Dict[str, Any]) -> str:
    head = (
        f"tentative {attempt['attempt'] or '?'} "
        f"(solver {attempt['solver_id'][:8] or '?'} | mission "
        f"{attempt['mission_id'][:8] or '?'})"
    )
    lines = ["", head, "-" * max(40, len(head))]
    if not attempt["steps"]:
        lines.append("  aucun evenement de cible - le gate n'a pas tourne.")
        return "\n".join(lines)
    for step in attempt["steps"]:
        prefix = f"  {step['step_id']:<10}"
        if step["gates"]:
            for gate in step["gates"][:4]:
                lines.append(prefix + _gate_line(gate))
        if step["bounds"]:
            for bound in step["bounds"][:4]:
                alias = bound.get("alias") or "?"
                name = bound.get("local_name") or "?"
                lines.append(
                    f"{' ' * 12}liee {alias} -> {name}"
                )
        if step["failures"]:
            for failure in step["failures"][:4]:
                code = failure.get("code") or ""
                reason = _short(
                    failure.get("reason") or failure.get("message"), 90
                )
                lines.append(f"{' ' * 12}echec {code} \"{reason}\"")
        if step["flags"]:
            lines.append(f"{' ' * 12}! " + ", ".join(step["flags"]))
    # Verdict global, volontairement binaire et prudent.
    if "LIEN SANS PREUVE" in attempt["flags"] or "GATE ABSENT" in attempt["flags"]:
        verdict = "ANCRAGE NON PROUVE — a investiguer avant de conclure"
    elif attempt["flags"] and "REFUS PROPRE" in attempt["flags"]:
        verdict = "refus propre (le plan doit repartir, pas de clic non prouve)"
    elif any(step["proven"] for step in attempt["steps"]):
        verdict = "preuve positive presente"
    else:
        verdict = "aucune cible produite"
    lines.append(f"  => {verdict}")
    return "\n".join(lines)


def summarize(attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    gates: Dict[str, int] = {}
    verdicts: Dict[str, int] = {}
    for attempt in attempts:
        for step in attempt["steps"]:
            for gate in step["gates"]:
                key = str(gate.get("status") or "?")
                gates[key] = gates.get(key, 0) + 1
                verdict = str(gate.get("verdict") or "")
                if verdict:
                    verdicts[verdict] = verdicts.get(verdict, 0) + 1
    flags = sorted({flag for attempt in attempts for flag in attempt["flags"]})
    return {"gates": gates, "verdicts": verdicts, "flags": flags}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyse compacte de la chaine d'ancrage des cibles."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=None,
        help=f"sink(s) JSONL (defaut : {DEFAULT_EVENTS})",
    )
    parser.add_argument("--mission-id", default="", help="ne garder qu'une mission")
    parser.add_argument("--json", action="store_true", help="sortie machine")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="code 1 si un lien existe sans preuve (ou si le gate est absent)",
    )
    args = parser.parse_args(argv)

    # Console Windows : un caractere non representable ne doit jamais tuer
    # l'analyse (le log hote peut contenir n'importe quoi).
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    paths = args.paths or [DEFAULT_EVENTS]
    events = read_events(paths)
    if not events:
        print(f"aucun evenement lisible dans : {', '.join(paths)}")
        return 2

    attempts = split_attempts(events)
    if args.mission_id:
        attempts = [
            attempt
            for attempt in attempts
            if attempt["mission_id"].startswith(args.mission_id)
        ]
    if not attempts:
        print("aucune tentative correspondante.")
        return 2

    analysed = [read_attempt(attempt) for attempt in attempts]

    if args.json:
        payload = {
            "attempts": [
                {
                    "mission_id": item["mission_id"],
                    "solver_id": item["solver_id"],
                    "attempt": item["attempt"],
                    "flags": item["flags"],
                    "steps": [
                        {
                            "step_id": step["step_id"],
                            "flags": step["flags"],
                            "proven": step["proven"],
                            "refused": step["refused"],
                            "gates": step["gates"],
                            "bounds": step["bounds"],
                            "failures": step["failures"],
                        }
                        for step in item["steps"]
                    ],
                }
                for item in analysed
            ],
            "summary": summarize(analysed),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in analysed:
            print(render_text(item))
        total = summarize(analysed)
        print("\n" + "=" * 60)
        print(f"tentatives      : {len(analysed)}")
        print(
            "gate            : "
            + (", ".join(f"{k}={v}" for k, v in sorted(total["gates"].items())) or "aucun")
        )
        print(
            "verdicts        : "
            + (
                ", ".join(f"{k}={v}" for k, v in sorted(total["verdicts"].items()))
                or "aucun"
            )
        )
        print("alertes         : " + (", ".join(total["flags"]) or "aucune"))

    if args.strict:
        blocked = {"LIEN SANS PREUVE", "GATE ABSENT"}
        if any(blocked & set(item["flags"]) for item in analysed):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

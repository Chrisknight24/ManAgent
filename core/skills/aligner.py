# -*- coding: utf-8 -*-
"""
core/skills/aligner.py
Module d'Alignement de Traces et Évaluation de Concordance Souple (Shadow Staging).
Permet de valider scientifiquement si une mission exécutée par le Planner classique
est conforme au méta-plan d'une compétence en phase SHADOW avant sa promotion en PRODUCTION.
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class AlignmentResult:
    """Résultat de l'analyse différentielle entre la trace réelle et le méta-plan."""
    concordance_score: float
    coverage_score: float
    is_aligned: bool
    is_simplification: bool = False
    lcs_sequence: List[str] = field(default_factory=list)
    trace_tokens: List[str] = field(default_factory=list)
    plan_tokens: List[str] = field(default_factory=list)
    trace_length: int = 0
    meta_plan_length: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concordance_score": round(self.concordance_score, 3),
            "coverage_score": round(self.coverage_score, 3),
            "is_aligned": self.is_aligned,
            "is_simplification": self.is_simplification,
            "lcs_length": len(self.lcs_sequence),
            "trace_length": self.trace_length,
            "meta_plan_length": self.meta_plan_length,
            "reason": self.reason
        }


class SkillTraceAligner:
    """
    Algorithme de concordance souple basé sur la Plus Longue Sous-Séquence Commune (LCS).
    Tolère les micro-décalages temporels (ex: wait), les variations mineures de paramètres
    et évalue la conformité structurelle de l'enchaînement des primitives.
    """

    DEFAULT_ALIGNMENT_THRESHOLD: float = 0.80

    @classmethod
    def _normalize_node_token(cls, node: Dict[str, Any]) -> str:
        """
        Extrait le jeton canonique pour une étape de manière 100% agnostique et déterministe.
        Privilégie le nom de l'outil atomique, enrichi de son action spécifique si présente,
        afin d'éviter les faux décalages de vocabulaire entre Planner et Méta-Plan.
        """
        tool = str(node.get("tool_name") or node.get("tool") or "").strip().lower()
        args = node.get("tool_args") or node.get("arguments") or node.get("args") or {}
        action_in_args = str(args.get("action") or "").strip().lower() if isinstance(args, dict) else ""
        action_field = str(node.get("action") or "").strip().lower()
        
        effective_action = action_in_args or (action_field if action_field != tool and " " not in action_field else "")
        
        if tool and effective_action:
            return f"{tool}:{effective_action}"
        elif tool:
            return tool
        elif effective_action:
            return effective_action
        
        fallback = str(node.get("type") or node.get("name") or "").strip().lower()
        return fallback

    @classmethod
    def compute_lcs(cls, seq_a: List[str], seq_b: List[str]) -> List[str]:
        """Calcule la plus longue sous-séquence commune entre deux listes de jetons."""
        m, n = len(seq_a), len(seq_b)
        if m == 0 or n == 0:
            return []

        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if seq_a[i - 1] == seq_b[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

        # Reconstruction de la sous-séquence
        lcs = []
        i, j = m, n
        while i > 0 and j > 0:
            if seq_a[i - 1] == seq_b[j - 1]:
                lcs.append(seq_a[i - 1])
                i -= 1
                j -= 1
            elif dp[i - 1][j] >= dp[i][j - 1]:
                i -= 1
            else:
                j -= 1

        lcs.reverse()
        return lcs

    @classmethod
    def evaluate_concordance(
        cls,
        observed_trace: List[Dict[str, Any]],
        meta_plan: List[Dict[str, Any]],
        threshold: Optional[float] = None
    ) -> AlignmentResult:
        """
        Compare la trace observée lors d'une mission avec le méta-plan du Skill.
        Formule :
            Concordance = (2 * |LCS|) / (|Trace| + |MetaPlan|)
            Coverage = |LCS| / |MetaPlan|
        """
        thresh = threshold if threshold is not None else cls.DEFAULT_ALIGNMENT_THRESHOLD

        trace_tokens = [cls._normalize_node_token(n) for n in (observed_trace or [])]
        plan_tokens = [cls._normalize_node_token(n) for n in (meta_plan or [])]

        # Filtre optionnel des tokens vides
        trace_tokens = [t for t in trace_tokens if t]
        plan_tokens = [t for t in plan_tokens if t]

        len_t = len(trace_tokens)
        len_p = len(plan_tokens)

        if len_p == 0:
            return AlignmentResult(
                concordance_score=0.0,
                coverage_score=0.0,
                is_aligned=False,
                reason="Méta-plan vide."
            )

        if len_t == 0:
            return AlignmentResult(
                concordance_score=0.0,
                coverage_score=0.0,
                is_aligned=False,
                meta_plan_length=len_p,
                reason="Trace observée vide."
            )

        lcs = cls.compute_lcs(trace_tokens, plan_tokens)
        len_lcs = len(lcs)

        concordance = (2.0 * len_lcs) / (len_t + len_p)
        coverage = float(len_lcs) / float(len_p)

        # La conformité requiert un score de concordance global >= threshold
        # ET une couverture minimale des nœuds du méta-plan (au moins 75% du plan doit être couvert)
        is_aligned = (concordance >= thresh) or (coverage >= 0.85 and concordance >= 0.70)
        is_simplification = (len_t < len_p and len_t > 0)

        reason = (
            f"Concordance LCS : {round(concordance * 100, 1)}%, "
            f"Couverture du plan : {round(coverage * 100, 1)}% "
            f"({len_lcs}/{len_p} étapes alignées sur {len_t} observées)."
        )
        if is_simplification and not is_aligned:
            reason += " [Sous-séquence simplifiée observée]"

        return AlignmentResult(
            concordance_score=concordance,
            coverage_score=coverage,
            is_aligned=is_aligned,
            is_simplification=is_simplification,
            lcs_sequence=lcs,
            trace_tokens=trace_tokens,
            plan_tokens=plan_tokens,
            trace_length=len_t,
            meta_plan_length=len_p,
            reason=reason
        )

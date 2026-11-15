"""Politique d'appel LLM : profils de delais, surchargeable par l'hote.

Module AGNOSTIQUE : aucun nom de provider, aucun nom d'hote, aucun outil.
Il decrit *combien de temps* un appel a le droit de vivre selon sa *classe
d'usage* (selection de cible, analyse, defaut). Le but est de ne plus tuer un
raisonnement productif a une duree fixe arbitraire, sans pour autant laisser
un appel mort bloquer indefiniment.

Hierarchie de resolution (du plus fort au plus faible) :
  1. manifeste hote : ``metadata["llm_policy"]`` puis ``environment["llm_policy"]`` ;
  2. defauts ManAgent ci-dessous.

Format accepte pour ``llm_policy`` (tout est optionnel) :

    {
      "on_stall": "abort",
      "stall_interval_s": 30,
      "profiles": {
        "selector": 120,
        "analysis": {"total_s": 300, "on_stall": "abort", "thinking_level": "high"},
        "default": 600
      }
    }

Champs de raisonnement : ``thinking_level`` et ``reasoning_effort`` sont lus,
validés dans un vocabulaire NEUTRE (minimal/low/medium/high) et transmis au
fournisseur comme des indices. Un fournisseur qui n'a pas de traduction native
les ignore explicitement ; le cerveau ne fabrique jamais une valeur a la place
d'une absence. Une valeur hors vocabulaire est ignoree (log), pas devinee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from utils.logger import Logger

MANIFEST_KEY = "llm_policy"

MIN_TOTAL_SECONDS = 30.0
MAX_TOTAL_SECONDS = 1800.0
DEFAULT_TOTAL_SECONDS = 600.0

# Classes d'usage connues. Toute autre classe retombe sur "default".
DEFAULT_TOTALS: Dict[str, float] = {
    "selector": 120.0,
    "analysis": 300.0,
    "default": DEFAULT_TOTAL_SECONDS,
}

# Seul mode actif en P0. Les autres modes (retry_same, rotate_per_policy,
# hedge) appartiennent au chantier « vitalite » : les accepter aujourd'hui
# donnerait l'illusion d'une fonctionnalite inexistante.
ACTIVE_STALL_POLICIES = ("abort",)
DEFAULT_STALL_POLICY = "abort"

# Battement de vie : pendant un appel muet, on ecrit une ligne « toujours en
# vol » a intervalle regulier. Ce n'est PAS un timeout et ca ne raccourcit
# jamais le budget : le plafond de la classe reste la seule autorite. 0 (ou
# false / "off") desactive explicitement le battement.
DEFAULT_STALL_INTERVAL_SECONDS = 30.0
MIN_STALL_INTERVAL_SECONDS = 1.0
MAX_STALL_INTERVAL_SECONDS = 300.0

_DISABLED_INTERVALS = ("0", "off", "none", "false", "disabled")


def _coerce_stall_interval(value: Any, fallback: float) -> float:
    """Intervalle de battement : 0 = desactive ; valeur illisible = defaut."""
    if isinstance(value, bool):
        return 0.0 if value is False else float(fallback)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in _DISABLED_INTERVALS:
            return 0.0
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return float(fallback)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if seconds != seconds:  # NaN
        return float(fallback)
    if seconds <= 0:
        return 0.0
    return max(MIN_STALL_INTERVAL_SECONDS, min(MAX_STALL_INTERVAL_SECONDS, seconds))



def _clamp_seconds(value: Any, fallback: float) -> float:
    """Borne un delai en secondes dans [MIN, MAX] ; valeur illisible = defaut."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if seconds != seconds:  # NaN
        return float(fallback)
    return max(MIN_TOTAL_SECONDS, min(MAX_TOTAL_SECONDS, seconds))


@dataclass(frozen=True)
class CallProfile:
    """Duree maximale d'un appel d'une classe donnee."""

    kind: str
    total_s: float
    on_stall: str = DEFAULT_STALL_POLICY
    # Indices NEUTRES de raisonnement (voir THINKING_LEVELS / REASONING_EFFORTS).
    # "" = l'hote n'a rien demande : on n'invente aucun defaut.
    thinking_level: str = ""
    reasoning_effort: str = ""


@dataclass(frozen=True)
class LlmPolicy:
    """Politique complete resolue pour une session."""

    profiles: Mapping[str, CallProfile] = field(default_factory=dict)
    on_stall: str = DEFAULT_STALL_POLICY
    stall_interval_s: float = DEFAULT_STALL_INTERVAL_SECONDS
    source: str = "default"

    def profile_for(self, kind: Optional[str]) -> CallProfile:
        """Profil d'une classe ; toute classe inconnue retombe sur ``default``."""
        key = str(kind or "default").strip().lower() or "default"
        profile = self.profiles.get(key)
        if profile is not None:
            return profile
        fallback = self.profiles.get("default")
        if fallback is not None:
            return CallProfile(
                kind=key,
                total_s=fallback.total_s,
                on_stall=fallback.on_stall,
                thinking_level=fallback.thinking_level,
                reasoning_effort=fallback.reasoning_effort,
            )
        return CallProfile(kind=key, total_s=DEFAULT_TOTAL_SECONDS)


def default_policy() -> LlmPolicy:
    """Politique de reference quand l'hote ne declare rien."""
    profiles = {
        kind: CallProfile(kind=kind, total_s=total, on_stall=DEFAULT_STALL_POLICY)
        for kind, total in DEFAULT_TOTALS.items()
    }
    return LlmPolicy(profiles=profiles, on_stall=DEFAULT_STALL_POLICY, source="default")


# Vocabulaire NEUTRE des indices de raisonnement, commun a tous les
# fournisseurs. Un provider traduit ces mots dans SON vocabulaire natif, ou
# les ignore explicitement : le cerveau n'invente jamais une valeur a la place
# d'une absence, et ne devine pas ce qu'un fournisseur comprendrait.
THINKING_LEVELS = ("minimal", "low", "medium", "high")
REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def _coerce_reasoning_hint(value: Any, allowed: Tuple[str, ...], label: str,
                           kind: str) -> str:
    """Un indice connu est garde ; inconnu -> absent (jamais un defaut invente)."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in allowed:
        return raw
    Logger.info(
        f"[LlmPolicy] {label}='{raw}' inconnu pour le profil '{kind}' "
        f"(attendu: {', '.join(allowed)}) : ignore."
    )
    return ""


def _coerce_stall_policy(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return fallback
    if candidate in ACTIVE_STALL_POLICIES:
        return candidate
    Logger.info(
        f"[LlmPolicy] on_stall='{candidate}' ignore (modes actifs: "
        f"{', '.join(ACTIVE_STALL_POLICIES)}). Utilisation de '{fallback}'."
    )
    return fallback


def _coerce_profile(kind: str, raw: Any, default_total: float) -> Optional[CallProfile]:
    """Un profil peut etre un nombre (secondes) ou un dict partiel."""
    if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
        return CallProfile(
            kind=kind,
            total_s=_clamp_seconds(raw, default_total),
            on_stall=DEFAULT_STALL_POLICY,
        )
    if not isinstance(raw, Mapping):
        return None

    total = _clamp_seconds(
        raw.get("total_s", raw.get("total", default_total)), default_total
    )
    known = {
        "total_s", "total", "on_stall",
        "thinking_level", "reasoning_effort",
    }
    unknown = [key for key in raw if key not in known]
    if unknown:
        Logger.debug(f"[LlmPolicy] Profil '{kind}' : cles inconnues ignorees {unknown}.")
    return CallProfile(
        kind=kind,
        total_s=total,
        on_stall=_coerce_stall_policy(raw.get("on_stall"), DEFAULT_STALL_POLICY),
        thinking_level=_coerce_reasoning_hint(
            raw.get("thinking_level"), THINKING_LEVELS, "thinking_level", kind
        ),
        reasoning_effort=_coerce_reasoning_hint(
            raw.get("reasoning_effort"), REASONING_EFFORTS, "reasoning_effort", kind
        ),
    )


def policy_from_mapping(raw: Any, source: str = "manifest") -> LlmPolicy:
    """Construit une politique depuis un dict de manifeste (pure, tolerante)."""
    base = default_policy()
    if not isinstance(raw, Mapping):
        return base

    global_stall = _coerce_stall_policy(raw.get("on_stall"), base.on_stall)
    stall_interval = _coerce_stall_interval(
        raw.get("stall_interval_s", base.stall_interval_s), base.stall_interval_s
    )
    raw_profiles = raw.get("profiles")
    profiles: Dict[str, CallProfile] = dict(base.profiles)

    if isinstance(raw_profiles, Mapping):
        for kind, value in raw_profiles.items():
            key = str(kind or "").strip().lower()
            if not key:
                continue
            fallback_total = DEFAULT_TOTALS.get(key, DEFAULT_TOTAL_SECONDS)
            profile = _coerce_profile(key, value, fallback_total)
            if profile is None:
                Logger.debug(f"[LlmPolicy] Profil '{key}' illisible : ignore.")
                continue
            profiles[key] = profile

    known_top = {"on_stall", "profiles", "stall_interval_s"}
    unknown_top = [key for key in raw if key not in known_top]
    if unknown_top:
        Logger.debug(f"[LlmPolicy] Cles racine inconnues ignorees {unknown_top}.")

    return LlmPolicy(
        profiles=profiles,
        on_stall=global_stall,
        stall_interval_s=stall_interval,
        source=source,
    )


def _manifest_llm_policy(manifest: Any) -> Any:
    """Premier ``llm_policy`` declare par l'hote (metadata puis environment)."""
    if manifest is None:
        return None
    for holder_name in ("metadata", "environment"):
        holder = getattr(manifest, holder_name, None)
        if isinstance(holder, Mapping):
            candidate = holder.get(MANIFEST_KEY)
            if candidate is not None:
                return candidate
    return None


def resolve_policy(runtime_state: Any = None) -> LlmPolicy:
    """Politique effective de la session (hote prioritaire, defauts sinon)."""
    try:
        manifest = getattr(runtime_state, "host_manifest", None)
    except Exception:
        manifest = None
    raw = _manifest_llm_policy(manifest)
    if raw is None:
        return default_policy()
    return policy_from_mapping(raw, source="manifest")


def profile_for(runtime_state: Any = None, kind: Optional[str] = None) -> CallProfile:
    """Raccourci : profil effectif d'une classe d'appel."""
    return resolve_policy(runtime_state).profile_for(kind)


def stall_interval_for(runtime_state: Any = None) -> float:
    """Intervalle du battement de vie, en secondes (0 = desactive)."""
    try:
        return float(resolve_policy(runtime_state).stall_interval_s)
    except Exception:
        return DEFAULT_STALL_INTERVAL_SECONDS

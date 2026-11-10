"""Ciblage visible : monde observe, decision et inspection progressive.

Ce module est AGNOSTIQUE. Il ne connait aucun nom d'outil ni de champ hote :
tout ce qui est specifique vient du `target_contract` (producteurs, attributs,
bloc `disclosure`). Le selecteur ne voit que des cles locales, des attributs
declares, des vues nommees et des identifiants de sondes.

Deux façons seulement d'addresser une cible, et ce sont des concepts du
cerveau, pas d'un hote :
  - `local` : une cle publiee par le registre de cibles ;
  - `anchor` : une valeur brute d'un `kind` declare par l'hote
    (`disclosure.anchors`), par exemple une cellule, un fichier, une touche,
    un symbole ou un axe de robot.

Boucle bornee « anchor or disclose » :
  1. le LLM recoit le monde observe (raw, medias, candidates, vues, sondes) ;
  2. il choisit une cible finale, ou demande UNE inspection ;
  3. la sonde est executee avec les arguments declares par l'hote ;
  4. le monde est rafraichi et le LLM retente, jusqu'a epuisement du budget.

Aucune reference hote n'est devinee : une sonde `local` utilise la reference
brute (ou le nom local quand la membrane egress le resout), une cible finale
`local` est un nom local, une cible `anchor` est une valeur declaree.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.base_schema import BaseDiscoverySchema
from core.i18n import _
from core.target_contract import (
    DEFAULT_EVIDENCE,
    DEFAULT_RELIABILITY,
    EVIDENCE_VALUES,
    LOCAL_SUBJECT_KIND,
    RELIABILITY_VALUES,
    DisclosureSpec,
    ProbeSpec,
    TargetContract,
)
from core.target_ingress import (
    apply_target_updates,
    collect_projected_targets,
    project_target_payload,
)
from core.targets import TargetEntry, TargetRegistry


MAX_TARGETING_ROUNDS = 2
MAX_PROMPT_CANDIDATES = 120
MAX_PROMPT_VIEW_CHARS = 12000
MAX_PROMPT_ATTRIBUTE_CHARS = 600
MAX_RAW_ANCHOR_CHARS = 2000

TARGET_KIND_LOCAL = "local"
TARGET_KIND_ANCHOR = "anchor"


@dataclass
class MediaRef:
    """Reference d'un media deja stocke dans le canal multimedia."""

    uri: str = ""
    mime: str = ""
    name: str = ""
    asset: Any = None


@dataclass
class ObservedCandidate:
    """Candidate telle que vue par le selecteur (jamais de ref brute)."""

    key: str
    description: str = ""
    scope: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)

    def as_prompt(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "scope": self.scope,
            "attributes": _trim_attributes(self.attributes),
        }


@dataclass
class ProbeRecord:
    """Trace d'une inspection demandee par le selecteur."""

    probe_id: str
    kind: str
    subject: str = ""
    ok: bool = False
    message: str = ""
    produced_keys: List[str] = field(default_factory=list)

    def as_prompt(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "kind": self.kind,
            "subject": self.subject,
            "ok": self.ok,
            "message": self.message[:300],
            "produced_keys": list(self.produced_keys[:40]),
        }


@dataclass
class ObservedWorld:
    """Tout ce que le selecteur doit voir pour decider sans deviner."""

    raw: Any = None
    payload: Any = None
    media: List[MediaRef] = field(default_factory=list)
    candidates: List[ObservedCandidate] = field(default_factory=list)
    # Vues nommees declarees par l'hote (`disclosure.views`) : nom libre ->
    # contenu lu au chemin declare. Aucune semantique codee en dur.
    views: Dict[str, Any] = field(default_factory=dict)
    view_meta: Dict[str, str] = field(default_factory=dict)
    probes: List[ProbeRecord] = field(default_factory=list)

    @classmethod
    def from_payload(
        cls,
        registry: Optional[TargetRegistry],
        contract: Optional[TargetContract],
        payload: Any,
        *,
        media_assets: Optional[List[Any]] = None,
        disclosure: Optional[DisclosureSpec] = None,
    ) -> "ObservedWorld":
        if disclosure is None and contract is not None:
            disclosure = contract.disclosure
        world = cls()
        world.refresh(
            registry,
            contract,
            payload,
            media_assets=media_assets,
            disclosure=disclosure,
        )
        return world

    def refresh(
        self,
        registry: Optional[TargetRegistry],
        contract: Optional[TargetContract],
        payload: Any,
        *,
        media_assets: Optional[List[Any]] = None,
        disclosure: Optional[DisclosureSpec] = None,
    ) -> None:
        if payload is not None:
            self.payload = payload
            self.raw = _payload_data(payload)
        if media_assets is not None:
            self.media = [_media_ref(asset) for asset in media_assets]
        if disclosure is None and contract is not None:
            disclosure = contract.disclosure
        if registry is not None and contract is not None and self.payload is not None:
            entries = collect_projected_targets(registry, contract, self.payload)
            self.candidates = [_observed_candidate(entry) for entry in entries]
        if disclosure is not None and self.payload is not None:
            # Un refresh remplace le monde : une vue absente du nouveau
            # payload ne doit pas rester affichee comme si elle etait fraiche.
            self.views = {}
            self.view_meta = {}
            for view in disclosure.views:
                value = _read_path(self.payload, view.path)
                if value is not None:
                    self.views[view.name] = value
                    if view.description:
                        self.view_meta[view.name] = view.description

    def find_entry(
        self, registry: Optional[TargetRegistry], key: str
    ) -> Optional[TargetEntry]:
        if registry is None or not isinstance(key, str) or not key:
            return None
        for candidate in self.candidates:
            if candidate.key == key:
                entry = registry.get(key)
                return entry if entry is not None and entry.active else None
        return None

    def media_assets(self) -> List[Any]:
        return [media.asset for media in self.media if media.asset is not None]


@dataclass
class TargetingResult:
    """Resultat de la boucle de ciblage.

    `kind` ne peut valoir que `local`, `anchor` ou `none` : ce sont les deux
    façons d'addresser une cible, pas des types d'objet d'un hote.
    """

    kind: str = "none"
    entry: Optional[TargetEntry] = None
    anchor_kind: str = ""
    anchor_value: str = ""
    reason: str = ""
    world: Optional[ObservedWorld] = None
    error_code: str = ""
    error_message: str = ""
    # `deterministic` quand une correspondance exacte unique a evite le LLM.
    strategy: str = ""
    # Doutes non bloquants (fail-open, preuves insuffisantes) destines au
    # planner (retries) et a la convergence. Jamais une liste de candidats.
    warnings: List[str] = field(default_factory=list)
    # Trace de verification (verdict, fiabilite, suite). Optionnelle.
    # `verification` reste le dernier verdict (compatibilite) ; `verifications`
    # porte TOUS les verdicts de la boucle, mismatch initial inclus, pour que
    # l'observabilite ne perde pas l'historique.
    verification: Optional[Dict[str, Any]] = None
    verifications: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error_code and self.kind in {
            TARGET_KIND_LOCAL,
            TARGET_KIND_ANCHOR,
        }

    def local_name(self) -> str:
        return self.entry.local_name if self.entry is not None else ""

    def target_value(self) -> str:
        if self.kind == TARGET_KIND_ANCHOR:
            return self.anchor_value
        return self.local_name()

    def target_scope(self) -> str:
        if self.kind == TARGET_KIND_ANCHOR:
            return self.anchor_kind
        return getattr(self.entry, "scope", "") if self.entry is not None else ""


class TargetDecision(BaseDiscoverySchema):
    """Decision du selecteur : cible finale OU demande d'inspection."""

    target_key: str = Field(
        default="",
        description=_(
            "Cle locale exacte d'une cible publiee, ou valeur brute d'une "
            "ancre declaree par l'hote. Vide si une inspection est demandee."
        ),
    )
    target_kind: str = Field(
        default=TARGET_KIND_LOCAL,
        description=_(
            "Facon d'addresser la cible : 'local' (cle du registre) ou "
            "'anchor' (valeur brute d'un type d'ancre declare par l'hote)."
        ),
    )
    anchor_kind: str = Field(
        default="",
        description=_(
            "Si target_kind='anchor' : type d'ancre declare par l'hote "
            "(par exemple cell, file, key, joint, symbol...). Sinon vide."
        ),
    )
    no_target: bool = Field(
        default=False,
        description=_(
            "Vrai si aucune cible ne convient et qu'aucune inspection utile "
            "n'est possible."
        ),
    )
    reason: str = Field(
        default="",
        description=_("Justification courte, une phrase."),
    )


class TargetVerification(BaseDiscoverySchema):
    """Re-verification finale d'une cible choisie (garde anti-substitution).

    Le selecteur peut choisir l'element « le plus proche » alors que la
    question demande une nature differente (zone vide vs fichier). Ce schema
    ne sert qu'a confirmer/infirmer la correspondance semantique ; il ne
    choisit jamais une autre cible. Il peut seulement ORIENTER (`next`,
    `prefer_internal`) : c'est le harnais qui reinterroge le selecteur.
    """

    matches: bool = Field(
        default=False,
        description=_(
            "Vrai seulement si la cible decrite satisfait REELLEMENT la "
            "question (meme nature d'objet, meme role). Une simple proximite "
            "ou un libelle proche ne suffit pas."
        ),
    )
    confidence: str = Field(
        default="low",
        description=_("low, medium ou high : confiance dans ce verdict."),
    )
    verdict: str = Field(
        default="",
        description=_(
            "match | mismatch | uncertain. Vide = deduit de `matches` et "
            "`confidence` (compatibilite)."
        ),
    )
    next: str = Field(
        default="",
        description=_(
            "accept | zoom | replan. Vide = deduction. `zoom` demande une "
            "inspection de l'ancienne candidate, jamais un nouveau choix."
        ),
    )
    prefer_internal: str = Field(
        default="",
        description=_(
            "Cle locale d'une cible INTERNE qui semble meilleure. Indicateur "
            "pour le selecteur, pas une selection."
        ),
    )
    reason: str = Field(
        default="",
        description=_("Une phrase : pourquoi la cible correspond ou non."),
    )

    def effective_verdict(self) -> str:
        value = str(self.verdict or "").strip().lower()
        if value in ("match", "mismatch", "uncertain"):
            return value
        if self.matches:
            return "match"
        if str(self.confidence or "").strip().lower() in ("medium", "high"):
            return "mismatch"
        return "uncertain"

    def effective_next(self) -> str:
        value = str(self.next or "").strip().lower()
        if value in ("accept", "zoom", "replan"):
            return value
        return ""


@dataclass
class VerificationOutcome:
    """Resultat de verification, plus riche qu'un booleen.

    `verdict` tranche (match/mismatch/uncertain), `next` oriente la suite.
    Aucune cible n'est choisie ici : `prefer_internal` reste un indicateur.
    """

    verdict: str = "match"
    confidence: str = "low"
    reason: str = ""
    next: str = "accept"
    prefer_internal: str = ""
    reliability: str = DEFAULT_RELIABILITY
    verified: bool = False
    # Cle locale de la candidate verifiee : indispensable pour rattacher
    # chaque verdict a sa cible quand la boucle en verifie plusieurs.
    local_name: str = ""

    @property
    def matches(self) -> bool:
        return self.verdict == "match"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "reason": self.reason[:300],
            "next": self.next,
            "prefer_internal": self.prefer_internal,
            "reliability": self.reliability,
            "verified": self.verified,
            "local_name": self.local_name,
        }


def _payload_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def _media_ref(asset: Any) -> MediaRef:
    uri = ""
    mime = ""
    name = ""
    try:
        meta = getattr(asset, "asset_meta", None)
        if meta is not None:
            mime = str(getattr(meta, "mime_type", "") or "")
            name = str(getattr(meta, "name", "") or "")
        uri = str(getattr(asset, "target_id", "") or "")
    except Exception:
        pass
    return MediaRef(uri=uri, mime=mime, name=name, asset=asset)


def _observed_candidate(entry: TargetEntry) -> ObservedCandidate:
    attributes = getattr(entry, "attributes", None)
    return ObservedCandidate(
        key=entry.local_name,
        description=entry.description or "",
        scope=entry.scope or "",
        attributes=copy.deepcopy(attributes) if isinstance(attributes, dict) else {},
    )


_NORMALIZE_RE = re.compile(r"[^0-9a-z\u00c0-\u024f]+", re.IGNORECASE)


def _normalize_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _NORMALIZE_RE.sub(" ", text).strip()


def exact_candidate(
    world: ObservedWorld, question: str
) -> Optional[ObservedCandidate]:
    """Candidate dont un libelle EGALE exactement la question (P2-10).

    Resolution deterministe et conservatrice : on ne retient que l'egalite
    (normalisee) avec la description ou avec une valeur d'attribut textuelle
    suffisamment longue, et seulement si elle est UNIQUE. Des qu'il y a
    ambiguite ou simple similarite, on renvoie `None` et le selecteur LLM
    garde la main. Aucun vocabulaire d'hote n'est suppose. Fonction pure.
    """
    query = _normalize_label(question)
    if not query:
        return None
    matches: List[ObservedCandidate] = []
    for candidate in getattr(world, "candidates", []) or []:
        labels = [_normalize_label(candidate.description)]
        attributes = candidate.attributes if isinstance(candidate.attributes, dict) else {}
        for name, value in attributes.items():
            if not isinstance(value, str):
                continue
            normalized = _normalize_label(value)
            # Un attribut court (`true`, `A1`, `ui`) egale trop de choses.
            if len(normalized) >= 3:
                labels.append(normalized)
        if query in labels and candidate not in matches:
            matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    return None


def _trim_attributes(attributes: Any) -> Any:
    if not isinstance(attributes, dict):
        return {}
    trimmed: Dict[str, Any] = {}
    for name, value in list(attributes.items())[:20]:
        try:
            encoded = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            encoded = str(value)
        if len(encoded) > MAX_PROMPT_ATTRIBUTE_CHARS:
            encoded = encoded[:MAX_PROMPT_ATTRIBUTE_CHARS] + "..."
        trimmed[str(name)] = encoded
    return trimmed


def effective_reliability(
    disclosure: Optional[DisclosureSpec],
    contract: Optional[TargetContract],
    scope: str,
) -> str:
    """Fiabilite declaree pour un scope de cible (agnostique).

    Ordre : surcharge d'ancre (meme kind) > surcharge de producteur (meme
    scope ou `usable_as`) > defaut global `disclosure.reliability` > medium.
    """
    scope = str(scope or "").strip()
    if disclosure is not None:
        for anchor in disclosure.anchors:
            if anchor.kind == scope and anchor.reliability:
                return anchor.reliability
    if contract is not None and scope:
        for producer in contract.producers:
            if producer.reliability and (
                producer.scope == scope or scope in producer.usable_as
            ):
                return producer.reliability
    if disclosure is not None and disclosure.reliability in RELIABILITY_VALUES:
        return disclosure.reliability
    return DEFAULT_RELIABILITY


def _declared_evidence(disclosure: Optional[DisclosureSpec]) -> Tuple[str, ...]:
    if disclosure is not None and disclosure.evidence:
        return tuple(disclosure.evidence)
    return DEFAULT_EVIDENCE


def build_target_evidence(
    entry: TargetEntry,
    world: Optional["ObservedWorld"],
    disclosure: Optional[DisclosureSpec],
) -> Dict[str, Any]:
    """Bundle de preuves generique, selon les capacites declarees.

    Ne code aucune semantique d'hote : `image` vient du canal media,
    `rect`/`invariants` sont des attributs declares, `nested_targets` sont
    les autres candidates visibles du monde courant.
    """
    attributes = getattr(entry, "attributes", {}) or {}
    evidence: Dict[str, Any] = {
        "description": str(getattr(entry, "description", "") or "")[:300],
        "scope": str(getattr(entry, "scope", "") or ""),
        "attributes": _trim_attributes(attributes),
    }
    caps = set(_declared_evidence(disclosure))
    if "image" in caps and world is not None:
        media = [
            {"uri": m.uri, "mime": m.mime, "name": m.name}
            for m in getattr(world, "media", []) or []
        ]
        if media:
            evidence["image"] = media[:4]
    if "rect" in caps and isinstance(attributes, dict):
        rect = attributes.get("rect") or attributes.get("bbox")
        if rect is not None:
            evidence["rect"] = rect
    if "nested_targets" in caps and world is not None:
        nested = [
            candidate.as_prompt()
            for candidate in getattr(world, "candidates", []) or []
            if candidate.key != getattr(entry, "local_name", "")
        ][:MAX_PROMPT_CANDIDATES]
        if nested:
            evidence["nested"] = nested
    if "invariants" in caps and isinstance(attributes, dict):
        invariants = attributes.get("invariants")
        if invariants is not None:
            evidence["invariants"] = invariants
    return evidence


def _read_path(obj: Any, path: Optional[str]) -> Any:
    """Lit un chemin declare, a la racine ou sous `data` (payload hote)."""
    if not isinstance(path, str) or not path:
        return None
    roots: List[Any] = [obj]
    if isinstance(obj, dict) and "data" in obj:
        roots.append(obj.get("data"))
    for root in roots:
        current = root
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found:
            return current
    return None


def _json_block(value: Any, max_chars: int) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        encoded = str(value)
    if len(encoded) > max_chars:
        encoded = encoded[:max_chars] + "\n... [tronque]"
    return encoded


def _probe_prompt(probe: ProbeSpec) -> Dict[str, Any]:
    subject = None
    if probe.subject is not None:
        subject = {
            "kind": probe.subject.kind,
            "param": probe.subject.param,
        }
    return {
        "id": probe.id,
        "kind": probe.kind,
        "description": probe.description,
        "subject": subject,
        "produces": list(probe.produces),
    }


def _normalize_probe_id(value: str) -> str:
    raw = value.strip()
    for prefix in ("probe:", "targeting:", "inspect:"):
        if raw.lower().startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def _probe_request(
    decision: TargetDecision,
    disclosure: Optional[DisclosureSpec],
) -> Optional[Tuple[ProbeSpec, str]]:
    """Traduit `discovery_request` en sonde declaree (jamais un outil invente)."""
    if disclosure is None or not disclosure.probes:
        return None
    request = getattr(decision, "discovery_request", None)
    if request is None:
        return None

    goals = list(getattr(request, "technical_goals", None) or [])
    data_type = getattr(request, "data_type", "") or ""
    probe: Optional[ProbeSpec] = None
    for raw_value in list(goals) + [data_type]:
        value = _normalize_probe_id(str(raw_value or ""))
        if value:
            probe = disclosure.probe(value)
            if probe is not None:
                break
    if probe is None:
        return None

    targets = list(getattr(request, "targets", None) or [])
    subject = str(targets[0]).strip() if targets else ""
    return probe, subject


def _probe_subject_value(
    *,
    probe: ProbeSpec,
    subject: str,
    world: ObservedWorld,
    registry: Optional[TargetRegistry],
    disclosure: Optional[DisclosureSpec],
    tools_mgr: Any,
) -> Tuple[Any, str, Optional[str]]:
    """Valeur a envoyer pour le sujet, selon la declaration de l'hote.

    - `local` : nom local si le parametre est un consommateur declare
      (la membrane egress le resout en reference brute), sinon reference brute ;
    - tout autre `kind` : valeur brute d'une ancre declaree. Le consommateur,
      s'il existe, doit autoriser `raw: allow`.
    """
    if probe.subject is None:
        return None, "", None
    param = probe.subject.param
    kind = probe.subject.kind
    if not subject:
        return None, "", _("La sonde '{id}' exige un sujet.").format(id=probe.id)

    probe_contract = None
    if probe.tool and tools_mgr is not None and hasattr(tools_mgr, "get_target_contract"):
        try:
            probe_contract = tools_mgr.get_target_contract(probe.tool)
        except Exception:
            probe_contract = None
    consumer = (
        probe_contract.consumer_for(param) if probe_contract is not None else None
    )

    if kind == LOCAL_SUBJECT_KIND:
        entry = world.find_entry(registry, subject)
        if entry is None:
            return None, "", _(
                "Cible locale inconnue pour la sonde '{id}' : {key!r}."
            ).format(id=probe.id, key=subject[:120])
        value = entry.local_name if consumer is not None else entry.ref.value
        return value, entry.local_name, None

    if disclosure is not None and disclosure.anchors:
        if disclosure.anchor(kind) is None:
            return None, "", _(
                "La sonde '{id}' utilise le type d'ancre '{kind}', que l'hote "
                "ne declare pas."
            ).format(id=probe.id, kind=kind)
    if consumer is not None and not consumer.raw_allow:
        return None, "", _(
            "La sonde '{id}' vise une ancre brute mais le parametre "
            "'{param}' n'autorise pas les valeurs brutes."
        ).format(id=probe.id, param=param)
    return subject, subject, None


async def _run_probe(
    *,
    probe: ProbeSpec,
    subject: str,
    world: ObservedWorld,
    registry: Optional[TargetRegistry],
    contract: Optional[TargetContract],
    disclosure: Optional[DisclosureSpec],
    tools_mgr: Any,
    question: str,
) -> Tuple[Optional[ProbeRecord], Optional[str]]:
    if probe.tool is None:
        return None, _(
            "La sonde '{id}' ne declare aucun outil : inspection impossible."
        ).format(id=probe.id)
    if tools_mgr is None or not hasattr(tools_mgr, "execute_tool"):
        return None, _("Aucun gestionnaire d'outils pour executer la sonde.")

    args = dict(probe.args or {})
    if probe.hint_param and question and not args.get(probe.hint_param):
        args[probe.hint_param] = question

    value, subject_label, error = _probe_subject_value(
        probe=probe,
        subject=subject,
        world=world,
        registry=registry,
        disclosure=disclosure,
        tools_mgr=tools_mgr,
    )
    if error:
        return None, error
    if probe.subject is not None:
        args[probe.subject.param] = value

    try:
        raw = await tools_mgr.execute_tool(probe.tool, args)
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        return ProbeRecord(
            probe_id=probe.id,
            kind=probe.kind,
            subject=subject_label or subject,
            ok=False,
            message=_("Sonde '{id}' en echec : {err}").format(
                id=probe.id, err=exc
            ),
        ), None

    failed = isinstance(payload, dict) and not payload.get("result", True)
    message = ""
    if failed:
        message = str(
            payload.get("error_reason")
            or payload.get("message")
            or _("Sonde en echec.")
        )

    # Le monde courant est sauvegarde avant la sonde : une inspection en
    # echec ou muette ne doit ni effacer les candidates deja vues, ni
    # re-ancrer les vues sur un payload partiel (crop, erreur).
    snapshot = {
        "candidates": list(world.candidates),
        "views": dict(world.views),
        "view_meta": dict(world.view_meta),
        "media": list(world.media),
        "payload": world.payload,
        "raw": world.raw,
    }
    payload, media_assets = extract_media_assets(tools_mgr, probe.tool, payload)
    if registry is not None and contract is not None and isinstance(payload, dict):
        payload, _count = project_target_payload(
            registry, contract, payload, register=True
        )
        payload, _applied = apply_target_updates(registry, payload)
    world.refresh(
        registry,
        contract,
        payload,
        media_assets=media_assets,
        disclosure=disclosure,
    )
    produced_keys = [candidate.key for candidate in world.candidates]
    if failed or not produced_keys:
        # Sonde en echec ou sans candidate : on conserve l'ancien monde.
        # Le nouveau payload est ecarte, pas fusionne : une vue issue d'un
        # crop ne doit pas remplacer la vue d'ecran complete.
        world.candidates = snapshot["candidates"]
        world.views = snapshot["views"]
        world.view_meta = snapshot["view_meta"]
        world.media = snapshot["media"]
        world.payload = snapshot["payload"]
        world.raw = snapshot["raw"]
        if failed:
            # Rien de fiable n'a ete produit : ne pas annoncer de cles.
            produced_keys = []
    return ProbeRecord(
        probe_id=probe.id,
        kind=probe.kind,
        subject=subject_label or subject,
        ok=not failed,
        message=message,
        produced_keys=produced_keys,
    ), None


def _build_prompt(
    *,
    question: str,
    world: ObservedWorld,
    disclosure: Optional[DisclosureSpec],
    allow_anchor: bool,
    anchor_kinds: Optional[List[str]],
    allow_probes: bool,
    language: str,
    verifier_hint: str = "",
) -> str:
    from core.prompt_loader import get_prompt_loader

    candidates = [
        candidate.as_prompt()
        for candidate in world.candidates[:MAX_PROMPT_CANDIDATES]
    ]
    candidate_lines = "\n".join(
        f"- {candidate.key}: {candidate.description[:200]} "
        f"(scope={candidate.scope})"
        for candidate in world.candidates[:MAX_PROMPT_CANDIDATES]
    )
    probes = (
        [_probe_prompt(probe) for probe in disclosure.probes]
        if allow_probes and disclosure is not None
        else []
    )
    anchor_specs = []
    if allow_anchor:
        known = set()
        if disclosure is not None:
            for anchor in disclosure.anchors:
                anchor_specs.append(
                    {"kind": anchor.kind, "description": anchor.description}
                )
                known.add(anchor.kind)
        for kind in anchor_kinds or []:
            if kind not in known:
                anchor_specs.append({"kind": kind, "description": ""})
                known.add(kind)
    views = [
        {
            "name": name,
            "description": world.view_meta.get(name, ""),
            "value": value,
        }
        for name, value in world.views.items()
    ]
    payload = {
        "question": question,
        "candidates_json": _json_block(candidates, 12000),
        "candidate_lines": candidate_lines,
        "views_json": _json_block(views, MAX_PROMPT_VIEW_CHARS),
        "view_names": ", ".join(world.views.keys()),
        "media_json": _json_block(
            [
                {"uri": media.uri, "mime": media.mime, "name": media.name}
                for media in world.media
            ],
            3000,
        ),
        "probes_json": _json_block(probes, 4000),
        "probe_history_json": _json_block(
            [record.as_prompt() for record in world.probes], 4000
        ),
        "anchors_json": _json_block(anchor_specs, 4000),
        "allow_anchor": bool(allow_anchor and anchor_specs),
        "allow_probes": bool(allow_probes),
        # Indice du verificateur apres un rejet : oriente le selecteur vers
        # l'ancienne candidate, sans jamais imposer un choix.
        "verifier_hint": verifier_hint or "",
        # Le selecteur n'est appele que lorsque le ciblage est actif : le
        # gabarit reste vide pour tout rendu generique (capacite OFF).
        "target_selection_enabled": True,
    }
    try:
        return get_prompt_loader().load(
            "target_selection.md", lang=language, **payload
        )
    except Exception:
        # Repli deterministe : jamais de crash, jamais de nom d'outil.
        lines = [
            _("Choisis une cible visible ou demande une inspection."),
            _("Question : {question}").format(question=question),
            _("Cibles locales :"),
            payload["candidates_json"],
            _("Vues declarees :"),
            payload["views_json"],
            _("Ancres declarees :"),
            payload["anchors_json"],
            _("Sondes :"),
            payload["probes_json"],
        ]
        return "\n".join(lines)


async def _ask_selector(
    *,
    llm: Any,
    question: str,
    world: ObservedWorld,
    disclosure: Optional[DisclosureSpec],
    allow_anchor: bool,
    anchor_kinds: Optional[List[str]],
    allow_probes: bool,
    language: str,
    tag: str,
    verifier_hint: str = "",
) -> Optional[TargetDecision]:
    prompt = _build_prompt(
        question=question,
        world=world,
        disclosure=disclosure,
        allow_anchor=allow_anchor,
        anchor_kinds=anchor_kinds,
        allow_probes=allow_probes,
        language=language,
        verifier_hint=verifier_hint,
    )
    try:
        decision = await llm.generate_structured(
            prompt=prompt,
            schema=TargetDecision,
            tag=tag,
            with_discovery=False,
            media_assets=world.media_assets() or None,
        )
    except Exception:
        return None
    if isinstance(decision, TargetDecision):
        return decision
    try:
        return TargetDecision.model_validate(decision)
    except Exception:
        pass
    # Repli tolerant : un faux LLM de test (ou un provider) peut renvoyer un
    # objet simple portant les memes champs.
    if hasattr(decision, "target_key"):
        try:
            return TargetDecision(
                target_key=str(getattr(decision, "target_key", "") or ""),
                target_kind=str(
                    getattr(decision, "target_kind", TARGET_KIND_LOCAL)
                    or TARGET_KIND_LOCAL
                ),
                anchor_kind=str(getattr(decision, "anchor_kind", "") or ""),
                no_target=bool(getattr(decision, "no_target", False)),
                reason=str(getattr(decision, "reason", "") or ""),
                discovery_request=getattr(decision, "discovery_request", None),
            )
        except Exception:
            return None
    return None


def _build_verification_prompt(
    *,
    question: str,
    entry: Any,
    language: str,
    evidence: Optional[Dict[str, Any]] = None,
    verifier_hint: str = "",
) -> str:
    """Prompt de re-verification generique (metadonnees + preuves declarees).

    L'image eventuelle passe par le canal media (jamais en base64 dans le
    texte). Le prompt ne suppose aucune semantique d'hote.
    """
    if evidence is None:
        description = str(getattr(entry, "description", "") or "")
        scope = str(getattr(entry, "scope", "") or "")
        attributes = _trim_attributes(getattr(entry, "attributes", {}) or {})
        evidence = {
            "description": description[:300],
            "scope": scope,
            "attributes": attributes,
        }
    target_info = evidence
    try:
        from core.prompt_loader import get_prompt_loader

        return get_prompt_loader().load(
            "target_verification.md",
            lang=language,
            question=question,
            target_json=json.dumps(target_info, ensure_ascii=False, indent=1),
            verifier_hint=verifier_hint or "",
        )
    except Exception:
        return (
            _("La cible choisie satisfait-elle REELLEMENT la question ?")
            + f"\n{_('Question')} : {question}"
            + f"\n{_('Cible')} : {json.dumps(target_info, ensure_ascii=False)}"
            + (f"\n{_('Indice precedent')} : {verifier_hint}" if verifier_hint else "")
        )


def _derive_next(
    parsed: TargetVerification,
    *,
    reliability: str,
    zoom_available: bool,
) -> str:
    """Traduit un verdict en suite, sans jamais choisir de cible.

    - `match` / `uncertain` -> `accept` (un `uncertain` sera porte par un
      warning, jamais par un fallback silencieux) ;
    - `mismatch` -> `zoom` si une inspection de l'ancienne candidate est
      possible et si la fiabilite est faible ou qu'une cible interne est
      indiquee ; sinon `replan`.
    """
    explicit = parsed.effective_next()
    verdict = parsed.effective_verdict()
    if verdict in ("match", "uncertain"):
        return "accept"
    if explicit == "replan":
        return "replan"
    if explicit == "zoom" and zoom_available:
        return "zoom"
    if zoom_available and (
        reliability == "low" or str(parsed.prefer_internal or "").strip()
    ):
        return "zoom"
    return "replan"


async def _verify_target(
    *,
    llm: Any,
    question: str,
    entry: Any,
    language: str,
    tag: str,
    disclosure: Optional[DisclosureSpec] = None,
    contract: Optional[TargetContract] = None,
    world: Optional["ObservedWorld"] = None,
    verifier_hint: str = "",
) -> VerificationOutcome:
    """Verifie une cible choisie avec les preuves declarees par l'hote.

    Fail-open controle : une absence de preuve, une panne du verificateur ou
    un verdict `uncertain` n'ecartent jamais la cible ; ils produisent un
    `VerificationOutcome` que l'appelant peut transformer en warning. Seul un
    `mismatch` explicite demande une suite (`zoom` ou `replan`).
    Cout borne : UN appel, jamais de boucle.
    """
    scope = str(getattr(entry, "scope", "") or "")
    local_name = str(getattr(entry, "local_name", "") or "")
    reliability = effective_reliability(disclosure, contract, scope)
    caps = set(_declared_evidence(disclosure))
    description = str(getattr(entry, "description", "") or "").strip()
    attributes = getattr(entry, "attributes", {}) or {}
    has_meta = bool(description or attributes)
    has_image = "image" in caps and world is not None and bool(world.media)

    if not has_meta and not has_image:
        return VerificationOutcome(
            verdict="uncertain",
            confidence="low",
            reason=_("aucune preuve disponible pour verifier la cible"),
            next="accept",
            reliability=reliability,
            verified=False,
            local_name=local_name,
        )

    evidence = build_target_evidence(entry, world, disclosure)
    prompt = _build_verification_prompt(
        question=question,
        entry=entry,
        language=language,
        evidence=evidence,
        verifier_hint=verifier_hint,
    )
    media_assets = world.media_assets() if has_image else None
    try:
        verdict = await llm.generate_structured(
            prompt=prompt,
            schema=TargetVerification,
            tag=f"{tag}_verify",
            with_discovery=False,
            media_assets=media_assets,
        )
    except Exception:
        return VerificationOutcome(
            verdict="uncertain",
            confidence="low",
            reason=_("verificateur indisponible"),
            next="accept",
            reliability=reliability,
            verified=False,
            local_name=local_name,
        )
    if isinstance(verdict, TargetVerification):
        parsed = verdict
    else:
        try:
            parsed = TargetVerification.model_validate(verdict)
        except Exception:
            return VerificationOutcome(
                verdict="uncertain",
                confidence="low",
                reason=_("reponse de verification invalide"),
                next="accept",
                reliability=reliability,
                verified=False,
                local_name=local_name,
            )
    verdict_value = parsed.effective_verdict()
    confidence = str(getattr(parsed, "confidence", "") or "").strip().lower()
    reason = str(getattr(parsed, "reason", "") or "").strip()
    zoom_available = bool(disclosure is not None and disclosure.zoom_probe)
    next_action = _derive_next(
        parsed, reliability=reliability, zoom_available=zoom_available
    )
    if verdict_value == "match":
        return VerificationOutcome(
            verdict="match",
            confidence=confidence or "medium",
            reason=reason,
            next="accept",
            prefer_internal=str(getattr(parsed, "prefer_internal", "") or "").strip(),
            reliability=reliability,
            verified=True,
            local_name=local_name,
        )
    if verdict_value == "uncertain":
        return VerificationOutcome(
            verdict="uncertain",
            confidence=confidence or "low",
            reason=reason or _("preuves insuffisantes pour conclure"),
            next="accept",
            prefer_internal=str(getattr(parsed, "prefer_internal", "") or "").strip(),
            reliability=reliability,
            verified=True,
            local_name=local_name,
        )
    return VerificationOutcome(
        verdict="mismatch",
        confidence=confidence or "medium",
        reason=reason or _("la cible ne correspond pas a la question"),
        next=next_action,
        prefer_internal=str(getattr(parsed, "prefer_internal", "") or "").strip(),
        reliability=reliability,
        verified=True,
        local_name=local_name,
    )


def extract_media_assets(
    tools_mgr: Any,
    tool_name: str,
    payload: Any,
) -> Tuple[Any, Optional[List[Any]]]:
    """Decode les charges declarees `returns` d'un payload hote.

    Generique : suit la declaration du manifeste, jamais un nom de champ code
    en dur. Les octets passent par le canal media, pas par le prompt.
    """
    media_assets: Optional[List[Any]] = None
    try:
        returns = (
            tools_mgr.get_tool_returns(tool_name)
            if hasattr(tools_mgr, "get_tool_returns")
            else []
        )
    except Exception:
        returns = []
    if not returns or not isinstance(payload, dict):
        return payload, media_assets
    try:
        from core.discovery.data_asset import extract_typed_payloads

        payloads = extract_typed_payloads(
            payload, returns, target_prefix=f"perceive_{tool_name}"
        )
        if not payloads or not hasattr(tools_mgr, "register_typed_assets"):
            return payload, media_assets
        uris = tools_mgr.register_typed_assets(payloads)
        media_assets = [asset for asset in payloads if asset.target_id in uris]
        if not media_assets:
            return payload, media_assets
        cleaned = dict(payload)
        for asset in media_assets:
            field = ""
            try:
                field = (
                    asset.asset_meta.custom_attributes or {}
                ).get("declared_field", "")
            except Exception:
                field = ""
            parts = field.split(".") if field else []
            node = cleaned
            for part in parts[:-1]:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    node = None
                    break
            if isinstance(node, dict) and parts and parts[-1] in node:
                node[parts[-1]] = (
                    f"[Image : {uris[asset.target_id]} "
                    f"({len(asset.raw_bytes)} octets)]"
                )
        return cleaned, media_assets
    except Exception:
        return payload, None


async def resolve_target(
    *,
    llm: Any,
    question: str,
    world: ObservedWorld,
    contract: Optional[TargetContract] = None,
    registry: Optional[TargetRegistry] = None,
    tools_mgr: Any = None,
    disclosure: Optional[DisclosureSpec] = None,
    allow_anchor: bool = True,
    anchor_kinds: Optional[List[str]] = None,
    language: str = "fr",
    max_rounds: int = MAX_TARGETING_ROUNDS,
    tag: str = "target_selection",
    deterministic: bool = False,
    verify: bool = False,
) -> TargetingResult:
    """Choisit une cible visible, avec inspections bornees.

    Ne leve pas : toute impossibilite est un `TargetingResult` avec un code
    ferme et un message honnete. Aucune cible n'est devinee.
    """
    if world is None:
        world = ObservedWorld()
    if disclosure is None and contract is not None:
        disclosure = contract.disclosure
    if llm is None:
        return TargetingResult(
            kind="none",
            world=world,
            error_code="TARGET_NOT_FOUND",
            error_message=_("Aucun LLM disponible pour choisir une cible."),
        )

    budget = max(0, int(max_rounds))
    decision: Optional[TargetDecision] = None
    used: set = set()
    # Doutes non bloquants et trace de verification, portes jusqu'au resultat.
    warnings: List[str] = []
    verification_trace: Optional[Dict[str, Any]] = None
    # Tous les verdicts de la boucle (mismatch initial inclus). `warnings`
    # reste global ; l'observabilite les rattache au dernier verdict.
    verifications: List[Dict[str, Any]] = []
    verifier_hint = ""

    # P2-10 : une correspondance exacte ET unique avec la question (ou un
    # attribut declare) est resolue sans appel de selection. Conserve le
    # determinisme, economise un appel LLM, et n'introduit aucune guess :
    # des qu'il y a ambiguite ou similarite, le selecteur garde la main.
    preset = exact_candidate(world, question) if deterministic else None
    if preset is not None and registry is not None:
        preset_entry = world.find_entry(registry, preset.key)
        if preset_entry is not None:
            return TargetingResult(
                kind=TARGET_KIND_LOCAL,
                entry=preset_entry,
                reason=_(
                    "Correspondance exacte unique avec « {q} »."
                ).format(q=question[:120]),
                world=world,
                strategy="deterministic",
                verifications=verifications,
            )

    for _round in range(budget + 1):
        allow_probes = budget > 0
        decision = await _ask_selector(
            llm=llm,
            question=question,
            world=world,
            disclosure=disclosure,
            allow_anchor=allow_anchor,
            anchor_kinds=anchor_kinds,
            allow_probes=allow_probes,
            language=language,
            tag=tag,
            verifier_hint=verifier_hint,
        )
        if decision is None:
            return TargetingResult(
                kind="none",
                world=world,
                error_code="TARGET_NOT_FOUND",
                error_message=_("Selection de cible impossible : reponse invalide."),
                verifications=verifications,
            )

        request = _probe_request(decision, disclosure) if allow_probes else None
        if request is None:
            if verify:
                _key = (decision.target_key or "").strip().strip("\"'")
                _kind = (decision.target_kind or TARGET_KIND_LOCAL).strip().lower()
                _entry = (
                    world.find_entry(registry, _key)
                    if _kind == TARGET_KIND_LOCAL and _key and registry is not None
                    else None
                )
                if _entry is not None:
                    _outcome = await _verify_target(
                        llm=llm,
                        question=question,
                        entry=_entry,
                        language=language,
                        tag=tag,
                        disclosure=disclosure,
                        contract=contract,
                        world=world,
                        verifier_hint=verifier_hint,
                    )
                    verification_trace = _outcome.as_dict()
                    verifications.append(verification_trace)
                    _label = str(
                        getattr(_entry, "description", "") or _key
                    )[:120]
                    if _outcome.verdict == "uncertain":
                        # Fail-open controle : on accepte, mais le doute doit
                        # atteindre le planner et la convergence.
                        warnings.append(
                            _(
                                "Cible '{label}' acceptee sous reserve : {why}"
                            ).format(
                                label=_label,
                                why=_outcome.reason
                                or _("preuves insuffisantes"),
                            )
                        )
                        break
                    if _outcome.verdict == "mismatch":
                        _reason = _outcome.reason or _(
                            "nature incompatible avec la question"
                        )
                        _zoom_id = (
                            disclosure.zoom_probe
                            if disclosure is not None
                            else None
                        )
                        if (
                            _outcome.next == "zoom"
                            and budget > 0
                            and _zoom_id
                        ):
                            # Zoom implicite sur l'ANCIENNE candidate : on ne
                            # relit pas tout l'ecran, et le selecteur garde le
                            # choix apres inspection.
                            budget -= 1
                            _zoom_probe = (
                                disclosure.probe(_zoom_id)
                                if disclosure is not None
                                else None
                            )
                            if _zoom_probe is not None:
                                _record, _error = await _run_probe(
                                    probe=_zoom_probe,
                                    subject=_key,
                                    world=world,
                                    registry=registry,
                                    contract=contract,
                                    disclosure=disclosure,
                                    tools_mgr=tools_mgr,
                                    question=question,
                                )
                                if _record is not None:
                                    world.probes.append(_record)
                                if _error:
                                    world.probes.append(
                                        ProbeRecord(
                                            probe_id=_zoom_probe.id,
                                            kind=_zoom_probe.kind,
                                            subject=_key,
                                            ok=False,
                                            message=_error,
                                        )
                                    )
                                # Politique de zoom (option B) : si la sonde a
                                # reussi ET produit des candidates, le nouveau
                                # monde fait foi et l'ancienne candidate n'est
                                # PAS re-ajoutee. Si elle a echoue ou n'a rien
                                # produit, `_run_probe` a restaure l'ancien
                                # monde : les references restees valides
                                # demeurent visibles.
                            _hint = _outcome.reason or _reason
                            _prefer = str(
                                getattr(_outcome, "prefer_internal", "") or ""
                            ).strip()
                            if _prefer:
                                _hint = (
                                    f"{_hint} ({_('cible interne suggeree')} : "
                                    f"{_prefer})"
                                )
                            verifier_hint = _hint
                            continue
                        if budget > 0:
                            # Creuser sans zoom declare : la cible rejetee
                            # devient une trace visible pour le selecteur.
                            budget -= 1
                            world.probes.append(
                                ProbeRecord(
                                    probe_id="target_verification",
                                    kind="verification",
                                    subject=_key,
                                    ok=False,
                                    message=_(
                                        "Cible '{label}' ecartee : {why}"
                                    ).format(label=_label, why=_reason),
                                )
                            )
                            verifier_hint = _outcome.reason or _reason
                            continue
                        return TargetingResult(
                            kind="none",
                            world=world,
                            error_code="TARGET_MISMATCH",
                            error_message=_(
                                "Cible '{label}' ecartee par verification : "
                                "{why}. Aucune cible promue (approfondir ou "
                                "replanifier)."
                            ).format(label=_label, why=_reason),
                            reason=_reason,
                            warnings=warnings,
                            verification=verification_trace,
                            verifications=verifications,
                        )
            break

        probe, subject = request
        marker = (probe.id, subject)
        if marker in used:
            return TargetingResult(
                kind="none",
                world=world,
                error_code="TARGET_PROBE_LOOP",
                error_message=_(
                    "La sonde '{id}' a deja ete demandee pour ce sujet : "
                    "inspection repetee refusee."
                ).format(id=probe.id),
                verifications=verifications,
            )
        used.add(marker)
        budget -= 1

        record, error = await _run_probe(
            probe=probe,
            subject=subject,
            world=world,
            registry=registry,
            contract=contract,
            disclosure=disclosure,
            tools_mgr=tools_mgr,
            question=question,
        )
        if record is not None:
            world.probes.append(record)
        if error:
            # Une inspection impossible est un echec honnete, pas une cible
            # devinee. Le selecteur peut retenter tant que le budget le permet.
            world.probes.append(ProbeRecord(
                probe_id=probe.id,
                kind=probe.kind,
                subject=subject,
                ok=False,
                message=error,
            ))

    if decision is None:
        return TargetingResult(
            kind="none",
            world=world,
            error_code="TARGET_NOT_FOUND",
            error_message=_("Selection de cible impossible."),
            warnings=warnings,
            verification=verification_trace,
            verifications=verifications,
        )

    key = (decision.target_key or "").strip().strip("\"'")
    kind = (decision.target_kind or TARGET_KIND_LOCAL).strip().lower()
    anchor_kind = (decision.anchor_kind or "").strip()
    reason = (decision.reason or "").strip()

    if decision.no_target or not key:
        if getattr(decision, "discovery_request", None) is not None and budget <= 0:
            message = _(
                "Budget d'inspection epuise sans cible sure pour « {q} » : "
                "aucune devinette."
            ).format(q=question[:120])
        else:
            message = _(
                "Aucune cible sure pour « {q} » : aucune devinette."
            ).format(q=question[:120])
        return TargetingResult(
            kind="none",
            reason=reason,
            world=world,
            error_code="TARGET_ANCHOR_UNSUPPORTED",
            error_message=message,
            warnings=warnings,
            verification=verification_trace,
            verifications=verifications,
        )

    if len(key) > MAX_RAW_ANCHOR_CHARS:
        return TargetingResult(
            kind="none",
            reason=reason,
            world=world,
            error_code="TARGET_SCHEMA_INVALID",
            error_message=_(
                "Valeur de cible brute trop longue ({size} caracteres, "
                "maximum {limit})."
            ).format(size=len(key), limit=MAX_RAW_ANCHOR_CHARS),
            verifications=verifications,
        )

    if kind == TARGET_KIND_ANCHOR:
        declared = (
            tuple(anchor_kinds)
            if anchor_kinds is not None
            else (disclosure.anchor_kinds() if disclosure is not None else ())
        )
        if not allow_anchor or not declared:
            return TargetingResult(
                kind="none",
                reason=reason,
                world=world,
                error_code="TARGET_ANCHOR_UNSUPPORTED",
                error_message=_(
                    "Une ancre brute a ete choisie mais l'hote n'en declare "
                    "aucune pour cette etape."
                ),
                verifications=verifications,
            )
        if anchor_kind not in declared:
            return TargetingResult(
                kind="none",
                reason=reason,
                world=world,
                error_code="TARGET_ANCHOR_UNSUPPORTED",
                error_message=_(
                    "Le type d'ancre '{kind}' n'est pas declare par l'hote "
                    "(declare : {known})."
                ).format(
                    kind=anchor_kind or "?",
                    known=", ".join(declared),
                ),
                verifications=verifications,
            )
        return TargetingResult(
            kind=TARGET_KIND_ANCHOR,
            anchor_kind=anchor_kind,
            anchor_value=key,
            reason=reason,
            world=world,
            warnings=warnings,
            verification=verification_trace,
            verifications=verifications,
        )

    entry = world.find_entry(registry, key)
    if entry is None:
        return TargetingResult(
            kind="none",
            reason=reason,
            world=world,
            error_code="TARGET_NOT_FOUND",
            error_message=_("Cle locale inconnue : {key!r}").format(key=key[:120]),
            verifications=verifications,
        )
    return TargetingResult(
        kind=TARGET_KIND_LOCAL,
        entry=entry,
        reason=reason,
        world=world,
        warnings=warnings,
        verification=verification_trace,
        verifications=verifications,
    )

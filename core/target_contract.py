"""Contrat `target_contract` declare par un outil hote.

Le parseur est pur : il ne journalise pas et ne leve pas pour un manifeste
invalide. Il renvoie un contrat valide ou des diagnostics, afin que l'outil
reste utilisable mais inerte quand le contrat est invalide.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.targets import TargetError


RESERVED_CONSUMER_PARAMS = frozenset({"source_data", "source", "sources"})
SUPPORTED_VERSION = 1
MODES = frozenset({"single", "candidates"})
FIELD_ROLES = frozenset(
    {"ref", "description", "scope", "correlation_id", "generation"}
)
# Vocabulaire du cerveau, jamais celui d'un hote :
# - `local` designe une cle du registre de cibles (nom local) ;
# - tout autre `kind` de sujet/ancre est declare par l'hote et transmis tel
#   quel. Aucun nom de type de cible (cell, grid, file, key, joint...) n'est
#   code en dur ici.
LOCAL_SUBJECT_KIND = "local"

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


@dataclass(frozen=True)
class ConsumerSpec:
    param: str
    scope: Optional[str] = None
    required: bool = True
    raw_allow: bool = False
    # Type JSON declare par le manifeste pour ce parametre (ex: "string").
    # `None` = non declare : la reference brute est transmise telle quelle.
    wire_type: Optional[str] = None


@dataclass(frozen=True)
class ProducerSpec:
    path: str
    mode: str
    scope: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)
    # Champs libres exposes au selecteur (role, rect, cells, interactive...).
    # Declaration explicite : `fields.attributes: {nom: chemin}`.
    attributes: Dict[str, str] = field(default_factory=dict)
    usable_as: Tuple[str, ...] = ()

    @property
    def has_static_scope(self) -> bool:
        return self.scope is not None


@dataclass(frozen=True)
class ViewSpec:
    """Vue nommee declaree par l'hote (`disclosure.views[]`).

    `name` et `path` sont libres : grille, liste de fichiers, symboles,
    etat de capteurs... Le cerveau affiche la vue au selecteur sans en
    connaitre la semantique.
    """

    name: str
    path: str
    description: str = ""


@dataclass(frozen=True)
class AnchorSpec:
    """Ancre brute declaree par l'hote (`disclosure.anchors[]`).

    `kind` est un vocabulaire HOTE (cell, file, key, joint, symbol...).
    `param` est le parametre consommateur qui recoit la valeur brute.
    """

    kind: str
    param: str
    description: str = ""


@dataclass(frozen=True)
class ProbeSubjectSpec:
    """Sujet d'une sonde de ciblage (jamais un nom d'outil hote).

    `kind == "local"` : sujet = nom local du registre. Tout autre `kind`
    renvoie a une ancre declaree par l'hote (`disclosure.anchors`).
    """

    kind: str
    param: str
    scheme: Optional[str] = None


@dataclass(frozen=True)
class ProbeSpec:
    """Recette d'inspection declaree par l'hote dans `disclosure.probes`."""

    id: str
    kind: str
    description: str = ""
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    hint_param: Optional[str] = None
    subject: Optional[ProbeSubjectSpec] = None
    produces: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DisclosureSpec:
    """Capacite d'inspection progressive declaree par l'hote."""

    version: int
    views: Tuple[ViewSpec, ...] = ()
    anchors: Tuple[AnchorSpec, ...] = ()
    probes: Tuple[ProbeSpec, ...] = ()

    def probe(self, probe_id: str) -> Optional[ProbeSpec]:
        for probe in self.probes:
            if probe.id == probe_id:
                return probe
        return None

    def view(self, name: str) -> Optional[ViewSpec]:
        for view in self.views:
            if view.name == name:
                return view
        return None

    def anchor(self, kind: str) -> Optional[AnchorSpec]:
        for anchor in self.anchors:
            if anchor.kind == kind:
                return anchor
        return None

    def anchor_kinds(self) -> Tuple[str, ...]:
        return tuple(anchor.kind for anchor in self.anchors)


@dataclass(frozen=True)
class TargetContract:
    version: int
    consumers: Tuple[ConsumerSpec, ...] = ()
    producers: Tuple[ProducerSpec, ...] = ()
    disclosure: Optional[DisclosureSpec] = None
    tool_name: str = ""

    @property
    def is_effective(self) -> bool:
        return bool(self.consumers or self.producers)

    def consumer_param_names(self) -> set:
        return {consumer.param for consumer in self.consumers}

    def producer_specs(self) -> Tuple[ProducerSpec, ...]:
        return self.producers

    def consumer_for(self, param: str) -> Optional[ConsumerSpec]:
        for consumer in self.consumers:
            if consumer.param == param:
                return consumer
        return None


@dataclass
class TargetContractResult:
    contract: Optional[TargetContract] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def consumer_param_names(contract: TargetContract) -> set:
    return contract.consumer_param_names()


def declared_consumer_params(tools_mgr: Any, tool_name: str) -> set:
    """Parametres consommateurs declares pour un outil (vide si absent)."""
    try:
        contract = tools_mgr.get_target_contract(tool_name)
    except Exception:
        return set()
    return contract.consumer_param_names() if contract else set()


def producer_specs(contract: TargetContract) -> Tuple[ProducerSpec, ...]:
    return contract.producer_specs()


_REFERENCE_TOKEN_RE = re.compile(r"(\$@_|@\$_)[A-Za-z0-9_-]+")


def _wire_value(value: Any, wire_type: Optional[str]) -> Any:
    """Adapte une reference brute au type declare par le manifeste.

    Certains hotes declarent le parametre consommateur en `type: "string"`
    tout en renvoyant une reference objet (`{kind, id}`) : envoyer l'objet
    contredit leur propre schema (gate `TARGET_REQUIRED`, mission 008bcdba).
    La forme `"<kind>:<id>"` est acceptee par `s_parseTargetRef`
    (rpahosttools.cpp:290-298). Tout le reste est transmis inchange.
    """
    if wire_type != "string" or isinstance(value, str):
        return value
    if isinstance(value, dict):
        kind = value.get("kind")
        ident = value.get("id")
        if (
            isinstance(kind, str)
            and kind.strip()
            and isinstance(ident, (str, int))
            and not isinstance(ident, bool)
            and str(ident).strip()
        ):
            return f"{kind.strip()}:{str(ident).strip()}"
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        try:
            return json.dumps(list(value), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    return value


def resolve_consumer_arguments(
    registry: Any,
    contract: Optional[TargetContract],
    arguments: Any,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """Resout les noms locaux des consommateurs en references hote brutes.

    Regles S1 : strict par defaut (`raw: deny`), tout ou rien, aucune
    substitution partielle. Retourne `(arguments_resolus, arguments_affiches,
    erreur)`. En cas d'erreur, `arguments_resolus` est l'original et
    `arguments_affiches` vaut None : l'appelant ne doit pas dispatcher.
    """
    if not isinstance(arguments, dict):
        return arguments, None, None
    if contract is None or not contract.consumers:
        return arguments, None, None

    resolved = dict(arguments)
    display: Optional[Dict[str, Any]] = None
    for consumer in contract.consumers:
        param = consumer.param
        if param not in resolved:
            continue
        if consumer.raw_allow:
            # `raw: allow` = le parametre accepte AUSSI un litteral hote
            # (ex. opt-out global `none`). Un nom local CONNU reste resolu ;
            # un litteral inconnu est transmis tel quel. Aucune valeur
            # concrete n'est codee en dur ici : l'hote reste l'autorite sur
            # son propre vocabulaire.
            if (
                registry is None
                or not isinstance(resolved[param], str)
                or registry.get(resolved[param]) is None
            ):
                continue
        if display is None:
            display = dict(arguments)
        value = resolved[param]
        if not isinstance(value, str) or not value.strip():
            return arguments, None, {
                "code": "TARGET_SCHEMA_INVALID",
                "message": f"consumer '{param}' expects a local target name",
            }
        if _REFERENCE_TOKEN_RE.search(value):
            stripped = value.strip()
            if _REFERENCE_TOKEN_RE.fullmatch(stripped):
                return arguments, None, {
                    "code": "TARGET_NOT_FOUND",
                    "message": (
                        f"unresolved target reference {stripped!r} for consumer "
                        f"'{param}': no local target binding (producer must publish "
                        "output_target_name)"
                    ),
                }
            return arguments, None, {
                "code": "TARGET_NOT_FOUND",
                "message": (
                    f"consumer '{param}' mixes text and target reference: {value!r}"
                ),
            }
        if registry is None:
            return arguments, None, {
                "code": "TARGET_NOT_FOUND",
                "message": f"no target registry for consumer '{param}'",
            }
        try:
            ref = registry.resolve(value, consumer.scope)
        except TargetError as exc:
            return arguments, None, {"code": exc.code, "message": exc.message}
        resolved[param] = _wire_value(ref.value, consumer.wire_type)
    return resolved, display, None


def lint_target_payload(tools_payload: Any) -> List[str]:
    """Warnings de lint d'un payload d'outils (sans modifier ni lever)."""

    warnings: List[str] = []
    if not isinstance(tools_payload, (list, tuple)):
        return warnings
    for index, tool in enumerate(tools_payload):
        if not isinstance(tool, dict):
            continue
        if tool.get("targets") and not tool.get("target_contract"):
            name = tool.get("name") or f"index {index}"
            warnings.append(
                f"tool '{name}': 'targets' declared without 'target_contract'; "
                "targets stay inert"
            )
    return warnings


def parse_target_contract(
    raw: Any,
    *,
    tool_name: str = "",
    parameters_schema: Optional[Dict[str, Any]] = None,
) -> TargetContractResult:
    prefix = f"tool '{tool_name}': " if tool_name else ""
    errors: List[str] = []
    warnings: List[str] = []

    def error(message: str) -> None:
        errors.append(prefix + message)

    def warning(message: str) -> None:
        warnings.append(prefix + message)

    if not isinstance(raw, dict):
        error("target_contract must be an object")
        return TargetContractResult(errors=errors, warnings=warnings)

    unknown_keys = set(raw) - {"version", "consumes", "produces", "disclosure"}
    for key in sorted(unknown_keys):
        warning(f"unknown target_contract key: {key}")

    version = raw.get("version")
    if version is None:
        error("version is required")
    elif isinstance(version, bool) or not isinstance(version, int):
        error("version must be an integer")
    elif version != SUPPORTED_VERSION:
        error(f"unsupported version: {version}")

    consumers: List[ConsumerSpec] = []
    consumers_raw = raw.get("consumes", [])
    if consumers_raw is None:
        consumers_raw = []
    if not isinstance(consumers_raw, list):
        error("consumes must be a list")
    else:
        seen_params: set = set()
        for index, item in enumerate(consumers_raw):
            spec = _parse_consumer(
                item,
                index=index,
                parameters_schema=parameters_schema,
                seen_params=seen_params,
                error=error,
            )
            if spec is not None:
                consumers.append(spec)

    producers: List[ProducerSpec] = []
    producers_raw = raw.get("produces", [])
    if producers_raw is None:
        producers_raw = []
    if not isinstance(producers_raw, list):
        error("produces must be a list")
    else:
        for index, item in enumerate(producers_raw):
            spec = _parse_producer(
                item,
                index=index,
                error=error,
                warning=warning,
            )
            if spec is not None:
                producers.append(spec)

    if version == SUPPORTED_VERSION and not consumers and not producers:
        warning("contract has no consumers or producers; it stays inert")

    disclosure = None
    disclosure_raw = raw.get("disclosure")
    if disclosure_raw is not None:
        disclosure = _parse_disclosure(
            disclosure_raw,
            error=error,
            warning=warning,
        )

    if errors:
        return TargetContractResult(errors=errors, warnings=warnings)

    contract = TargetContract(
        version=SUPPORTED_VERSION,
        consumers=tuple(consumers),
        producers=tuple(producers),
        disclosure=disclosure,
        tool_name=tool_name,
    )
    return TargetContractResult(contract=contract, warnings=warnings)


def _parse_disclosure(raw: Any, *, error, warning) -> Optional[DisclosureSpec]:
    """Parse le bloc optionnel `disclosure` (version 1).

    Le cerveau ne connait aucun nom d'outil/champ hote : tout est declare ici.
    Un bloc invalide est un diagnostic, jamais une exception.

    Forme generique :
      - `views`: vues nommees que le selecteur peut lire ;
      - `anchors`: types d'ancre brute que l'hote accepte ;
      - `probes`: inspections bornees a executer via les outils declares.
    """
    if not isinstance(raw, dict):
        error("disclosure must be an object")
        return None

    unknown = set(raw) - {"version", "views", "anchors", "probes"}
    for key in sorted(unknown):
        warning(f"disclosure has unknown key: {key}")

    version = raw.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        error("disclosure.version must be 1")
        return None

    views = _parse_views(raw.get("views"), error=error, warning=warning)
    if views is None:
        return None

    anchors = _parse_anchors(raw.get("anchors"), error=error, warning=warning)
    if anchors is None:
        return None

    probes_raw = raw.get("probes", [])
    if probes_raw is None:
        probes_raw = []
    if not isinstance(probes_raw, list):
        error("disclosure.probes must be a list")
        return None

    probes: List[ProbeSpec] = []
    seen_ids: set = set()
    for index, item in enumerate(probes_raw):
        probe = _parse_probe(
            item, index=index, error=error, warning=warning, seen_ids=seen_ids
        )
        if probe is not None:
            probes.append(probe)

    if not probes:
        warning("disclosure declares no probe; inspections stay unavailable")

    return DisclosureSpec(
        version=1,
        views=tuple(views),
        anchors=tuple(anchors),
        probes=tuple(probes),
    )


def _parse_views(raw: Any, *, error, warning) -> Optional[List[ViewSpec]]:
    if raw is None:
        return []
    views: List[ViewSpec] = []
    if isinstance(raw, dict):
        items = []
        for name, path in raw.items():
            items.append({"name": name, "path": path})
    elif isinstance(raw, list):
        items = raw
    else:
        error("disclosure.views must be an object or a list")
        return None

    seen: set = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            error(f"disclosure.views[{index}] must be an object")
            return None
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            error(f"disclosure.views[{index}].name is required")
            return None
        name = name.strip()
        if name in seen:
            error(f"disclosure.views[{index}].name is duplicated: {name!r}")
            return None
        seen.add(name)
        path = item.get("path")
        if not isinstance(path, str) or not _PATH_RE.fullmatch(path.strip()):
            error(f"disclosure.views[{index}].path is invalid: {path!r}")
            return None
        description = item.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            error(f"disclosure.views[{index}].description must be a string")
            return None
        views.append(
            ViewSpec(name=name, path=path.strip(), description=description)
        )
    return views


def _parse_anchors(raw: Any, *, error, warning) -> Optional[List[AnchorSpec]]:
    if raw is None:
        return []
    anchors: List[AnchorSpec] = []
    if isinstance(raw, dict):
        items = []
        for kind, param in raw.items():
            items.append({"kind": kind, "param": param})
    elif isinstance(raw, list):
        items = raw
    else:
        error("disclosure.anchors must be an object or a list")
        return None

    seen: set = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            error(f"disclosure.anchors[{index}] must be an object")
            return None
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            error(f"disclosure.anchors[{index}].kind is required")
            return None
        kind = kind.strip()
        if kind in seen:
            error(f"disclosure.anchors[{index}].kind is duplicated: {kind!r}")
            return None
        seen.add(kind)
        param = item.get("param")
        if not isinstance(param, str) or not param.strip():
            error(f"disclosure.anchors[{index}].param is required")
            return None
        description = item.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            error(f"disclosure.anchors[{index}].description must be a string")
            return None
        anchors.append(
            AnchorSpec(
                kind=kind,
                param=param.strip(),
                description=description,
            )
        )
    return anchors


def _parse_probe(item: Any, *, index: int, error, warning, seen_ids: set) -> Optional[ProbeSpec]:
    if not isinstance(item, dict):
        error(f"disclosure.probes[{index}] must be an object")
        return None

    unknown = set(item) - {
        "id", "kind", "description", "tool", "args", "hint_param",
        "subject", "produces",
    }
    for key in sorted(unknown):
        warning(f"disclosure.probes[{index}] has unknown key: {key}")

    probe_id = item.get("id")
    if not isinstance(probe_id, str) or not probe_id.strip():
        error(f"disclosure.probes[{index}].id is required")
        return None
    probe_id = probe_id.strip()
    if probe_id in seen_ids:
        error(f"disclosure.probes[{index}].id is duplicated: {probe_id!r}")
        return None
    seen_ids.add(probe_id)

    kind = item.get("kind", "inspect")
    if not isinstance(kind, str) or not kind.strip():
        error(f"disclosure.probes[{index}].kind must be a non-empty string")
        return None
    # `kind` est un vocabulaire HOTE (widen, zoom, read_file, ...) : le cerveau
    # ne lui donne aucune semantique et ne le filtre pas.
    kind = kind.strip()

    description = item.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        error(f"disclosure.probes[{index}].description must be a string")
        return None

    tool = item.get("tool")
    if tool is not None:
        if not isinstance(tool, str) or not tool.strip():
            error(f"disclosure.probes[{index}].tool is invalid")
            return None
        tool = tool.strip()

    args = item.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        error(f"disclosure.probes[{index}].args must be an object")
        return None

    hint_param = item.get("hint_param")
    if hint_param is not None:
        if not isinstance(hint_param, str) or not hint_param.strip():
            error(f"disclosure.probes[{index}].hint_param is invalid")
            return None
        hint_param = hint_param.strip()

    subject = None
    subject_raw = item.get("subject")
    if subject_raw is not None:
        if not isinstance(subject_raw, dict):
            error(f"disclosure.probes[{index}].subject must be an object")
            return None
        subject_kind = subject_raw.get("kind")
        if not isinstance(subject_kind, str) or not subject_kind.strip():
            error(f"disclosure.probes[{index}].subject.kind is required")
            return None
        subject_kind = subject_kind.strip()
        subject_param = subject_raw.get("param")
        if not isinstance(subject_param, str) or not subject_param.strip():
            error(f"disclosure.probes[{index}].subject.param is required")
            return None
        scheme = subject_raw.get("scheme")
        if scheme is not None and (
            not isinstance(scheme, str) or not scheme.strip()
        ):
            error(f"disclosure.probes[{index}].subject.scheme is invalid")
            return None
        subject = ProbeSubjectSpec(
            kind=subject_kind,
            param=subject_param.strip(),
            scheme=scheme.strip() if isinstance(scheme, str) else None,
        )

    produces_raw = item.get("produces", [])
    if produces_raw is None:
        produces_raw = []
    if not isinstance(produces_raw, list):
        error(f"disclosure.probes[{index}].produces must be a list")
        return None
    produces: List[str] = []
    for value in produces_raw:
        if not isinstance(value, str) or not value.strip():
            error(f"disclosure.probes[{index}].produces has an invalid value")
            return None
        produces.append(value.strip())

    return ProbeSpec(
        id=probe_id,
        kind=kind,
        description=description,
        tool=tool,
        args=dict(args),
        hint_param=hint_param,
        subject=subject,
        produces=tuple(produces),
    )


def _valid_scope(value: Any) -> bool:
    return isinstance(value, str) and bool(_SCOPE_RE.fullmatch(value.strip()))


def _parse_consumer(
    item: Any,
    *,
    index: int,
    parameters_schema: Optional[Dict[str, Any]],
    seen_params: set,
    error,
) -> Optional[ConsumerSpec]:
    if not isinstance(item, dict):
        error(f"consumes[{index}] must be an object")
        return None

    param = item.get("param")
    if not isinstance(param, str) or not param.strip():
        error(f"consumes[{index}].param is required")
        return None
    param = param.strip()
    if param in RESERVED_CONSUMER_PARAMS:
        error(f"consumer parameter '{param}' is reserved")
        return None
    if param in seen_params:
        error(f"duplicate consumer parameter '{param}'")
        return None
    seen_params.add(param)

    scope = item.get("scope")
    if scope is not None:
        if not _valid_scope(scope):
            error(f"consumes[{index}].scope is invalid: {scope!r}")
        else:
            scope = scope.strip()

    wire_type: Optional[str] = None

    required = item.get("required", True)
    if not isinstance(required, bool):
        error(f"consumes[{index}].required must be a boolean")
        required = True

    raw_policy = item.get("raw", "deny")
    if raw_policy is None:
        raw_policy = "deny"
    if not isinstance(raw_policy, str) or raw_policy.strip().lower() not in (
        "allow",
        "deny",
    ):
        error(f"consumes[{index}].raw must be 'allow' or 'deny'")
        raw_allow = False
    else:
        raw_allow = raw_policy.strip().lower() == "allow"

    if isinstance(parameters_schema, dict):
        properties = parameters_schema.get("properties")
        if isinstance(properties, dict):
            if param not in properties:
                error(
                    f"consumer parameter '{param}' is absent from the tool schema"
                )
            else:
                spec = properties.get(param)
                if isinstance(spec, dict):
                    enum = spec.get("enum")
                    if isinstance(enum, (list, tuple)) and enum:
                        error(
                            f"consumer parameter '{param}' must not declare enum"
                        )
                    declared_type = spec.get("type")
                    if isinstance(declared_type, str) and declared_type.strip():
                        wire_type = declared_type.strip()

    return ConsumerSpec(
        param=param,
        scope=scope,
        required=required,
        raw_allow=raw_allow,
        wire_type=wire_type,
    )


def _parse_producer(
    item: Any,
    *,
    index: int,
    error,
    warning,
) -> Optional[ProducerSpec]:
    if not isinstance(item, dict):
        error(f"produces[{index}] must be an object")
        return None

    path = item.get("path")
    if not isinstance(path, str) or not path.strip():
        error(f"produces[{index}].path is required")
        return None
    path = path.strip()
    if not _PATH_RE.fullmatch(path):
        error(f"produces[{index}].path is invalid: {path!r}")
        return None

    mode = item.get("mode")
    if mode not in MODES:
        error(f"produces[{index}].mode must be one of {sorted(MODES)}")
        return None

    scope = item.get("scope")
    if scope is not None:
        if not _valid_scope(scope):
            error(f"produces[{index}].scope is invalid: {scope!r}")
            return None
        scope = scope.strip()

    fields = item.get("fields")
    if not isinstance(fields, dict):
        error(f"produces[{index}].fields must be an object")
        return None
    if not isinstance(fields.get("ref"), str) or not fields.get("ref").strip():
        error(f"produces[{index}].fields.ref is required")
        return None

    dynamic_scope = fields.get("scope")
    if dynamic_scope is not None:
        if not isinstance(dynamic_scope, str) or not dynamic_scope.strip():
            error(f"produces[{index}].fields.scope is invalid")
            return None
        dynamic_scope = dynamic_scope.strip()
    if scope is not None and dynamic_scope is not None:
        error(f"produces[{index}]: scope XOR fields.scope")
        return None
    if scope is None and dynamic_scope is None:
        error(f"produces[{index}]: scope or fields.scope is required")
        return None

    cleaned_fields: Dict[str, str] = {}
    cleaned_attributes: Dict[str, str] = {}
    for role, field_name in fields.items():
        if role == "attributes":
            # Champs libres exposes au selecteur : {nom: chemin.de.lecture}.
            if not isinstance(field_name, dict):
                error(f"produces[{index}].fields.attributes must be an object")
                continue
            for attr_name, attr_path in field_name.items():
                if not isinstance(attr_name, str) or not attr_name.strip():
                    error(f"produces[{index}].fields.attributes has an invalid name")
                    continue
                if (
                    not isinstance(attr_path, str)
                    or not _PATH_RE.fullmatch(attr_path.strip())
                ):
                    error(
                        f"produces[{index}].fields.attributes.{attr_name} "
                        f"is invalid: {attr_path!r}"
                    )
                    continue
                cleaned_attributes[attr_name.strip()] = attr_path.strip()
            continue
        if role not in FIELD_ROLES:
            warning(f"produces[{index}].fields has unknown role: {role}")
            continue
        if not isinstance(field_name, str) or not field_name.strip():
            error(f"produces[{index}].fields.{role} is invalid")
            continue
        cleaned_fields[role] = field_name.strip()

    usable_raw = item.get("usable_as")
    if usable_raw is None:
        usable: Tuple[str, ...] = (scope,) if scope is not None else ()
    else:
        if not isinstance(usable_raw, list):
            error(f"produces[{index}].usable_as must be a list")
            return None
        cleaned_usable: List[str] = []
        for value in usable_raw:
            if not _valid_scope(value):
                error(f"produces[{index}].usable_as has invalid scope: {value!r}")
                return None
            normalized = value.strip()
            if normalized not in cleaned_usable:
                cleaned_usable.append(normalized)
        usable = tuple(cleaned_usable)
        if scope is not None and scope not in usable:
            warning(
                f"produces[{index}].usable_as does not include its own scope "
                f"'{scope}'"
            )

    return ProducerSpec(
        path=path,
        mode=mode,
        scope=scope,
        fields=cleaned_fields,
        attributes=cleaned_attributes,
        usable_as=usable,
    )

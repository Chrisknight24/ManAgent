"""
plan_models.py
==============
Le contrat de données strict pour l'architecture 'Tout est Plan'.
Mis à jour avec la méthode Stringified JSON pour les arguments d'outils,
la logique conditionnelle par registre et la prévention des appels vides.
Version avec héritage BaseDiscoverySchema pour la Progressive Disclosure.
Ajout : champ is_crucial et validation stricte du nommage des variables.
"""

import json
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema
try:
    from pydantic import model_validator
except ImportError:
    from pydantic import root_validator
    import inspect
    def model_validator(mode="after"):
        def decorator(func):
            target_func = func.__func__ if isinstance(func, classmethod) else func
            sig = inspect.signature(target_func)
            params = list(sig.parameters.keys())
            if mode == "before":
                def wrapper(cls, values):
                    res = target_func(cls, values) if (params and params[0] in ("cls", "self")) else target_func(values)
                    return res
                return root_validator(pre=True, allow_reuse=True)(wrapper)
            else:
                def wrapper(cls, values):
                    return values
                return root_validator(pre=False, allow_reuse=True)(wrapper)
        return decorator
from core.constants import OrchestratorMode
from core.i18n import _
from core.execution_models import ExecutionTree
from core.execution_models import ExecutionTree, FailureClass
from core.base_schema import BaseDiscoverySchema
from utils.logger import Logger  # <-- NOUVEAU pour les warnings
from core.discovery.models import DiscoveryRequest


# =====================================================
# PARSING TOLÉRANT DES ARGS D'OUTILS (P-B, décision GO 03/11)
# =====================================================
def parse_tool_args_json(raw: Any) -> Optional[Dict[str, Any]]:
    """Parse tolérant (P-B) d'un `tool_args_json` en dict, sans jamais lever.

    Cause 1 confirmée (runs 31/10) : le modèle écrit un JSON *dans une chaîne*
    et se rate (fences de code, commentaire qui traîne après l'objet,
    virgule finale, guillemets typographiques). `json.loads` strict rejette
    tout → « args illisibles » → plan refusé → tentative brûlée.

    Échelle déterministe, la plus conservatrice d'abord :
      1. dict déjà parsé → tel quel ; str vide/"{}" → {} ;
      2. `json.loads` strict (chemin rapide) ;
      3. fences de code retirées (` ```json … ``` `) puis re-tentative ;
      4. `json.JSONDecoder.raw_decode` depuis la première accolade : accepte
         un commentaire qui traîne APRÈS l'objet, uniquement s'il ne contient
         plus aucune accolade (aucune structure perdue) ;
      5. virgules traînantes retirées, guillemets typographiques normalisés ;
      6. réparation des séquences d'échappement invalides (portage de la
         boucle historique de `executor._safe_json_loads`).

    Retour : dict en cas de succès, sinon None (le signal « illisible »,
    interprété par la validation/expédition). Aucune exception ne fuit.
    """
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text == "{}":
        return {}

    # 2. Chemin strict (le cas sain majoritaire : zéro comportement changé).
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # 3. Fences de code (```json / ```).
    fenced = text
    if fenced.startswith("```"):
        first_nl = fenced.find("\n")
        fenced = fenced[first_nl + 1:] if first_nl != -1 else ""
        if fenced.rstrip().endswith("```"):
            fenced = fenced.rstrip()[:-3].rstrip()
        if fenced:
            try:
                parsed = json.loads(fenced)
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                pass

    # 4. Objet embarqué + commentaire traîne (sans accolade pendante).
    # NB : brace_ix >= 0 aussi pour index 0 — le chemin strict a déjà
    # échoué, donc un début par '{' peut cacher un commentaire traîne.
    # INERTIE OBLIGATOIRE : la traîne ne doit contenir ni '{', ni '}', ni
    # ':' ni '=' — `…} waiting_ms:1000` n'est pas un commentaire, c'est un
    # champ qui a raté son endroit ; l'avaler ferait perdre un paramètre
    # en silence (test régression test_malformed_args_report_the_offending_text).
    brace_ix = text.find("{")
    if brace_ix >= 0:
        try:
            parsed, end = json.JSONDecoder().raw_decode(text, brace_ix)
            if isinstance(parsed, dict):
                tail = text[end + 1:].strip()
                if (tail and "{" not in tail and "}" not in tail
                        and ":" not in tail and "=" not in tail):
                    return parsed
        except Exception:
            pass

    # 5. Assouplissements bénins : virgule traînante, guillemets typographiques.
    relaxed = text
    import re as _re
    relaxed = _re.sub(r",\s*([}\]])", r"\1", relaxed)
    for _bad, _good in (("\u201c", "\""), ("\u201d", "\""),
                        ("\u00ab", "\""), ("\u00bb", "\"")):
        relaxed = relaxed.replace(_bad, _good)
    if relaxed != text:
        try:
            parsed = json.loads(relaxed)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    # 6. Séquences d'échappement invalides (ex : "\l", "\a").
    try:
        parsed = json.loads(_repair_invalid_escapes(text))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _repair_invalid_escapes(json_str: str) -> str:
    """Double les backslashes devant les échappements non JSON (portage
    de la boucle historique de `Executor._safe_json_loads`).
    """
    result = []
    i = 0
    n = len(json_str)
    while i < n:
        ch = json_str[i]
        if ch == '\\' and i + 1 < n:
            nxt = json_str[i + 1]
            if nxt in ('"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u'):
                result.append(ch)
                result.append(nxt)
                i += 2
                continue
            result.append('\\\\')
            i += 1
            continue
        result.append(ch)
        i += 1
    return "".join(result)


# =====================================================
# ÉNUMÉRATIONS
# =====================================================
class StepType(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    TOOL_CALL = "tool_call"
    ABSTRACT_TASK = "abstract_task"

class ExecutionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    MAX_RETRIES_REACHED = "max_retries_reached"
    RUNNING = "running"

class RiskLevel(str, Enum):
    """Niveau de risque perçu d'un plan par la validation finale de l'Orchestrateur."""
    LOW = "low"
    MEDIUM = "medium"
    CRITICAL = "critical"

# =====================================================
# MODÈLES (héritent désormais de BaseDiscoverySchema)
# =====================================================

class FeasibilityDecision(BaseDiscoverySchema):
    """Décision initiale du Solver sur la faisabilité de l'objectif."""
    is_possible: bool = Field(
        ..., 
        description=_("True si les outils disponibles permettent de résoudre le but, False sinon.")
    )
    reason: str = Field(
        ..., 
        description=_("Si is_possible est False, l'explication polie et directe destinée à l'utilisateur. Si True, une courte justification technique.")
    )
    refined_strategy: str = Field(
        default="", 
        description=_("La stratégie globale d'approche à transmettre au Planner. Laisser vide si is_possible est False.")
    )


class PlanStep(BaseModel):
    id: str = Field(..., description=_("Identifiant unique de l'étape (ex: step_1)"))
    description: str = Field(..., description=_("Ce que cette étape est censée accomplir"))
    type: StepType = Field(..., description=_("Le type d'action à mener pour cette étape"))

    # P-C (GO 03/11) : `tool_name` + `tool_args_json` déclarés JUSTE APRÈS
    # `type` — Cause 1 confirmée (runs 31/10) : en fin de schéma, le champ
    # args était ignoré par le planner. L'ordre de déclaration Pydantic est
    # l'ordre du schéma envoyé aux providers (identique pour tous).
    tool_name: Optional[str] = Field(
        None,
        description=_("Nom EXACT de l'outil exécuté par ce step (celui de la liste des outils). Obligatoire non vide si type == 'tool_call'.")
    )
    tool_args_json: str = Field(
        default="{}",
        description=_("Arguments de l'outil, en JSON d'objet valide : {\"button\": \"left\"}. Une chaîne JSON, SANS commentaire ni texte avant/après. Pour les données du registre, référencer $@_nom. Pour perceive_understand : {\"question\": \"...\", \"source_tool\": \"...\"} ou {\"question\": \"...\", \"source_data\": \"...\"} ou {\"question\": \"...\", \"target_producer\": \"...\", \"target_args\": {...}} ; pour le flux composite : {\"question\": \"...\", \"source_tool\": \"...\", \"target_producer\": \"...\", \"target_args\": {...}} (la source rend les candidates, le producteur en consomme une).")
    )

    step_context: str = Field(
        default="", 
        description=_("Le contexte local, données ou prérequis spécifiques nécessaires à l'exécution.")
    )
    
    expected_result: Optional[str] = Field(
        None,
        description=_("Si type == 'tool_call' : 'true', 'false' ou 'any'. Omis/null = 'true' (rigide). 'any' = la valeur n'est pas jugée, le plan branche dessus (un résultat négatif est accepté). Si type == 'abstract_task', décrit sémantiquement l'état attendu.")
    )

    output_variable_name: Optional[str] = Field(
        None, 
        description=_("Nom d'une VALEUR produite (booléen, texte, structure) à stocker dans le registre. Pour une CIBLE (fenêtre, élément, forme), utiliser 'output_target_name', pas ce champ.")
    )
    output_variable_desc: Optional[str] = Field(
        None,
        description=_("Description de ce que représente cette donnée dans le registre.")
    )

    output_target_name: Optional[str] = Field(
        None,
        description=_("Nom local d'une CIBLE produite par cette étape (outil à contrat 'produces', sans préfixe data_/bool_). Les étapes suivantes la consomment comme argument 'target' via '$@_<nom>'. Ne pas confondre avec output_variable_name.")
    )

    execute_if: Optional[str] = Field(
        None,
        description=_("Condition déterministe STRICTEMENT booléenne. Ex: '$@_bool_nom == True'.")
    )

    # --- NOUVEAU : Variable cruciale pour le RUM ---
    is_crucial: bool = Field(
        False,
        description=_("Si True, cette variable sera ajoutée au Registre Utile de Mission (RUM) visible par le Présentateur. True pour la donnée qui RÉPOND à la question de l'utilisateur (résultat, valeur, confirmation finale). Jamais pour les artefacts intermédiaires sauf demande explicite.")
    )

    # --- NOUVEAU : déclaration d'irréversibilité (validation finale Orchestrateur) ---
    is_irreversible: bool = Field(
        False,
        description=_(
            "Si True, cette étape a un effet difficilement ou pas réversible une fois exécuté "
            "(suppression, envoi, paiement, écrasement de données, etc.). C'est une DÉCLARATION "
            "du Planner, pas une déduction automatique : soyez honnête, l'Orchestrateur peut "
            "contester ce jugement lors de la validation finale mais part de votre déclaration."
        )
    )
    irreversibility_reason: Optional[str] = Field(
        None,
        description=_("Si is_irreversible=True, explique brièvement en quoi l'effet est irréversible.")
    )

    # --- Changement d'état monde attendu (P2, optionnel, null = inerte) ---
    should_world_state_change_after_action: Optional[bool] = Field(
        default=None,
        description=_(
            "Le planner déclare-t-il qu'un changement du monde est attendu après "
            "cette action ? True = oui (ex : clic sur une icône), False = non, "
            "null (défaut) = indéterminé : l'executor ne change strictement rien."
        )
    )
    verify_with: Optional[str] = Field(
        default=None,
        description=_(
            "Nom d'un outil [perception] du manifeste pour l'arbitrage si le "
            "changement attendu ne se produit pas. Optionnel ; sinon convention "
            "get_world_state ; sinon pas d'arbitrage (échec honnête)."
        )
    )
    waiting_ms: Optional[int] = Field(
        default=None,
        ge=0,
        le=30000,
        description=_(
            "Attente fixe APRÈS l'action du step, en millisecondes. None "
            "(défaut) = non réfléchi : défaut appliqué (hôte déclaré, sinon "
            "ManAgent). 0 explicite = aucune attente (décision assumée). "
            "> 0 = consigne planner, prime sur le défaut hôte."
        )
    )

    @model_validator(mode='after')
    def validate_tool_integrity(self) -> 'PlanStep':
        if self.type == StepType.TOOL_CALL:
            if not self.tool_name or not str(self.tool_name).strip():
                raise ValueError(_("Un step de type 'tool_call' exige obligatoirement un 'tool_name' non vide."))
            if str(self.tool_name).strip() == "perceive_understand":
                raw_args = self.tool_args_json
                try:
                    parsed = parse_tool_args_json(raw_args)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    question = str(parsed.get("question") or "").strip()
                    source_tool = str(parsed.get("source_tool") or "").strip()
                    source_data = str(parsed.get("source_data") or "").strip()
                    target_producer = str(parsed.get("target_producer") or "").strip()
                    if not question:
                        raise ValueError(_("perceive_understand exige 'question' (que chercher)."))
                    if not source_tool and not source_data and not target_producer:
                        raise ValueError(_("perceive_understand exige 'source_tool', 'source_data' ou 'target_producer' (d'où lire le monde)."))
        
        
        if self.execute_if:
            v_lower = self.execute_if.lower()
            if "_data" in v_lower and not is_simple_data_condition(self.execute_if):
                raise ValueError("CRITICAL REJECTION: Le champ 'execute_if' n'accepte que des signaux booléens ($@_bool_nom) ou des comparaisons simples ($@_data_x == \"texte\"). Pas de notation pointée, ni IN/CONTAINS.")
            if "." in self.execute_if:
                raise ValueError("CRITICAL REJECTION: La notation pointée (ex: .result, .data) est strictement prohibée dans 'execute_if'.")
            if "contains" in v_lower or " in " in v_lower:
                raise ValueError("CRITICAL REJECTION: Les opérateurs sémantiques (IN, CONTAINS) sont interdits dans l'infrastructure de flux. Utilisez des outils dédiés en amont.")
                
        return self

    # --- Validation et normalisation idempotente du nommage des variables ---
    @model_validator(mode='after')
    def validate_variable_naming(self) -> 'PlanStep':
        import re
        if self.output_variable_name:
            raw = self.output_variable_name.strip()
            is_bool = raw.startswith("bool_")
            is_data = raw.startswith("data_")
            clean_root = re.sub(r'^(bool_|data_)+', '', raw).strip()
            if not clean_root:
                clean_root = f"out_{self.id}"
            if is_bool:
                self.output_variable_name = f"bool_{clean_root}"
            elif is_data:
                self.output_variable_name = f"data_{clean_root}"
            else:
                self.output_variable_name = f"data_{clean_root}"

        if self.execute_if:
            self.execute_if = re.sub(r'\$@_bool_(?:bool_)+', '$@_bool_', self.execute_if)
            self.execute_if = re.sub(r'\$@_data_(?:data_)+', '$@_data_', self.execute_if)
            self.execute_if = re.sub(r'@\$_bool_(?:bool_)+', '@$_bool_', self.execute_if)
            self.execute_if = re.sub(r'@\$_data_(?:data_)+', '@$_data_', self.execute_if)
        return self

    response_text: Optional[str] = Field(
        None,
        description=_("Si type == 'direct_answer' : la réponse intégrale destinée à l'utilisateur, en langage naturel. Sinon None.")
    )

    # P-C (GO 03/11) : `tool_name` / `tool_args_json` n'apparaissent plus
    # ici — leurs déclarations uniques vivent juste après `type` (en tête
    # de classe), pour figurer TÔT dans le schéma envoyé aux providers.

    # Champs purement internes (écrits par l'exécuteur, jamais par le modèle) :
    # exclus du schéma JSON exposé au LLM pour ne pas payer ni suggérer un
    # statut inventé. `model_dump()` et l'exécution restent inchangés.
    status: SkipJsonSchema[ExecutionStatus] = ExecutionStatus.PENDING
    result_context: SkipJsonSchema[str] = ""

    @property
    def get_parsed_args(self) -> dict:
        try:
            return parse_tool_args_json(self.tool_args_json) or {}
        except Exception:
            return {}
    

class Plan(BaseDiscoverySchema):
    """Le plan global proposé par un Solver."""
    goal: str = Field(..., description=_("L'objectif global de ce plan"))
    steps: List[PlanStep] = Field(default_factory=list, description=_("Liste ordonnée des étapes à exécuter"))
    
    def is_complete(self) -> bool:
        return all(step.status in [ExecutionStatus.SUCCESS, ExecutionStatus.SKIPPED] for step in self.steps)


class SolverResult(BaseModel):
    """Contrat de retour unifié du Solver vers l'Orchestrateur."""
    status: ExecutionStatus
    final_context: str 
    response: str = "" 
    error_reason: Optional[str] = None
    resolved_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description=_("Données du registre local renvoyées au parent."))
    execution_tree: Optional[ExecutionTree] = None
    failure_bundle: Optional[Any] = None
    breakout_report: Optional[Any] = None
    material_success_count: int = Field(default=0, description=_("Étapes tool_call convergées (actions matérielles réussies)."))
    tool_call_count: int = Field(default=0, description=_("Étapes tool_call tentées (hors sautées)."))
    skipped_crucial_ids: List[str] = Field(default_factory=list, description=_("Étapes cruciales sautées : la mission ne peut pas converger dessus."))
    failure_class: Optional[FailureClass] = Field(
        None,
        description=_("Classe d'échec détectée par l'Executor (EXECUTION_FAILURE ou CONVERGENCE_FAILURE).")
    )
    failed_tool: Optional[str] = Field(
        default=None,
        description=_("Outil dont l'étape a échoué, quand il est connu (diagnostic de signature)."),
    )
    error_code: Optional[str] = Field(
        default=None,
        description=_("Code d'erreur fermé renvoyé par l'hôte ou l'outil, quand il est disponible."),
    )
    target_entity: Optional[str] = Field(
        None,
        description=_("Entité tenue pour responsable, fixée par le code au point d'échec exact (Executor pour EXECUTION_FAILURE/CONVERGENCE_FAILURE).")
    )


def is_mission_success(has_tool_steps: bool, material_successes: int) -> tuple:
    """Règle anti faux-succès (Alerte 1) : une mission n'est un succès que si
    une action MATÉRIELLE a réussi — les affirmations (direct_answer) ne
    comptent jamais.

    - Aucune étape outil dans aucun plan -> (True, "") : réponse/refus direct OK.
    - Étapes outil vues -> (material_successes > 0, raison sinon).
    Fonction pure, testable vite.
    """
    if not has_tool_steps:
        return True, ""
    if material_successes > 0:
        return True, ""
    return False, (
        "Aucune action matérielle réussie : la mission ne peut pas être "
        "déclarée succès sur de simples affirmations."
    )


def is_suspect_final_answer(plan: Any, fail_history: Any) -> bool:
    """Plan final sans outil après des échecs d'exécution : suspect (R1).

    Un `direct_answer` pur est légitime en première intention (refus
    honnête) mais suspect après des échecs : il doit passer la vérification
    finale STRICTE (monde frais exigé, corroboration explicite).
    Fonction pure, testable vite.
    """
    try:
        steps = list(getattr(plan, "steps", []) or [])
        has_tool = any(
            getattr(getattr(s, "type", None), "value", getattr(s, "type", None)) == "tool_call"
            for s in steps
        )
        if has_tool:
            return False
        return bool(fail_history)
    except Exception:
        return False


import re
import hashlib
from typing import List, Optional, Dict, Any


def is_simple_data_condition(expr: str) -> bool:
    """Dit si une condition n'utilise que des comparaisons simples.

    Autorisé : `$@_data_x == "texte"`, `!=`, combinés par and/or (insensible
    à la casse pour True/False). L'executor sait évaluer ça (égalité de
    chaînes/valeurs interpolées). Interdit : notation pointée, IN/CONTAINS,
    fonctions, tout le reste. Fonction pure, testée vite.
    """
    import ast as _ast

    if not expr or not isinstance(expr, str):
        return True
    # Masque les variables $@_xxx / @$_xxx par des noms simples
    # (tirets normalisés : les IDs step_N-XXXXX restent un seul nom).
    masked = re.sub(r'(?:\$@_|@\$_)([a-zA-Z0-9_-]+)',
                    lambda m: 'VAR_' + m.group(1).replace('-', '_'), expr)
    try:
        tree = _ast.parse(masked, mode='eval')
    except Exception:
        return False

    def _ok(node) -> bool:
        if isinstance(node, _ast.Expression):
            return _ok(node.body)
        if isinstance(node, _ast.BoolOp):
            return isinstance(node.op, (_ast.And, _ast.Or)) and all(_ok(v) for v in node.values)
        if isinstance(node, _ast.Compare):
            if not all(isinstance(o, (_ast.Eq, _ast.NotEq)) for o in node.ops):
                return False
            parts = [node.left] + list(node.comparators)
            return all(isinstance(p, (_ast.Constant, _ast.Name)) for p in parts)
        if isinstance(node, (_ast.Constant, _ast.Name)):
            return True
        return False

    if "." in masked:
        return False
    low = masked.lower()
    if " in " in f" {low} " or "contains" in low:
        return False
    return _ok(tree)

FR_TO_EN_ACTIONS = {
    "ouvrir": "open",
    "fermer": "close",
    "lancer": "launch",
    "demarrer": "start",
    "démarrer": "start",
    "cliquer": "click",
    "taper": "type",
    "ecrire": "type",
    "écrire": "type",
    "saisir": "type",
    "appuyer": "press",
    "presser": "press",
    "creer": "create",
    "créer": "create",
    "supprimer": "delete",
    "rechercher": "search",
    "chercher": "search",
    "telecharger": "download",
    "télécharger": "download",
    "executer": "execute",
    "exécuter": "execute",
    "afficher": "show",
    "masquer": "hide",
    "lire": "read",
    "copier": "copy",
    "coller": "paste",
}

FR_TO_EN_OBJECTS = {
    "boite de dialogue executer": "run dialog box",
    "boîte de dialogue exécuter": "run dialog box",
    "boite de dialogue \"executer\"": "run dialog box",
    "boîte de dialogue \"exécuter\"": "run dialog box",
    "dialogue executer": "run dialog box",
    "dialogue exécuter": "run dialog box",
    "dialogue 'executer'": "run dialog box",
    "dialogue 'exécuter'": "run dialog box",
    "menu demarrer": "start menu",
    "menu démarrer": "start menu",
    "menu demarrer - executer": "run dialog box",
    "menu démarrer - exécuter": "run dialog box",
    "navigateur": "browser",
    "navigateur web": "web browser",
    "calculatrice": "calculator",
    "bloc-notes": "notepad",
    "bloc notes": "notepad",
    "explorateur de fichiers": "file explorer",
    "gestionnaire de taches": "task manager",
    "gestionnaire de tâches": "task manager",
    "invite de commandes": "command prompt",
}

def clean_signature_str(val: Optional[str]) -> str:
    if not val:
        return ""
    # Strip quotes, backticks, parenthesis, braces, brackets
    cleaned = re.sub(r'["\'`«»()\[\]{}]', '', val)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()
    return cleaned

class MissionSignature(BaseModel):
    action: str = Field(..., description="The action to perform in English, infinitive verb without punctuation (e.g. open, close, launch, click, type, press)")
    object: str = Field(..., description="The target entity/object in English without quotes (e.g. run dialog box, start menu, notepad, browser)")
    desired_state: Optional[str] = Field(None, description="Desired final state in English (optional, e.g. open, closed, focused, active)")

    @model_validator(mode='after')
    def normalize_signature(self) -> 'MissionSignature':
        """Nettoie et normalise en anglais canonique la signature."""
        cleaned_act = clean_signature_str(self.action)
        cleaned_obj = clean_signature_str(self.object)
        
        # Fallback automatique traduction FR -> EN si le LLM a produit du français
        if cleaned_act in FR_TO_EN_ACTIONS:
            cleaned_act = FR_TO_EN_ACTIONS[cleaned_act]
            
        if cleaned_obj in FR_TO_EN_OBJECTS:
            cleaned_obj = FR_TO_EN_OBJECTS[cleaned_obj]
            
        self.action = cleaned_act
        self.object = cleaned_obj
        
        if self.desired_state:
            cleaned_state = clean_signature_str(self.desired_state)
            if cleaned_state in {"ouvert", "ouverte"}:
                cleaned_state = "open"
            elif cleaned_state in {"fermé", "fermée"}:
                cleaned_state = "closed"
            self.desired_state = cleaned_state
            
        return self

    def to_hash(self) -> str:
        """Génère un hash/signature canonique normalisé pour l'indexation et le retrieval de Skills."""
        act = clean_signature_str(self.action)
        obj = clean_signature_str(self.object)
        if self.desired_state:
            state = clean_signature_str(self.desired_state)
            return f"sig:{act}:{obj}:{state}"
        return f"sig:{act}:{obj}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "object": self.object,
            "desired_state": self.desired_state
        }

class AssetInjection(BaseModel):
    uri: str = Field(..., description="L'URI exact de la ressource (ex: inputs://turn_1, files://photo.jpg)")
    variable_name: str = Field(..., description="Le nom de la variable à créer (doit commencer par data_, ex: data_target_image)")
    description: str = Field(..., description="Description de ce que contient cet asset pour aider le Planner")

class OrchestratorDecision(BaseModel):
    """
    Décision de l'Orchestrateur : choisir entre direct, mission ou request.
    """
    type: OrchestratorMode = Field(
        ...,
        description=_("Mode de traitement : direct, mission ou request")
    )
    output: str = Field(
        ...,
        description=_("Réponse utilisateur directe OU description analytique de la mission OU intention de la requête.")
    )
    signatures: List[MissionSignature] = Field(
        default_factory=list,
        description=_("Liste des missions simples extraites de la demande, si applicable (obligatoire si type='mission').")
    )
    injected_assets: List[AssetInjection] = Field(
        default_factory=list,
        description=_("Liste des assets à injecter dans la mémoire du Solver sous forme de variables (obligatoire s'il y a des fichiers/inputs nécessaires à la mission). N'y mettre QUE des URI vues verbatim dans le contexte, jamais inventées.")
    )
    discovery_request: Optional[DiscoveryRequest] = Field(
        ...,  # OBLIGATOIRE dans le JSON, mais peut être null
        description=_(
            "⚠️ OBLIGATOIRE : Ce champ est requis dans la réponse JSON (même pour le mettre à null). "
            "Remplissez‑le UNIQUEMENT si vous avez choisi le type 'request'. "
            "Dans ce cas, vous DEVEZ fournir les paramètres (goal, data_type, target, technical_goal). "
            "Pour les types 'direct' ou 'mission', mettez‑le à null."
        )
    )
    learned_facts: List[str] = Field(
        default_factory=list,
        description=_("Faits durables et préférences de l'utilisateur déduits de sa demande. "
                      "Utile pour construire la mémoire sémantique du système. "
                      "Laissez vide s'il n'y a pas de nouvelle information stable.")
    )

    @model_validator(mode='after')
    def validate_discovery_consistency(self) -> 'OrchestratorDecision':
        if self.type == OrchestratorMode.REQUEST:
            if self.discovery_request is None:
                raise ValueError(_("Le champ 'discovery_request' est obligatoire lorsque type='request'."))
            if not getattr(self.discovery_request, 'data_type', None) or not getattr(self.discovery_request, 'targets', None):
                raise ValueError(_("Le champ 'discovery_request' doit spécifier un 'data_type' et au moins une cible."))
        else:
            if self.discovery_request is not None:
                self.discovery_request = None
        return self


def drop_unknown_injected_assets(injected_assets, known_uris):
    """Sépare les assets injectés connus des fantômes (pur, sans LLM).

    Générique : compare des URI, jamais de nom d'outil ou d'hôte.
    Retourne (gardés, jetés). Fantôme = URI sans objet enregistré.
    """
    known = set(known_uris or [])
    kept, dropped = [], []
    for asset in injected_assets or []:
        uri = getattr(asset, "uri", None)
        (kept if uri in known else dropped).append(asset)
    return kept, dropped


FAILURE_ORIGINS = frozenset({
    "ARGS_INVALID",
    "HOST_REFUSAL",
    "TOOL_ERROR",
    "ALIGNMENT",
    "PLAN_REJECTED",
    "CONVERGENCE",
    "USER_CANCELLED",
    "UNKNOWN",
})

# Codes/expressions qui disent « l'appel est mal formé » (le monde n'est pas
# en panne). Volontairement agnostiques : aucun nom d'outil en dur.
_HOST_REFUSAL_CODES = (
    "TARGET_REQUIRED",
    "TARGET_SCHEMA_INVALID",
    "TARGET_ANCHOR_UNSUPPORTED",
    "TARGET_PRODUCER_UNSUPPORTED",
    "TARGET_PRODUCER_NOT_UTILITY",
    "TARGET_NOT_FOUND",
    "TARGET_SCOPE_MISMATCH",
    "TARGET_STALE_REF",
)
_HOST_REFUSAL_MARKERS = (
    "ancrage obligatoire",
    "target required",
    "requires a target",
    "missing target",
    "refus",
    "refuse",
    "not allowed",
    "non autorise",
    "non autorisé",
)
_ARGS_INVALID_MARKERS = (
    "argument",
    "parametre",
    "paramètre",
    "mal appel",
    "invalid",
    "invalide",
    "requis",
    "required",
    "schema",
    "schéma",
    "enum",
)

_HASH_EXCLUDED_CODES = frozenset({
    "JSON", "LLM", "API", "URL", "GUI", "RUM", "MCP", "HTTP", "HTTPS",
    "TRUE", "FALSE", "NONE", "NULL",
})


def classify_failure_origin(
    failure_class: Any,
    reason: Any,
    *,
    tool_name: str = "",
    error_code: str = "",
) -> str:
    """Taxonomie d'origine d'un échec (pure, sans LLM).

    Distingue ce qui est réparable par le plan (stratégie/convergence) de ce
    qui ne l'est pas (arguments invalides, refus de contrat hôte) et de ce qui
    accuse réellement l'outil (panne d'exécution).
    """
    cls = str(getattr(failure_class, "value", failure_class) or "").strip().lower()
    text = str(reason or "").lower()
    code = str(error_code or "").strip().upper()

    if cls == "alignment_failure":
        return "ALIGNMENT"
    if cls in ("plan_rejected_validation", "plan_rejected_supervisor"):
        return "PLAN_REJECTED"
    if cls == "user_cancelled":
        return "USER_CANCELLED"

    if code and code in _HOST_REFUSAL_CODES:
        return "HOST_REFUSAL"
    for token in _HOST_REFUSAL_CODES:
        if token.lower() in text:
            return "HOST_REFUSAL"
    if any(marker in text for marker in _HOST_REFUSAL_MARKERS):
        return "HOST_REFUSAL"
    if any(marker in text for marker in _ARGS_INVALID_MARKERS):
        return "ARGS_INVALID"

    if cls == "convergence_failure":
        return "CONVERGENCE"
    if cls == "execution_failure":
        return "TOOL_ERROR"
    if cls == "max_retries_reached":
        return "CONVERGENCE"
    return "UNKNOWN"


def _signature_tool(reason: str) -> str:
    import re
    patterns = (
        r"(?:outil|tool)\s*['\"\[]?([A-Za-z_][\w.\-]*)",
        r"step[_\-][\w\-]+\s*[:\-]\s*([A-Za-z_][\w.\-]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, reason, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().lower()
    return "?"


def _signature_code(reason: str) -> str:
    import re
    for match in re.finditer(r"\b([A-Z][A-Z0-9_]{2,})\b", reason):
        value = match.group(1)
        if value not in _HASH_EXCLUDED_CODES:
            return value.lower()
    return ""


def failure_signature_class(signature: Any) -> str:
    """Classe d'une signature, ancien (` :: `) ou nouveau (`|`) format."""
    text = str(signature or "")
    if "|" in text:
        return text.split("|", 1)[0]
    return text.split(" :: ", 1)[0]


def failure_signature_origin(signature: Any) -> str:
    """Origine encodée dans une signature structurée (vide si ancienne)."""
    text = str(signature or "")
    if "|" not in text:
        return ""
    parts = text.split("|")
    if len(parts) < 3:
        return ""
    return parts[2].strip().lower()


def normalize_failure_signature(
    failure_class: Any,
    reason: Any,
    *,
    tool: str = "",
    code: str = "",
) -> str:
    """Signature comparable et DISCRIMINANTE d'un échec (pur, sans LLM).

    Format : `classe|outil|origine_ou_code|prose[:120]|digest`.
    Les champs discriminants sont en tête ; la prose est normalisée (IDs,
    UUID, nombres) et n'est tronquée qu'à la fin. Le digest porte sur la prose
    complète normalisée : deux causes différentes ne fusionnent plus.
    """
    import hashlib
    import re

    cls = str(getattr(failure_class, "value", failure_class) or "").strip().lower()
    raw = str(reason or "")
    origin = classify_failure_origin(
        failure_class, raw, tool_name=tool, error_code=code
    )
    tool_value = (tool or _signature_tool(raw) or "?").strip().lower()
    code_value = (code or _signature_code(raw) or origin).strip().lower()

    text = raw.lower()
    text = re.sub(r'step[_\-][\w\-]+', 'step_#', text)
    text = re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){0,3}\b', '#id', text)
    text = re.sub(r'\d+', '#', text)
    text = re.sub(r'\s+', ' ', text).strip()
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{cls}|{tool_value}|{code_value}|{text[:120]}|{digest}"


def is_quota_streak(history, threshold: int = 2) -> bool:
    """Le quota LLM est-il la cause répétée (pur, sans LLM) ?

    history = liste de (signature, actions_matérielles), signatures déjà
    normalisées en minuscules. Vrai si les `threshold` derniers échecs
    parlent quota/débit. Le replan ne réparera pas un quota vide : stopper
    proprement au lieu de brûler des tentatives en spirale 429.
    """
    import re as _re
    if not isinstance(history, list) or len(history) < threshold:
        return False
    pattern = _re.compile(r"quota|exhaust|resource_exhausted|\brate\b|d[ée]bit")
    try:
        tail = history[-threshold:]
    except Exception:
        return False
    for sig, _ in tail:
        if not pattern.search(str(sig or "")):
            return False
    return True


def is_sterile_streak(history) -> bool:
    """Faut-il abandonner : même échec 2 fois sans progrès (pur) ?

    history = liste de (signature, actions_matérielles_réussies).
    Stérile = les 2 dernières signatures identiques ET aucun progrès
    matériel au-delà du meilleur score précédent.
    """
    if not isinstance(history, list) or len(history) < 2:
        return False
    last_sig, last_mat = history[-1]
    prev_sig, _ = history[-2]
    if last_sig != prev_sig:
        return False
    # Seule une panne RÉELLE de l'outil justifie l'abandon pour stérilité.
    # Un refus hôte, des args invalides, un désalignement ou une divergence
    # ont leur propre garde-fou : les confondre relançait/arrêtait à tort.
    origin = failure_signature_origin(last_sig)
    if origin and origin not in ("tool_error", "unknown"):
        return False
    try:
        best_before = max(int(m) for _, m in history[:-1])
    except Exception:
        best_before = 0
    try:
        return int(last_mat) <= int(best_before)
    except Exception:
        return True


def is_nonretryable_contract_failure(reason: Any) -> bool:
    """Erreur de contrat pré-dispatch que replanifier ne peut pas réparer.

    Seul le cas d'une référence de cible non liée est visé : l'alias a bien
    été déclaré (les gates structurelles l'ont accepté), mais le runtime ne
    l'a pas publié. Rejouer le même plan ne peut pas réparer cette liaison.
    """
    text = str(reason or "").lower()
    return "[pre-dispatch]" in text and "unresolved target reference" in text


# Refus d'argument rendus par l'hôte APRÈS dispatch : le contrat d'appel est
# en cause, pas la stratégie. Un seul essai de correction, puis abandon.
HOST_TARGET_CONTRACT_CODES = ("TARGET_REQUIRED", "TARGET_SCHEMA_INVALID")
HOST_REFUSAL_MAX_CORRECTIONS = 1


def is_host_target_contract_refusal(reason: Any) -> bool:
    """L'hôte a refusé l'argument cible (contrat), pas le monde.

    Distinct de `TARGET_NOT_FOUND` (cible périmée : recréer peut réparer) :
    ici l'argument envoyé viole le schéma déclaré par l'hôte, donc relancer
    la même stratégie ne change rien. Fonction pure.
    """
    text = str(reason or "")
    return any(code in text for code in HOST_TARGET_CONTRACT_CODES)


def host_refusal_budget_exhausted(refusal_count: int) -> bool:
    """Vrai quand le quota de corrections après refus hôte est épuisé.

    `HOST_REFUSAL_MAX_CORRECTIONS = 1` : le premier refus autorise un nouvel
    essai (le modèle peut corriger l'argument), le deuxième abandonne.
    """
    try:
        return int(refusal_count) > HOST_REFUSAL_MAX_CORRECTIONS
    except (TypeError, ValueError):
        return False


def build_plan_validation_payload(outcome: Any, solver_id: str, plan_id: Any) -> Dict[str, Any]:
    """Payload de l'event `plan_validation_decision` (pur, testé vite).

    Expose `is_partial` / `partial_gaps` : un plan accepté avec lacunes ne
    doit pas être indiscernable d'un plan complet côté front/observabilité.
    """
    risk = getattr(outcome, "risk_level", None)
    _raw_gaps = getattr(outcome, "partial_gaps", None) or []
    if isinstance(_raw_gaps, str):
        _raw_gaps = [_raw_gaps]
    _gaps = []
    try:
        for _g in list(_raw_gaps)[:8]:
            _text = str(_g).strip()
            if _text:
                _gaps.append(_text)
    except Exception:
        _gaps = []
    return {
        "solver_id": solver_id,
        "plan_id": plan_id,
        "is_valid": bool(getattr(outcome, "is_valid", False)),
        "reason": getattr(outcome, "reason", ""),
        "risk_level": getattr(risk, "value", risk),
        "requires_human_confirmation": bool(
            getattr(outcome, "requires_human_confirmation", False)
        ),
        "human_confirmed": bool(getattr(outcome, "human_confirmed", False)),
        "irreversibility_flags": list(
            getattr(outcome, "irreversibility_flags", None) or []
        ),
        "is_partial": bool(getattr(outcome, "is_partial", False)),
        "partial_gaps": _gaps,
    }


class ConvergenceDecision(BaseDiscoverySchema):
    is_convergent: bool = Field(
        ..., 
        description=_("True si le résultat réel de l'action remplit et valide l'output attendu, False sinon.")
    )
    reason: str = Field(
        ..., 
        description=_("Analyse technique de la convergence ou explication précise de la divergence constatée.")
    )


class PlanValidationDecision(BaseDiscoverySchema):
    """
    Décision de l'Orchestrateur (le "LLM Judge") sur la conformité d'un plan
    proposé par un Solver, avant exécution. Contrairement à FeasibilityDecision
    (est-ce POSSIBLE ?) et ConvergenceDecision (est-ce que le RÉSULTAT converge ?),
    celle-ci juge le PLAN lui-même : conformité à rules.md, patterns récursifs
    signalés, actions irréversibles déclarées par le Planner.
    """
    is_conformant: bool = Field(
        ...,
        description=_("True si le plan respecte les critères de rules.md et converge raisonnablement vers l'objectif, False sinon.")
    )
    reason: str = Field(
        ...,
        description=_(
            "Justification technique de la décision. Si is_conformant=False, doit être "
            "assez précise pour que le Planner puisse corriger son prochain plan. "
            "Ce texte est réinjecté tel quel dans le contexte du Planner en cas de refus."
        )
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description=_("Niveau de risque perçu du plan, indépendamment de sa conformité.")
    )
    requires_human_confirmation: bool = Field(
        default=False,
        description=_(
            "True si, même conforme, ce plan doit être confirmé par un humain avant "
            "exécution (action critique et/ou irréversible). Ignoré si is_conformant=False "
            "(un plan déjà rejeté n'a pas besoin d'être confirmé)."
        )
    )
    irreversibility_flags: List[str] = Field(
        default_factory=list,
        description=_(
            "Liste des identifiants d'étapes jugées irréversibles ou critiques par "
            "l'Orchestrateur — peut différer de ce que le Planner a déclaré via "
            "is_irreversible sur chaque PlanStep (l'Orchestrateur peut contester)."
        )
    )
    is_partial: bool = Field(
        default=False,
        description=_(
            "True si le plan est sûr mais n'atteint qu'une partie de l'objectif. "
            "Les parties manquantes sont listées dans partial_gaps ; elles seront "
            "vérifiées par la convergence finale. Une lacune critique ou irréversible "
            "rend le plan non conforme."
        )
    )
    partial_gaps: List[str] = Field(
        default_factory=list,
        description=_(
            "Parties de l'objectif non couvertes par un plan partiel sûr. "
            "Chaque entrée doit être assez précise pour être vérifiée après exécution."
        )
    )
    warnings: List[str] = Field(
        default_factory=list,
        description=_(
            "Avertissements NON BLOQUANTS : doutes, ancrage faible, cible littérale "
            "ou vérification incertaine. Ne jamais refuser un plan à cause d'un "
            "avertissement ; la convergence finale les évaluera."
        )
    )


class CompactedAdvice(BaseDiscoverySchema):
    advice: str = Field(..., description="Conseil stratégique (stratégies clés + pièges à éviter) pour le Planner.")
    is_novel: bool = Field(..., description="True si la mission actuelle semble réellement nouvelle (peu de patterns connus), False si elle est déjà bien couverte par les missions similaires.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Niveau de confiance du compactor dans son jugement (optionnel).")


class DepthEscalationDecision(BaseDiscoverySchema):
    """
    Décision de l'Orchestrateur sur une demande d'extension de la profondeur
    maximale de récursion (abstract_task imbriqués). Ne juge PAS un plan
    précis (contrairement à PlanValidationDecision) mais la CHAÎNE de
    sous-tâches ayant mené à ce point : est-ce une décomposition légitime
    d'un problème réellement complexe, ou un motif dégénéré (boucle,
    redite du même objectif sous des formulations différentes, absence de
    progression tangible d'un niveau à l'autre) ?
    """
    is_legitimate_complexity: bool = Field(
        ...,
        description=_(
            "True si la chaîne de sous-tâches reflète une décomposition légitime d'un "
            "problème réellement complexe (chaque niveau a un objectif distinct qui fait "
            "progresser la mission). False si c'est un motif récursif dégénéré : le même "
            "objectif reformulé, une boucle sans progression, ou une décomposition qui "
            "n'apporte rien de nouveau par rapport au niveau parent."
        )
    )
    reason: str = Field(
        ...,
        description=_("Justification technique, assez précise pour comprendre le jugement même en cas de refus.")
    )


class RetryExtensionDecision(BaseDiscoverySchema):
    """
    Décision sur une demande de rallonge du budget d'exécution (miroir de
    DepthEscalationDecision pour la profondeur). Le budget standard est
    épuisé ; le progrès accompli justifie-t-il UNE tentative de plus ?
    """
    is_worthwhile: bool = Field(
        ...,
        description=_(
            "True si une tentative de plus a une chance réelle : progrès matériel "
            "accompli, erreur nouvelle ou transitoire, plan suivant différent. "
            "False si stérile : même erreur, aucun progrès, anomalie insoluble."
        )
    )
    reason: str = Field(
        ...,
        description=_("Justification technique, assez précise pour comprendre le jugement même en cas de refus.")
    )

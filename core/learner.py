# core/learner.py
# =====================================================
# PHASE 3 – LEARNER v2 (LLM + async + déduplication)
# =====================================================

import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from core.entity import Entity
from core.llm import Llm
from utils.logger import Logger
from memory.lesson_store import LessonStore
from memory.mission_store import MissionStore
from core.execution_models import ExecutionTree, PlanAttempt, FailureClass
from core.entity_manifest import get_entity_role
import asyncio

# =====================================================
# MODÈLE PYDANTIC POUR L'EXTRACTION DE LEÇON PAR LLM
# =====================================================

class ExtractedLesson(BaseModel):
    """
    Structure attendue du LLM pour une leçon.
    """
    scope: str = Field(
        ...,
        description="Identité STABLE et étroite de la leçon (clé de déduplication/evidence — "
                    "ne doit PAS varier d'un épisode à l'autre pour la même situation)."
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="Mots-clés LARGES de découvrabilité (applications, actions, synonymes, outils) "
                    "qui aideront un futur LLM à retrouver cette leçon depuis un but de mission "
                    "différent. Volontairement plus permissif que scope — pense à toutes les façons "
                    "dont un utilisateur pourrait exprimer une mission qui retomberait dans ce piège."
    )
    recommendation: str = Field(
        ...,
        description="Règle impérative et courte (1-2 phrases) pour éviter cette erreur à l'avenir."
    )

    polarity: str = Field(..., description="avoid ou prefer")  # <--- NOUVEAU


class RerankedLessons(BaseModel):
    """
    Réponse du LLM reranker : parmi les leçons candidates listées (par leur id), lesquelles
    s'appliquent réellement au but de mission courant.
    """
    relevant_lesson_ids: List[int] = Field(
        default_factory=list,
        description="IDs des leçons (parmi celles listées dans le prompt) réellement applicables "
                    "à ce but précis. Liste vide si aucune ne s'applique — ne force jamais une "
                    "correspondance approximative."
    )
    reasoning: str = Field(
        ...,
        description="Explication brève (1-2 phrases) de la sélection, ou de l'absence de sélection."
    )


# =====================================================
# ANALYZER (avec LLM)
# =====================================================

class Analyzer:
    """
    Analyse les épisodes (ExecutionTree) pour en extraire des leçons via LLM.
    """

    def __init__(self, lesson_store: LessonStore, llm: Llm):
        self.lesson_store = lesson_store
        self.llm = llm

    async def analyze_episode(self, episode: Dict[str, Any]) -> None:
        """
        Analyse un épisode et génère des leçons (async).
        """
        # Le Presentator n'est pas dans l'ExecutionTree (il est appelé après coup par
        # l'Orchestrateur, hors de la récursion Solver/Executor) : sa télémétrie vit dans une
        # colonne séparée et doit être traitée indépendamment de la présence d'un arbre.
        await self._analyze_presentator_failure(episode)

        tree_json = episode.get("execution_tree_json")
        if not tree_json or tree_json == "{}":
            return

        try:
            tree_data = json.loads(tree_json)
            tree = ExecutionTree(**tree_data)
        except Exception as e:
            Logger.error(f"[Analyzer] Erreur de désérialisation de l'arbre pour {episode.get('mission_id')}: {e}")
            return

        # Parcourir l'arbre récursivement
        await self._traverse_tree(tree, episode.get("environment", "simulated"), episode.get("mission_id"))

    async def _analyze_presentator_failure(self, episode: Dict[str, Any]) -> None:
        """
        Analyse l'échec éventuel du Presentator pour cet épisode.
        target_entity="Presentator" est ici une certitude absolue (pas d'inférence) : personne
        d'autre que l'Orchestrateur n'appelle generate_mission_report/generate_error_report, et
        cette méthode n'est déclenchée QUE si cet appel précis a échoué.
        """
        raw = episode.get("presentator_result_json")
        if not raw or raw in ("{}", "null"):
            return
        try:
            result = json.loads(raw)
        except Exception:
            return
        if not result or result.get("status") != "failed":
            return

        error_reason = result.get("error_reason") or "Raison inconnue"
        environment = episode.get("environment", "simulated")

        try:
            prompt = f"""
Tu analyses l'échec d'une entité précise d'un moteur agentique.

ENTITÉ RESPONSABLE : Presentator
RÔLE DE CETTE ENTITÉ : {get_entity_role("Presentator")}

CONTEXTE : la génération du rapport final de mission a échoué.
DÉTAIL DE L'ERREUR : {error_reason}

Instructions :
1. Identifie un mot-clé de scope STABLE et étroit résumant la situation (ex: 'generation_rapport_echec',
   'contexte_trop_long', 'donnees_manquantes').
2. Propose 5 à 8 mots-clés LARGES de découvrabilité (types de mission, symptômes) qui
   permettront de retrouver cette leçon plus tard — pas plus, la liste est plafonnée côté code
   de toute façon (voir LessonStore.MAX_KEYWORDS_PER_CALL).
3. Produis une règle impérative courte (1-2 phrases), adressée directement au Presentator.
"""
            extracted = await asyncio.wait_for(
                self.llm.generate_structured(prompt=prompt, schema=ExtractedLesson),
                timeout=30.0
            )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon Presentator : {e}")
            scope = "presentator_generation_fallback"
            recommendation = (
                "La génération du rapport final a échoué. Vérifier la robustesse de "
                "generate_mission_report/generate_error_report face à un contexte incomplet ou mal formé."
            )
            keywords = []

        self.lesson_store.upsert_lesson(
            entity_type="Presentator",
            scope=scope,
            recommendation=recommendation,
            environment=environment,
            keywords=keywords,
            mission_id=episode.get("mission_id")
        )


    async def _traverse_tree(self, tree: ExecutionTree, environment: str, mission_id: Optional[str] = None) -> None:
        """
        Parcourt récursivement l'arbre et génère des leçons pour chaque tentative et nœud.
        """
        previous_outcomes = []  # <--- NOUVEAU : track les outcomes précédents pour le contraste
        for attempt in tree.attempts:
            # On regarde si une tentative précédente a échoué
            has_previous_failure = "failed" in previous_outcomes
            await self._analyze_attempt(attempt, tree.goal, environment, mission_id, has_previous_failure)
            previous_outcomes.append(attempt.outcome)  # on ajoute le résultat de cette tentative pour les suivantes
            
            # Parcourir les nœuds pour les sous-arbres (inchangé)
            for node in attempt.nodes:
                if node.child_execution_tree:
                    await self._traverse_tree(node.child_execution_tree, environment, mission_id)


    async def _analyze_attempt(self, attempt: PlanAttempt, goal: str, environment: str, 
                            mission_id: Optional[str] = None, has_previous_failure: bool = False) -> None:
        """
        Analyse une tentative et génère des leçons.
        - Si échec -> leçon "avoid".
        - Si succès après au moins un échec -> leçon "prefer".
        - Si succès direct (pas d'échec avant) -> aucune leçon.
        """
        failure_class = attempt.failure_class
        failure_reason = attempt.failure_reason or ""

        # --- B3 : annulation utilisateur = pas de leçon ---
        if failure_class == FailureClass.USER_CANCELLED:
            return

        # --- Cas 1 : ÉCHEC ---
        if attempt.outcome == "failed":
            await self._generate_avoid_lesson(attempt, goal, environment, mission_id)
            return

        # --- Cas 2 : SUCCÈS ---
        if attempt.outcome == "success":
            # On ne crée une leçon de succès que s'il y a eu un échec avant (contraste)
            if has_previous_failure:
                await self._generate_prefer_lesson(attempt, goal, environment, mission_id)
            # Sinon, on ignore (succès trivial, pas de valeur d'apprentissage)
            return

        # (Cas improbable, mais sécurité)
        Logger.warning(f"[Analyzer] Outcome non reconnu : {attempt.outcome}")

    async def _generate_avoid_lesson(self, attempt: PlanAttempt, goal: str, environment: str, 
                                    mission_id: Optional[str] = None) -> None:
        """
        Génère une leçon de type 'avoid' à partir d'un échec.
        (Code existant, légèrement refactorisé)
        """
        entity_type = attempt.target_entity
        if not entity_type:
            Logger.warning(f"[Analyzer] Attempt sans target_entity — leçon avoid ignorée.")
            return

        role_description = get_entity_role(entity_type)
        failure_class = attempt.failure_class
        failure_reason = attempt.failure_reason or ""

        scope: Optional[str] = None
        recommendation: Optional[str] = None
        keywords: List[str] = []
        try:
            pruned = self._prune_attempt_for_llm(attempt)
            prompt = f"""
    Tu analyses l'échec d'une entité précise d'un moteur agentique.

    ENTITÉ RESPONSABLE : {entity_type}
    RÔLE DE CETTE ENTITÉ : {role_description}

    OBJECTIF DE LA SOUS-TÂCHE : {goal}
    TYPE D'ERREUR : {failure_class.value}
    DÉTAIL DE L'ERREUR : {failure_reason}

    SÉQUENCE D'EXÉCUTION (prunée) :
    {pruned}

    Instructions :
    1. Identifie un mot-clé de scope STABLE et étroit qui résume la SITUATION précise à l'origine
    de l'échec (ex: 'keyboard_run_dialog_focus_loss'). Ce n'est pas forcément une application.
    2. Propose 5 à 8 mots-clés LARGES et variés (applications, actions, synonymes, outils
    impliqués) qui permettront de retrouver cette leçon depuis un but de mission différent.
    3. Produis une règle impérative courte (1-2 phrases), adressée directement à {entity_type}, pour
    ÉVITER cette erreur à l'avenir compte tenu de son rôle ci-dessus.
    """
            extracted = await asyncio.wait_for(
                self.llm.generate_structured(prompt=prompt, schema=ExtractedLesson),
                timeout=30.0
            )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon avoid : {e}")
            scope = f"{failure_class.value}_fallback"
            recommendation = self._generate_recommendation_fallback(failure_class, failure_reason)
            keywords = []

        # Upsert avec polarity='avoid'
        self.lesson_store.upsert_lesson(
            entity_type=entity_type,
            scope=scope,
            recommendation=recommendation,
            environment=environment,
            keywords=keywords,
            mission_id=mission_id,
            polarity="avoid"  # <--- explicite
        )

        # Leçon complémentaire outil (si applicable) - on conserve la polarité avoid
        if failure_class in (FailureClass.EXECUTION_FAILURE, FailureClass.CONVERGENCE_FAILURE):
            for node in attempt.nodes:
                if node.status == "failed" and node.tool_name:
                    tool_scope = f"{node.tool_name}:{failure_class.value}"
                    tool_recommendation = f"Échec de l'outil {node.tool_name}. {recommendation}"
                    self.lesson_store.upsert_lesson(
                        entity_type=entity_type,
                        scope=tool_scope,
                        recommendation=tool_recommendation,
                        environment=environment,
                        keywords=list(set(keywords + [node.tool_name])),
                        mission_id=mission_id,
                        polarity="avoid"
                    )
                    break


    async def _generate_prefer_lesson(self, attempt: PlanAttempt, goal: str, environment: str,
                                    mission_id: Optional[str] = None) -> None:
        """
        Génère une leçon de type 'prefer' à partir d'un succès SURVENU APRÈS UN ÉCHEC.
        Le prompt est orienté "contraste" : qu'est-ce qui a permis de débloquer la situation ?
        """
        # On utilise l'entité ciblée par la tentative (déjà posée par le code, même si c'est un succès, 
        # on hérite du contexte ou on prend "Planner" par défaut). 
        # Dans un succès, target_entity peut être None. On met un fallback sur "Planner" car c'est lui qui choisit la stratégie.
        entity_type = attempt.target_entity or "Planner"
        role_description = get_entity_role(entity_type)

        # Pour le contraste, on récupère l'erreur de la tentative précédente (si elle existe)
        # On va se baser sur le failure_reason du PlanAttempt actuel (qui est vide ici) 
        # ou on pourrait remonter l'historique. Mais comme on a passé `has_previous_failure`, 
        # on sait juste qu'il y a eu un échec. On va faire un prompt générique.
        # Idéalement, on voudrait le résumé de l'échec précédent. 
        # Pour l'instant, on lui donne juste le contexte de la réussite, et on lui demande de déduire le contraste.
        
        # On récupère le plan proposé pour cette tentative (pour voir ce qui a été tenté avec succès)
        proposed_plan_desc = json.dumps(attempt.proposed_plan, indent=2, ensure_ascii=False) if attempt.proposed_plan else "Aucun plan stocké."

        try:
            pruned = self._prune_attempt_for_llm(attempt)
            prompt = f"""
    Tu analyses une réussite INTERVENUE APRÈS UN ÉCHEC dans un moteur agentique.

    ENTITÉ RESPONSABLE DE LA STRATÉGIE GAGNANTE : {entity_type}
    RÔLE DE CETTE ENTITÉ : {role_description}

    OBJECTIF DE LA SOUS-TÂCHE : {goal}

    SÉQUENCE D'EXÉCUTION QUI A RÉUSSI (prunée) :
    {pruned}

    PLAN PROPOSÉ POUR CETTE TENTATIVE RÉUSSIE :
    {proposed_plan_desc}

    Instructions (contraste implicite avec les échecs précédents) :
    1. Identifie un mot-clé de scope STABLE et étroit qui résume la STRATÉGIE GAGNANTE à l'origine
    de ce succès (ex: 'keyboard_win_r_alternative', 'vision_based_ui_navigation').
    2. Propose 5 à 8 mots-clés LARGES et variés.
    3. Produis une règle impérative courte (1-2 phrases), adressée directement à {entity_type}, pour
    PRIVILÉGIER cette approche à l'avenir, en mentionnant en quoi elle est plus robuste que l'approche
    qui a échoué précédemment.
    """
            extracted = await asyncio.wait_for(
                self.llm.generate_structured(prompt=prompt, schema=ExtractedLesson),
                timeout=30.0
            )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords

        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon prefer : {e}")
            scope = "success_contrast_fallback"
            recommendation = "Lorsqu'une méthode échoue, privilégier une approche alternative (ex: utiliser la vision ou la souris) plutôt que de répéter la même action."
            keywords = ["alternative", "retry", "success"]

        # Upsert avec polarity='prefer'
        self.lesson_store.upsert_lesson(
            entity_type=entity_type,
            scope=scope,
            recommendation=recommendation,
            environment=environment,
            keywords=keywords,
            mission_id=mission_id,
            polarity="prefer"  # <--- explicite
        )

    def _prune_attempt_for_llm(self, attempt: PlanAttempt) -> str:
        """
        Réduit un PlanAttempt à une séquence légère pour le LLM.
        Ne garde que [step_id, description, expected_result, actual_result, error_reason].
        """
        pruned_nodes = []
        for node in attempt.nodes:
            pruned_nodes.append({
                "step_id": node.step_id,
                "description": node.description[:100],  # tronquer si très long
                "expected_result": node.expected_result,
                "actual_result": node.actual_result[:100] if node.actual_result else None,
                "error_reason": node.error_reason[:100] if node.error_reason else None,
                "status": node.status
            })
        return json.dumps(pruned_nodes, indent=2, ensure_ascii=False)

    def _generate_recommendation_fallback(self, failure_class: FailureClass, failure_reason: str) -> str:
        """
        Fallback statique en cas d'échec du LLM.
        """
        if failure_class == FailureClass.PLAN_REJECTED_VALIDATION:
            return "Le plan a été rejeté par validation statique. Vérifiez les variables utilisées, les conditions et la syntaxe du plan généré. Évitez les variables non déclarées et les opérateurs interdits dans les conditions."
        elif failure_class == FailureClass.PLAN_REJECTED_SUPERVISOR:
            return "Le plan a été rejeté par le superviseur. Assurez-vous que le plan converge vers l'objectif global et qu'il respecte les contraintes de l'environnement."
        elif failure_class == FailureClass.EXECUTION_FAILURE:
            if "tool" in failure_reason.lower():
                return "Un outil a échoué lors de l'exécution. Vérifiez les arguments passés à l'outil, la disponibilité de l'outil, ou utilisez 'any' comme expected_result pour éviter un rejet brutal."
            else:
                return "L'exécution a échoué pour une raison inconnue. Analysez les logs pour identifier l'étape problématique."
        elif failure_class == FailureClass.CONVERGENCE_FAILURE:
            return "La convergence sémantique n'a pas été atteinte. Le LLM a détecté une divergence entre le résultat attendu et le résultat réel. Reformulez l'expected_result ou utilisez des outils plus précis."
        elif failure_class == FailureClass.USER_CANCELLED:
            return "La mission a été annulée par l'utilisateur. Aucune action corrective n'est nécessaire, mais le système doit être prêt à reprendre."
        elif failure_class == FailureClass.MAX_RETRIES_REACHED:
            return "Le nombre maximal de tentatives a été atteint. Envisagez de simplifier le plan ou d'utiliser des outils plus robustes."
        else:
            return f"Échec de classe {failure_class.value}. Consultez les logs pour plus de détails."


# =====================================================
# ADVISOR (reranker LLM — remplace le RAG lexical)
# =====================================================

class Advisor:
    """
    Fournit des conseils aux entités via un reranker LLM, plus de matching lexical.

    Principe : on ne filtre plus les leçons par LIKE/seuils AVANT de les montrer — on présente
    TOUTES les leçons actives de l'environnement courant à un LLM, confidence/evidence donnés
    comme CONTEXTE de jugement, pas comme porte binaire en amont. Ça règle deux problèmes en
    même temps :
      1. Le mismatch de vocabulaire : scope='keyboard_run_dialog_focus_loss' ne partage aucun
         mot avec goal='lancer chrome', un LIKE ne les aurait jamais rapprochés.
      2. Le cercle vicieux du seuil dur : une leçon à evidence=1 n'était jamais montrée, donc
         jamais confirmée par l'usage, donc jamais promue au-dessus du seuil.

    Le seul filtre qui reste un pur SQL, jamais délégué au LLM : l'environnement. Une leçon
    'simulated' ne doit jamais apparaître dans une requête 'real' — non négociable.
    """

    def __init__(self, lesson_store: LessonStore, runtime_state, llm: Llm):
        self.lesson_store = lesson_store
        self.runtime_state = runtime_state  # B2 : seule source de vérité pour l'environnement
        self.llm = llm
        self.advice_cache: Dict[str, str] = {}  # utilisé pour le goal racine (cache Orchestrateur)

    async def prepare_advice(self, goal: str) -> None:
        """Prépare le conseil pour le goal racine (utilisé par l'Orchestrateur au lancement de mission)."""
        advice = await self._llm_rerank_advice(["Planner", "Executor"], goal)
        self.advice_cache["Planner"] = advice

    async def get_advice(self, entity_types: List[str], goal: str) -> str:
        """
        Retourne un conseil fusionné pour une liste d'entités et un goal donné.
        C'est la voie normale d'utilisation (recherche à la volée), y compris pour les
        sous-tâches déléguées à un Child Solver — chacune avec SON propre goal, pas celui
        de la mission racine (voir la discussion sur la granularité de l'injection).
        """
        return await self._llm_rerank_advice(entity_types, goal)

    async def _llm_rerank_advice(self, entity_types: List[str], goal: str) -> str:
        """
        Cœur du RAG v2 : récupère les leçons actives, les soumet à un LLM reranker avec le goal
        courant, formate uniquement celles jugées applicables — groupées par entité, pour que
        le Planner distingue clairement ses propres leçons de celles qui concernent la fiabilité
        des outils (voir ENTITY_MANIFEST : l'Executor n'a jamais la main pour changer de
        stratégie, seul le Planner peut agir sur ce type de leçon).
        """
        # Un seul flag, lu directement : plus de dev_force_injection (voir runtime_state.py).
        # "real" ici veut dire "traite cette session comme faisant confiance à la base de
        # connaissance de prod" — une décision consciente du front, pas une bascule de test
        # séparée de ce qui est réellement écrit dans les épisodes.
        effective_environment = getattr(self.runtime_state, "environment", "simulated")

        candidates = self.lesson_store.get_active_lessons(entity_types, effective_environment)
        if not candidates:
            Logger.debug(f"[Advisor] Aucune leçon active pour {entity_types} (env={effective_environment}).")
            return ""

        strictness_note = (
            "ENVIRONNEMENT RÉEL : sois strict, ne retiens que les leçons avec une confiance et un "
            "nombre de confirmations élevés — une fausse recommandation ici a un coût réel."
            if effective_environment == "real"
            else "Environnement simulé/dev : tu peux être plus exploratoire dans ta sélection."
        )

        candidates_text = "\n".join(
            f"- id={c['id']} | entité={c['entity_type']} | scope={c['scope']} | "
            f"mots-clés={c['keywords']} | confiance={c['confidence']:.2f} | "
            f"confirmations={c['evidence_count']} | contradictions={c['contradiction_count']}\n"
            f"    recommandation: {c['recommendation']}"
            for c in candidates
        )

        # --- A.2 : le raisonnement du reranker (champ "reasoning") est un log de debug lu
        # directement par le développeur pour comprendre ce qui s'est passé — rien n'imposait
        # sa langue jusqu'ici, et on l'a vu sortir en anglais une fois alors que tout le reste
        # tourne en français. Ça ne change rien pour l'utilisateur final (le reasoning ne lui
        # est jamais montré), mais ça sert directement l'objectif d'observabilité : des logs
        # illisibles à moitié dans une langue, à moitié dans une autre, ne servent à rien.
        lang = getattr(self.runtime_state, "language", "fr")

        prompt = f"""
Tu juges la pertinence de leçons apprises pour une mission à venir.

BUT DE LA MISSION : {goal}

{strictness_note}

LEÇONS CANDIDATES (issues d'échecs passés, pas forcément liées à ce but précis) :
{candidates_text}

Instructions :
Sélectionne UNIQUEMENT les leçons dont la situation d'origine est réellement susceptible de se
reproduire dans cette mission — pas un simple mot en commun, un vrai rapport sémantique.
Une liste vide est une réponse parfaitement valide si rien ne s'applique réellement.
Rédige le champ "reasoning" dans la langue de code ISO "{lang}".
"""
        try:
            reranked = await asyncio.wait_for(
                self.llm.generate_structured(prompt=prompt, schema=RerankedLessons, tag="RerankedLessons"),
                timeout=35.0  # <-- 1. Augmenté à 35s
            )
        except Exception as e:
            # --- 2. Fallback : top 3 leçons les plus confiantes ---
            Logger.error(f"[Advisor] Échec du reranker LLM : {type(e).__name__}: {e}")
            if candidates:
                fallback_selected = candidates[:3]  # déjà triées par confidence DESC
                fallback_lines = ["⚠️ Reranker indisponible (timeout/erreur). Voici les conseils les plus confiants :"]
                for c in fallback_selected:
                    fallback_lines.append(f"- {c['recommendation']}")
                Logger.warning(f"[Advisor] Fallback utilisé : {len(fallback_selected)} leçon(s) retournée(s).")
                return "\n".join(fallback_lines)
            return ""

        Logger.debug(f"[Advisor] Reranker : {reranked.reasoning}")

        selected = [c for c in candidates if c["id"] in reranked.relevant_lesson_ids]
        if not selected:
            Logger.debug(f"[Advisor] Aucune leçon retenue par le reranker pour {entity_types}.")
            return ""

        # --- NOUVEAU : Regrouper par polarité, puis par entité ---
        by_polarity: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            "avoid": {},
            "prefer": {}
        }
        for c in selected:
            polarity = c.get("polarity", "avoid")  # fallback pour les anciennes leçons
            entity = c["entity_type"]
            if entity not in by_polarity[polarity]:
                by_polarity[polarity][entity] = []
            by_polarity[polarity][entity].append(c)

        section_titles = {
            "Planner": "Planner",
            "Executor": "Executor",
            "Solver": "Solver",
            "Presentator": "Presentator",
        }
        
        blocks = []

        # 1. Bloc AVOID
        avoid_items = by_polarity.get("avoid", {})
        if avoid_items:
            avoid_lines = ["### 🚫 À éviter (Issus d'échecs)"]
            for entity, lessons in avoid_items.items():
                title = section_titles.get(entity, entity)
                avoid_lines.append(f"#### {title}")
                for l in lessons:
                    avoid_lines.append(f"- {l['recommendation']}")
            blocks.append("\n".join(avoid_lines))

        # 2. Bloc PREFER
        prefer_items = by_polarity.get("prefer", {})
        if prefer_items:
            prefer_lines = ["### ✅ À privilégier (Issus de succès après échec)"]
            for entity, lessons in prefer_items.items():
                title = section_titles.get(entity, entity)
                prefer_lines.append(f"#### {title}")
                for l in lessons:
                    prefer_lines.append(f"- {l['recommendation']}")
            blocks.append("\n".join(prefer_lines))

        if not blocks:
            return ""

        Logger.info(f"[Advisor] {len(selected)} leçon(s) retenue(s) par le reranker pour {entity_types}.")
        return "\n\n".join(blocks)

# =====================================================
# LEARNER (Entité principale)
# =====================================================

class Learner(Entity):
    """
    Entité d'apprentissage. Hérite d'Entity.
    - Analyse les épisodes non encore analysés (async).
    - Utilise le LLM pour extraire des leçons sémantiques.
    - Fournit des conseils adaptés au goal courant.
    """

    def __init__(self, name: str, mission_store: MissionStore, runtime_state,
                 llm: Optional[Llm] = None, parent: Optional[Entity] = None):
        super().__init__(name=name, role="learner", llm=llm, parent=parent)
        self.mission_store = mission_store
        self.runtime_state = runtime_state
        self.lesson_store = LessonStore()
        self.analyzer = Analyzer(self.lesson_store, self.llm)  # <-- on passe le llm
        self.advisor = Advisor(self.lesson_store, runtime_state, self.llm)  # <-- llm requis pour le reranker
        self.advice_cache: Dict[str, str] = {}

    # =====================================================
    # MÉTHODE PRINCIPALE (contrat Entity)
    # =====================================================

    async def process(self, command: str = "analyze", **kwargs) -> Any:
        """
        Point d'entrée pour les commandes du Learner.
        - command="analyze" : analyse tous les épisodes non encore analysés (kwarg force=True pour tout ré-analyser).
        - command="prepare_advice" : prépare les conseils pour un goal donné.
        """
        if command == "analyze":
            return await self.analyze_all_episodes(force=kwargs.get("force", False))
        elif command == "prepare_advice":
            goal = kwargs.get("goal")
            if goal:
                await self.prepare_advice(goal)
            return None
        else:
            raise ValueError(f"Commande learner inconnue : {command}")

    # =====================================================
    # ANALYSE (async + déduplication)
    # =====================================================

    async def analyze_all_episodes(self, force: bool = False) -> int:
        """
        Analyse tous les épisodes non encore analysés.
        Utilise MissionStore pour la déduplication.

        force=True : réinitialise analyzed_at=NULL pour TOUS les épisodes avant de commencer,
        pour permettre une ré-analyse complète après une évolution de la logique de l'Analyzer.
        Ceci doit rester une action déclenchée consciemment, jamais un comportement par défaut
        (sinon on retombe exactement dans le bug de ré-analyse silencieuse qu'on vient de fermer).
        """
        if force:
            reset_count = self.mission_store.reset_analyzed()
            Logger.info(f"[Learner] Ré-analyse forcée demandée : {reset_count} épisode(s) remis à zéro.")

        episodes = self.mission_store.get_unanalyzed_episodes()
        if not episodes:
            Logger.info("[Learner] Aucun épisode non analysé.")
            return 0

        count = 0
        for episode in episodes:
            mission_id = episode.get("mission_id")
            try:
                await self.analyzer.analyze_episode(episode)
                # Marquer comme analysé après succès
                self.mission_store.mark_analyzed(mission_id)
                count += 1
            except Exception as e:
                Logger.error(f"[Learner] Erreur analyse épisode {mission_id} : {e}")
                # On ne marque pas comme analysé pour permettre une retentative plus tard
        Logger.info(f"[Learner] Analyse terminée : {count} épisodes traités.")
        return count

    # =====================================================
    # PRÉPARATION / RÉCUPÉRATION DES CONSEILS (reranker LLM)
    # =====================================================

    async def prepare_advice(self, goal: str) -> None:
        """Prépare le conseil pour le goal racine (async : passe maintenant par un appel LLM)."""
        await self.advisor.prepare_advice(goal)
        self.advice_cache = self.advisor.advice_cache

    async def get_advice(self, entity_types: List[str], goal: str) -> str:
        """
        Retourne un conseil fusionné pour une liste d'entités et un goal donné (recherche à la
        volée, LLM-drivée). C'est la voie normale — y compris pour un but de sous-tâche, pas
        seulement le but racine de la mission.

        Note de compatibilité : accepte aussi une simple string (un seul entity_type) pour ne
        pas casser un appelant qui n'aurait pas encore été mis à jour vers la liste.
        """
        if isinstance(entity_types, str):
            entity_types = [entity_types]
        return await self.advisor.get_advice(entity_types, goal)

    # NOTE : get_production_advice() a été retiré. La distinction "seuils stricts en prod" ne
    # vit plus dans un second chemin de code parallèle (c'était une source de confusion — cf.
    # le bug où seul ce canal lisait dynamiquement l'environnement) : elle est maintenant une
    # instruction donnée AU reranker lui-même via `strictness_note` dans Advisor._llm_rerank_advice,
    # conditionnée par le même environnement effectif. Le seul filtre qui reste un pur SQL non
    # négociable est l'environnement (simulated/real) — jamais délégué au jugement du LLM.
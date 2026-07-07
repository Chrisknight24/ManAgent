"""
entity_manifest.py
===================
Registre STATIQUE des entités connues du moteur et de leur rôle.

Ce n'est pas de la connaissance apprise, c'est de la configuration déclarée
par le développeur : le nombre d'entités est petit, fixe, et connu à la
conception (en ajouter une = un changement de code, pas un événement
runtime). Ça ne vit donc pas dans une table SQLite ni dans une classe
mutable — un simple dict suffit et évite de faire passer pour "appris" ce
qui est en réalité déclaré une fois pour toutes.

Le Learner consulte ce manifeste pour donner au LLM d'analyse le contexte
organisationnel qui lui manquait ("tu analyses un échec du Planner, dont le
rôle est de...") au lieu de raisonner sur un failure_class nu, sans savoir
qui a réellement fauté ni ce que cette entité est censée faire.
"""
from typing import Optional, Dict

ENTITY_MANIFEST: Dict[str, str] = {
    "Orchestrator": (
        "Point d'entrée unique du moteur. Route chaque requête (réponse directe "
        "ou mission), crée et supervise le RootSolver, valide ou rejette les plans "
        "proposés par les Solvers (fait office de superviseur hiérarchique). "
        "Erreurs typiques : validation de plan trop stricte ou trop permissive, "
        "mauvais routage direct/mission."
    ),
    "Planner": (
        "Traduit une stratégie en plan structuré (liste de PlanStep : tool_call, "
        "abstract_task, direct_answer), déclare les variables de sortie "
        "(output_variable_name) et les conditions d'exécution (execute_if). "
        "Erreurs typiques : variables utilisées mais jamais déclarées, conditions "
        "syntaxiquement invalides, tool_args_json malformé, plan sans étapes."
    ),
    "Executor": (
        "Exécute un plan validé étape par étape : appelle les outils, vérifie la "
        "convergence (rigide pour un tool_call, sémantique via LLM pour un "
        "abstract_task ou direct_answer), délègue les abstract_task à un Child "
        "Solver. Erreurs typiques : retour d'outil invalide ou inattendu, "
        "divergence entre le résultat réel et le résultat attendu."
    ),
    "Solver": (
        "Orchestre la boucle réflexion → plan → exécution → replanification pour "
        "un objectif donné (racine ou sous-tâche déléguée), gère les tentatives "
        "successives (max_tries) et le feedback d'échec injecté au Planner. "
        "Erreurs typiques : épuisement des tentatives sans qu'aucune n'ait "
        "convergé."
    ),
    "Presentator": (
        "Rédige, en langage naturel, le rapport final présenté à l'utilisateur "
        "(succès ou échec), à partir du contexte accumulé de la mission. "
        "Erreurs typiques : échec de génération du rapport, ton ou niveau de "
        "détail inadapté au contexte (dev vs production)."
    ),
}


def get_entity_role(entity_type: Optional[str]) -> str:
    """
    Retourne la description du rôle d'une entité, ou une phrase neutre si
    l'entité n'est pas (encore) répertoriée — ne jamais lever d'exception ici,
    le manifeste est un enrichissement du prompt, pas une validation bloquante.
    """
    if not entity_type:
        return "Entité non identifiée avec certitude."
    return ENTITY_MANIFEST.get(
        entity_type,
        f"Entité '{entity_type}' non documentée dans le manifeste (à ajouter dans core/entity_manifest.py)."
    )
"""
id_generator.py
================
Générateur d'identifiants d'étapes/solvers courts, lisibles et GARANTIS uniques
pour toute la durée du process.

CONTEXTE DU FIX :
Avant ce module, l'unicité des IDs était obtenue par concaténation de la
lignée complète (parent_id + step_id) à chaque niveau de récursion. Deux
conséquences en prod :
  1. Un bug de double-préfixage entre Solver.run() et
     Executor._handle_abstract_task() faisait dupliquer l'ID du solver
     parent à chaque appel récursif -> IDs de plusieurs centaines de
     caractères passé 4-5 niveaux de profondeur.
  2. Comme l'ID du solver ne change pas entre deux tentatives de
     replanification (current_try 0/1/2), un Planner qui régénère les
     mêmes noms d'étapes génériques ("step_1", "step_2"...) produisait le
     MÊME step.id d'une tentative à l'autre -> collisions côté TreeWidget
     C++ (un essai raté et le suivant partagent le même Id d'item).

PRINCIPE DU FIX :
L'unicité ne dépend plus JAMAIS de la lignée (parent, profondeur, tentative).
Elle vient uniquement d'un compteur global, tiré une fois par étape générée,
quel que soit l'endroit du code qui la génère. Même logique que celle déjà
utilisée pour les tool calls (uuid.uuid4() dans Orchestrator.execute_tool),
en plus court/lisible puisqu'ici on reste dans un seul process Python
séquentiel (une mission à la fois), pas besoin de l'espace UUID complet.

itertools.count().__next__() est atomique : dans une boucle asyncio
mono-thread, il n'y a pas de section critique à protéger tant qu'aucun
'await' ne s'intercale entre la lecture et l'incrémentation (ce qui est le
cas ici, l'appel est synchrone).
"""
import itertools

_counter = itertools.count(1)


def next_unique_suffix() -> str:
    """Retourne un entier strictement croissant, unique pour toute la durée du process."""
    return f"{next(_counter):05d}"


def make_step_id(local_id: str) -> str:
    """
    Construit un ID d'étape court et garanti unique.

    Ne dépend NI du solver parent, NI de la profondeur de récursion,
    NI du numéro de tentative : l'unicité vient exclusivement du compteur.
    Le nom local (fourni par le Planner, ex: "step_1", "step_save_file")
    est conservé en préfixe pour garder les logs lisibles.

    Exemples :
        make_step_id("step_1")        -> "step_1-00001"
        make_step_id("step_1")        -> "step_1-00002"   (même nom, ID différent)
        make_step_id("step_save_file")-> "step_save_file-00003"
    """
    safe_local = (local_id or "step").strip()
    return f"{safe_local}-{next_unique_suffix()}"
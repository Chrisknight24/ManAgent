# Chantier PD / routeur de scope — note de cadrage (2026-11-04)

> Statut : **annoté, non commencé**. Les phases 0→5 du plan du 04/11 sont
> exécutées d'abord ; ce chantier vient après. Aucun code PD n'est modifié
> par les phases 0→5.

## 1. Le problème constaté

Aujourd'hui, une entité qui utilise la Progressive Disclosure (planner,
solver, etc.) reçoit **avant même sa mission** une section de prompt PD
auto-injectée, lourde et divertissante :

- la règle de découverte,
- la liste des scopes,
- les cibles disponibles,
- les objectifs techniques,
- les formats attendus.

La plupart du temps, l'entité ne fera **aucune** requête PD. Ce bruit
arrive avant l'identité et la mission, et peut dégrader la qualité de la
réponse principale. Le routeur de scope (`investigate`) existe déjà mais
est désactivé par défaut (`MANAGENT_SCOPE_ROUTER`), et le planner n'active
sa PD qu'au retry (`PLANNER_PD_ENABLED=False`).

## 2. L'idée validée : une entité-pont sémantique

Créer un **pont** entre l'entité et le système, dans lequel l'entité peut
s'exprimer en langage naturel avant de répondre :

> « Tu as une mission. Parfois, il te manque des informations ou du
> contexte. Le système est là et peut te répondre sur les scopes suivants :
> `world`, `registry`, `history`, etc. Tout ce que tu as à faire, c'est ne
> pas répondre directement et faire un *discovery request* en langage
> naturel, en sélectionnant ton scope. Au prochain tour, tu auras les
> informations et tu pourras continuer. »

Le routeur (LLM ou déterministe) prend cette requête en langage naturel et
la route vers le DataProvider approprié, ce qui réactive l'explorer de plan
et la Progressive Disclosure **comme aujourd'hui**. Mais pour l'entité
principale, la section PD massive disparaît : il ne reste qu'une phrase de
contrat.

## 3. Effets attendus

- Le solver pourrait **pré-analyser le monde** (voire pré-produire des
  cibles si l'analyse passe par `perceive_understand` et que la perception
  produit une cible) **avant** de fournir son `refined_strategy`.
- Le planner ne planifierait plus un `perceive_understand` + `direct_answer`
  pour « regarder avant de répondre » : il ferait d'abord une interrogation
  de contexte, puis produirait son plan.
- La masse du prompt PD diminuerait fortement, au profit de la mission.

## 4. Contraintes

- **Agnostique** : le pont ne connaît ni `cell`, ni `grid`, ni `UIA`, ni
  `pixels`. Il ne connaît que les schémas d'outils et les contrats de cibles
  déclarés par l'hôte.
- **Lecture seule** : le pont ne dispatche aucune action ; il interroge des
  DataProviders.
- **Refus honnête** : « aucun scope ne peut répondre » est une réponse
  normale, jamais un rejet de plan.
- **Pas de régression** : flag off = comportement actuel strictement
  inchangé.
- **Observabilité** : tracer chaque `scope_route` (question, providers
  choisis, refus, taille de la réponse, tokens).

## 5. Périmètre technique probable (à confirmer au démarrage)

- `core/llm.py` : réduire l'injection `discovery_injection.md` à un contrat
  court quand le routeur est actif.
- `core/scope_router.py` + `prompts/*/scope_router.md` : routeur NL existant,
  à étendre si nécessaire.
- `tools/internal_tools.py` (`investigate`) : point d'entrée unique.
- `core/planner.py` : gate `PLANNER_PD_ENABLED`, activation au premier tour
  si le contrat court est utilisé.
- `core/discovery/providers/world_provider.py` : `answer` passe par
  `get_world_state` (texte). Si l'expérience montre un besoin réel, le
  manifeste hôte pourra déclarer une source de perception monde
  (`metadata.world_perception.source_tool`), sans nom d'outil en dur.
- `prompts/{base,en,fr}/discovery_injection.md` : version courte.

## 6. Protocole d'évaluation

1. Run témoin avec flags off (comportement actuel).
2. Run avec `MANAGENT_SCOPE_ROUTER=1` seul.
3. Run avec `MANAGENT_SCOPE_ROUTER=1` + `PLANNER_PD_ENABLED=True`.
4. Mesurer : nombre de `scope_route`, taux de réponses vs refus, tokens
   d'entrée/sortie, part du prompt PD, usage réel de `investigate`, et
   qualité de la mission (succès/échec, nombre de tentatives).

**Critère de décision** : on garde le flag seulement si les événements
`scope_route` montrent un usage réel et un gain de tokens sans perte de
qualité. Sinon, on laisse off et on documente pourquoi.

## 7. Ce qui n'est pas dans ce chantier

- Aucune modification du code CondiFlow : la lettre `brain_to_host.md` reste
  le canal.
- Aucune réparation automatique `label` → `output_target_name`.
- Aucun outil dédié aux pixels.
- Aucune connaissance hôte en dur dans ManAgent.

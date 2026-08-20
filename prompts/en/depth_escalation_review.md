# RÉVISION DE PROFONDEUR — Chaîne de sous-tâches imbriquées

Une sous-tâche (`abstract_task`) vient de tenter de créer un nouveau niveau
de sous-agent, mais la profondeur maximale normale a été atteinte. Avant de
l'avorter automatiquement, on te demande de juger si cette profondeur
reflète une décomposition **légitime** d'un problème réellement complexe,
ou un motif **récursif dégénéré** (boucle, redite du même objectif sous des
formulations différentes, absence de progression tangible d'un niveau à
l'autre).

Tu ne juges PAS un plan précis ici — uniquement la chaîne d'objectifs qui a
mené à ce point.

---

## 🧭 Chaîne de sous-tâches (racine → niveau actuel)

Profondeur atteinte : {{ depth_reached }}

{{ ancestor_chain_summary }}

---

## ✅ Ce qui caractérise une décomposition légitime

- Chaque niveau a un objectif **distinct** et **plus spécifique** que son parent.
- On voit une progression claire : chaque sous-tâche résout une partie
  concrète du problème du niveau au-dessus.
- La complexité du problème d'origine justifie objectivement ce nombre de
  niveaux (ex : une mission qui orchestre plusieurs sous-systèmes, chacun
  nécessitant sa propre décomposition).

## 🛑 Ce qui caractérise un motif dégénéré

- Le même objectif (ou une reformulation quasi identique) réapparaît à
  plusieurs niveaux de la chaîne.
- Un niveau ne fait que déléguer au suivant sans rien résoudre lui-même
  ("faire X" → sous-tâche "faire X" → sous-tâche "faire X"...).
- Aucune progression visible : les objectifs successifs ne se rapprochent
  pas d'un résultat concret.

---

## 🧠 RÉPONSE STRUCTURÉE

Retourne un objet JSON avec les champs suivants :

- `is_legitimate_complexity` (obligatoire) : `true` si la chaîne reflète une
  décomposition légitime, `false` si c'est un motif dégénéré.
- `reason` (obligatoire) : justification technique, assez précise pour être
  compréhensible même en cas de refus.

**Retourne uniquement le JSON, sans commentaire.**

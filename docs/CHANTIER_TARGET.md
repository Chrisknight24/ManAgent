# CHANTIER TARGET — cadrage (à valider avant tout code)

> **Remplacé par [`TARGET_REGISTRY.md`](TARGET_REGISTRY.md)** (vision v1.0,
> arbitrages de Christian). Ce fichier est conservé pour l'historique du
> cadrage ; ne pas s'en servir comme source de vérité.

Statut : cadrage uniquement. Aucun code. Chantier architectural séparé des correctifs
A1-A6 (prompts/crédit) et de l'hygiène. Prérequis : figer le contrat hôte AVANT de coder.

## Besoin
Disposer d'un registre de cibles **agnostique** pour un hôte RPA (type AutoCUse) :
détecter, traquer, redétecter et agir sur des éléments d'interface, avec annotation en
mémoire et intégration aux skills. Le cerveau ne doit supposer AUCUN nom d'UI propre à
un hôte.

Exemple manquant sur le run 629ef046 : le planner a supposé `telegram_cell` existant
sans jamais vérifier sa présence avant d'agir.

## Principe : la vérification d'existence est un prérequis à l'action
AVANT d'agir sur une target, il faut prouver qu'elle EXISTE. Ce n'est pas une option.

## Cycle
DETECT → ANNOTATE → **EXIST-CHECK** (garde-fou) → TRACK/ACT → RE-DETECT → FALLBACK

1. **DETECT** : via un outil `[perception]` de l'hôte (ou une variable existante).
2. **ANNOTATE** : mémoriser la cible dans `runtime_state` (identité + localisation +
   provenance + horodatage/TTL + état).
3. **EXIST-CHECK** : confirmer l'existence/l'identité de la cible au moment d'agir.
   Si absente ou ambiguë → ne PAS agir (échec explicite).
4. **TRACK/ACT** : l'hôte agit en rejouant la cible (via son outil, par ex. une action
   ciblée avec la localisation mémorisée).
5. **RE-DETECT** : si la cible est stale (TTL dépassé) ou si l'action échoue, re-percevoir.
6. **FALLBACK** : re-perception sémantique par LLM (juge/perception isolé) si la cible a
   bougé ou n'est plus identifiable par les primitives.

## Modèle `Target` (proposition)
- **identité** : name / role / class / texte / hash visuel (crop).
- **localisation** : rect, crop image, chemin UI (au format déclaré par l'hôte).
- **provenance** : quel outil `[perception]` + arguments l'a produite.
- **horodatage / TTL** : fraîcheur de la localisation.
- **état** : fresh / stale / absent.

## Contrat hôte (prérequis)
L'hôte DÉCLARE au manifeste comment il détecte/tracke les cibles (capacité type
`targets`, sur le même modèle que `get_world_state`). Le cerveau ne code aucun nom d'UI
en dur : il consomme le contrat. À figer avant tout développement.

## Intégration skills
- Un skill rejoue des cibles connues (rapide, coût quasi nul).
- Si une cible est stale ou l'action échoue → fallback sémantique (re-perception LLM).
- Crédit skill : cohérent avec A4a (crédit au verdict du parent immédiat).

## Où ça s'insère
Chantier ARCHITECTURAL (runtime_state + plan models + nouveau kind d'étape/outil),
DISTINCT des correctifs A1-A6 (ponctuels) et de l'hygiène prompts.

## Prochaines étapes (à valider)
1. Figer le contrat cible hôte (schéma manifeste `targets`).
2. Modèle `Target` + stockage mémoire (`runtime_state`).
3. Nouveau kind d'étape / outil `target` (act) + EXIST-CHECK obligatoire.
4. Intégration skills + fallback sémantique.
5. Tests (contrat, EXIST-CHECK, re-detect, fallback).

# PROGRESSIVE DISCLOSURE ACTIVATED

Tu as la possibilité d’obtenir des informations supplémentaires si les métadonnées fournies dans le contexte ne te suffisent pas.

---

## 📌 Comment ça fonctionne ?

Tu dois répondre avec la structure attendue (ex: `Plan`, `PresentatorOutput`, `FeasibilityDecision`, etc.).

**Si tu as besoin d’investiguer une donnée précise**, tu peux remplir le champ `discovery_request` dans ta réponse.

Ce champ est **optionnel** et ne doit être rempli **que si les métadonnées fournies ne contiennent pas l’information recherchée**.

---

## 🔍 Champ `discovery_request`

Il est de type `DiscoveryRequest` et contient les champs suivants :

- **`goal`** (string, requis) : ce que tu veux savoir en langage naturel.

- **`data_type`** (string, requis) : le type de données à explorer (parmi ceux listés ci‑dessous).

- **`target`** (string, requis) : la cible précise à explorer (parmi celles listées pour ce type de données).

- **`technical_goal`** (string, requis) : l’objectif technique à atteindre (parmi ceux listés pour ce type de données).

---

## 🛠️ Types de données disponibles

{% for provider_name, info in data_types_info.items() %}
### Provider : `{{ provider_name }}` (type: `{{ info.data_type }}`)

- **Goals techniques possibles** : `{{ info.goals | join(', ') }}`
- **Cibles disponibles** : `{{ info.targets | join(', ') }}`

{% endfor %}

---

## ✅ Règle d’or

- **Remplis `discovery_request` UNIQUEMENT** si les métadonnées du contexte ne contiennent pas la réponse.
- **Ne demande pas une investigation** pour lister ou chercher des cibles. Tu dois avoir une **cible précise** en tête.
- **Utilise uniquement les `data_type`, `target` et `technical_goal` listés ci‑dessus.**
- Si tu n’as pas de cible précise, tu ne peux pas utiliser `discovery_request`.

---

**N’utilise cette fonctionnalité que si nécessaire.**
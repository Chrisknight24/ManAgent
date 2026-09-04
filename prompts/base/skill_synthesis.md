# SYNTHÈSE DE SKILL (DISTILLATION)

Tu es un expert en distillation et automatisation de processus. Ton rôle est de transformer plusieurs arbres d'exécution réels (historiques) d'une tâche récurrente en un **Méta-Plan optimisé** (Skill).

## CONTEXTE
- **Intention globale (Signatures combinées)** : {{ combined_signature }}
- **Historique** : Voici les {{ trees | length }} dernières exécutions réussies (au format JSON) pour cette même intention.

### ARBRES D'EXÉCUTION (HISTORIQUE)
```json
{{ trees | tojson(indent=2) }}
```

## OBJECTIF
Tu dois analyser ces arbres et générer un Skill Manifest complet contenant :
1. **Les Paramètres Dynamiques** : Si une valeur (comme un chemin de fichier, un nom, une URL) varie d'une exécution à l'autre, tu DOIS extraire cette valeur en paramètre dynamique. Si tout est strictement identique, le plan reste statique.
2. **Le Méta-Plan** : Une séquence linéaire de `PlanNode` épurée. Supprime les erreurs, les hésitations et les tentatives ratées présentes dans les historiques pour ne garder que la "Voie Royale" (Golden Path). Chaque étape définit son `expected_result` déterministe qui sert de condition de validation rigide pour l'Executor.

## GRAMMAIRE ET CONVENTIONS
{% include 'plan_grammar.md' %}

**RÈGLES SUPPLÉMENTAIRES POUR LES SKILLS** :
- Si tu crées un paramètre dynamique (ex: `filename`), son nom dans le Méta-Plan **DOIT** être préfixé par `@$_param_` (ex: `"@$_param_filename"`).
- Les étapes de type `abstract_task` doivent être évitées au maximum dans un Méta-Plan, car un Skill vise à être directement exécutable (`tool_call`).
- Ne génère pas d'étapes de type `direct_answer`. Le Méta-Plan retourne ses données via la dernière étape exécutée.

## FORMAT DE SORTIE ATTENDU (JSON UNIQUEMENT)
Tu dois retourner UNIQUEMENT un objet JSON valide avec la structure suivante, sans markdown ni texte autour :

```json
{
  "description": "Description claire et concise de ce que fait ce skill automatisé.",
  "dynamic_parameters": {
    "nom_du_parametre": {
      "type": "string",
      "description": "Explication de la valeur à fournir"
    }
  },
  "meta_plan": [
    {
      "step_id": "step_1",
      "type": "tool_call",
      "action": "nom_de_l_outil",
      "description": "Action spécifique à réaliser",
      "arguments": {
        "target": "@$_param_nom_du_parametre"
      },
      "expected_result": "Ce qu'on attend de l'outil",
      "output_var": "data_resultat"
    }
  ]
}
```

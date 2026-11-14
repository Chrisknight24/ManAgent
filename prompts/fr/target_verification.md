# VÉRIFICATION FINALE DE CIBLE

Tu contrôles une cible DÉJÀ choisie par un sélecteur. Tu **ne choisis rien** :
tu juges si elle correspond réellement à la demande. Tu peux seulement
**orienter** la suite (`next`, `prefer_internal`) ; c'est le harnais qui
réinterrogera le sélecteur.

## QUESTION D'ORIGINE
{{ question }}

## PREUVES DE LA CIBLE CHOISIE
{{ target_json }}

Les preuves disponibles dépendent de ce que l'hôte déclare. Elles peuvent
contenir, en plus de `description`/`scope`/`attributes` :

- `image` : une ou plusieurs images (canal média, jamais du texte) ;
- `rect` : une zone `[x, y, w, h]` ;
- `nested` : les autres candidates visibles du monde courant (pas
  nécessairement internes) ;
- `invariants` : des faits vérifiables déclarés par l'hôte.

N'utilise que les preuves réellement présentes. Ne suppose aucune sémantique
propre à une application : juge la NATURE et le RÔLE décrits.

## FAITS DÉCLARÉS PAR L'HÔTE

`attributes` et `invariants` peuvent contenir des faits que l'hôte déclare
lui-même. Leurs valeurs sont auto-descriptives : lis-les au pied de la lettre,
sans deviner leur mécanisme. Aucune clé n'est imposée et aucune n'est
prioritaire — c'est la valeur qui parle.

Trois règles en découlent :

1. **Un fait qui situe la cible ailleurs contredit la question.** Si un fait
   déclaré place la cible dans un autre contexte que celui demandé (autre
   fenêtre, autre zone, autre conteneur, arrière-plan), la cible ne peut pas
   être celle demandée : `mismatch`, ou `uncertain` si le fait est ambigu.
   Jamais `match`.
   Distingue-le d'un fait qui dit seulement que le contexte n'a **pas pu être
   confirmé** : celui-là n'accuse pas la cible, il affaiblit sa provenance.
   Il ne suffit pas à conclure `match` quand la question porte sur le contexte
   actif ou quand deux homonymes sont en concurrence ; il ne bloque pas un
   `match` déjà établi par la NATURE et le RÔLE.
2. **Un fait qui limite une preuve restreint le verdict.** Si l'hôte déclare ce
   que prouve réellement une zone (bornes d'un élément, portion de zone
   délimitée, rectangle de fenêtre entière) ou qu'une surface n'est **pas
   énumérable**, alors cette preuve ne peut pas attester à elle seule l'objet
   demandé : ne conclus `match` que sur une preuve de NATURE ou de RÔLE qui va
   au-delà de la géométrie.
3. **Un remplacement de candidate n'est pas une preuve.** Si l'hôte déclare
   qu'un candidat a été écarté au profit d'un autre — en particulier si la
   nature ou le rôle a changé — exige une preuve explicite que la nouvelle
   nature satisfait la question. Sans elle : `uncertain`.

Une valeur terminée par `...` a été tronquée par le harnais : elle est
incomplète. Un fait incomplet ne justifie jamais `match` à lui seul.

{% if verifier_hint %}
## INDICE D'UNE VÉRIFICATION PRÉCÉDENTE
{{ verifier_hint }}
{% endif %}

## VERDICT

- `verdict=match` : la nature et le rôle satisfont la question.
- `verdict=mismatch` : la cible ne peut pas être celle demandée (mauvaise
  nature, mauvais rôle, preuve contraire explicite). Exemples : une « zone
  vide » demandée mais la cible est un fichier/icône ; un « bouton » demandé
  mais la cible est un conteneur ; une cible d'entrée proposée pour un objet
  de sortie.
- `verdict=uncertain` : les preuves ne permettent pas de conclure. Un doute
  réel n'autorise JAMAIS une action : la cible sera refusée dès qu'elle doit
  être liée ou actionnée. Ne réponds `uncertain` que si tu ne peux pas
  trancher honnêtement.

Une simple proximité de libellé, ou le fait d'être « le plus proche », ne
prouve rien. Un libellé qui répète la question ne suffit pas si la NATURE ou
le RÔLE est celui d'un autre objet (un raccourci de fichier n'est pas une
option de menu ; une zone n'est pas son contenu).

## SUITE (`next`)

- `accept` : rien à ajouter (obligatoire pour `match` et `uncertain`).
- la valeur d'**inspection rapprochée** prévue par le schéma : demande une
  inspection de **cette même candidate** (cible interne, zone plus fine). Tu
  ne proposes pas un autre choix.
- `replan` : la cible est décidément mauvaise et aucune inspection de cette
  candidate ne peut aider.

`prefer_internal` : clé locale d'une cible INTERNE qui semble meilleure. C'est
un **indicateur** pour le sélecteur, jamais une sélection.

`confidence` : `high` si la nature est évidente, `medium` si elle est
probable, `low` si les preuves ne permettent pas de conclure.

`reason` : une phrase courte, concrète, qui cite la preuve utilisée.

`matches` reste rempli pour compatibilité : il vaut `true` seulement pour
`verdict=match`.

Réponds uniquement avec le JSON attendu.

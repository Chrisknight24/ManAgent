# ManAgent — TargetRegistry
## Vision architecturale et base de reprise pour l’agent de codage

**Version :** 1.0  
**Date :** 20 octobre 2026  
**Statut :** vision architecturale consolidée à partir des arbitrages de Christian. Le document fixe les principes et le comportement attendu ; il ne prétend pas connaître le code actuel ni imposer prématurément la forme exacte du protocole.

> Nom de fichier dépôt : `docs/TARGET_REGISTRY.md` (nom d'origine : `ManAgent_TargetRegistry_Vision_Architecturale_v1.0.md`).


## 0. Mission de ce document

TargetRegistry doit permettre à ManAgent de **réutiliser des cibles du monde de l’hôte sans inventer leur référence, sans agir à l’aveugle et sans devoir percevoir à nouveau le monde à chaque étape**.

Ce document est destiné à l’agent de codage ManAgent. Il doit s’en approprier les principes, les confronter au code existant, puis proposer une conception technique compatible avec le runtime réel. Les noms de champs, les enveloppes JSON, les classes et les transitions détaillées restent à déterminer après inspection du code et du manifeste.

Le périmètre est générique : computer use, robot, automate ou tout hôte capable de déclarer des cibles de son monde. Les exemples d’interface Windows ne sont que des illustrations.

### Principes déjà arbitrés par Christian

- Une entrée du registre représente **une cible précise déjà trouvée**. Elle ne représente pas une intention abstraite qui pourrait être automatiquement rattachée à n’importe quelle cible ressemblante.
- Le nom local de la cible, sa description et le contexte de la demande peuvent être exposés au Solver et au Planner. La référence hôte, elle, reste opaque et cachée au LLM.
- La cible peut être découverte à différents moments : pendant une consultation du monde via Progressive Disclosure, pendant la préparation de la stratégie, pendant la préparation du plan ou à l’exécution d’une étape `perceive`.
- **Détecter n’est pas obligatoire à chaque action.** Si une cible pertinente est déjà présente dans le registre et affichée dans le contexte du Planner, celui-ci peut choisir son nom local dans une étape d’action.
- Lorsqu’une perception produit explicitement une cible retenue, le runtime l’enregistre automatiquement. Les observations et candidates intermédiaires ne deviennent pas toutes des entrées durables de mission.
- L’hôte est propriétaire de la validité opérationnelle, de l’invalidation et du rafraîchissement de ses cibles. Il peut mettre à jour ou invalider le registre du cerveau à travers un mécanisme déclaré.
- Une invalidation ne supprime pas l’entrée. Elle la désactive en interne ; son état activé/désactivé n’est pas exposé au LLM dans la vue normale du registre.
- Si une action dépendante d’une cible échoue, ManAgent ne remplace pas automatiquement la cible et ne retente pas localement l’action. L’échec remonte au Solver, qui réévalue la mission avec un état du monde actualisé.
- Les outils hôte normalisés continuent à retourner un booléen de succès/échec et un `error_reason` optionnel. La spec n’impose pas une nouvelle taxonomie universelle d’erreurs.
- Les cibles sont partagées au niveau du `runtime_state` de la mission, sans persistance opérationnelle après la fin de celle-ci.
- La découverte de capacités et les comportements de Progressive Disclosure doivent rester agnostiques : aucun nom de cible, d’application, d’OS ou d’outil hôte ne doit être codé en dur dans le cerveau.

### Registre des arbitrages intégrés

Cette table fait foi sur les décisions de cette version. Elle remplace les valeurs provisoires ou contradictoires des dossiers d’arbitrage précédents.

| Référence | Décision intégrée | Statut |
|---|---|---|
| D01 | Une entrée/slot représente une cible précise déjà trouvée ; l’intention de découverte lui est attachée. | **Décidé : A** |
| D02 | Toute voie de perception peut enregistrer automatiquement une cible explicitement extraite et sélectionnée. Les observations et candidates intermédiaires ne sont pas enregistrées en bloc. | **Décision reformulée par Christian** |
| D03 | Le cerveau peut formuler une requête en langage naturel ; seule une sortie de l’hôte peut fournir la référence finale. | **Décidé : B** |
| D04 | L’hôte possède la validité et le rafraîchissement. Il peut pousser les mises à jour/invalidation ; le cerveau peut demander le refresh ciblé si la capacité existe. | **Décidé sur le principe** |
| D05 | Pas de remplacement automatique par une cible similaire. Une nouvelle sélection passe par une réévaluation du Solver. | **Décidé** |
| D06 | Les outils conservent le résultat booléen et le champ `error_reason` optionnel. Pas de nouvelle taxonomie globale obligatoire. | **Décidé** |
| D07 | Après échec d’action ciblée, remonter au Solver ; pas de retry local automatique. | **Décidé : B** |
| D08 | L’hôte déclare ses scopes ; chaque action déclare les scopes qu’elle accepte. | **Décidé : B** |
| D09 | Choisir entre fonctions génériques imposées et outils hôte déclarés par rôle après inspection du code/manifeste. | **Délégué à l’agent de codage** |
| D10 | Vue compacte du registre, cibles récentes/pertinentes en premier, autres entrées récupérables via Progressive Disclosure. | **Décidé : C** |
| D11 | Registre unique partagé dans `runtime_state`, avec mutations contrôlées. | **Décidé : B** |
| D12 | Si l’hôte redémarre, ne pas supposer les anciennes références valides ; invalider/rafraîchir ou laisser l’action échouer. Le save/restore est optionnel si ses garanties sont suffisantes. | **Principe décidé ; mécanisme délégué** |
| D13 | Faire confiance au manifeste en production ; tester les déclarations en développement/intégration, pas par des actions-sondes en production. | **Décidé : A** |
| D14 | Le Solver/Planner voit les noms locaux et descriptions des cibles connues et peut les réutiliser directement dans le plan ; l’Executor les résout en références hôte. | **Décidé : A, avec référence cachée** |
| D15 | Intégrer la découverte itérative à la protection Progressive Disclosure déjà existante, sans inventer de plafond fixe arbitraire. | **Délégué à l’agent de codage** |


## 1. Le problème à résoudre

Le cerveau de ManAgent peut raisonner correctement sur une mission, mais une action peut tout de même partir sans cible opérationnelle fiable : cliquer sur une position où le bouton n’existe plus, saisir du texte alors que le focus est ailleurs, utiliser une cellule de tableau imaginée ou agir sur un objet qui a disparu.

La règle de provenance existante — une référence hôte ne doit pas être inventée — est nécessaire, mais ne suffit pas. Il manque un cycle de vie partagé : découverte, enregistrement, réutilisation, rafraîchissement ou invalidation, puis résolution au moment d’une action.

TargetRegistry ne doit pas devenir un second modèle complet du monde. Il doit conserver juste assez d’informations pour que le cerveau puisse nommer une cible déjà trouvée, choisir si elle sert à la mission et demander son utilisation à l’hôte. L’hôte reste la source de vérité opérationnelle.

### Résultat attendu

Lorsqu’une étape demande d’agir sur une cible :

1. si une cible appropriée est déjà connue, le plan peut la réutiliser par son nom local ;
2. sinon, le plan ou le raisonnement préalable peut demander une perception en langage naturel ;
3. la couche de perception utilise les capacités de l’hôte, au besoin de manière itérative, et produit une cible structurée si elle en trouve une ;
4. le runtime vérifie que cette cible provient réellement d’une sortie autorisée de l’hôte et l’enregistre ;
5. l’Executor transforme le nom local en référence hôte à l’intérieur du runtime, au dernier moment ;
6. l’action hôte vérifie elle-même que cette référence peut être utilisée et retourne le résultat ;
7. si l’action échoue, le Solver reprend le raisonnement au lieu que l’Executor change discrètement de cible.


## 2. Vocabulaire commun — sans multiplier les abstractions

Pour éviter que « intention », « alias » et « slot » désignent trois choses différentes selon le composant, ce document utilise les termes suivants. L’agent de codage peut mapper ces termes vers les noms déjà présents dans le code.

### 2.1 Entrée de cible

Une **entrée de cible** est un enregistrement du registre qui correspond à une cible particulière déjà trouvée. Elle possède un nom local lisible par le cerveau, une courte description, un scope déclaré par l’hôte, une date d’extraction et, à l’intérieur du runtime seulement, la référence opaque et les informations nécessaires à son cycle de vie.

Le nom peut être par exemple `fenetre_notepad_premier_plan` ou `icone_demarrer`. Ces noms sont des exemples, pas une convention obligatoire. Le nom doit être unique dans le contexte concerné et ne doit jamais être confondu avec la référence hôte.

### 2.2 Nom local de cible (alias)

Le nom local est le handle que le Solver ou le Planner peut utiliser dans son raisonnement et dans un plan. Il désigne **une seule entrée de cible**. Il n’est ni une donnée du monde hôte ni une référence que l’on peut transmettre directement à une action hôte.

Le runtime est responsable de la génération ou de la validation de ce nom, de son unicité et de sa résolution. Le LLM ne doit pas pouvoir inventer un nom et le faire accepter comme une cible existante.

### 2.3 Référence hôte opaque

La référence hôte est la valeur réellement comprise par l’hôte pour agir sur la cible. Elle peut être une chaîne, un nombre, un objet JSON ou une autre forme compatible avec le protocole. Le cerveau ne la parse pas, ne la décrit pas, ne la reconstruit pas et ne la demande pas au LLM de la recopier.

Le runtime la conserve et l’injecte mécaniquement au moment du dispatch. Le format exact est décidé par le contrat de l’hôte.

### 2.4 Scope de cible

Un **scope** est une catégorie déclarée par l’hôte qui indique la nature ou le mode de désignation d’une cible et aide à vérifier sa compatibilité avec une action. Un hôte computer use peut déclarer des scopes tels que `UI`, `IMAGE`, `TEXT` ou une zone ; un robot peut déclarer des objets, des points, des volumes ou des repères. Ces noms sont des exemples : ManAgent n’impose pas ces catégories comme ontologie universelle.

### 2.5 Requête, observation et cible trouvée

- Une **requête** est une demande en langage naturel, par exemple « trouver la croix de fermeture de la fenêtre Bloc-notes au premier plan ».
- Une **observation** est une donnée du monde, par exemple une capture d’écran, une arborescence UI ou une mesure de capteur.
- Une **candidate** est une possibilité intermédiaire retournée pendant la recherche.
- Une **cible trouvée** est une candidate sélectionnée et associée à une référence hôte réelle, fournie par une sortie autorisée et compatible avec le contrat.

Une description en langage naturel, une image annotée ou une hypothèse du LLM ne constituent pas, à elles seules, une référence opérationnelle.

### 2.6 Une cible par entrée, mais une action peut en utiliser plusieurs

L’entrée de registre correspond à **une seule cible précise**. L’intention ou le but de découverte associé à cette entrée décrit pourquoi cette cible a été recherchée, sans transformer l’entrée en un ensemble de cibles interchangeables.

Une action peut exiger plusieurs cibles, par exemple un point de départ et un point d’arrivée pour déplacer un objet. Dans ce cas, elle consomme plusieurs entrées distinctes. Cela ne signifie pas qu’une seule entrée représente plusieurs cibles.


## 3. Architecture générale et responsabilités

### 3.1 Le cerveau ManAgent

Le cerveau raisonne sur la mission, les capacités déclarées, l’état du monde disponible et la liste de cibles connue. Il décide si une perception est nécessaire ou si une cible connue suffit. Il formule les demandes de perception en langage naturel et construit les étapes de plan.

Il ne doit pas décider lui-même qu’une référence hôte est valide maintenant. Il ne doit pas reconstruire une référence à partir d’une description, ni considérer une cible ressemblante comme la même cible.

### 3.2 La pile `perceive`

`perceive` est une capacité interne du cerveau qui analyse les données retournées par des outils et peut demander d’autres observations via Progressive Disclosure. Elle peut servir à comprendre l’état du monde, à répondre à une question ou à extraire une cible.

Elle n’est pas un outil hôte. Elle peut utiliser différents outils de perception de l’hôte, selon le manifeste. Si elle choisit explicitement d’extraire une cible, sa réponse structurée doit référencer une candidate réellement fournie par les données de l’hôte. Le runtime enregistre automatiquement cette cible.

### 3.3 `perceive_action`

`perceive_action` est une opération interne composite qui perçoit puis déclenche une action. Elle reste utile lorsqu’une acquisition et une action immédiate forment un même geste. Elle ne remplace pas le registre.

Si `perceive_action` produit une cible destinée à être réutilisée après l’opération composite, sa sortie doit déclarer cette cible de manière exploitable afin que le runtime puisse l’enregistrer. Si sa cible reste strictement locale au geste et n’est pas exposée comme sortie réutilisable, le runtime ne doit pas prétendre qu’elle est enregistrée.

### 3.4 Le registre de cibles

Le registre est un service interne à la mission, situé dans le `runtime_state` partagé. Il conserve les entrées, les références hôte opaques, leur provenance et leur état opérationnel interne. Il offre une vue compacte aux entités de raisonnement et une API contrôlée pour la résolution, les mises à jour et les invalidations.

Le registre ne constitue pas une vérité autonome sur le monde. Il contient les dernières informations connues, dont certaines peuvent être invalidées par l’hôte.

### 3.5 L’Executor et le point de résolution

L’Executor prend le plan structuré et exécute ses étapes. Pour une action qui consomme une cible, il résout le nom local de l’entrée dans le registre, vérifie que l’entrée existe et que son scope correspond à ce que le manifeste déclare, puis injecte la référence hôte sans la faire passer par le LLM.

Toutes les voies d’appel — Solver enfant, skill, action directe ou opération composite — doivent respecter le même principe. Aucune branche ne doit permettre à une action déclarée comme dépendante d’une cible de recevoir une référence inventée ou un nom local non résolu.

### 3.6 L’hôte

L’hôte déclare les scopes de cibles qu’il comprend, les capacités permettant d’observer et de produire des cibles, les paramètres d’action qui consomment ces scopes, ainsi que les mécanismes de rafraîchissement ou d’invalidation disponibles.

Il est le seul propriétaire de la validité opérationnelle. Il vérifie la référence au moment de l’action et peut pousser des mises à jour vers le cerveau. Le cerveau ne doit pas reproduire en Python la hiérarchie interne des objets, fenêtres, widgets, pièces ou capteurs de l’hôte.


## 4. Le moment de la découverte : elle n’est pas obligatoirement dans une étape du plan

La découverte d’une cible peut avoir lieu par plusieurs voies. Elles doivent alimenter le même registre et respecter la même provenance.

### Voie A — Pendant le raisonnement préalable du Solver

Le Solver peut, via Progressive Disclosure, consulter l’état du monde et certaines capacités de perception avant de rédiger sa stratégie détaillée. S’il trouve et sélectionne une cible utile, cette cible peut être enregistrée avant la planification.

### Voie B — Pendant le raisonnement préalable du Planner

Le Planner peut également consulter via Progressive Disclosure le registre, l’état du monde ou une capacité de perception si cela l’aide à produire un plan plus précis. Si cette analyse produit une cible explicite, le runtime l’enregistre suivant les mêmes règles.

### Voie C — Pendant une étape `perceive` du plan

C’est le cas classique lorsque le Solver prévoit une étape qui doit chercher une cible sans avoir besoin de connaître sa référence à l’avance. L’Executor lance `perceive` avec une requête en langage naturel ; la pile de perception explore les capacités disponibles et extrait une cible si elle en trouve une.

### Voie D — À l’intérieur de `perceive_action`

L’opération composite peut découvrir une cible avant de l’utiliser immédiatement. Une cible ne devient une entrée réutilisable du registre que si la sortie composite expose explicitement le résultat de cette découverte.

### Règle commune

**Tout chemin de perception peut enregistrer une cible ; aucun chemin n’est obligé de le faire.** L’enregistrement automatique intervient lorsqu’une sortie structurée contient explicitement une cible sélectionnée et que le runtime peut vérifier que sa référence vient d’une donnée réelle de l’hôte.

Les résultats intermédiaires de recherche restent locaux à l’analyse. Une observation générale ne remplit pas automatiquement le registre de toutes les choses qu’elle semble contenir.


## 5. Scénario détaillé : ouvrir le Bloc-notes et interagir avec sa fenêtre

Ce scénario illustre le fonctionnement, sans imposer les mêmes outils à tous les hôtes.

### Situation initiale

L’utilisateur demande : « Ouvre le Bloc-notes. » Le snapshot disponible indique, par exemple, que la fenêtre au premier plan est une fenêtre Qt et qu’un champ d’édition a le focus. Le manifeste déclare des actions clavier, des capacités de perception et des scopes de cibles compatibles avec certaines actions souris/clavier.

### Stratégie possible du Solver

1. Envoyer le raccourci `Win+R` via une action clavier qui ne nécessite pas de cible.
2. Percevoir le dialogue ouvert, trouver son champ de saisie, puis saisir `notepad` et valider. Le Solver peut représenter cette étape par `perceive_action`, ou par une perception suivie d’une action distincte, selon les capacités déclarées.
3. Percevoir le monde jusqu’à établir que la fenêtre Bloc-notes est apparue. La cible « fenêtre Bloc-notes ouverte par cette mission » peut alors être enregistrée.
4. Vérifier que l’objectif d’ouverture est atteint.

Le Solver ne doit pas coder en dur le nom d’un outil hôte précis. Il décrit la stratégie à partir des capacités du manifeste.

### Ce qui se passe pendant la recherche du champ de saisie

La requête en langage naturel peut dire : « trouver le champ d’édition du dialogue Exécuter pour y saisir notepad ». L’analyse commence avec la donnée disponible. Si elle ne suffit pas, elle peut demander plus de détails via Progressive Disclosure : par exemple une vue agrandie d’une région, les éléments UI d’une zone, une recherche textuelle ou une autre capacité que l’hôte a réellement déclarée.

Les noms de ces capacités viennent du manifeste. Le cerveau ne doit pas supposer que chaque hôte possède un outil `ZOOM_CELL`, `SHOW_UIS_IN_CELL` ou tout autre nom particulier.

Une fois le champ identifié, la sortie de perception sélectionne une référence réellement fournie par l’hôte. Le runtime crée une entrée telle que `champ_saisie_dialogue_executer`, associe une description courte et conserve la référence opaque. Si la prochaine action doit immédiatement écrire dans ce champ, l’étape peut utiliser ce nom local.

### Interagir plus tard avec la fenêtre

Une autre mission ou étape demande : « clique sur la croix pour fermer le Bloc-notes au premier plan ».

- Si le registre expose déjà `fenetre_notepad_premier_plan` ou `bouton_fermeture_notepad` et que le Planner juge la cible appropriée, le plan peut utiliser ce nom local dans l’étape d’action : aucune nouvelle perception n’est nécessaire uniquement pour retrouver une cible déjà connue.
- Si la cible n’est pas connue, le plan peut demander une étape `perceive` ou le Solver/Planner peut faire cette découverte via Progressive Disclosure avant que le plan soit finalisé.
- Si la référence est invalidée entre-temps, l’hôte rejette l’action. Le runtime ne cherche pas automatiquement une autre croix ; l’échec remonte au Solver, qui décide quoi faire après une mise à jour du contexte.
- Une perception postérieure peut vérifier que la fenêtre a effectivement disparu. La réussite de l’appel souris ne suffit pas à prouver que l’objectif global est atteint.


## 6. Modèle minimal d’une entrée de cible

Le modèle de données exact reste à adapter au runtime. Conceptuellement, une entrée doit permettre au cerveau de la nommer et à l’hôte de la mettre à jour sans dévoiler sa référence au LLM.

| Information conceptuelle | Visible au Solver/Planner ? | Rôle |
|---|---|---|
| Nom local de cible | Oui | Permet de choisir la cible dans un plan. Exemple : `fenetre_notepad_premier_plan`. |
| Description courte | Oui | Décrit la cible en une ligne pour faciliter le choix. |
| Scope déclaré par l’hôte | Oui, lorsqu’utile à la compatibilité | Permet de choisir une action capable de consommer cette cible. |
| Heure/date d’extraction | Oui | Donne un repère sur le moment où la cible a été trouvée. Ce n’est pas une preuve de validité. |
| Intention ou requête de découverte associée | Peut être exposée si utile | Explique pourquoi cette cible précise a été recherchée. Cela ne transforme pas l’entrée en cible substituable. |
| Référence hôte opaque | **Non** | Valeur technique utilisée uniquement par le runtime lors du dispatch. |
| Provenance technique | En principe non | Permet au runtime de vérifier que l’entrée provient d’une sortie autorisée et d’auditer la découverte. |
| Identifiant/corrélation interne hôte | Non | Permet de rattacher les futurs événements de mise à jour à cette entrée. Sa forme est propre au contrat. |
| État interne d’activation/invalidation | **Non** | Le runtime l’utilise pour appliquer les updates et empêcher l’usage local d’une entrée explicitement désactivée. Il n’est pas ajouté à la vue normale du LLM. |
| Version/révision des updates | Non | Permet d’ignorer un événement ancien si un événement plus récent a déjà été appliqué. |
| Heure du dernier rafraîchissement | Pas nécessairement | Métadonnée de runtime utile à l’audit ou au diagnostic. La date d’extraction initiale ne doit pas être réécrite comme si la cible venait d’être détectée de nouveau. |

### Contraintes du modèle

1. Une entrée correspond à une seule cible précise.
2. Le nom local est unique dans sa portée et stable pour cette entrée pendant la mission.
3. Le nom local ne contient pas la référence hôte et ne doit pas être accepté comme référence par l’hôte.
4. La description facilite le raisonnement ; elle ne remplace pas la référence.
5. L’état interne peut être mis à jour sans exposer au LLM la raison technique de l’invalidation.
6. Une référence mise à jour ne peut rester rattachée à la même entrée que si l’hôte garantit qu’il s’agit de la même cible logique. Sinon, l’entrée précédente reste invalidée et une nouvelle cible doit être enregistrée séparément.
7. Les entrées et leurs références opérationnelles appartiennent à la mission. Elles ne sont pas offertes comme mémoire de cibles persistante aux missions suivantes.


## 7. Le registre partagé de mission

Le registre doit résider dans le `runtime_state` partagé par l’arbre de solvers, et non dans un snapshot indépendant de variables de chaque sous-solver.

### Pourquoi un registre partagé ?

Un sous-solver peut découvrir une cible et un autre l’utiliser plus tard. Si chaque sous-solver possède sa copie, une mise à jour ou une invalidation peut ne pas parvenir à toutes les branches. Cela créerait exactement le problème que le registre doit résoudre : une branche qui croit encore connaître une cible dont la référence a déjà changé.

### Règles de partage

- Tous les solvers concernés consultent la même source de registre de mission.
- Les sous-solvers peuvent recevoir une vue ou un snapshot de prompt, mais les mutations réelles sont centralisées. Une vieille vue ne peut pas écraser silencieusement une mise à jour récente.
- Le registre est encapsulé derrière une API ou un service logique plutôt que traité comme un dictionnaire librement modifiable partout.
- Les variables de plan ou de skill peuvent transporter le nom local de cible ; elles ne doivent pas transporter des copies de références hôte.
- Toute écriture, invalidation, réactivation ou mise à jour passe par le même chemin contrôlé.
- Si des opérations asynchrones sont concurrentes, les mises à jour doivent être sérialisées ou versionnées de façon à ce qu’un événement ancien ne remplace pas une version plus récente.

Le détail du mécanisme de concurrence doit être choisi après inspection du runtime réel. L’invariant, lui, est fixe : **une seule vérité interne de registre par mission, mise à jour de manière contrôlée**.


## 8. Contrat hôte : scopes, producteurs, consommateurs et suivi

ManAgent ne doit pas déduire le sens d’une cible du nom d’un outil. Il doit l’apprendre du manifeste et des capacités explicitement déclarées.

### 8.1 Scopes déclarés par l’hôte

L’hôte déclare les scopes de cibles qu’il sait produire et/ou consommer, avec une description suffisamment claire pour les prompts. Ces scopes peuvent être `UI`, `IMAGE`, `TEXT`, `REGION`, `OBJECT`, un point, un repère ou d’autres valeurs propres à l’hôte. Les exemples ne sont pas normatifs.

Les descriptions de scopes sont disponibles dynamiquement pour les entités qui en ont besoin. Le cerveau n’impose pas un vocabulaire universel de cibles, mais il comprend que deux scopes ne sont compatibles que si le contrat le déclare.

### 8.2 Capacités productrices

Une capacité productrice permet d’obtenir une candidate ou une cible exploitable à partir d’une perception ou d’une résolution. Le manifeste doit indiquer, de manière structurée, où se trouve la sortie de cible, son scope et les informations dont le runtime a besoin pour transporter la référence sans l’interpréter.

Une capacité qui ne retourne qu’une image, un arbre descriptif ou du texte ne devient pas automatiquement productrice de références de cible. Si le LLM décrit le bouton mais qu’aucun moyen de produire une désignation consommable n’existe, TargetRegistry ne peut pas inventer la référence manquante.

### 8.3 Capacités consommatrices

Une action déclare les paramètres qui acceptent une cible et les scopes compatibles avec chacun de ces paramètres. Les paramètres de cible doivent être identifiables structurellement dans le manifeste ; le cerveau ne doit pas déduire qu’un paramètre est une cible simplement parce qu’il s’appelle `id`, `point` ou `element`.

**Orientation recommandée :** lorsque l’action agit sémantiquement sur une cible, privilégier un paramètre typé cible, par exemple `clic(cible_UI)`, plutôt qu’une paire de coordonnées librement interprétées. Cela pousse naturellement Solver et Planner à découvrir ou réutiliser une cible, et permet à l’hôte de vérifier sa validité.

Ce principe n’interdit pas les actions en coordonnées pour les cas où une commande géométrique directe est réellement la bonne abstraction. Il demande de ne pas remplacer une cible sémantique par des coordonnées parce que c’est plus simple à exposer alors que l’hôte sait traiter la cible.

### 8.4 Compatibilité producteur-consommateur

Le runtime doit pouvoir vérifier qu’une cible de scope X peut être passée à une action qui accepte X, ou à une combinaison explicitement compatible. Si aucune correspondance n’est déclarée, il refuse le dispatch au lieu d’essayer par intuition.

Les paramètres d’une action qui nécessitent plusieurs cibles doivent être représentés comme plusieurs entrées/liaisons distinctes. L’absence d’une cible obligatoire ne doit pas se transformer silencieusement en coordonnées inventées ni en action partielle.

### 8.5 Déterminisme et garanties

Le déterminisme d’une action n’implique pas que le monde reste inchangé entre la perception et l’usage. Il faut distinguer :

- la capacité à identifier une cible sans ambiguïté ;
- la validité de la référence au moment de son utilisation ;
- la certitude quant à l’effet de l’action.

L’hôte peut fournir ses propres garanties de suivi, de renouvellement ou de validation à l’usage. ManAgent les consomme si elles sont déclarées. Il ne doit jamais déduire une garantie de suivi simplement du champ existant `effects = deterministic`.

### 8.6 Noms d’outils et contrat commun : décision technique laissée à l’agent de codage

Le cerveau ne doit coder aucun nom d’outil hôte particulier. En revanche, une découverte réellement agnostique nécessite un petit contrat de métadonnées commun permettant de reconnaître les rôles « produit une cible », « consomme une cible », « met à jour/invalide une cible » et, le cas échéant, « peut être interrogé pour rafraîchir une cible ».

Il n’est pas décidé ici si l’hôte doit réimplémenter un jeu de fonctions génériques imposées par ManAgent, ou s’il doit pouvoir déclarer des outils arbitraires qui remplissent ces rôles. **L’agent de codage doit trancher après inspection du manifeste, du `tool_manager`, de Progressive Disclosure et du protocole existant.** La préférence architecturale est de standardiser les rôles et la structure déclarative, pas les noms d’outils.


## 9. Le suivi des cibles appartient à l’hôte

Le cerveau ne doit pas reproduire la logique de l’hôte pour savoir quels objets, contrôles, régions ou pièces dépendent les uns des autres.

### Modèle hôte recommandé

L’hôte peut maintenir sa propre banque de cibles et une représentation des relations qu’il sait connaître : par exemple un parent et ses éléments enfants, une fenêtre et ses contrôles, un objet et les repères qui lui sont attachés, ou une cellule de travail et les éléments qu’elle contient.

Quand une action modifie le monde, l’hôte peut savoir quelles cibles doivent être rafraîchies, invalidées ou réactivées. Par exemple, la fermeture d’une fenêtre peut invalider son handle et les références de contrôles enfants, sans qu’il soit nécessaire de tester chaque référence une par une depuis le cerveau.

Cette ontologie interne est une stratégie d’implémentation hôte, pas une structure universelle que ManAgent impose à tous les domaines. Un hôte qui n’a pas de relation parent/enfant peut utiliser une autre méthode, à condition de respecter le contrat qu’il déclare.

### Ce que ManAgent doit offrir

Le runtime doit pouvoir :

1. recevoir un événement hôte signalant qu’une entrée est rafraîchie, invalidée ou réactivée ;
2. retrouver l’entrée correspondante sans exposer sa référence au LLM ;
3. mettre à jour la référence opaque ou l’état interne par le service de registre ;
4. préserver la date d’extraction initiale, tout en pouvant tracer séparément un rafraîchissement ;
5. demander, lorsqu’une capacité le permet, le rafraîchissement ciblé d’une entrée précise par son nom local ;
6. éviter qu’un événement ancien ne remplace une mise à jour plus récente.

### La question technique de corrélation

Puisque l’hôte doit pousser des updates après l’enregistrement, le runtime et l’hôte ont besoin d’un moyen fiable de savoir **quelle entrée de registre** un événement concerne. Le LLM ne doit pas assurer cette correspondance.

L’agent de codage doit déterminer, à partir des sorties actuelles, si cette corrélation peut s’appuyer sur un identifiant de suivi opaque fourni par l’hôte, une liaison enregistrée auprès de l’hôte, une référence stable ou un autre mécanisme. Si la référence hôte peut changer après un rafraîchissement, l’événement doit pouvoir viser l’entrée correcte sans exiger que le LLM recopie l’ancienne référence.

Les événements doivent être associés à la mission/session appropriée et pouvoir être ordonnés ou versionnés afin qu’un vieux message n’écrase pas un état plus récent. La forme JSON exacte appartient à l’agent de codage, après inspection du protocole JSONL actuel.


## 10. Rafraîchissement, invalidation et réactivation

### 10.1 L’hôte décide

Le runtime n’invente pas de durée de validité universelle. Il ne décrète pas qu’une cible a expiré parce qu’elle est ancienne ou parce qu’une action a eu lieu. L’hôte a la connaissance nécessaire pour décider quelles références restent utilisables et quand les actualiser.

L’heure d’extraction fournie au LLM est informative. Elle n’est pas un TTL et ne donne pas au cerveau l’autorité de déclarer une cible périmée.

### 10.2 Mise à jour vers la même cible

Si l’hôte sait qu’une référence actualisée désigne toujours la même cible précise, il peut demander au runtime de mettre à jour la référence hôte dans l’entrée existante tout en conservant son nom local. La continuité doit reposer sur les garanties de l’hôte, pas sur la seule similitude de description.

### 10.3 Invalidation sans suppression

Lorsqu’il invalide une cible, l’hôte n’a pas à supprimer son entrée du registre. Le runtime marque l’entrée comme désactivée dans son état interne et conserve ses métadonnées utiles. Cette entrée ne devient pas pour autant une référence valide.

**Le LLM ne voit pas le drapeau activé/désactivé.** La vue normale du registre expose le nom local, la description courte, le scope si utile et l’heure d’extraction, sans révéler l’état interne. L’objectif est de conserver une représentation simple du registre dans les prompts. Le contrôle opérationnel appartient à l’Executor/runtime et à l’hôte.

Si le Planner choisit une entrée qui a été invalidée en interne, le runtime ne la rebinde pas discrètement vers une autre cible. La résolution/l’action échoue de manière honnête et remonte au Solver.

### 10.4 Réactivation

Une entrée désactivée peut être réactivée seulement lorsque l’hôte peut établir que la cible précise correspondante est de nouveau utilisable. Si une nouvelle cible ressemblante apparaît mais que l’identité/continuité n’est pas garantie, elle doit être enregistrée comme une nouvelle cible plutôt que réactiver silencieusement l’ancienne.

Le comportement exact de réactivation et la forme d’un événement correspondant doivent être déterminés par le contrat hôte. Le cerveau ne les déduit pas depuis une ressemblance de description.

### 10.5 Demande explicite de rafraîchissement

Le runtime doit pouvoir demander un rafraîchissement pour une entrée précise, si le manifeste déclare cette capacité. La requête utilise le nom local au niveau cerveau ; le runtime le traduit vers l’identifiant de corrélation attendu par l’hôte. Il n’expose jamais la référence hôte au LLM.

Le rafraîchissement demandé n’est pas une autorisation d’utiliser une cible différente. Si la cible n’existe plus, la réponse doit être une invalidation/échec honnête ou une nouvelle cible séparée, selon le contrat.


## 11. Vue du registre dans les prompts et Progressive Disclosure

Le registre doit être une connaissance importante des prompts Solver et Planner, mais il ne doit pas transformer chaque prompt en inventaire complet de tout ce que la mission a rencontré.

### Vue normale recommandée

Pour chaque entrée présentée, la vue visible peut contenir :

- nom local de cible ;
- description en une ligne ;
- scope/catégorie de l’hôte si nécessaire pour choisir une action compatible ;
- heure d’extraction.

Elle ne contient jamais la référence hôte, les identifiants techniques cachés ni le booléen d’activation/invalidation.

### Ordre et volume

- Présenter d’abord les cibles les plus récentes et celles qui sont déjà pertinentes d’après le contexte disponible.
- Ne pas confondre limite d’affichage et limite du registre : une cible masquée n’est pas supprimée et doit rester consultable via Progressive Disclosure.
- Ne pas imposer un plafond fixe arbitraire qui fait perdre définitivement de vue les entrées plus anciennes.
- Si une partie du registre est masquée, le système doit permettre de demander les entrées pertinentes ou l’inventaire complet, et ne pas laisser entendre que la vue condensée est exhaustive.
- Un classifieur rapide ou un mécanisme de ranking pourra améliorer plus tard la sélection des cibles à exposer, mais il ne fait pas partie des prérequis de la v1.

### Doctrine destinée au Solver et au Planner

Les prompts doivent enseigner les principes suivants sans mentionner des outils hôte spécifiques :

1. Lorsqu’une action a besoin d’une cible et qu’une entrée connue semble correspondre à la cible précise voulue, préférer la réutilisation du nom local plutôt qu’une nouvelle perception inutile.
2. Si aucune cible appropriée n’est connue, une perception en langage naturel peut être demandée avant ou pendant l’exécution du plan.
3. Le nom local peut être utilisé dans le plan ; il n’est pas la référence hôte.
4. Ne jamais inventer un nom local et ne jamais construire une référence hôte.
5. Préférer les actions structurées qui consomment les scopes de cibles déclarés par l’hôte lorsqu’elles conviennent au besoin.
6. Si une action échoue sur une cible, ne pas choisir automatiquement une cible similaire ; remonter au Solver.

La doctrine doit rester agnostique. Les scopes, les capacités disponibles, leurs descriptions et leurs contraintes sont injectés depuis le manifeste via le mécanisme existant de Progressive Disclosure.


## 12. La découverte progressive dans `perceive`

La boucle interne de perception doit être capable de commencer avec une observation peu précise et d’affiner sa compréhension en demandant davantage de données ou en utilisant d’autres capacités hôte disponibles.

### Exemple computer use

Une capture d’écran générale est quadrillée ou annotée, mais les cellules sont trop grandes pour identifier la croix d’une fenêtre. Le LLM peut demander une vue plus détaillée de la région, une liste d’éléments UI dans cette région, une lecture de texte ou une autre capacité décrite dans le manifeste. Il continue si les nouvelles données permettent d’avancer vers l’identification de la cible.

### Exemple robot

Une caméra voit plusieurs pièces qui se ressemblent. Le LLM peut demander une autre vue, un capteur de profondeur, l’énumération des objets connus ou une mesure plus précise, si ces capacités sont déclarées. Le but est d’établir laquelle des pièces correspond à la demande, pas de choisir au hasard parmi des descriptions similaires.

### Règles d’architecture

- Les capacités consultées viennent de Progressive Disclosure et du manifeste ; aucun nom tel que `ZOOM_CELL` ou `SHOW_UIS_IN_CELL` n’est codé en dur dans le cerveau.
- Les observations et candidates intermédiaires restent locales à la recherche. Seule une cible explicitement sélectionnée dans une sortie structurée du processus de perception est proposée à l’enregistrement automatique.
- Une sortie structurée ne peut choisir que des candidates réelles fournies au runtime par les outils. Une référence inventée ou une clé candidate inconnue est rejetée avant l’enregistrement.
- Un résultat qui ne fournit qu’une localisation descriptive n’est pas une cible ancrée tant qu’aucun moyen déclaré ne transforme cette information en désignation que l’action sait consommer.
- La boucle peut être composée de plusieurs outils et tours LLM. Sa stratégie d’arrêt doit réutiliser les mécanismes de prévention de récursion et de requêtes répétitives déjà présents dans Progressive Disclosure, plutôt que créer un deuxième système isolé sans inspection du code.
- L’agent de codage est chargé de décider comment les garde-fous actuels (blocage des requêtes répétées, budget d’exécution et éventuel LLM de contrôle final) s’intègrent au cycle cible. Il ne doit ni imposer un nombre arbitraire de tours sans justification, ni autoriser une boucle infinie.

La limite de la boucle de découverte n’est pas un retry d’action. La découverte cherche à obtenir une cible avant l’action ; la récupération après échec d’action est traitée à la section suivante.


## 13. Exécution d’une action sur une cible connue

Pour une étape qui contient un nom local de cible :

1. L’Executor reconnaît, à partir du contrat du manifeste, que le paramètre concerné consomme une cible.
2. Il résout le nom local dans le registre partagé ; une entrée inconnue ou non enregistrée ne produit pas de référence.
3. Il vérifie que le scope de la cible est compatible avec le paramètre d’action déclaré. Il ne tente pas de deviner une conversion.
4. Il récupère la référence hôte opaque à partir de l’entrée interne et l’injecte mécaniquement dans l’appel. Le LLM ne voit pas la valeur et ne la recopie pas.
5. L’hôte arbitre la référence au moment de l’action. Il doit agir sur la cible désignée ou rejeter la demande, jamais agir silencieusement sur une cible ressemblante.
6. L’outil hôte retourne son booléen standard de succès/échec et, s’il possède cette information, un `error_reason`.
7. Si l’action échoue, l’Executor ne change pas de cible et ne retente pas automatiquement. Il retourne l’échec au Solver, avec l’information disponible.
8. Le Solver réévalue l’état du monde et la stratégie. Il peut conclure que l’objectif est déjà atteint, planifier une nouvelle perception, choisir une stratégie différente ou signaler l’impossibilité.

### Vérification de l’objectif

Un succès technique de l’action ne prouve pas que l’objectif métier est atteint. Après un clic, une perception peut établir que la fenêtre a disparu ; après une saisie, la lecture peut confirmer que le texte est présent ; après une prise robotique, un capteur peut confirmer que l’objet est saisi. Convergence s’appuie sur ces preuves selon l’architecture générale de ManAgent.


## 14. Politique d’échec : retour au Solver, pas de substitution implicite

La politique par défaut est simple : **si une action qui dépend d’une cible échoue, son échec remonte au Solver**.

ManAgent ne lance pas automatiquement une seconde recherche locale et ne retente pas le clic sur une nouvelle cible. Cette règle évite qu’une erreur locale change silencieusement l’objectif de la mission.

### Exemple : fermeture du Bloc-notes

Le Solver avait planifié de cliquer sur la cible `bouton_fermeture_notepad`. Au moment du dispatch, l’hôte indique que la référence n’est plus utilisable. Le bouton a peut-être disparu parce que l’utilisateur a fermé la fenêtre, parce qu’elle a changé ou parce que la cible n’est plus visible. Le runtime ne cherche pas une autre croix et ne tente rien d’autre. Il rapporte l’échec et rafraîchit le contexte disponible. Le Solver examine le nouvel état avant de décider de la suite.

### Exemple : prise d’un objet

Le robot ne peut plus saisir la cible enregistrée. Le cerveau n’attribue pas automatiquement la même action à l’objet le plus ressemblant. Le Solver réévalue la perception et les conditions de sécurité.

### Résultats de l’hôte

Les outils hôte gardent le contrat normalisé actuel : booléen succès/échec, avec `error_reason` optionnel. Cette vision ne demande pas de modifier toutes les actions pour qu’elles retournent une nouvelle taxonomie globale.

Si l’hôte fournit une raison, elle doit être transmise au raisonnement et aux journaux. Si aucune raison n’est disponible, l’échec reste un échec : ManAgent ne l’invente pas. Dans tous les cas, il n’y a pas de retry automatique local après échec d’une action ciblée.


## 15. Redémarrage de l’hôte et fin de mission

### Redémarrage de l’hôte

Le redémarrage de l’hôte en cours de mission est complexe, mais il ne doit pas forcer le cerveau à prétendre que les anciennes références restent valides. Selon les capacités existantes, l’hôte peut signaler un changement de session, invalider les entrées affectées, vider la liaison opérationnelle ou laisser l’action rapporter honnêtement qu’une référence ne peut plus être utilisée.

Le comportement par défaut doit être prudent : aucune référence ne reste présumée utilisable uniquement parce que sa valeur textuelle est identique après redémarrage. La sauvegarde/restauration est optionnelle et ne peut rétablir une cible qu’avec une garantie d’identité suffisante. L’agent de codage peut recommander le mécanisme adapté après examen du protocole et des besoins du host.

### Fin de mission

À la fin ou à l’annulation de la mission, le registre opérationnel de cibles est purgé et aucune référence ne peut être réutilisée dans une mission ultérieure. La mémoire durable éventuelle de ManAgent peut conserver des résumés ou des journaux selon sa politique, mais elle ne conserve pas des références hôte encore actionnables.


## 16. Dégradation gracieuse

TargetRegistry est une capacité ajoutée, non un prérequis au fonctionnement général du cerveau.

- **Aucun scope de cible déclaré :** les prompts n’inventent pas de scopes. Le runtime conserve les chemins historiques qui restent compatibles ; les nouvelles garanties ne sont pas annoncées.
- **Perception disponible, mais aucune sortie de cible exploitable :** le LLM peut comprendre et décrire le monde, mais le runtime ne prétend pas qu’une cible actionnable a été ancrée. Il peut utiliser un chemin composite existant s’il est réellement disponible, sinon il échoue honnêtement.
- **Cible produite, action consommatrice absente :** la cible peut être informative, mais elle ne peut pas être passée à une action qui n’accepte pas ce scope. Le plan doit utiliser une autre capacité existante ou s’arrêter.
- **Action consommatrice déclarée sans producteur compatible :** le manifeste doit donner lieu à un diagnostic de configuration en développement. En fonctionnement, le cerveau ne doit pas inventer la capacité manquante.
- **Rafraîchissement/push d’événements absent :** les autres capacités peuvent continuer. L’hôte reste responsable du contrôle au moment de l’action ; après un échec, le Solver reprend le raisonnement. Le cerveau ne fait pas semblant de bénéficier d’un suivi proactif qui n’est pas déclaré.
- **Registre indisponible :** les missions non dépendantes du registre doivent continuer ; les actions qui exigent une cible enregistrée ne contournent pas la règle en envoyant un alias comme référence hôte.

Si le manifeste ne déclare aucune capacité cible, TargetRegistry doit être inerte et la compatibilité historique doit être conservée autant que possible. Aucun mécanisme ne doit forcer toutes les missions à appeler `perceive`.


## 17. Sécurité et invariants d’agnosticité

Les lois suivantes doivent rester vraies indépendamment de l’OS, de l’application, du robot ou des noms choisis par l’hôte.

1. **Provenance :** toute référence hôte utilisée par une action est issue d’une sortie réelle de l’hôte autorisée dans le contexte de mission.
2. **Opacité :** le LLM ne voit pas la référence hôte ; il ne la parse pas et ne la reconstruit pas.
3. **Noms distincts :** le nom local du cerveau n’est pas une référence hôte. Le runtime est le seul à effectuer cette correspondance.
4. **Une entrée, une cible :** une entrée de registre représente une cible précise. Les actions qui nécessitent plusieurs cibles utilisent plusieurs entrées.
5. **Enregistrement automatique contrôlé :** toute perception peut produire des cibles ; seules les cibles explicitement sélectionnées et vérifiées par le runtime sont enregistrées. Les candidates intermédiaires ne sont pas toutes promues automatiquement.
6. **Compatibilité par contrat :** le scope produit et le scope consommé sont compatibles selon les déclarations du manifeste, pas selon une supposition du LLM.
7. **Validité appartenant à l’hôte :** le cerveau ne déduit pas qu’une référence est encore valide depuis son âge, sa description ou le succès d’une validation précédente.
8. **Arbitrage au dispatch :** l’hôte vérifie la cible au moment de l’action et n’agit pas sur un substitut silencieux.
9. **Aucune substitution automatique :** une cible invalidée n’est pas remplacée automatiquement par une cible ressemblante.
10. **Pas de retry local d’action ciblée :** l’échec remonte au Solver, qui décide après réévaluation.
11. **État caché :** le statut interne activé/désactivé ne figure pas dans la vue normale de la cible envoyée au LLM.
12. **Mises à jour fiables :** un événement cible doit être corrélé à la bonne entrée et ne doit pas écraser une révision plus récente.
13. **Registre partagé :** tous les sous-solvers utilisent la même source de registre de mission.
14. **Mission-scoped :** les références opérationnelles sont purgées à la fin de mission.
15. **Progressive Disclosure :** le système peut limiter ce qui est présenté dans un prompt, mais ne doit pas perdre les entrées stockées ni confondre absence dans la vue avec inexistence.
16. **Agnosticité :** aucun nom d’application, d’OS ou d’outil hôte n’est codé en dur dans le cerveau pour piloter la découverte ou la consommation de cibles.
17. **Dégradation honnête :** une capacité absente ne produit pas une garantie imaginaire ; les chemins compatibles continuent sans TargetRegistry lorsque c’est possible.
18. **Succès de mission distinct du succès d’action :** une action acceptée n’est pas la preuve que l’objectif final est atteint ; Convergence examine les preuves de résultat.


## 18. Tests d’intégration recommandés

Les tests de conformité actifs doivent être faits en développement/intégration, pas en production sur un environnement potentiellement réel. En production, le runtime peut valider la structure du manifeste et signaler des incohérences, mais ne doit pas lancer une action réelle juste pour vérifier qu’une déclaration est vraie.

### T1 — Cible connue, action directe

Pré-remplir le registre avec une cible valide et rendre son alias visible au Planner. Vérifier que le plan peut utiliser l’alias dans une action compatible sans ajouter une perception obligatoire. Le runtime résout l’alias et injecte la référence cachée.

### T2 — Cible inconnue, découverte par perception

Lancer une mission sans cible correspondante dans le registre. `perceive` exploite des capacités déclarées, sélectionne une sortie de cible réelle et le runtime l’enregistre automatiquement. L’action suivante peut utiliser son alias.

### T3 — Découverte pré-planification

Faire extraire une cible au Solver ou au Planner au moyen de Progressive Disclosure avant le plan final. Vérifier que l’entrée apparaît ensuite dans la vue du registre et que le plan peut la réutiliser.

### T4 — Candidate inventée

Faire retourner par le LLM une clé candidate qui n’existe pas dans le résultat réel. Le runtime doit refuser l’enregistrement ; aucune action hôte ne part avec cette valeur.

### T5 — Référence opaque difficile

Employer des références avec espaces, guillemets, texte ressemblant à une instruction, caractères inhabituels et longueurs variées. Vérifier que le LLM ne les voit jamais et que le runtime les transporte comme des valeurs opaques selon le protocole.

### T6 — Compatibilité de scope

Présenter une cible de scope non accepté par l’action. Le Plan Validator ou l’Executor rejette l’invocation avant le dispatch ; il n’essaie pas de convertir la cible de lui-même.

### T7 — Invalidation parent/enfant

Dans un hôte qui possède une hiérarchie, invalider un parent. Vérifier que l’hôte pousse les invalidations nécessaires des enfants et que le registre interne est actualisé sans supprimer les entrées. Cette mise en œuvre est spécifique à cet hôte ; le contrat général se limite à la fidélité des événements.

### T8 — Mise à jour suivie

Le host refresh une référence en garantissant qu’elle désigne la même cible précise. Vérifier que le même nom local reste utilisable, que la référence est remplacée en interne et que l’historique/horodatage d’extraction reste cohérent.

### T9 — Nouvelle cible ressemblante

L’ancienne fenêtre disparaît et une autre fenêtre similaire apparaît. Sans garantie d’identité, vérifier que l’ancienne entrée n’est pas réactivée silencieusement pour désigner la nouvelle fenêtre.

### T10 — Échec d’action

Faire échouer une action ciblée à cause d’une référence périmée. Vérifier que l’Executor ne relance pas localement l’action et ne sélectionne pas une autre cible. Le Solver reçoit l’échec et réévalue le monde.

### T11 — Refresh push et demande ciblée

Simuler une mise à jour et une invalidation initiées par l’hôte. Vérifier la corrélation, l’ordre des mises à jour, la bonne entrée modifiée et l’absence d’exposition de la référence au LLM. Vérifier aussi qu’une demande de refresh ciblée par nom local est traduite côté runtime vers la référence/corrélation nécessaire à l’hôte.

### T12 — Vue compacte et Progressive Disclosure

Créer un registre plus volumineux que la vue normale. Afficher d’abord les cibles récentes/pertinentes ; récupérer par Progressive Disclosure des cibles plus anciennes. Confirmer que masquer une entrée du prompt ne la supprime pas du registre.

### T13 — Concurrence entre sous-solvers

Un sous-solver enregistre une cible et un autre la consulte. Pendant ce temps, l’hôte la rafraîchit ou l’invalide. Vérifier que la mise à jour est visible dans la source partagée et qu’un snapshot obsolète ne la rétablit pas.

### T14 — Absence de capacités cibles

Avec un manifeste sans scopes ni producteurs/consommateurs déclarés, vérifier que le runtime n’ajoute pas de dépendance TargetRegistry aux tâches qui n’en ont pas besoin et qu’il conserve les chemins historiques compatibles.

### T15 — Renommage des outils

Renommer les outils hôte tout en conservant les mêmes rôles et la même déclaration de compatibilité. Le comportement doit rester équivalent. Ce test recherche les noms d’outils codés en dur dans les prompts ou le runtime.

### T16 — Redémarrage hôte

Redémarrer l’hôte pendant une mission. Vérifier qu’aucune référence n’est supposée valide simplement parce que sa valeur n’a pas changé. Le host/runtime invalide, rafraîchit ou fait échouer honnêtement les liaisons selon les capacités réellement déclarées ; le Solver peut alors reprendre le raisonnement.


## 19. Points explicitement confiés à l’agent de codage

Le mandat n’est pas de transposer ce texte directement en classes sans regarder le projet. L’agent doit se réapproprier la proposition, vérifier ce qui existe déjà et recommander une intégration proportionnée.

### 19.1 Inspecter le runtime et le contrat actuels

En priorité, examiner :

- les formats réels du manifeste (`parameters`, `returns`, descriptions et kinds) ;
- le `HOST_CONTRACT.md`, particulièrement la provenance des références, `STALE_REF`, `get_world_state` et `world_snapshot` ;
- la mise en œuvre de `perceive`, `perceive_action`, `perceive_understand` et `tool_manager` ;
- l’usage actuel de Progressive Disclosure, ses protections contre les requêtes répétées et sa gestion des limites d’exécution ;
- le flux d’appel des outils hôte dans l’Executor, y compris les chemins des skills et sous-solvers ;
- le bus d’événements et le protocole JSONL ;
- le registre de variables, le `runtime_state` partagé, les snapshots de variables et la concurrence éventuelle.

Ne pas affirmer qu’une capacité existe simplement parce qu’elle est souhaitable dans ce document. Distinguer ce qui existe, ce qui peut être étendu proprement et ce qui réclame un changement du contrat hôte.

### 19.2 Choix d’implémentation à justifier

1. Comment le manifeste déclare-t-il les producteurs, scopes et consommateurs sans imposer de noms d’outils hôte en dur ?
2. Comment le résultat structuré de perception expose-t-il une cible sélectionnée sans transmettre sa référence au LLM ?
3. Comment le runtime enregistre-t-il une cible issue de toute voie de perception, y compris Solver/Planner via Progressive Disclosure ?
4. Comment une action du plan référence-t-elle le nom local connu, puis le transforme-t-elle en référence hôte au dernier moment ?
5. Comment le host et le cerveau corrèlent-ils une entrée pour les événements ultérieurs, notamment si la référence hôte change ?
6. Comment les événements de rafraîchissement/invalidation s’intègrent-ils dans le protocole JSONL existant sans casser les corrélations requête/résultat ou le streaming ?
7. Comment l’Executor traite-t-il une entrée désactivée sans révéler son statut au LLM et sans substituer une autre cible ?
8. Comment `perceive` utilise-t-il les protections Progressive Disclosure déjà existantes pour garantir la terminaison de sa recherche, sans créer une boucle parallèle redondante ?
9. Comment maintenir le comportement historique lorsque TargetRegistry ou un scope n’est pas disponible ?
10. Quelles parties de la hiérarchie/banque de cibles doivent rester purement internes à l’hôte ?

### 19.3 Principe de sobriété

Ne pas construire une ontologie universelle des objets du monde dans le cerveau. Ne pas ajouter des couches uniquement pour copier dans Python le suivi que l’hôte sait déjà faire. Ne pas créer de nouvelles taxonomies globales si le contrat normalisé existant (booléen + `error_reason` optionnel) suffit. Ne pas mettre la complexité dans des prompts lorsque l’invariant peut être assuré mécaniquement par le runtime.

L’objectif est une séparation claire : **le cerveau choisit la cible abstraite ; le runtime conserve et résout la liaison ; l’hôte garde la connaissance opérationnelle du monde et décide si l’action peut être faite**.


## 20. Exemple générique complet : deux cibles et une action de transfert

Un utilisateur demande à un robot : « déplace cette boîte vers la zone de dépôt ». La boîte et la zone de dépôt sont deux cibles distinctes. L’une peut être un objet suivi, l’autre un volume spatial déclaré par l’hôte.

1. Si les deux cibles sont déjà visibles dans le registre, le Planner peut construire le plan avec leurs noms locaux et éviter une nouvelle perception.
2. Si la boîte est connue mais la zone de dépôt ne l’est pas, le plan peut réutiliser la boîte et demander une perception de la zone de dépôt seulement.
3. Si la zone est introuvable, aucune référence de zone n’est inventée. La stratégie doit échouer ou se replanifier.
4. L’Executor résout les deux noms locaux vers les références opaques correspondantes et vérifie les scopes requis.
5. L’action hôte arbitre les deux références au moment de l’opération. Si l’une n’est plus utilisable, elle échoue sans agir sur une autre boîte ou une autre zone.
6. L’échec remonte au Solver. Aucune substitution automatique de boîte ou de zone n’est faite par l’Executor.
7. Si l’action réussit, Convergence utilise les preuves disponibles pour confirmer que la boîte a effectivement été déposée au bon endroit.

Cet exemple généralise le cas du bouton de fermeture : une action peut consommer plusieurs cibles, mais chaque entrée désigne une seule cible précise, et l’hôte arbitre leurs liaisons au moment de l’action.


## 21. Résultat attendu de la reprise par l’agent de codage

Après s’être approprié cette vision, l’agent de codage devrait fournir :

1. une courte cartographie entre cette vision et les composants déjà existants ;
2. les garanties que le code actuel satisfait déjà et celles qui manquent ;
3. les choix techniques recommandés pour le contrat manifeste, l’enregistrement automatique, le dispatch, les événements d’update et la compatibilité JSONL ;
4. les questions qui exigent encore un arbitrage de Christian, sans rouvrir les décisions déjà affirmées ici ;
5. une proposition d’implémentation incrémentale et les tests associés.

Il peut proposer une autre forme technique s’il démontre qu’elle préserve mieux les invariants et s’intègre plus simplement au code réel. Il ne doit pas faire passer une hypothèse d’implémentation pour une décision déjà prise.

**Principe final :** TargetRegistry n’est pas un catalogue textuel de choses vues. C’est le lien opérationnel, mission-scoped, entre une cible abstraitement nommée dans le cerveau et une référence que l’hôte sait arbitrer. La découverte peut être progressive et arriver à différents moments ; la réutilisation est encouragée ; la référence reste opaque ; les mises à jour viennent de l’hôte ; l’échec d’action renvoie au raisonnement global au lieu d’encourager une substitution aveugle.

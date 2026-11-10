# 6. Décisions d'architecture

Chaque décision indique son statut : **actée** (déjà dans le cahier des charges ou le code), ou **proposée**
(choix de conception, à valider).

D-02, D-03, D-04 et D-06 ont été implémentées telles que proposées au lot 2 (Contract Net). D-07 à D-10 sont
les choix faits pendant cette implémentation. Passe leur statut à « actée » quand tu les valides.

## D-01. SPADE et XMPP pour les agents (actée)

Les agents sont des agents SPADE 4.1 qui communiquent par XMPP en FIPA-ACL, comme dans le TP JADE
(cahier § 3). Serveur XMPP local lancé par `spade run --host localhost`. Tester l'envoi de messages entre
deux agents est déjà possible (`src/livraison/agents/`, agents `ping_pong`).

## D-02. Les agents tournent dans un processus séparé de l'API (proposée)

**Contexte.** `uvicorn --reload` redémarre le processus à chaque modification du code. Des agents lancés
dans ce processus seraient détruits et recréés en permanence, et leurs conversations en cours perdues. Le
cahier demande aussi qu'aucune commande ne soit perdue, même après une panne (NF-04).

**Décision.** Un programme à part (`python -m livraison.agents`) héberge l'Annuaire, le Dispatcher, les
agents Commande et Livreur. L'API FastAPI ne parle pas aux agents : elle publie des événements.

**Conséquences.**
- (+) On peut redémarrer l'API sans toucher aux agents, et inversement.
- (+) La reprise après panne est un sujet à part : au démarrage, le programme des agents relit la base.
- (−) Un canal de communication de plus à maintenir (D-03).

## D-03. Redis pour les événements entre l'API et les agents (proposée)

**Décision.** L'API et les agents échangent des événements par Redis (publication / abonnement), par
exemple `livreur_en_ligne`, `commande_validee`, `position_mise_a_jour`, `livreur_confirme`. Redis est déjà
utilisé pour les positions et pour la diffusion vers la carte du répartiteur (canal `positions`).

**Conséquence.** Un événement Redis n'est pas durable : s'il est perdu, la base doit permettre de le
rejouer (D-04).

## D-04. La base PostgreSQL est la seule source de vérité (proposée)

**Décision.** L'état d'une commande, d'une offre et d'un livreur est écrit en base **avant** d'être annoncé.
Redis ne sert qu'au temps réel (position courante, événements). Un agent qui redémarre reconstruit son
état depuis la base : commandes non finales, offres en cours, livreurs en ligne.

**Conséquences.**
- (+) Aucune commande perdue (NF-04) ; reprise après panne simplifiée (F-36, lot 2, priorité importante).
- (+) Les garanties d'intégrité sont dans la base : une seule offre `retenue` par commande et par tour,
  un livreur toujours présent pour une commande « attribuée », « enlevée » ou « en livraison ».
- (−) Chaque message d'un agent qui change l'état coûte une écriture.

## D-05. OSRM auto-hébergé pour les itinéraires (actée, cahier F-42)

**Décision.** Les distances et durées viennent d'un serveur OSRM sur l'extrait OpenStreetMap de la Côte
d'Ivoire, lancé en conteneur Docker (`osrm_url` dans la configuration). Aucun frais par requête, aucune
dépendance à une API payante. Deux serveurs : profil **voiture** et profil **vélo**. La **moto utilise le
profil voiture** (OSRM n'en fournit pas pour la moto), ce qui sous-estime sa vitesse en cas d'embouteillage
et ignore les passages réservés aux deux-roues : limite à mesurer sur des trajets réels (lot 2).

**Risque.** La qualité des données OpenStreetMap à Abidjan (sens uniques, rues sans nom) : à mesurer sur
quelques trajets réels pendant le lot 2.

**Solution de repli proposée.** Si OSRM est injoignable, estimer la durée par la distance à vol d'oiseau
multipliée par un coefficient, et **le signaler** dans le journal et dans la réponse, jamais en silence.

## D-06. Le Dispatcher est le seul à attribuer (proposée)

**Décision.** Seul le Dispatcher envoie des `ACCEPT-PROPOSAL`. Les agents Livreur ne s'accordent pas
entre eux. C'est ce qui rend la non double attribution (F-32) simple à garantir : un seul acteur décide,
et il met à jour la charge du livreur dans une transaction avant de décider pour la commande suivante.

**Conséquence.** Le Dispatcher est un point de passage unique : il doit être rapide (NF-01, attribution en
moins de 5 secondes hors confirmation humaine) et redémarrable (D-04).

## D-07. Le livreur confirme dans les 30 secondes : son agent attend la réponse (proposée)

**Contexte.** F-34 : le livreur retenu a 30 secondes pour prendre la course. L'API ne parle pas aux agents
(D-02, D-03).

**Décision.** Quand le Dispatcher retient un livreur, son agent réserve une place de sa charge et annonce
`course_proposee` (Redis). Le livreur répond par l'API (`POST /livreurs/moi/proposition/{commande}/confirmer`
ou `/decliner`), qui publie `course_confirmee` ou `course_declinee` ; le gestionnaire d'agents transmet la
réponse à l'agent, qui attend 30 secondes (`ATTRIBUTION_CONFIRMATION_S`). L'application lit la course
proposée **en base** (`GET /livreurs/moi/proposition` : offre « retenue », commande encore en recherche,
délai non écoulé). Le Dispatcher, lui, attend 30 secondes **plus une marge** (`ATTRIBUTION_MARGE_S`) : si
l'agent du livreur est tombé, il ne reste pas bloqué.

**Conséquences.**
- (+) Redémarrer l'API ne perd aucune proposition : la base la porte.
- (−) Si l'agent du livreur tombe pendant la confirmation, le livreur voit la proposition jusqu'à
  son expiration ; une réponse tardive reçoit HTTP 410.

## D-08. La charge d'un livreur est contrôlée en base avant chaque attribution (proposée)

**Contexte.** F-32 : deux commandes ne doivent jamais prendre la même place d'un livreur.

**Décision.** Le Dispatcher ne retient une offre que si la charge du livreur **en base** reste sous
`LIVREUR_CHARGE_MAX` (3). Charge = commandes attribuées, enlevées ou en livraison + offres retenues dont la
confirmation est encore possible. La ligne du livreur est verrouillée pendant le contrôle. L'agent Livreur
compte aussi sa charge (pour son coût et son refus « sature »), mais **la base a le dernier mot** : si elle
écarte la meilleure offre, le Dispatcher essaie la suivante.

**Conséquence.** À chaque création d'agent, le gestionnaire relit la charge courante depuis la base
(commandes en cours et offres retenues encore valides). Le contrôle en base demeure la garantie finale
contre tout dépassement.

## D-09. Le coût d'une offre n'inclut pas de détour avant le lot 7 (proposée)

**Décision.** Coût = durée jusqu'à l'enlèvement (itinéraire réel) + pénalité x charge, avec une pénalité de
300 s par course portée (`ATTRIBUTION_PENALITE_CHARGE_S`). Le détour vaut 0 : le regroupement des courses
(lot 7) le calculera. Le champ existe déjà dans les messages (`PROPOSE`) et dans `cout_itineraire`.

**Conséquence.** Un livreur déjà chargé n'est jamais préféré pour sa « proximité avec sa course en cours » :
ce raffinement attend le lot 7. Une durée estimée à vol d'oiseau (OSRM injoignable) est signalée dans
l'offre (`estime`).

## D-10. Relances : le rayon grandit après un tour vide, pas après un refus humain (proposée)

**Décision.** Premier tour : 3 km (`ATTRIBUTION_RAYON_INITIAL_M`). Après un tour **sans livreur retenu** : pause de
10 s puis rayon multiplié par 2, plafonné à 20 km. Après un livreur qui **décline** ou laisse passer le délai :
nouveau tour immédiat, même rayon, sans ce livreur (l'exclusion est lue en base : offres « déclinées »).
Après 6 tours (`ATTRIBUTION_TOURS_MAX`) sans succès : une alerte pour le répartiteur est écrite dans le
journal (`evenement`, type `notification`) et annoncée (`attribution_echouee`) ; la commande **reste** en
« recherche de livreur » pour une réattribution manuelle (F-62).

**Conséquence.** Le numéro de tour et le rayon sont écrits en base (`tentatives_attribution`,
`rayon_recherche_m`) : après un redémarrage, l'attribution reprend là où elle s'était arrêtée. Les valeurs sont
des points de départ, à calibrer avec les premiers essais réels.

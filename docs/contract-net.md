# Contract Net : attribution d'une commande à un livreur (lot 2, F-31 à F-36)

Une commande validée est attribuée au livreur dont l'**offre est la moins chère**, le coût étant calculé sur
l'**itinéraire réel** (OSRM). Le livreur retenu a **30 secondes** pour confirmer ; s'il décline ou ne répond
pas, la commande repart pour un nouveau tour sans lui. Sans offre, le rayon de recherche s'élargit, puis le
répartiteur est alerté.

Tous les chemins partent de la **racine du dépôt**, le dossier `livraison-sma/`.

## 1. Fichiers livrés et où les placer

**REMPLACE** : le fichier existe déjà, remplace-le en entier. **NOUVEAU** : le fichier n'existe pas encore.
Chaque fichier est fourni séparément, avec son chemin.

| Chemin | Action | Rôle |
|---|---|---|
| `src/livraison/config.py` | REMPLACE | Ajoute les réglages `ATTRIBUTION_*` et `LIVREUR_CHARGE_MAX` (§ 6) |
| `src/livraison/modele/cout.py` | REMPLACE | Ajoute `cout_itineraire` et `classer_offres` ; la version « grille » du TP est intacte |
| `src/livraison/modele/attribution.py` | NOUVEAU | Règles pures : rayon des tours, exclusions, ordre des offres |
| `src/livraison/services/attribution.py` | NOUVEAU | Tout ce que le Contract Net écrit et lit en base (tours, offres, charge, journal) |
| `src/livraison/services/courses.py` | NOUVEAU | La course proposée au livreur et sa réponse (F-34) |
| `src/livraison/services/evenements.py` | REMPLACE | Ajoute les événements `commande_validee`, `course_confirmee`, `course_declinee`, `course_proposee`... |
| `src/livraison/api/schemas_courses.py` | NOUVEAU | Schémas de la proposition de course |
| `src/livraison/api/routes_livreur.py` | REMPLACE | Ajoute 3 routes : proposition en cours, confirmer, décliner |
| `src/livraison/agents/protocole.py` | REMPLACE | Ajoute les messages du Contract Net (CFP, PROPOSE, REFUSE, ACCEPT, REJECT, CANCEL...) |
| `src/livraison/agents/livreur.py` | REMPLACE | L'agent Livreur répond aux appels d'offres et gère les 30 secondes |
| `src/livraison/agents/dispatcher.py` | NOUVEAU | Le Dispatcher : mène les appels d'offres, décide (D-06) |
| `src/livraison/agents/commande.py` | NOUVEAU | L'agent Commande : tours, relances, alerte |
| `src/livraison/agents/gestionnaire_commandes.py` | NOUVEAU | Crée l'agent Commande de chaque commande qui attend un livreur |
| `src/livraison/agents/gestionnaire.py` | REMPLACE | Transmet les réponses des livreurs et les commandes validées |
| `src/livraison/agents/__main__.py` | REMPLACE | Lance aussi le Dispatcher et le gestionnaire des commandes |
| `scripts/creer_commande_test.py` | NOUVEAU | Crée une commande de test et l'annonce (l'API des commandes arrive au lot 4) |
| `docs/contract-net.md` | NOUVEAU | Ce guide |
| `docs/xmpp-depannage.md` | NOUVEAU | Serveur XMPP : problèmes rencontrés, résolution, commandes (§ 9) |
| `scripts/reinitialiser_mot_de_passe.py` | NOUVEAU | Réinitialise le mot de passe d'un compte répartiteur ou administrateur |
| `tests/test_reinitialisation_mot_de_passe.py` | NOUVEAU | 11 tests de ce script |
| `LANCEMENT.md` | REMPLACE | Mis à jour : serveur XMPP dans Docker, attribution des courses, dépannage |
| `scripts/diagnostic_xmpp.py` | NOUVEAU | Diagnostic de la connexion XMPP (§ 9) |
| `docs/agents.md` | REMPLACE | Mis à jour (limites, Dispatcher) |
| `docs/conception/06-decisions.md` | REMPLACE | Ajoute les décisions D-07 à D-10 |
| `tests/aides_commandes.py` | NOUVEAU | Fabriques de données pour les tests |
| `tests/test_aides_xmpp.py` | NOUVEAU | 4 tests de la vérification du serveur XMPP |
| `docker-compose.xmpp.yml` | NOUVEAU | Serveur XMPP de développement dans Docker (`docs/xmpp-depannage.md`) |
| `docker/xmpp/Dockerfile` | NOUVEAU | Image de ce serveur : spade 4.1.4, pyjabber 0.4.5 et greenlet sous Linux |
| `tests/aides_xmpp.py` | NOUVEAU | Vérifie, en 15 s au plus, que les agents peuvent vraiment se connecter au serveur XMPP (sans connexion à vide) |
| `tests/test_agents_xmpp.py` | REMPLACE | Seule la fixture `xmpp` change (elle utilise `aides_xmpp.py`) |
| `tests/test_attribution_regles.py` | NOUVEAU | 15 tests |
| `tests/test_attribution_service.py` | NOUVEAU | 27 tests (base réelle) |
| `tests/test_agents_contract_net_protocole.py` | NOUVEAU | 31 tests |
| `tests/test_agents_livreur_cfp.py` | NOUVEAU | 30 tests |
| `tests/test_agents_dispatcher.py` | NOUVEAU | 29 tests (base réelle) |
| `tests/test_agents_commande.py` | NOUVEAU | 11 tests |
| `tests/test_agents_gestionnaire_commandes.py` | NOUVEAU | 19 tests |
| `tests/test_courses.py` | NOUVEAU | 14 tests |
| `tests/test_contract_net_xmpp.py` | NOUVEAU | 7 tests avec de vrais agents (serveur XMPP requis ; même fixture `xmpp`) |

> Si ton `config.py` a changé depuis la livraison précédente, ne le remplace pas : ajoute seulement le bloc
> « Contract Net » (11 lignes, après `annuaire_max_resultats`).

## 2. Mise en route, dans l'ordre

**Étape 1.** Copie les fichiers aux chemins du tableau.

**Étape 2.** Démarre le serveur XMPP (Docker, voir `docs/xmpp-depannage.md`) puis lance les tests :

```powershell
docker compose -f docker-compose.xmpp.yml up -d --build
python scripts/preparer_base_test.py
pytest
```

Résultat attendu : tous les tests passent, **aucun ignoré** tant que le serveur XMPP tourne. Sans lui, les 16 tests
d'agents réels (`test_agents_xmpp.py` et
`test_contract_net_xmpp.py`) sont ignorés. Pour tout lancer **sauf** ces 16 tests :

```powershell
pytest --ignore tests/test_agents_xmpp.py --ignore tests/test_contract_net_xmpp.py
```

Si les 16 tests échouent sur la connexion, voir le § 9.

**Étape 3.** Essai à la main. Il faut PostgreSQL et Redis (`docker compose up -d`), OSRM, l'API, le serveur XMPP
(Docker, étape 2) et les agents :

```powershell
# Terminal C : les agents (Annuaire, Dispatcher, gestionnaires)
python -m livraison.agents
```

Avec l'application du livreur (ou l'API), passe un livreur **en ligne** et fais-lui envoyer une position
récente. Son dossier doit être **validé** (`peut_recevoir_des_courses`), sinon le Dispatcher ne le consulte
pas : c'est le cas d'un livreur de test tant qu'un administrateur n'a pas validé ses pièces.

```powershell
# Terminal D : une commande de test près du Plateau, suivie pendant 90 s
python scripts/creer_commande_test.py --suivre
```

Dans le terminal C tu lis, dans l'ordre : `Agent créé pour la commande ...`, puis (si un livreur convient) la
course est proposée. Le livreur la voit avec `GET /livreurs/moi/proposition` et répond **dans les 30 s** avec
`POST /livreurs/moi/proposition/<commande>/confirmer` (ou `/decliner`), par exemple depuis la documentation
interactive de ton API (`/docs`). Le terminal D affiche `état=attribuee` à la fin.

Sans livreur adapté, tu verras les tours défiler (`tour 1 sans livreur`, rayon 3 km, puis 6, 12 et 20 km), puis
`aucun livreur après 6 tours`. Pour aller plus vite en essai, mets `ATTRIBUTION_PAUSE_S=2` dans `.env`.

Regarder en base :

```sql
SELECT tour, livreur_id, cout, eta_s, resultat, motif_refus FROM offre ORDER BY cree_le;
SELECT cree_le, auteur, destinataire, performative FROM evenement
 WHERE type = 'message_agent' ORDER BY id;      -- le « Sniffer » (F-63)
```

## 3. Comment ça marche : un tour d'appel d'offres

Conversation `<commande_id>:<tour>` (docs/conception/03-sequences-protocole.md, S2 à S4) :

1. **Agent Commande** : écrit en base le numéro du tour, le rayon et l'état `recherche_livreur`, puis demande
   au Dispatcher d'attribuer (`REQUEST`).
2. **Dispatcher** : interroge l'Annuaire (`QUERY-REF`, livreurs libres dans le rayon), écarte ceux qui ont déjà
   décliné cette commande, ne garde que ceux que la **base** dit en ligne et autorisés (`peut_recevoir_des_courses`).
3. **CFP** à chacun. Chaque agent Livreur répond `PROPOSE` (son coût) ou `REFUSE` avec un motif :
   `hors_ligne`, `vehicule`, `trop_lourd`, `sature`, `gps_ancien`, `hors_zone`, `itineraire`.
   Le Dispatcher collecte pendant `ATTRIBUTION_COLLECTE_S` (3 s) et s'arrête dès que tous ont répondu.
4. Les offres sont **écrites en base**, puis la moins chère est retenue. La base peut l'écarter (livreur déjà
   saturé, commande annulée) : on passe à la suivante. `ACCEPT-PROPOSAL` au gagnant, `REJECT-PROPOSAL` aux autres.
5. L'agent du gagnant réserve une place de sa charge et annonce `course_proposee`. Le livreur humain
   confirme ou décline (30 s). Confirmé : la commande passe à `attribuee` (écrit par le Dispatcher) et l'agent
   Commande a fini. Décliné ou expiré : l'offre est « déclinée », le livreur est exclu de cette commande, et un
   nouveau tour commence aussitôt.

**Le coût d'une offre** (secondes) : durée de la position du livreur à l'enlèvement (itinéraire OSRM) +
300 s x nombre de courses déjà portées. Si OSRM est injoignable, la durée est estimée à vol d'oiseau et
l'offre est marquée `estime` : jamais en silence.

Tous les messages de la conversation sont journalisés dans `evenement` (type `message_agent`).

## 4. Choix à connaître

- **Le Dispatcher est le seul à attribuer** (D-06) et la **base a le dernier mot sur la charge** (D-08) : même
  si deux appels d'offres visent le même livreur en même temps, une seule commande l'obtient (scénario S6).
- **La confirmation en 30 s est suivie par l'agent du livreur** (D-07). L'API ne parle jamais aux agents : elle
  publie `course_confirmee` / `course_declinee` sur Redis. La course proposée se lit en base : redémarrer
  l'API ne la perd pas.
- **Deux précisions par rapport aux diagrammes** : un livreur qui devient saturé entre l'offre et
  l'acceptation répond `CANCEL` (motif `sature`) et non `REFUSE` ; et c'est le Dispatcher, non l'agent
  Commande, qui écrit l'état `attribuee` (il a déjà la transaction, D-06).
- **Les commandes urgentes passent avant les autres** (F-35, § 10), puis premier arrivé, premier servi.
- **L'agent Commande s'arrête à l'attribution.** Les lots suivants (enlèvement, livraison) le prolongeront.
- **Après tous les tours sans succès**, la commande reste en `recherche_livreur`, une alerte est écrite dans
  `evenement` (type `notification`) et annoncée (`attribution_echouee`) : réattribution manuelle au lot 6 (F-62).

## 5. En cas de panne

| Situation | Ce qui se passe |
|---|---|
| Un agent Livreur ne répond pas au CFP | Offre `expiree` ; le tour continue avec les autres |
| Le livreur ne confirme pas | Au bout de 30 s : `CANCEL`, offre `declinee`, nouveau tour sans lui |
| L'agent du livreur est tombé | Le Dispatcher attend 30 s + 5 s, libère l'offre et relance |
| Le Dispatcher est tombé | L'agent Commande attend ~45 s, compte un tour raté et relance ; une offre retenue orpheline libère le livreur au bout de 35 s |
| Redis est tombé | Les annonces sont perdues, jamais les requêtes ; la réconciliation (30 s) rattrape les commandes |
| La commande est annulée en cours de route | Le Dispatcher le voit en base : aucune attribution, le livreur récupère sa place |
| OSRM est injoignable | Offres estimées à vol d'oiseau et signalées (`estime`) |
| La base est injoignable pendant un tour | `FAILURE erreur_interne` : l'agent Commande compte un tour raté |

## 6. Réglages (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `ATTRIBUTION_COLLECTE_S` | `3` | Attente des offres après l'appel (NF-01 : attribution en moins de 5 s) |
| `ATTRIBUTION_ANNUAIRE_ATTENTE_S` | `2` | Attente de la réponse de l'Annuaire |
| `ATTRIBUTION_CONFIRMATION_S` | `30` | Délai du livreur humain pour confirmer (F-34) |
| `ATTRIBUTION_MARGE_S` | `5` | Marge du Dispatcher au-delà de ce délai |
| `ATTRIBUTION_RAYON_INITIAL_M` | `3000` | Rayon de recherche du premier tour |
| `ATTRIBUTION_RAYON_MAX_M` | `20000` | Le rayon ne dépasse jamais cette valeur |
| `ATTRIBUTION_FACTEUR_RAYON` | `2` | Multiplicateur après un tour sans livreur retenu |
| `ATTRIBUTION_PAUSE_S` | `10` | Pause avant de relancer un tour sans offre |
| `ATTRIBUTION_TOURS_MAX` | `6` | Tours avant d'alerter le répartiteur |
| `ATTRIBUTION_PENALITE_CHARGE_S` | `300` | Coût en secondes d'une course déjà portée |
| `LIVREUR_CHARGE_MAX` | `3` | Courses qu'un livreur porte en même temps (au-delà : `sature`) |

Ce sont des valeurs de départ : à calibrer avec les premiers essais réels à Abidjan.

## 7. Limites connues

- **Pas de détour avant le lot 7** (D-09) : le coût est « durée jusqu'à l'enlèvement + pénalité de charge ».
- À la création ou reconstruction d'un agent Livreur, sa **charge est relue depuis la base** : courses actives et
  propositions retenues encore valides. La base continue d'appliquer le garde-fou transactionnel.
- **La priorité urgente (F-35) ne reprend pas une décision déjà prise** : elle ordonne seulement les
  commandes qui attendent en même temps (§ 10). Une urgente qui arrive après qu'une commande normale a obtenu le
  dernier livreur libre ne la lui enlève pas.
- **Un seul programme d'agents, un seul Dispatcher** : la base verrouille tout de même la ligne du livreur
  pendant la décision, première étape vers plusieurs Dispatchers.
- **Les commandes n'ont pas encore d'API** (lot 4) : en attendant, `scripts/creer_commande_test.py`.
- **Sécurité de XMPP** : inchangée (voir `docs/agents.md`, § 7).

## 8. État de la vérification

**Vérifié chez toi** (Windows, Python 3.13), le 6 novembre 2026 :
- les tests qui n'ont pas besoin du serveur XMPP passent : 433 sur 433 (Annuaire, agent Livreur, Dispatcher, agent
  Commande, gestionnaires, service d'attribution sur la vraie base, courses proposées, tes tests existants) ;
- les **16 tests d'agents réels** passent (`test_agents_xmpp.py` 9, `test_contract_net_xmpp.py` 7), avec le serveur XMPP
  dans Docker : le moins cher gagne sur l'itinéraire réel, le livreur qui décline, le délai de confirmation qui expire,
  l'élargissement du rayon jusqu'à un livreur à 15 km, aucun livreur, deux commandes pour un seul livreur, commande
  annulée.

**Vérifié de mon côté** (Linux, PostgreSQL 16 + PostGIS, Redis, serveur XMPP de SPADE) : la suite complète plusieurs fois
de suite ; **14 défauts volontaires** introduits dans le code (coût sans pénalité, classement inversé, garde-fou de
charge supprimé, exclusion oubliée, rayon jamais élargi, place de charge non rendue, usurpation de l'agent Commande,
délai de confirmation ignoré...), tous détectés par les tests ; le programme `python -m livraison.agents` démarre,
trouve une commande validée en base, lance deux tours puis alerte le répartiteur.

**Non vérifié** :
- ton **`services/dossier.py` réel** : je ne l'avais pas reçu, j'ai utilisé sa signature vue dans tes tests
  (`peut_recevoir_des_courses(livreur)`, vrai pour un dossier validé) ; tes tests de dossier passent chez toi, ce qui
  suggère que c'est cohérent ;
- les routes de proposition et de course active sont testées au niveau API ; les apps Expo et Flet ont maintenant
  les parcours d'acceptation/refus, navigation, appel, enlèvement et code de livraison ;
- un **essai complet à la main** (livreur réel en ligne, commande de test, confirmation par Swagger) : à faire avec
  le § 2, étape 3.

## 9. Serveur XMPP

Le serveur XMPP tourne dans **Docker** (Linux), pas avec `spade run` sous Windows, où il bloque une partie des connexions
des agents. Mise en route, chronologie des problèmes rencontrés, cause, résolution et commandes de diagnostic :
**`docs/xmpp-depannage.md`**.

Rappel : `XMPP_HOST` et `XMPP_DOMAINE` valent `127.0.0.1` dans `.env`, et un seul serveur XMPP écoute sur le port 5222 à la fois.

## 10. Priorité urgente (F-35)

Une commande de priorité `urgente` est traitée avant les autres, à deux endroits :

1. **La file d'attente** (`services/attribution.py`, `lire_commandes_a_attribuer`) : les urgentes d'abord, puis les
   plus anciennes. C'est l'ordre dans lequel la réconciliation crée les agents Commande, donc dans lequel les tours
   démarrent après un redémarrage.
2. **La décision du Dispatcher** (`agents/verrou_priorite.py`) : le Dispatcher décide une commande à la fois (D-06).
   Quand plusieurs conversations attendent de décider en même temps, l'urgente passe la première, puis les autres
   dans l'ordre d'arrivée. C'est ce qui départage deux commandes qui visent le même livreur.

La règle est `rang_priorite` dans `modele/attribution.py` : 0 pour une urgente, 1 pour toute autre valeur (une
priorité inconnue ne bloque jamais une commande).

**Limites.**
- Le verrou ordonne les conversations qui attendent ensemble ; il ne reprend jamais un livreur à une commande qui
  l'a déjà obtenu.
- Une urgente ne change ni le rayon de recherche, ni le délai de collecte, ni le coût des offres.
- Un seul Dispatcher : avec plusieurs, seule la garde en base (D-08) évite la double attribution, sans la priorité.

## 11. Reprise après redémarrage (F-36)

Les agents sont reconstruits à partir de la base et des positions persistées :

- le gestionnaire recrée les agents Livreur des livreurs toujours en ligne, recharge leur dernière position Redis et
  initialise leur charge avec `charge_en_base` (courses attribuées/enlevées/en livraison et propositions retenues non expirées) ;
- le gestionnaire des commandes recrée les agents des commandes encore à attribuer, dans l'ordre de priorité ;
- les commandes déjà attribuées restent dans leur état durable. La route `GET /livreurs/moi/course-en-cours` relit
  l'étape active depuis la base pour que l'application du livreur la retrouve.

Les tests couvrent les briques de réconciliation et la lecture d'une course active depuis la base. Il reste à
effectuer un redémarrage contrôlé des services pendant une course réelle pour valider l'ensemble en conditions d'exécution.

**Fichiers de cette livraison** (chemin depuis la racine du dépôt) :

| Chemin | Action |
|---|---|
| `src/livraison/agents/verrou_priorite.py` | NOUVEAU |
| `src/livraison/modele/attribution.py` | REMPLACE |
| `src/livraison/services/attribution.py` | REMPLACE |
| `src/livraison/agents/dispatcher.py` | REMPLACE |
| `tests/test_verrou_priorite.py` | NOUVEAU (9 tests, sans base) |
| `tests/test_priorite_urgente.py` | NOUVEAU (11 tests, base réelle) |
| `tests/test_agents_dispatcher.py` | REMPLACE (1 test de plus) |
| `docs/contract-net.md`, `LANCEMENT.md` | REMPLACE |

**Vérification (8 novembre 2026).** Chez toi (Windows, Python 3.13, serveur XMPP dans Docker) : 487 tests collectés, dont le
test du Dispatcher (l'urgente gagne alors que la normale a proposé la première), le tri sur la vraie base et les 9 tests
du verrou. Un seul échec au premier passage, dans le test lui-même (une commande « attribuée » exige son livreur : contrainte
`ck_commande_livreur_si_en_cours`), corrigé ; le `pytest` complet est à relancer pour confirmer 487 passed.

# 3. Protocole entre agents : séquences

Les agents sont des agents **SPADE** qui s'échangent des messages **FIPA-ACL** par XMPP. Les applications
(iOS, web) ne parlent **jamais** directement aux agents : l'API traduit leurs actions en événements,
transmis aux agents par Redis, et renvoie leurs décisions (décision D-02, `06-decisions.md`).

## 3.1 Agents et responsabilités

| Agent | Quand il existe | Rôle |
|---|---|---|
| **Annuaire** | en permanence | Tient la liste des livreurs disponibles (service « livreur ») avec leur position et leur charge |
| **Dispatcher** | en permanence | Choisit à qui attribuer une commande : lance l'appel d'offres, compare les offres, désigne le gagnant |
| **Livreur** | pendant que le livreur est en ligne | Représente un livreur : calcule son coût pour une course, accepte ou refuse en son nom |
| **Commande** | de la validation à l'état final | Suit une commande, déclenche l'attribution et les relances, journalise |

## 3.2 Messages (performatives FIPA-ACL)

L'identifiant de conversation (`conversation-id`) est `<commande_id>:<tour>`.

| Message | De | Vers | Contenu |
|---|---|---|---|
| `REQUEST` (inscription) | Livreur | Annuaire | service `livreur`, position, véhicule, capacité, charge |
| `INFORM` (mise à jour) | Livreur | Annuaire | nouvelle position ou nouvelle charge |
| `QUERY-REF` | Dispatcher | Annuaire | livreurs disponibles dans un rayon autour d'un point |
| `CFP` | Dispatcher | Livreurs candidats | enlèvement, livraison, taille et poids du colis, priorité, délai de réponse |
| `PROPOSE` | Livreur | Dispatcher | coût, durée jusqu'à l'enlèvement, détour, charge |
| `REFUSE` | Livreur | Dispatcher | motif : `sature`, `hors_zone`, `trop_lourd`, `gps_ancien`, `vehicule`, `hors_ligne` |
| `ACCEPT-PROPOSAL` | Dispatcher | Livreur retenu | commande à exécuter |
| `REJECT-PROPOSAL` | Dispatcher | Autres livreurs | aucune action |
| `INFORM` (confirmation) | Livreur | Dispatcher et Commande | le livreur humain a confirmé |
| `CANCEL` / `FAILURE` | Livreur | Dispatcher | le livreur a décliné, n'a pas répondu, ou est tombé en panne |

Le **coût** d'une offre (secondes) :
`durée(position du livreur → enlèvement) + durée du détour + pénalité × charge`, les durées venant de
l'itinéraire réel (OSRM, F-42). Le livreur au coût le plus bas gagne. Exemple chiffré, pénalité de 300 s
par course déjà portée :

| Livreur | Jusqu'à l'enlèvement | Détour | Charge | Coût |
|---|---|---|---|---|
| A | 420 s | 0 s | 0 | 420 s |
| B | 180 s | 240 s | 2 | 180 + 240 + 600 = 1 020 s |

A gagne malgré un trajet plus long jusqu'à l'enlèvement, car B est déjà chargé (la fonction réelle est dans
`src/livraison/modele/cout.py` : ce tableau doit rester cohérent avec elle).

## S1. Un livreur passe en ligne (F-30)

```mermaid
sequenceDiagram
    autonumber
    participant App as Application livreur
    participant API as API FastAPI
    participant R as Redis
    participant GA as Gestionnaire d'agents
    participant AL as Agent Livreur
    participant AN as Annuaire

    App->>API: POST /livreurs/moi/en-ligne
    API->>API: contrôle du dossier (validé) et du profil
    API->>R: événement livreur_en_ligne
    API-->>App: 200 en ligne
    R-->>GA: livreur_en_ligne
    GA->>AL: crée l'agent du livreur
    AL->>AN: REQUEST inscription (position, véhicule, charge)
    AN-->>AL: AGREE
    loop toutes les 5 secondes
        App->>API: POST /livreurs/moi/position
        API->>R: position enregistrée
        R-->>AL: position mise à jour
        AL->>AN: INFORM nouvelle position
    end
```

## S2. Attribution d'une commande, cas nominal (F-31)

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (web)
    participant API as API FastAPI
    participant AC as Agent Commande
    participant D as Dispatcher
    participant AN as Annuaire
    participant AL as Agents Livreur
    participant O as Service d'itinéraires (OSRM)
    participant App as Application livreur

    C->>API: valide et paie la commande
    API->>AC: événement commande_validee
    AC->>D: REQUEST attribuer la commande
    D->>AN: QUERY-REF livreurs libres dans le rayon
    AN-->>D: liste des candidats
    D->>AL: CFP (enlèvement, livraison, colis)
    AL->>O: durée position vers enlèvement et détour
    O-->>AL: durées réelles
    AL-->>D: PROPOSE (coût) ou REFUSE (motif)
    D->>D: classe les offres, choisit le coût le plus bas
    D->>AL: ACCEPT-PROPOSAL au gagnant
    D->>AL: REJECT-PROPOSAL aux autres
    AL->>API: proposition de course pour son livreur
    API->>App: notification de course
    App->>API: le livreur accepte dans les 30 secondes
    API->>AL: confirmation du livreur
    AL->>D: INFORM confirmé
    D->>AC: INFORM attribuée
    AC->>API: état attribuee, client notifié
    API-->>C: livreur en route, heure d'arrivée estimée
```

## S3. Aucune offre : file d'attente et relance (F-33)

```mermaid
sequenceDiagram
    autonumber
    participant AC as Agent Commande
    participant D as Dispatcher
    participant AL as Agents Livreur
    participant R as Répartiteur (console)

    AC->>D: REQUEST attribuer la commande
    D->>AL: CFP (rayon initial)
    AL-->>D: REFUSE (sature, hors_zone) ou silence
    D->>D: aucune PROPOSE valable
    D->>AC: FAILURE sans offre
    AC->>AC: commande gardée en recherche_livreur
    Note over AC,D: pause puis relance avec un rayon élargi
    AC->>D: REQUEST attribuer la commande, nouveau tour
    D->>AL: CFP (rayon élargi)
    alt une offre arrive
        AL-->>D: PROPOSE
        D->>AC: INFORM offre retenue, suite comme S2
    else toujours aucune offre après plusieurs tentatives
        AC->>R: alerte, réattribution manuelle possible (F-62)
    end
```

## S4. Le livreur ne confirme pas dans les 30 secondes (F-34)

```mermaid
sequenceDiagram
    autonumber
    participant D as Dispatcher
    participant AL as Agent Livreur retenu
    participant App as Application livreur
    participant AC as Agent Commande

    D->>AL: ACCEPT-PROPOSAL
    AL->>App: proposition de course (via l'API)
    alt le livreur décline ou le délai de 30 s expire
        AL->>D: CANCEL (déclinée ou expirée)
        D->>D: offre marquée declinee, livreur exclu pour cette commande
        D->>AC: INFORM réattribution nécessaire
        AC->>D: REQUEST attribuer la commande, nouveau tour
        Note over D: nouvel appel d'offres, sans ce livreur
    else le livreur confirme
        App->>AL: confirmation
        AL->>D: INFORM confirmé
    end
```

## S5. Déroulement de la course jusqu'à la clôture (F-23)

```mermaid
sequenceDiagram
    autonumber
    participant App as Application livreur
    participant API as API FastAPI
    participant AC as Agent Commande
    participant C as Client (web)

    App->>API: enlèvement validé
    API->>AC: événement enlevee
    API-->>C: colis récupéré
    App->>API: départ vers le destinataire
    API->>AC: événement en_livraison
    loop pendant le trajet
        App->>API: position GPS
        API-->>C: position et heure d'arrivée estimée
    end
    C-->>App: le destinataire communique son code à usage unique
    App->>API: code saisi
    API->>API: contrôle du code (haché)
    API->>AC: événement livree
    AC->>API: facture émise, rémunération calculée, état cloturee
    API-->>C: facture disponible, invitation à noter
```

## S6. Deux commandes visent le même livreur en même temps (F-32)

```mermaid
sequenceDiagram
    autonumber
    participant D as Dispatcher
    participant AL as Agent Livreur L
    participant A as Commande A
    participant B as Commande B

    par appel d'offres de la commande A
        D->>AL: CFP (conversation A:1)
    and appel d'offres de la commande B
        D->>AL: CFP (conversation B:1)
    end
    AL-->>D: PROPOSE pour A (conversation A:1)
    AL-->>D: PROPOSE pour B (conversation B:1)
    D->>AL: ACCEPT-PROPOSAL pour A
    Note over D,AL: charge de L mise à jour avant toute autre attribution
    D->>AL: revérifie la capacité de L pour B
    alt L est maintenant saturé
        AL-->>D: REFUSE pour B (sature)
        D->>D: B choisit la meilleure offre restante, ou relance
    else L a encore de la place
        D->>AL: ACCEPT-PROPOSAL pour B
    end
    D->>B: résultat de l'attribution
```

Les identifiants de conversation distincts évitent toute confusion entre les deux appels d'offres. La
garantie de non double attribution est aussi dans la base : une seule offre `retenue` par commande et par
tour, et la charge d'un livreur n'est mise à jour qu'à l'intérieur d'une transaction.

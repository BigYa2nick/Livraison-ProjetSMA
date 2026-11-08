# 2. Diagrammes d'états

Les états de la commande, du livreur et du paiement sont ceux du code
(`src/livraison/persistance/enums.py`), pour que le dossier et le logiciel disent la même chose.
L'état de l'agent Livreur et celui d'une conversation d'appel d'offres sont propres au lot 2.

## 2.1 Commande (`EtatCommande`, cahier § 3.4)

```mermaid
stateDiagram-v2
    [*] --> creee : le client enregistre sa commande
    creee --> validee : paiement confirmé, ou paiement à la livraison accepté
    creee --> annulee : abandon ou annulation sans frais
    validee --> recherche_livreur : le Dispatcher ouvre un appel d'offres
    validee --> annulee : annulation sans frais
    recherche_livreur --> recherche_livreur : aucune offre, relance avec un rayon élargi
    recherche_livreur --> attribuee : offre retenue et confirmée par le livreur
    recherche_livreur --> annulee : annulation ou abandon du répartiteur
    attribuee --> recherche_livreur : refus, délai dépassé ou panne du livreur
    attribuee --> enlevee : enlèvement validé
    attribuee --> annulee : annulation avec frais
    enlevee --> en_livraison : le livreur part vers le destinataire
    enlevee --> echec_livraison : incident au retrait
    en_livraison --> livree : code à usage unique saisi
    en_livraison --> echec_livraison : destinataire absent ou adresse introuvable
    livree --> cloturee : facture émise, rémunération calculée
    livree --> litige : réclamation du client
    echec_livraison --> litige : réclamation
    litige --> cloturee : litige résolu
    cloturee --> [*]
    annulee --> [*]
    echec_livraison --> [*]
```

Les états **finaux** sont `cloturee`, `annulee` et `echec_livraison` : une commande qui y arrive n'évolue
plus (NF-04, constante `ETATS_FINAUX`). `litige` n'est pas final. Une commande « attribuée, enlevée ou en
livraison » a toujours un livreur : c'est une contrainte de la base de données.

## 2.2 Dossier du livreur (`StatutValidation`, F-03)

```mermaid
stateDiagram-v2
    [*] --> en_attente : profil livreur créé
    en_attente --> valide : valider, dossier complet et pièces acceptées
    en_attente --> rejete : rejeter, motif obligatoire
    rejete --> en_attente : le livreur dépose une pièce corrigée
    valide --> suspendu : suspendre, motif obligatoire
    suspendu --> valide : réactiver
```

Détail des règles : `docs/dossier-livreur.md`. Seul un livreur `valide` reçoit des courses.

## 2.3 Disponibilité d'un livreur et état de son agent (F-30, lot 2)

L'agent Livreur existe **uniquement** pendant que le livreur est en ligne.

```mermaid
stateDiagram-v2
    [*] --> disponible : le livreur passe en ligne, l'agent s'inscrit à l'Annuaire
    disponible --> disponible : CFP reçu, REFUSE ou PROPOSE envoyé
    disponible --> reserve : ACCEPT-PROPOSAL reçu du Dispatcher
    reserve --> en_course : le livreur confirme dans les 30 s
    reserve --> disponible : le livreur décline ou ne répond pas
    en_course --> disponible : livraison terminée
    en_course --> en_course : nouvelle course compatible, charge augmentée
    disponible --> [*] : hors ligne, l'agent se désinscrit
    reserve --> [*] : hors ligne ou panne
    en_course --> [*] : incident grave, réattribution
```

`en_course` garde le livreur disponible aux nouvelles propositions tant que sa **charge** reste sous son
maximum (F-32, F-44). C'est pourquoi la formule du coût contient une pénalité proportionnelle à la charge.

## 2.4 Une conversation d'appel d'offres (Contract Net, F-31 à F-33)

Une conversation est identifiée par la commande et le **tour** (un tour = un appel d'offres, table `offre`).

```mermaid
stateDiagram-v2
    [*] --> collecte : CFP envoyé aux livreurs candidats
    collecte --> evaluation : toutes les réponses reçues, ou délai de collecte écoulé
    evaluation --> sans_offre : aucune PROPOSE valable
    evaluation --> attribution : au moins une PROPOSE
    attribution --> attente_confirmation : ACCEPT au meilleur, REJECT aux autres
    attente_confirmation --> conclue : le livreur confirme
    attente_confirmation --> collecte : refus ou délai, nouveau tour sans ce livreur
    sans_offre --> collecte : relance, rayon élargi
    sans_offre --> abandon : trop de tentatives, alerte au répartiteur
    conclue --> [*]
    abandon --> [*]
```

Chaque offre reçoit un résultat (`ResultatOffre`) :

| Résultat | Signification |
|---|---|
| `proposee` | PROPOSE reçu, en attente de décision |
| `retenue` | ACCEPT-PROPOSAL envoyé (au plus une par commande et par tour, contrainte de la base) |
| `rejetee` | REJECT-PROPOSAL envoyé |
| `refusee` | REFUSE de l'agent (saturé, hors zone, colis trop lourd, GPS trop ancien) |
| `declinee` | le livreur a décliné ou n'a pas répondu à temps |
| `expiree` | aucune réponse de l'agent dans le délai de collecte |

## 2.5 Paiement (`EtatPaiement`, F-51 à F-55)

```mermaid
stateDiagram-v2
    [*] --> en_attente : paiement initié
    en_attente --> confirme : le prestataire confirme
    en_attente --> echoue : refus ou délai dépassé
    confirme --> rembourse : remboursement total ou partiel (F-55)
    echoue --> [*]
    rembourse --> [*]
    confirme --> [*]
```

Un seul paiement `confirme` par commande (contrainte de la base).

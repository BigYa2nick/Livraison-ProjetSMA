# Dossier de conception : Livraison SMA

Livrable du lot 1 (cahier des charges v3, § 8.1) : cas d'utilisation, modèle de données, diagrammes
d'états et de séquence du protocole, maquettes des écrans. S'y ajoutent le budget d'exploitation (§ 10.1)
et les décisions d'architecture. Version du 20 octobre 2026.

Tous les chemins partent de la **racine du dépôt**, le dossier `livraison-sma/`.

## 1. Fichiers livrés et où les placer

Tous les fichiers de ce dossier sont **NOUVEAUX** : rien n'est à remplacer.

```
livraison-sma/
└── docs/
    └── conception/
        ├── README.md                        (ce fichier)
        ├── 01-cas-utilisation.md
        ├── 02-diagrammes-etats.md
        ├── 03-sequences-protocole.md
        ├── 04-maquettes.md
        ├── 05-budget-exploitation.md
        ├── 06-decisions.md
        └── maquettes/
            ├── M-01-livreur-connexion.svg
            ├── M-02-livreur-accueil.svg
            ├── M-03-livreur-proposition.svg
            ├── M-04-livreur-course.svg
            ├── M-05-livreur-remise.svg
            ├── M-06-client-nouvelle-commande.svg
            ├── M-07-client-suivi.svg
            ├── M-08-repartiteur-console.svg
            └── M-09-admin-dossiers.svg
```

Le dossier `maquettes/` doit rester **à côté** de `04-maquettes.md` : le chapitre y renvoie par des liens
relatifs, qui se cassent si on déplace l'un sans l'autre.

## 2. Contenu

| Fichier (chemin complet depuis la racine) | Contenu | Exigence du cahier (§ 8.1 ou 10.1) |
|---|---|---|
| `docs/conception/01-cas-utilisation.md` | 6 acteurs, 4 diagrammes, 30 cas d'utilisation rattachés aux exigences F-xx et aux lots, scénario détaillé de commande | Cas d'utilisation |
| `docs/modele-donnees.md` (déjà en place) | Schéma de la base, choix de conception | Modèle de données |
| `docs/conception/02-diagrammes-etats.md` | États de la commande, du dossier livreur, de l'agent Livreur, d'une conversation d'appel d'offres, du paiement | Diagrammes d'états |
| `docs/conception/03-sequences-protocole.md` | Agents, messages FIPA-ACL, coût d'une offre, 6 séquences (passage en ligne, attribution, relance, refus, livraison, concurrence) | Diagrammes de séquence |
| `docs/conception/04-maquettes.md` + `docs/conception/maquettes/*.svg` | 9 écrans en fil de fer (5 iOS, 2 web client, 2 web console) | Maquettes des écrans |
| `docs/conception/05-budget-exploitation.md` | Coûts mensuels en XOF pour deux scénarios, sources, limites | Budget d'exploitation |
| `docs/conception/06-decisions.md` | 6 décisions d'architecture (actées ou proposées) | Complément |

## 3. Comment lire les diagrammes

Les diagrammes sont en **Mermaid** (texte dans les fichiers `.md`) : GitHub les affiche directement. Dans
VS Code, installer l'extension « Markdown Preview Mermaid Support », puis ouvrir l'aperçu du fichier
(`Ctrl + Maj + V`). Les maquettes `.svg` s'ouvrent dans un navigateur ou dans l'aperçu de VS Code.

Tous les diagrammes ont été vérifiés avec l'analyseur officiel de Mermaid : ils sont syntaxiquement
valides.

## 4. À valider avant le lot 2

| Décision | Sujet | Statut |
|---|---|---|
| D-02 | Les agents tournent dans un processus séparé de l'API | **proposée** |
| D-03 | Redis transporte les événements entre l'API et les agents | **proposée** |
| D-04 | PostgreSQL est la seule source de vérité, Redis ne sert qu'au temps réel | **proposée** |
| D-06 | Seul le Dispatcher attribue | **proposée** |

Voir `docs/conception/06-decisions.md`. Si l'une d'elles change, les séquences S1 à S6 sont à ajuster.

## 5. Limites

- Les maquettes sont des **fils de fer** : elles fixent le contenu et le comportement des écrans, pas le
  design (couleurs, polices, icônes).
- Le budget repose sur des tarifs **à confirmer par devis** ; deux postes sont sans source et signalés.
- Le coût d'une offre (`03-sequences-protocole.md`) doit rester cohérent avec
  `src/livraison/modele/cout.py`, que je n'ai pas relu pour ce dossier.
- Le dossier est écrit en Markdown, pour être versionné avec le code. S'il faut le rendre en Word ou en
  PDF pour un enseignant, c'est une conversion à faire à part.

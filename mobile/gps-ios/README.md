# Livreur GPS : prototype React Native / Expo (iOS)

Prototype du suivi GPS du livreur pour le lot 1 du projet **Livraison SMA**. Il se branche sur le
serveur FastAPI existant (`/auth/code`, `/auth/verifier`, `/livreurs/moi/...`) et fait apparaître
le livreur sur la carte du répartiteur. Il remplace, côté iPhone, le prototype Flet de
`mobile/livreur/`.

## Ce que fait l'application

- Connexion par code SMS (le code simulé s'affiche dans la console d'uvicorn) et création du profil de test (moto, 25 kg).
- Bouton **Passer en ligne / hors ligne**.
- Deux sources de position :
  - **GPS de l'iPhone**, application ouverte au premier plan ;
  - **Simulation** d'un trajet Plateau → Cocody, utile pour voir bouger le point sur la carte sans se déplacer.
- Envoi fiable : si le Wi-Fi coupe, les positions s'accumulent (500 au maximum) puis partent dans l'ordre au retour du réseau.
- Mesures affichées en direct : trous dans la réception du GPS, retard mesure → serveur, durée des requêtes, positions envoyées / en attente / refusées / perdues.

## Arborescence

```
gps-ios/
├── App.tsx                  assemble les écrans et le journal
├── index.ts                 point d'entrée Expo
├── app.json                 configuration Expo (permission de localisation, réseau local)
├── package.json             dépendances à versions fixées (Expo SDK 57)
├── tsconfig.json
└── src/
    ├── config.ts            tous les paramètres : adresse du serveur, cadence, seuils
    ├── clientApi.ts         client HTTP (jetons, rafraîchissement, délai de 10 s)
    ├── erreurs.ts           ErreurReseau / ErreurApi
    ├── emetteur.ts          file d'envoi fiable des positions
    ├── cadence.ts           détection des trous dans le flux GPS
    ├── simulateur.ts        faux GPS (trajet à Abidjan)
    ├── instant.ts           contrôle de l'horodatage des mesures
    ├── useCollecte.ts       collecte GPS / simulation + bascule en ligne
    ├── journal.ts           journal affiché à l'écran
    ├── stockage.ts          mémorise l'adresse du serveur et le numéro
    ├── ecrans/              EcranConnexion.tsx, EcranSuivi.tsx
    └── ui/                  Journal.tsx, theme.ts
```

## Prérequis

- Node.js récent et l'application **Expo Go** sur l'iPhone, (l'App Store n'installe que la dernière version : aujourd'hui elle gère le **SDK 57**, et le projet est figé sur ce SDK ; si Expo Go se met à jour vers un autre SDK, il faudra migrer le projet).
- L'iPhone et le PC sur le **même Wi-Fi**.
- Le serveur lancé avec `--host 0.0.0.0` (déjà le cas dans le récap) et le port 8000 ouvert dans le pare-feu Windows.

## Installation et lancement

```powershell
# 1. Serveur (dans livraison-sma, venv activé, Docker démarré)
docker compose up -d
uvicorn livraison.api.main:app --host 0.0.0.0 --reload --reload-dir src

# 2. Application (dans mobile/gps-ios)
npm install
npx expo start
```

Scanner le QR code avec l'appareil photo de l'iPhone : Expo Go s'ouvre. Au premier lancement,
iOS demande l'accès au **réseau local** : accepter.

Avant de lancer, mettre l'adresse IPv4 Wi-Fi du PC (commande `ipconfig`) dans `SERVEUR_PAR_DEFAUT`
de `src/config.ts`, ou la saisir à l'écran de connexion (elle est ensuite mémorisée). Cette
adresse peut changer d'un jour à l'autre.

Vérification rapide du réseau : ouvrir `http://<IP du PC>:8000/docs` dans Safari sur l'iPhone.

Le numéro de test (`07 00 00 00 02`) doit être **différent** de celui du répartiteur et de ceux
des autres livreurs. Contrôle de typage : `npm run typecheck`.

## Scénarios de test à faire sur le terrain

1. **Simulation** : passer en ligne en mode Simulation, vérifier que le point bouge sur la carte du répartiteur.
2. **GPS à l'arrêt** : mode GPS, iPhone posé, vérifier la position et la précision.
3. **GPS en marchant** (5 à 10 min dehors) : relever les trous et le retard dans les statistiques.
4. **Coupure Wi-Fi** pendant 30 s : « en attente » monte, puis revient à 0 sans position perdue.
5. **Écran verrouillé / autre application** : constater l'arrêt du suivi (voir les limites) et le noter.

Reporter les résultats dans `docs/prototype-gps.md` du dépôt.

## Choix techniques et limites connues

- **Premier plan uniquement.** Dans Expo Go sur iOS, `TaskManager` ne permet pas l'exécution en arrière-plan : il faut un development build (d'après la doc Expo du SDK 58, à revérifier pour le SDK 57). Le cahier des charges v3 classe le suivi écran verrouillé en extension. L'application garde donc l'écran allumé tant qu'elle est en ligne.
- **Cadence.** `timeInterval` n'existe que sur Android ; sur iOS, le système envoie environ une position par seconde en mouvement. L'application n'en envoie qu'une toutes les 5 s (`INTERVALLE_S`) et compte les autres comme « espacées ».
- **Précision.** `Accuracy.High` (de l'ordre de 10 m) : suffisant en ville et plus économe que `BestForNavigation`.
- **Horodatage.** Flet avait produit des mesures datées de 1970 sous Windows. Ici, un horodatage absurde est remplacé par l'heure du téléphone **et compté** (« horodatages GPS corrigés »), car le retard affiché devient alors trop optimiste.
- **Jetons non conservés** entre deux lancements : on se reconnecte à chaque fois (prototype). Un stockage sécurisé (`expo-secure-store`) est à prévoir pour la vraie application.
- **HTTP en clair** autorisé uniquement vers le réseau local (`NSAllowsLocalNetworking`). La production demandera du HTTPS.

## État actuel

Vérifié :
- le typage TypeScript passe (`tsc --noEmit`, mode strict) sur un projet Expo SDK 57 neuf, et les versions de `package.json` sont celles que `npx expo install` impose pour ce SDK ;
- la logique pure (file d'envoi, cadence, horodatage, simulateur) a été testée par des scripts : réseau coupé puis rétabli, refus 422, erreur 500, file pleine, envois concurrents, détection de trous ;
- le client API a été testé contre un faux serveur qui reproduit le contrat lu dans les sources FastAPI : corps des requêtes, rôle `livreur`, rafraîchissement du jeton sur 401, erreurs 422, serveur arrêté.

**Non vérifié** (aucun iPhone à disposition lors de l'écriture) : exécution dans Expo Go, GPS réel, fenêtre de permission, écran maintenu allumé, accès au serveur depuis le Wi-Fi (pare-feu). Ce sont les points que les scénarios ci-dessus doivent confirmer.
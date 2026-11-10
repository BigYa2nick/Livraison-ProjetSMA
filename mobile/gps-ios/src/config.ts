/** Paramètres du prototype, regroupés en un seul endroit. */

import { Accuracy } from "expo-location";

// -- Connexion au serveur -------------------------------------------------------------

/** Adresse IPv4 Wi-Fi du PC qui fait tourner uvicorn (commande `ipconfig`). Elle peut changer. */
export const SERVEUR_PAR_DEFAUT = "http://192.168.32.1:8000";

/** Numéro de test. Il doit différer de celui du répartiteur et de ceux des autres livreurs. */
export const TELEPHONE_PAR_DEFAUT = "07 00 00 00 02";

/** Profil créé automatiquement à la première connexion (valeurs de test). */
export const PROFIL_PAR_DEFAUT = { typeVehicule: "moto", capaciteKg: 25 } as const;

/** Durée maximale d'une requête avant de la considérer comme perdue. */
export const DELAI_REQUETE_MS = 10_000;

// -- Collecte GPS ---------------------------------------------------------------------

/** Précision demandée à l'iPhone. `High` suffit pour du suivi urbain et ménage la batterie. */
export const PRECISION_GPS = Accuracy.High;

/** Cadence de la simulation, et écart minimal entre deux envois en mode GPS (en secondes). */
export const INTERVALLE_S = 5;

/** Écart entre deux positions GPS à partir duquel on signale un « trou » (en secondes). */
export const SEUIL_TROU_S = 15;

// -- Envoi fiable ---------------------------------------------------------------------

/** Positions gardées en mémoire quand le réseau est coupé ; au-delà, les plus anciennes sautent. */
export const TAILLE_FILE_MAX = 500;

/** Réponses du serveur pour lesquelles renvoyer la même position ne servirait à rien. */
export const STATUTS_DEFINITIFS: readonly number[] = [409, 422];

// -- Propositions de course (F-21) -----------------------------------------------------

/** Écart entre deux lectures de la course proposée (en millisecondes), application au premier plan. */
export const INTERVALLE_PROPOSITION_MS = 2_000;

/** Délai de confirmation par défaut du serveur (ATTRIBUTION_CONFIRMATION_S) : sert à la barre de temps. */
export const DUREE_CONFIRMATION_S = 30;

/** Vibration à l'arrivée d'une course (sur iOS, le système impose une vibration unique). */
export const MOTIF_VIBRATION_MS = [0, 400, 200, 400];

// -- Horodatage -----------------------------------------------------------------------

/** Une mesure plus de 60 s dans le futur ou plus d'1 h dans le passé est jugée aberrante. */
export const TOLERANCE_FUTUR_S = 60;
export const TOLERANCE_PASSE_S = 3600;

// -- Journal --------------------------------------------------------------------------

export const JOURNAL_MAX = 150;

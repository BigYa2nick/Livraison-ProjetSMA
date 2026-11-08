/** Collecte des positions (GPS de l'iPhone ou simulation) et envoi au serveur. */

import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import * as Location from "expo-location";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState, Linking } from "react-native";

import type { ClientApi } from "./clientApi";
import { SuiviCadence } from "./cadence";
import { INTERVALLE_S, PRECISION_GPS, SEUIL_TROU_S } from "./config";
import { Emetteur } from "./emetteur";
import { messageErreur } from "./erreurs";
import { instantMesure } from "./instant";
import type { Log } from "./journal";
import { Simulateur } from "./simulateur";

export type Mode = "gps" | "simule";

export const MODES: Record<Mode, string> = {
  gps: "GPS de l'iPhone (application ouverte)",
  simule: "Simulation (sans GPS)",
};

export interface Statistiques {
  dureeMin: number;
  recues: number;
  trous: number;
  trouMaxS: number;
  espacees: number;
  horodatagesCorriges: number;
  envoyees: number;
  enAttente: number;
  refusees: number;
  perdues: number;
  echecsReseau: number;
  retardS: number | null;
  latenceMs: number | null;
  derniereErreur: string | null;
}

const VEILLE = "suivi-gps"; // garde l'écran allumé : sans cela l'iPhone suspend le suivi

export function useCollecte(api: ClientApi, log: Log) {
  const emetteur = useMemo(() => new Emetteur((p) => api.envoyerPosition(p)), [api]);
  const cadence = useMemo(() => new SuiviCadence(SEUIL_TROU_S), []);

  const [enLigne, setEnLigne] = useState(false);
  const [mode, setMode] = useState<Mode>("gps");
  const [permission, setPermission] = useState("inconnue");
  const [stats, setStats] = useState<Statistiques | null>(null);

  const enLigneRef = useRef(false);
  const occupe = useRef(false);
  const abonnement = useRef<Location.LocationSubscription | null>(null);
  const minuteur = useRef<ReturnType<typeof setInterval> | null>(null);
  const debut = useRef<Date | null>(null);
  const dernierEnvoiMs = useRef(0);
  const espacees = useRef(0);
  const corriges = useRef(0);

  const rafraichirStats = useCallback(() => {
    setStats({
      dureeMin: debut.current ? (Date.now() - debut.current.getTime()) / 60_000 : 0,
      recues: cadence.recues,
      trous: cadence.nbTrous,
      trouMaxS: cadence.trouMaxS,
      espacees: espacees.current,
      horodatagesCorriges: corriges.current,
      envoyees: emetteur.envoyees,
      enAttente: emetteur.attente.length,
      refusees: emetteur.abandonnees,
      perdues: emetteur.perdues,
      echecsReseau: emetteur.echecsReseau,
      retardS: emetteur.dernierRetardS,
      latenceMs: emetteur.derniereLatenceMs,
      derniereErreur: emetteur.derniereErreur,
    });
  }, [cadence, emetteur]);

  // Les statistiques se rafraîchissent chaque seconde tant qu'on est en ligne.
  useEffect(() => {
    if (!enLigne) return;
    rafraichirStats();
    const horloge = setInterval(rafraichirStats, 1000);
    return () => clearInterval(horloge);
  }, [enLigne, rafraichirStats]);

  // -- traitement d'une position -------------------------------------------------------

  const traiter = useCallback(
    async (
      latitude: number,
      longitude: number,
      precision: number | null,
      instant: Date,
      espacer: boolean,
    ) => {
      const trou = cadence.enregistrer(instant);
      if (trou !== null) log(`Trou de ${trou.toFixed(0)} s entre deux positions GPS`, "avertissement");
      if (espacer) {
        const maintenant = Date.now();
        if (maintenant - dernierEnvoiMs.current < INTERVALLE_S * 1000) {
          espacees.current += 1; // trop proche de la précédente : comptée mais pas envoyée
          return;
        }
        dernierEnvoiMs.current = maintenant;
      }
      try {
        await emetteur.pousser({ latitude, longitude, precision_m: precision, mesure_le: instant });
      } catch (e) {
        log(`Envoi en échec inattendu : ${messageErreur(e)}`, "erreur");
      }
    },
    [cadence, emetteur, log],
  );

  // -- permissions ---------------------------------------------------------------------

  const assurerPermission = useCallback(async (): Promise<boolean> => {
    if (!(await Location.hasServicesEnabledAsync())) {
      log("La localisation de l'iPhone est désactivée : Réglages > Confidentialité > Service de localisation.", "erreur");
      return false;
    }
    const reponse = await Location.requestForegroundPermissionsAsync();
    setPermission(reponse.status);
    log(`Permission localisation : ${reponse.status}`);
    if (reponse.status !== "granted") {
      log(
        reponse.canAskAgain
          ? "Permission refusée : touchez « Autoriser la position »."
          : "Permission refusée définitivement : ouvrez les Réglages de l'iPhone.",
        "erreur",
      );
      return false;
    }
    return true;
  }, [log]);

  const autoriser = useCallback(async () => {
    try {
      await assurerPermission();
    } catch (e) {
      log(`Permission impossible : ${messageErreur(e)}`, "erreur");
    }
  }, [assurerPermission, log]);

  const ouvrirReglages = useCallback(async () => {
    try {
      await Linking.openSettings();
    } catch (e) {
      log(`Réglages indisponibles : ${messageErreur(e)}`, "erreur");
    }
  }, [log]);

  // -- démarrage / arrêt de la source de position --------------------------------------

  const arreterSource = useCallback(() => {
    abonnement.current?.remove();
    abonnement.current = null;
    if (minuteur.current !== null) {
      clearInterval(minuteur.current);
      minuteur.current = null;
    }
  }, []);

  const demarrerSource = useCallback(
    async (modeChoisi: Mode) => {
      if (modeChoisi === "gps") {
        // `timeInterval` n'existe que sur Android : sur iOS, c'est le système qui cadence les
        // positions (environ une par seconde en mouvement). L'espacement de 5 s est fait dans `traiter`.
        abonnement.current = await Location.watchPositionAsync(
          { accuracy: PRECISION_GPS, distanceInterval: 0 },
          (loc) => {
            const { instant, horlogeCorrigee } = instantMesure(loc.timestamp);
            if (horlogeCorrigee) {
              corriges.current += 1;
              if (corriges.current === 1) {
                log("Horodatage GPS aberrant : l'heure du téléphone est utilisée à la place.", "avertissement");
              }
            }
            const { latitude, longitude, accuracy } = loc.coords;
            void traiter(latitude, longitude, accuracy !== null && accuracy >= 0 ? accuracy : null, instant, true);
          },
          (raison) => log(`Erreur GPS : ${raison}`, "erreur"),
        );
        log("Flux GPS démarré (application au premier plan).");
      } else {
        const simulateur = new Simulateur();
        minuteur.current = setInterval(() => {
          const p = simulateur.avancer(INTERVALLE_S);
          void traiter(p.latitude, p.longitude, p.precision_m, new Date(), false);
        }, INTERVALLE_S * 1000);
        log("Simulation démarrée.");
      }
    },
    [log, traiter],
  );

  const quitterLigneLocalement = useCallback(() => {
    arreterSource();
    enLigneRef.current = false;
    setEnLigne(false);
    deactivateKeepAwake(VEILLE);
  }, [arreterSource]);

  // -- bascule en ligne / hors ligne ---------------------------------------------------

  const basculerEnLigne = useCallback(async () => {
    if (occupe.current) return;
    occupe.current = true;
    try {
      if (!enLigneRef.current) {
        if (mode === "gps" && !(await assurerPermission())) return;
        try {
          await api.passerEnLigne();
        } catch (e) {
          log(`Impossible de passer en ligne : ${messageErreur(e)}`, "erreur");
          return;
        }
        cadence.reinitialiser();
        debut.current = new Date();
        dernierEnvoiMs.current = 0;
        espacees.current = 0;
        corriges.current = 0;
        enLigneRef.current = true;
        setEnLigne(true);
        await activateKeepAwakeAsync(VEILLE);
        log(`En ligne, mode : ${MODES[mode]}`, "ok");
        try {
          await demarrerSource(mode);
        } catch (e) {
          log(`Démarrage de la source de position impossible : ${messageErreur(e)}`, "erreur");
          quitterLigneLocalement();
          try {
            await api.passerHorsLigne();
          } catch (e2) {
            log(`Le serveur n'a pas été prévenu du retour hors ligne : ${messageErreur(e2)}`, "avertissement");
          }
        }
      } else {
        quitterLigneLocalement();
        try {
          await api.passerHorsLigne();
        } catch (e) {
          log(`Hors ligne en local, mais le serveur n'a pas répondu : ${messageErreur(e)}`, "avertissement");
        }
        log("Hors ligne.");
        rafraichirStats();
      }
    } finally {
      occupe.current = false;
    }
  }, [api, assurerPermission, cadence, demarrerSource, log, mode, quitterLigneLocalement, rafraichirStats]);

  const choisirMode = useCallback(
    (nouveau: Mode) => {
      if (enLigneRef.current) {
        log("Passez hors ligne avant de changer de mode.", "avertissement");
        return;
      }
      setMode(nouveau);
      log(`Mode choisi : ${MODES[nouveau]}`);
    },
    [log],
  );

  // -- cycle de vie de l'application ---------------------------------------------------

  useEffect(() => {
    const abo = AppState.addEventListener("change", (etat) => {
      if (!enLigneRef.current) return;
      if (etat === "active") {
        emetteur.vider().catch((e) => log(`Renvoi en échec : ${messageErreur(e)}`, "erreur"));
      } else {
        log(
          "Application en arrière-plan : le suivi iOS s'arrête tant qu'elle n'est pas rouverte (Expo Go).",
          "avertissement",
        );
      }
    });
    return () => abo.remove();
  }, [emetteur, log]);

  // Nettoyage à la fermeture de l'écran.
  useEffect(
    () => () => {
      arreterSource();
      deactivateKeepAwake(VEILLE);
    },
    [arreterSource],
  );

  return { enLigne, mode, permission, stats, autoriser, ouvrirReglages, basculerEnLigne, choisirMode };
}

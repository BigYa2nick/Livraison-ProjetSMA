/** Lecture de la course proposée au livreur, compte à rebours, confirmation et refus (F-21). */

import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Vibration } from "react-native";

import type { ClientApi, PropositionCourse } from "./clientApi";
import { INTERVALLE_PROPOSITION_MS, MOTIF_VIBRATION_MS } from "./config";
import { ErreurApi, messageErreur } from "./erreurs";
import type { Log } from "./journal";
import { cleProposition, echeanceMs, secondesRestantes } from "./proposition";

export type Envoi = "aucun" | "confirmation" | "refus";

const MESSAGE_INFO_MS = 10_000;
const REPONSES_GARDEES = 20;

/**
 * `actif` : le livreur est en ligne. La lecture s'arrête hors ligne et quand l'application passe en
 * arrière-plan (iOS suspend de toute façon le réseau de l'application).
 */
export function useProposition(api: ClientApi, log: Log, actif: boolean) {
  const [proposition, setProposition] = useState<PropositionCourse | null>(null);
  const [restantS, setRestantS] = useState(0);
  const [envoi, setEnvoi] = useState<Envoi>("aucun");
  const [info, setInfo] = useState<string | null>(null);
  const [premierPlan, setPremierPlan] = useState(AppState.currentState === "active");

  const echeance = useRef<number | null>(null);
  const cleCourante = useRef<string | null>(null);
  const repondues = useRef<string[]>([]); // courses déjà confirmées ou refusées, à ne plus afficher
  const envoiEnCours = useRef(false);
  const echecs = useRef(0);
  const lectureEnCours = useRef<Promise<void> | null>(null);

  const vider = useCallback(() => {
    cleCourante.current = null;
    echeance.current = null;
    setProposition(null);
    setRestantS(0);
  }, []);

  const appliquer = useCallback(
    (p: PropositionCourse | null, recuLeMs: number) => {
      if (envoiEnCours.current) return; // une réponse part : elle décide de l'affichage
      if (p === null) {
        if (cleCourante.current !== null) {
          log("La proposition n'est plus disponible (délai dépassé, ou course reprise).", "avertissement");
        }
        vider();
        return;
      }
      const cle = cleProposition(p);
      if (repondues.current.includes(cle)) return; // déjà répondu : le serveur finit son travail
      const nouvelle = cle !== cleCourante.current;
      echeance.current = echeanceMs(recuLeMs, p.secondes_restantes, nouvelle ? null : echeance.current);
      if (nouvelle) {
        cleCourante.current = cle;
        setInfo(null);
        log(`Nouvelle course proposée : ${p.secondes_restantes} s pour répondre.`, "ok");
        Vibration.vibrate(MOTIF_VIBRATION_MS);
      }
      setProposition(p);
      setRestantS(secondesRestantes(echeance.current, recuLeMs));
    },
    [log, vider],
  );

  /** Lit la proposition. Une seule lecture à la fois ; ne lève jamais d'erreur (elles sont journalisées). */
  const lire = useCallback((): Promise<void> => {
    if (lectureEnCours.current !== null) return lectureEnCours.current;
    const lecture = (async () => {
      try {
        const p = await api.lireProposition();
        if (echecs.current > 0) log("Les propositions sont de nouveau lues.", "ok");
        echecs.current = 0;
        appliquer(p, Date.now());
      } catch (e) {
        echecs.current += 1;
        if (echecs.current === 1) {
          log(`Lecture des propositions en échec : ${messageErreur(e)}`, "avertissement");
        }
      }
    })().finally(() => {
      lectureEnCours.current = null;
    });
    lectureEnCours.current = lecture;
    return lecture;
  }, [api, appliquer, log]);

  // -- premier plan ----------------------------------------------------------------------

  useEffect(() => {
    const abo = AppState.addEventListener("change", (etat) => setPremierPlan(etat === "active"));
    return () => abo.remove();
  }, []);

  // -- lecture régulière tant que le livreur est en ligne --------------------------------

  useEffect(() => {
    if (!actif || !premierPlan) return;
    let arrete = false;
    let minuteur: ReturnType<typeof setTimeout> | null = null;
    const boucle = async () => {
      await lire();
      if (!arrete) minuteur = setTimeout(boucle, INTERVALLE_PROPOSITION_MS);
    };
    void boucle();
    return () => {
      arrete = true;
      if (minuteur !== null) clearTimeout(minuteur);
    };
  }, [actif, premierPlan, lire]);

  // Hors ligne : plus aucune proposition à montrer.
  useEffect(() => {
    if (actif) return;
    vider();
    setInfo(null);
    echecs.current = 0;
  }, [actif, vider]);

  // -- compte à rebours ------------------------------------------------------------------

  const presente = proposition !== null;
  useEffect(() => {
    if (!presente) return;
    const horloge = setInterval(() => {
      if (echeance.current !== null) setRestantS(secondesRestantes(echeance.current, Date.now()));
    }, 250);
    return () => clearInterval(horloge);
  }, [presente]);

  // Le message de fin (« confirmée ») disparaît de lui-même.
  useEffect(() => {
    if (info === null) return;
    const minuteur = setTimeout(() => setInfo(null), MESSAGE_INFO_MS);
    return () => clearTimeout(minuteur);
  }, [info]);

  // -- réponse du livreur ----------------------------------------------------------------

  const repondre = useCallback(
    async (accepte: boolean) => {
      const courante = proposition;
      if (courante === null || envoiEnCours.current) return;
      envoiEnCours.current = true;
      setEnvoi(accepte ? "confirmation" : "refus");
      try {
        if (accepte) await api.confirmerCourse(courante.commande_id);
        else await api.declinerCourse(courante.commande_id);
        repondues.current = [...repondues.current, cleProposition(courante)].slice(-REPONSES_GARDEES);
        vider();
        if (accepte) {
          setInfo("Course confirmée : le serveur finalise l'attribution.");
          log("Course confirmée, en attente de l'attribution.", "ok");
        } else {
          log("Course déclinée : elle est proposée à un autre livreur.");
        }
      } catch (e) {
        log(`Réponse non transmise : ${messageErreur(e)}`, "erreur");
        if (e instanceof ErreurApi) {
          envoiEnCours.current = false; // le serveur a répondu : on relit son état (course expirée ?)
          await lire();
        }
      } finally {
        envoiEnCours.current = false;
        setEnvoi("aucun");
      }
    },
    [api, lire, log, vider, proposition],
  );

  return {
    proposition,
    restantS,
    envoi,
    info,
    expiree: proposition !== null && restantS === 0,
    confirmer: () => repondre(true),
    decliner: () => repondre(false),
  };
}

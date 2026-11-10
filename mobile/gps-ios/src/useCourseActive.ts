/** Gestion de la course active du livreur (F-22, F-23). */

import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";

import type { CourseActive, ClientApi, MotifIncident } from "./clientApi";
import { ErreurApi, messageErreur } from "./erreurs";
import type { Log } from "./journal";

const INTERVALLE_COURSE_ACTIVE_MS = 5_000;

export function useCourseActive(api: ClientApi, log: Log, actif: boolean) {
  const [course, setCourse] = useState<CourseActive | null>(null);
  const [actionEnCours, setActionEnCours] = useState<string | null>(null);
  const [erreurAction, setErreurAction] = useState<string | null>(null);
  const [premierPlan, setPremierPlan] = useState(AppState.currentState === "active");

  const enCoursRef = useRef(false);

  // Détecte le premier plan
  useEffect(() => {
    const abo = AppState.addEventListener("change", (etat) => setPremierPlan(etat === "active"));
    return () => abo.remove();
  }, []);

  const rafraichir = useCallback(async (): Promise<void> => {
    if (enCoursRef.current) return;
    enCoursRef.current = true;
    try {
      const c = await api.lireCourseActive();
      setCourse(c);
      setErreurAction(null);
    } catch (e) {
      // Ignoré si réseau temporairement instable
    } finally {
      enCoursRef.current = false;
    }
  }, [api]);

  // Polling périodique quand en ligne et au premier plan
  useEffect(() => {
    if (!actif || !premierPlan) return;
    void rafraichir();
    const intervalle = setInterval(rafraichir, INTERVALLE_COURSE_ACTIVE_MS);
    return () => clearInterval(intervalle);
  }, [actif, premierPlan, rafraichir]);

  const validerEnlevement = useCallback(async () => {
    if (!course) return;
    setActionEnCours("enlevement");
    setErreurAction(null);
    try {
      await api.enleverColis(course.commande_id);
      log("Colis enlevé avec succès. En route vers la livraison.", "ok");
      await rafraichir();
    } catch (e) {
      const msg = messageErreur(e);
      setErreurAction(msg);
      log(`Échec validation enlèvement : ${msg}`, "erreur");
    } finally {
      setActionEnCours(null);
    }
  }, [api, course, log, rafraichir]);

  const validerLivraison = useCallback(
    async (codeConfirmation: string) => {
      if (!course) return;
      if (!codeConfirmation.trim()) {
        setErreurAction("Saisis le code secret à 6 chiffres donné par le destinataire.");
        return;
      }
      setActionEnCours("livraison");
      setErreurAction(null);
      try {
        await api.livrerColis(course.commande_id, codeConfirmation.trim());
        log("Course livrée et clôturée avec succès !", "ok");
        setCourse(null);
        await rafraichir();
      } catch (e) {
        const msg = messageErreur(e);
        setErreurAction(msg);
        log(`Échec validation livraison : ${msg}`, "erreur");
      } finally {
        setActionEnCours(null);
      }
    },
    [api, course, log, rafraichir],
  );

  const signalerIncident = useCallback(
    async (motif: MotifIncident) => {
      if (!course) return;
      setActionEnCours("incident");
      setErreurAction(null);
      try {
        await api.signalerIncident(course.commande_id, motif);
        log(`Incident « ${motif} » signalé. La tentative de livraison est terminée.`, "erreur");
        setCourse(null);
        await rafraichir();
      } catch (e) {
        const msg = messageErreur(e);
        setErreurAction(msg);
        log(`Échec du signalement d'incident : ${msg}`, "erreur");
      } finally {
        setActionEnCours(null);
      }
    },
    [api, course, log, rafraichir],
  );

  return {
    course,
    actionEnCours,
    erreurAction,
    rafraichir,
    validerEnlevement,
    validerLivraison,
    signalerIncident,
  };
}

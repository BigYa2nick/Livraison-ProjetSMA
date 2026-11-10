/** Règles pures de l'écran de proposition de course : échéance, secondes restantes, libellés. */

import type { PropositionCourse } from "./clientApi";

/** Écart toléré entre deux échéances calculées à partir de lectures successives (en millisecondes). */
export const TOLERANCE_ECHEANCE_MS = 2_000;

/** Identifie une proposition : une même commande peut être reproposée à un autre tour. */
export function cleProposition(p: Pick<PropositionCourse, "commande_id" | "tour">): string {
  return `${p.commande_id}:${p.tour}`;
}

/**
 * Instant (horloge du téléphone) où la proposition expire.
 *
 * On part de `secondes_restantes`, calculé par le serveur, et non de `expire_le` comparé à l'heure du
 * téléphone : les horloges du PC et de l'iPhone peuvent être décalées (déjà vu : positions refusées).
 * Si une lecture donne une échéance proche de la précédente, on garde la précédente : le compte à
 * rebours ne saute pas à chaque lecture (le serveur arrondit à la seconde).
 */
export function echeanceMs(
  recuLeMs: number,
  secondesRestantes: number,
  precedente: number | null,
  tolerance: number = TOLERANCE_ECHEANCE_MS,
): number {
  const nouvelle = recuLeMs + Math.max(0, secondesRestantes) * 1000;
  if (precedente !== null && Math.abs(nouvelle - precedente) <= tolerance) return precedente;
  return nouvelle;
}

/** Secondes à afficher : arrondies au-dessus, jamais négatives. */
export function secondesRestantes(echeance: number, maintenantMs: number): number {
  return Math.max(0, Math.ceil((echeance - maintenantMs) / 1000));
}

/** Part du délai qui reste, entre 0 et 1 (pour la barre de temps). */
export function partRestante(restantS: number, dureeS: number): number {
  if (dureeS <= 0) return 0;
  return Math.min(1, Math.max(0, restantS / dureeS));
}

export function estUrgente(p: Pick<PropositionCourse, "priorite">): boolean {
  return p.priorite === "urgente";
}

const TAILLES: Record<string, string> = { petit: "Petit colis", moyen: "Colis moyen", grand: "Grand colis" };

export function libelleColis(p: Pick<PropositionCourse, "taille_colis" | "poids_kg">): string {
  const taille = TAILLES[p.taille_colis] ?? p.taille_colis;
  if (p.poids_kg === null) return taille;
  return `${taille} · ${String(p.poids_kg).replace(".", ",")} kg`;
}

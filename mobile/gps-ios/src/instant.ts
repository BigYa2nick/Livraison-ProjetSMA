/** Heure de mesure d'une position GPS, avec contrôle de cohérence. */

import { TOLERANCE_FUTUR_S, TOLERANCE_PASSE_S } from "./config";

export interface InstantMesure {
  instant: Date;
  /** true si l'horodatage fourni par le GPS était absurde et que l'heure du téléphone l'a remplacé. */
  horlogeCorrigee: boolean;
}

/**
 * Convertit l'horodatage du GPS (millisecondes depuis 1970) en date.
 *
 * Un horodatage absurde a déjà été observé avec Flet sous Windows (mesure datée de 1970).
 * Dans ce cas on prend l'heure du téléphone, mais on le signale : l'appelant doit le journaliser,
 * car le « retard mesure -> serveur » devient alors trop optimiste.
 */
export function instantMesure(
  horodatageMs: number | null | undefined,
  maintenant: Date = new Date(),
): InstantMesure {
  if (typeof horodatageMs === "number" && Number.isFinite(horodatageMs)) {
    const ecartS = (maintenant.getTime() - horodatageMs) / 1000;
    if (ecartS >= -TOLERANCE_FUTUR_S && ecartS <= TOLERANCE_PASSE_S) {
      return { instant: new Date(horodatageMs), horlogeCorrigee: false };
    }
  }
  return { instant: maintenant, horlogeCorrigee: true };
}

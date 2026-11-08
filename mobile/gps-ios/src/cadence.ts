/** Repère les trous dans la réception des positions du GPS (écran éteint, économie d'énergie...). */

export class SuiviCadence {
  recues = 0;
  nbTrous = 0;
  trouMaxS = 0;
  private derniere: Date | null = null;

  constructor(readonly seuilS: number) {}

  /** Note une position reçue. Renvoie la durée du trou (en secondes) s'il dépasse le seuil. */
  enregistrer(instant: Date): number | null {
    this.recues += 1;
    let trou: number | null = null;
    if (this.derniere !== null) {
      const ecart = (instant.getTime() - this.derniere.getTime()) / 1000;
      this.trouMaxS = Math.max(this.trouMaxS, ecart);
      if (ecart > this.seuilS) {
        this.nbTrous += 1;
        trou = ecart;
      }
    }
    this.derniere = instant;
    return trou;
  }

  reinitialiser(): void {
    this.recues = 0;
    this.nbTrous = 0;
    this.trouMaxS = 0;
    this.derniere = null;
  }
}

/** Envoi fiable des positions : celles qui n'ont pas pu partir sont gardées puis renvoyées. */

import type { PositionAcceptee, PositionAEnvoyer } from "./clientApi";
import { STATUTS_DEFINITIFS, TAILLE_FILE_MAX } from "./config";
import { ErreurApi, ErreurReseau } from "./erreurs";

export type FonctionEnvoi = (position: PositionAEnvoyer) => Promise<PositionAcceptee>;

/**
 * File d'envoi des positions, dans l'ordre chronologique.
 *
 * - réseau coupé : les positions s'accumulent (TAILLE_FILE_MAX au maximum), puis partent au
 *   prochain envoi réussi ;
 * - position refusée par le serveur (hors ligne, horodatage invalide) : abandonnée ;
 * - toute autre erreur inattendue est propagée à l'appelant, jamais avalée.
 */
export class Emetteur {
  readonly attente: PositionAEnvoyer[] = [];
  envoyees = 0;
  perdues = 0; // supprimées parce que la file était pleine
  abandonnees = 0; // refusées par le serveur
  echecsReseau = 0;
  dernierRetardS: number | null = null; // mesure sur téléphone -> réception serveur
  derniereLatenceMs: number | null = null; // durée de la requête
  derniereErreur: string | null = null;

  private enCours: Promise<void> | null = null;

  constructor(
    private readonly envoyer: FonctionEnvoi,
    private readonly tailleMax: number = TAILLE_FILE_MAX,
  ) {}

  async pousser(position: PositionAEnvoyer): Promise<void> {
    if (this.attente.length >= this.tailleMax) {
      this.attente.shift();
      this.perdues += 1;
    }
    this.attente.push(position);
    await this.vider();
  }

  /** Vide la file. Un seul vidage à la fois : un appel concurrent attend celui en cours. */
  vider(): Promise<void> {
    if (this.enCours === null) {
      this.enCours = this.viderInterne().finally(() => {
        this.enCours = null;
      });
    }
    return this.enCours;
  }

  private retirer(position: PositionAEnvoyer): void {
    // La file a pu perdre sa tête pendant l'envoi (file pleine) : on ne retire que la bonne.
    if (this.attente[0] === position) this.attente.shift();
  }

  private async viderInterne(): Promise<void> {
    while (this.attente.length > 0) {
      const position = this.attente[0];
      const debut = performance.now();
      let reponse: PositionAcceptee;
      try {
        reponse = await this.envoyer(position);
      } catch (e) {
        if (e instanceof ErreurReseau) {
          this.echecsReseau += 1;
          this.derniereErreur = e.message;
          return; // on réessaiera au prochain appel
        }
        if (e instanceof ErreurApi) {
          this.derniereErreur = `${e.statut} ${e.detail}`;
          if (STATUTS_DEFINITIFS.includes(e.statut)) {
            this.retirer(position);
            this.abandonnees += 1;
            continue;
          }
          return;
        }
        throw e;
      }
      this.retirer(position);
      this.envoyees += 1;
      this.derniereErreur = null;
      this.derniereLatenceMs = performance.now() - debut;
      this.dernierRetardS = reponse.retard_s;
    }
  }
}

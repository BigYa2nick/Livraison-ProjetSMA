/** Erreurs du client HTTP et mise en forme pour l'affichage. */

/** Le serveur n'a pas pu être joint (pas de réseau, délai dépassé...). On peut réessayer. */
export class ErreurReseau extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ErreurReseau";
    Object.setPrototypeOf(this, ErreurReseau.prototype);
  }
}

/** Le serveur a répondu par une erreur HTTP. */
export class ErreurApi extends Error {
  readonly statut: number;
  readonly detail: string;

  constructor(statut: number, detail: string) {
    super(`${statut} ${detail}`);
    this.name = "ErreurApi";
    this.statut = statut;
    this.detail = detail;
    Object.setPrototypeOf(this, ErreurApi.prototype);
  }
}

export function messageErreur(e: unknown): string {
  if (e instanceof ErreurApi) return `${e.statut} ${e.detail}`;
  if (e instanceof ErreurReseau) return `Réseau : ${e.message}`;
  if (e instanceof Error) return `${e.name}: ${e.message}`;
  return String(e);
}

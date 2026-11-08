/** Client HTTP du serveur Livraison SMA, miroir de `mobile/livreur/src/client_api.py`. */

import { DELAI_REQUETE_MS } from "./config";
import { ErreurApi, ErreurReseau } from "./erreurs";

export type TypeVehicule = "moto" | "voiture" | "velo";

interface Jetons {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  nouveau_compte: boolean;
}

export interface PositionAEnvoyer {
  latitude: number;
  longitude: number;
  precision_m: number | null;
  mesure_le: Date;
}

export interface PositionAcceptee {
  recu_le: string;
  retard_s: number;
}

type Methode = "GET" | "POST" | "PUT";

export class ClientApi {
  private readonly base: string;
  private acces: string | null = null;
  private rafraichissement: string | null = null;

  constructor(adresse: string) {
    const base = adresse.trim().replace(/\/+$/, "");
    if (!/^https?:\/\/[^\s/]+$/.test(base)) {
      throw new Error(
        "Adresse du serveur invalide : attendu http://<IP du PC>:8000, sans espace ni chemin.",
      );
    }
    this.base = base;
  }

  // -- appel générique -----------------------------------------------------------------

  private async appeler(
    methode: Methode,
    chemin: string,
    corps: unknown,
    avecAuth: boolean,
  ): Promise<Response> {
    const controleur = new AbortController();
    const minuteur = setTimeout(() => controleur.abort(), DELAI_REQUETE_MS);
    const entetes: Record<string, string> = { Accept: "application/json" };
    if (corps !== undefined) entetes["Content-Type"] = "application/json";
    if (avecAuth && this.acces) entetes.Authorization = `Bearer ${this.acces}`;
    try {
      return await fetch(`${this.base}${chemin}`, {
        method: methode,
        headers: entetes,
        body: corps === undefined ? undefined : JSON.stringify(corps),
        signal: controleur.signal,
      });
    } catch (e) {
      // coupure réseau, délai dépassé, serveur arrêté, pare-feu...
      throw new ErreurReseau(e instanceof Error ? `${e.name}: ${e.message}` : String(e));
    } finally {
      clearTimeout(minuteur);
    }
  }

  private async detail(r: Response): Promise<string> {
    const texte = await r.text();
    try {
      const detail = JSON.parse(texte)?.detail;
      if (detail === undefined) return texte;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch {
      return texte; // la réponse n'est pas du JSON : on l'affiche telle quelle
    }
  }

  private async requete<T>(
    methode: Methode,
    chemin: string,
    corps?: unknown,
    avecAuth = true,
  ): Promise<T> {
    for (let essai = 0; essai < 2; essai++) {
      const r = await this.appeler(methode, chemin, corps, avecAuth);
      if (r.status === 401 && avecAuth && essai === 0 && (await this.rafraichir())) {
        continue; // jeton d'accès expiré : on réessaie avec le nouveau
      }
      if (r.status >= 400) throw new ErreurApi(r.status, await this.detail(r));
      return (await r.json()) as T;
    }
    throw new ErreurApi(401, "Session expirée.");
  }

  private async rafraichir(): Promise<boolean> {
    if (!this.rafraichissement) return false;
    const r = await this.appeler(
      "POST",
      "/auth/rafraichir",
      { refresh_token: this.rafraichissement },
      false,
    );
    if (r.status !== 200) return false;
    this.memoriser((await r.json()) as Jetons);
    return true;
  }

  private memoriser(jetons: Jetons): void {
    this.acces = jetons.access_token;
    this.rafraichissement = jetons.refresh_token;
  }

  // -- authentification ----------------------------------------------------------------

  async demanderCode(telephone: string): Promise<void> {
    await this.requete("POST", "/auth/code", { telephone }, false);
  }

  /** Valide le code SMS. Renvoie true si le compte vient d'être créé. */
  async verifierCode(telephone: string, code: string): Promise<boolean> {
    const jetons = await this.requete<Jetons>(
      "POST",
      "/auth/verifier",
      { telephone, code, role: "livreur" },
      false,
    );
    this.memoriser(jetons);
    return jetons.nouveau_compte;
  }

  // -- livreur -------------------------------------------------------------------------

  async definirProfil(
    typeVehicule: TypeVehicule,
    capaciteKg: number,
    immatriculation: string | null = null,
  ): Promise<void> {
    await this.requete("PUT", "/livreurs/moi/profil", {
      type_vehicule: typeVehicule,
      capacite_kg: capaciteKg,
      immatriculation,
    });
  }

  async passerEnLigne(): Promise<void> {
    await this.requete("POST", "/livreurs/moi/en-ligne");
  }

  async passerHorsLigne(): Promise<void> {
    await this.requete("POST", "/livreurs/moi/hors-ligne");
  }

  async envoyerPosition(p: PositionAEnvoyer): Promise<PositionAcceptee> {
    return this.requete<PositionAcceptee>("POST", "/livreurs/moi/position", {
      latitude: p.latitude,
      longitude: p.longitude,
      precision_m: p.precision_m,
      mesure_le: p.mesure_le.toISOString(), // ex. 2026-10-17T09:00:00.000Z : fuseau inclus
    });
  }
}

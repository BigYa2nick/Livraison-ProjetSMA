/** Client HTTP du serveur Livraison SMA, miroir de `mobile/livreur/src/client_api.py`. */

import { DELAI_REQUETE_MS } from "./config";
import { ErreurApi, ErreurReseau } from "./erreurs";

export type TypeVehicule = "moto" | "voiture" | "velo";
export type MotifIncident =
  | "destinataire_absent"
  | "adresse_introuvable"
  | "panne"
  | "accident";

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

/** Course proposée au livreur (`GET /livreurs/moi/proposition`) : aucune donnée personnelle du destinataire. */
export interface PropositionCourse {
  commande_id: string;
  tour: number;
  adresse_enlevement: string;
  adresse_livraison: string;
  taille_colis: string;
  poids_kg: number | null;
  description_colis: string | null;
  priorite: string;
  distance_m?: number | null;
  gain_estime_xof?: number | null;
  expire_le: string;
  secondes_restantes: number;
}

export interface PointGps {
  latitude: number;
  longitude: number;
}

export interface CourseActive {
  commande_id: string;
  etat: "attribuee" | "enlevee" | "en_livraison";
  priorite: string;
  adresse_enlevement: string;
  enlevement: PointGps;
  adresse_livraison: string;
  livraison: PointGps;
  destinataire_nom: string;
  destinataire_telephone: string;
  consignes: string | null;
  taille_colis: string;
  poids_kg: number | null;
  description_colis: string | null;
  distance_m: number | null;
  prix_xof: number;
  gain_estime_xof: number | null;
  attribuee_le: string | null;
  enlevee_le: string | null;
}

export interface ChangementEtatCourse {
  commande_id: string;
  nouvel_etat: string;
  horodatage: string;
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

  /** La course que le Dispatcher propose au livreur, ou null s'il n'y en a pas. */
  async lireProposition(): Promise<PropositionCourse | null> {
    return this.requete<PropositionCourse | null>("GET", "/livreurs/moi/proposition");
  }

  /** Le livreur prend la course (la réponse est transmise à son agent, qui l'attend 30 s). */
  async confirmerCourse(commandeId: string): Promise<void> {
    await this.requete("POST", `/livreurs/moi/proposition/${encodeURIComponent(commandeId)}/confirmer`);
  }

  /** Le livreur refuse la course : elle est aussitôt proposée à un autre livreur. */
  async declinerCourse(commandeId: string): Promise<void> {
    await this.requete("POST", `/livreurs/moi/proposition/${encodeURIComponent(commandeId)}/decliner`);
  }

  /** La course actuellement en cours pour ce livreur (attribuee, enlevee, ou null). */
  async lireCourseActive(): Promise<CourseActive | null> {
    return this.requete<CourseActive | null>("GET", "/livreurs/moi/course-en-cours");
  }

  /** Valide l'enlèvement du colis chez l'expéditeur (F-23). */
  async enleverColis(commandeId: string): Promise<ChangementEtatCourse> {
    return this.requete<ChangementEtatCourse>(
      "POST",
      `/livreurs/moi/course-en-cours/${encodeURIComponent(commandeId)}/enlever`,
    );
  }

  /** Valide la remise du colis au destinataire avec son code OTP (F-23). */
  async livrerColis(commandeId: string, codeConfirmation: string): Promise<ChangementEtatCourse> {
    return this.requete<ChangementEtatCourse>(
      "POST",
      `/livreurs/moi/course-en-cours/${encodeURIComponent(commandeId)}/livrer`,
      { code_confirmation: codeConfirmation },
    );
  }

  /** Signale un incident F-24 et termine la tentative de livraison. */
  async signalerIncident(commandeId: string, motif: MotifIncident): Promise<void> {
    await this.requete(
      "POST",
      `/livreurs/moi/course-en-cours/${encodeURIComponent(commandeId)}/incident`,
      { motif },
    );
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

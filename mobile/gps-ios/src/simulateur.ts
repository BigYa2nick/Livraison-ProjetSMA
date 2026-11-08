/**
 * Simulateur de GPS : parcourt un trajet à Abidjan, sans satellite.
 *
 * Sert à tester le serveur et la carte du répartiteur depuis l'iPhone, même immobile.
 * Le trajet est une ligne brisée approximative (Plateau vers Cocody), pas un itinéraire réel.
 * Miroir de `mobile/livreur/src/simulateur.py`.
 */

export type Point = readonly [latitude: number, longitude: number];

export const TRAJET_PLATEAU_COCODY: readonly Point[] = [
  [5.3236, -4.0197],
  [5.329, -4.01],
  [5.335, -4.002],
  [5.342, -3.993],
  [5.348, -3.987],
];

const METRES_PAR_DEGRE = 111_320;
const RAYON_TERRE_M = 6_371_000;

const enRadians = (degres: number) => (degres * Math.PI) / 180;

/** Distance en mètres entre deux points (formule de haversine). */
export function distanceM(a: Point, b: Point): number {
  const lat1 = enRadians(a[0]);
  const lat2 = enRadians(b[0]);
  const h =
    Math.sin((lat2 - lat1) / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(enRadians(b[1] - a[1]) / 2) ** 2;
  return 2 * RAYON_TERRE_M * Math.asin(Math.sqrt(h));
}

export interface PositionSimulee {
  latitude: number;
  longitude: number;
  precision_m: number;
}

export interface OptionsSimulateur {
  trajet?: readonly Point[];
  vitesseMs?: number; // environ 29 km/h par défaut
  bruitM?: number;
  decalageM?: number; // permet d'espacer plusieurs livreurs simulés
  alea?: () => number; // uniforme sur ]0, 1[ ; remplaçable pour des tests reproductibles
}

export class Simulateur {
  private readonly trajet: readonly Point[];
  private readonly vitesse: number;
  private readonly bruit: number;
  private readonly alea: () => number;
  private readonly segments: number[];
  private readonly longueur: number;
  private parcouru: number;

  constructor(options: OptionsSimulateur = {}) {
    this.trajet = options.trajet ?? TRAJET_PLATEAU_COCODY;
    if (this.trajet.length < 2) throw new Error("Le trajet doit contenir au moins deux points.");
    this.vitesse = options.vitesseMs ?? 8;
    this.bruit = options.bruitM ?? 4;
    this.alea = options.alea ?? Math.random;
    this.parcouru = options.decalageM ?? 0;
    this.segments = this.trajet.slice(0, -1).map((p, i) => distanceM(p, this.trajet[i + 1]));
    this.longueur = this.segments.reduce((somme, s) => somme + s, 0);
  }

  /** Loi normale centrée réduite (Box-Muller). */
  private gauss(): number {
    const u = 1 - this.alea();
    const v = this.alea();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  private pointA(distance: number): Point {
    let d = distance % (2 * this.longueur);
    if (d > this.longueur) d = 2 * this.longueur - d; // trajet retour
    for (let i = 0; i < this.segments.length; i++) {
      const longueur = this.segments[i];
      if (d <= longueur || i === this.segments.length - 1) {
        const f = Math.min(d / longueur, 1);
        const [lat1, lon1] = this.trajet[i];
        const [lat2, lon2] = this.trajet[i + 1];
        return [lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f];
      }
      d -= longueur;
    }
    return this.trajet[this.trajet.length - 1];
  }

  /** Fait avancer le livreur de dtS secondes et renvoie sa position (avec un peu de bruit). */
  avancer(dtS: number): PositionSimulee {
    this.parcouru += this.vitesse * dtS;
    const [lat, lon] = this.pointA(this.parcouru);
    const dLat = (this.gauss() * this.bruit) / METRES_PAR_DEGRE;
    const dLon = (this.gauss() * this.bruit) / (METRES_PAR_DEGRE * Math.cos(enRadians(lat)));
    const arrondi = (x: number) => Math.round(x * 1e6) / 1e6;
    return {
      latitude: arrondi(lat + dLat),
      longitude: arrondi(lon + dLon),
      precision_m: Math.round(Math.max(3, this.bruit * 1.5) * 10) / 10,
    };
  }
}

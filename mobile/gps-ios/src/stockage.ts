/** Mémorise l'adresse du serveur et le numéro entre deux lancements (évite de les retaper). */

import AsyncStorage from "@react-native-async-storage/async-storage";

const CLE = "livreur:connexion";

export interface Connexion {
  serveur: string;
  telephone: string;
}

export async function lireConnexion(): Promise<Connexion | null> {
  try {
    const brut = await AsyncStorage.getItem(CLE);
    if (brut === null) return null;
    const valeur = JSON.parse(brut) as Partial<Connexion>;
    if (typeof valeur.serveur !== "string" || typeof valeur.telephone !== "string") return null;
    return { serveur: valeur.serveur, telephone: valeur.telephone };
  } catch {
    // Confort seulement : si la lecture échoue, l'écran affiche les valeurs par défaut.
    return null;
  }
}

export async function sauverConnexion(connexion: Connexion): Promise<void> {
  await AsyncStorage.setItem(CLE, JSON.stringify(connexion));
}

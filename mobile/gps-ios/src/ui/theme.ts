import { StyleSheet } from "react-native";

import type { Niveau } from "../journal";

export const couleurs = {
  fond: "#f8f9ff",
  texte: "#1a1c22",
  discret: "#5a5f6b",
  bordure: "#8a8f9c",
  accent: "#2c5f9e",
  info: "#1a1c22",
  ok: "#2e7d32",
  avertissement: "#e65100",
  erreur: "#c62828",
} as const;

export const couleurNiveau: Record<Niveau, string> = {
  info: couleurs.info,
  ok: couleurs.ok,
  avertissement: couleurs.avertissement,
  erreur: couleurs.erreur,
};

export const styles = StyleSheet.create({
  titre: { fontSize: 20, fontWeight: "700", color: couleurs.texte, marginBottom: 8 },
  champ: {
    borderWidth: 1,
    borderColor: couleurs.bordure,
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: couleurs.texte,
    marginBottom: 10,
    backgroundColor: "#fff",
  },
  etiquette: { fontSize: 12, color: couleurs.discret, marginBottom: 2 },
  rangee: { flexDirection: "row", gap: 8, marginBottom: 10, flexWrap: "wrap" },
  texte: { fontSize: 13, color: couleurs.texte, lineHeight: 19 },
  bloc: { marginBottom: 16 },
});

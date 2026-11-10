/** Course proposée au livreur : gros compte à rebours et deux grands boutons, utilisables d'une main. */

import { Pressable, StyleSheet, Text, View } from "react-native";

import type { PropositionCourse } from "../clientApi";
import { DUREE_CONFIRMATION_S } from "../config";
import { estUrgente, libelleColis, partRestante } from "../proposition";
import type { Envoi } from "../useProposition";
import { couleurs } from "./theme";

interface Props {
  proposition: PropositionCourse;
  restantS: number;
  envoi: Envoi;
  expiree: boolean;
  onConfirmer: () => void;
  onDecliner: () => void;
}

function formatDistance(distanceM?: number | null): string | null {
  if (distanceM == null || distanceM <= 0) return null;
  if (distanceM < 1000) return `${distanceM} m`;
  return `${(distanceM / 1000).toFixed(1)} km`;
}

function formatGain(gainXof?: number | null): string | null {
  if (gainXof == null || gainXof <= 0) return null;
  return `${gainXof.toLocaleString("fr-FR")} F CFA`;
}

export default function CarteProposition({ proposition, restantS, envoi, expiree, onConfirmer, onDecliner }: Props) {
  const urgente = estUrgente(proposition);
  const occupe = envoi !== "aucun";
  const bloque = occupe || expiree;
  const pressant = restantS <= 10;
  const couleurTemps = expiree ? couleurs.discret : pressant ? couleurs.erreur : couleurs.accent;

  return (
    <View style={[styles.carte, urgente && styles.carteUrgente]} accessibilityLiveRegion="polite">
      <View style={styles.entete}>
        <Text style={styles.titre}>Nouvelle course</Text>
        {urgente && <Text style={styles.badge}>URGENT</Text>}
      </View>

      <Text style={[styles.temps, { color: couleurTemps }]} accessibilityLabel={`${restantS} secondes pour répondre`}>
        {expiree ? "Délai dépassé" : `${restantS} s`}
      </Text>
      <View style={styles.barre}>
        <View
          style={[
            styles.barreRemplie,
            { width: `${Math.round(partRestante(restantS, DUREE_CONFIRMATION_S) * 100)}%`, backgroundColor: couleurTemps },
          ]}
        />
      </View>

      <Text style={styles.etiquette}>Enlèvement</Text>
      <Text style={styles.adresse}>{proposition.adresse_enlevement}</Text>
      <Text style={styles.etiquette}>Livraison</Text>
      <Text style={styles.adresse}>{proposition.adresse_livraison}</Text>
      <Text style={styles.colis}>{libelleColis(proposition)}</Text>
      {proposition.description_colis ? <Text style={styles.description}>{proposition.description_colis}</Text> : null}

      {(proposition.distance_m || proposition.gain_estime_xof) ? (
        <View style={styles.chiffres}>
          {proposition.gain_estime_xof ? (
            <View style={styles.encartChiffre}>
              <Text style={styles.etiquetteChiffre}>Gain estimé</Text>
              <Text style={styles.valeurGain}>{formatGain(proposition.gain_estime_xof)}</Text>
            </View>
          ) : null}
          {proposition.distance_m ? (
            <View style={styles.encartChiffre}>
              <Text style={styles.etiquetteChiffre}>Distance</Text>
              <Text style={styles.valeurChiffre}>{formatDistance(proposition.distance_m)}</Text>
            </View>
          ) : null}
        </View>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: bloque }}
        disabled={bloque}
        onPress={onConfirmer}
        style={({ pressed }) => [styles.bouton, styles.accepter, bloque && styles.inactif, pressed && styles.appuye]}
      >
        <Text style={styles.texteBouton}>{envoi === "confirmation" ? "Envoi…" : "Accepter la course"}</Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: bloque }}
        disabled={bloque}
        onPress={onDecliner}
        style={({ pressed }) => [styles.bouton, styles.decliner, bloque && styles.inactif, pressed && styles.appuye]}
      >
        <Text style={[styles.texteBouton, styles.texteDecliner]}>{envoi === "refus" ? "Envoi…" : "Décliner"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  carte: {
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: couleurs.accent,
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  carteUrgente: { borderColor: couleurs.avertissement },
  entete: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  titre: { fontSize: 20, fontWeight: "700", color: couleurs.texte },
  badge: {
    fontSize: 12,
    fontWeight: "800",
    color: "#fff",
    backgroundColor: couleurs.avertissement,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    overflow: "hidden",
  },
  temps: { fontSize: 44, fontWeight: "800", textAlign: "center", marginVertical: 4 },
  barre: { height: 8, borderRadius: 4, backgroundColor: "#e3e6ee", overflow: "hidden", marginBottom: 14 },
  barreRemplie: { height: 8, borderRadius: 4 },
  etiquette: { fontSize: 12, color: couleurs.discret, marginTop: 6 },
  adresse: { fontSize: 18, fontWeight: "600", color: couleurs.texte },
  colis: { fontSize: 15, color: couleurs.texte, marginTop: 10 },
  description: { fontSize: 14, color: couleurs.discret, marginTop: 2 },
  chiffres: {
    flexDirection: "row",
    gap: 12,
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#e3e6ee",
  },
  encartChiffre: { flex: 1 },
  etiquetteChiffre: { fontSize: 11, color: couleurs.discret, textTransform: "uppercase" },
  valeurGain: { fontSize: 18, fontWeight: "800", color: couleurs.ok, marginTop: 2 },
  valeurChiffre: { fontSize: 17, fontWeight: "700", color: couleurs.texte, marginTop: 2 },
  bouton: { minHeight: 64, borderRadius: 10, alignItems: "center", justifyContent: "center", marginTop: 14 },
  accepter: { backgroundColor: couleurs.ok },
  decliner: { backgroundColor: "#fff", borderWidth: 2, borderColor: couleurs.erreur },
  texteBouton: { fontSize: 20, fontWeight: "800", color: "#fff" },
  texteDecliner: { color: couleurs.erreur },
  inactif: { opacity: 0.4 },
  appuye: { opacity: 0.8 },
});

/** Affichage et guidage pour la course en cours attribuée au livreur (F-22, F-23). */

import { useState } from "react";
import {
  Alert,
  Linking,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { CourseActive, MotifIncident } from "../clientApi";
import { couleurs } from "./theme";

interface Props {
  course: CourseActive;
  actionEnCours: string | null;
  erreurAction: string | null;
  onEnlever: () => void;
  onLivrer: (code: string) => void;
  onIncident: (motif: MotifIncident) => void;
}

const MOTIFS_INCIDENT: { valeur: MotifIncident; libelle: string }[] = [
  { valeur: "destinataire_absent", libelle: "Destinataire absent" },
  { valeur: "adresse_introuvable", libelle: "Adresse introuvable" },
  { valeur: "panne", libelle: "Panne" },
  { valeur: "accident", libelle: "Accident" },
];

function ouvrirItineraire(lat: number, lon: number, libelle: string) {
  const nomEncode = encodeURIComponent(libelle);
  const url =
    Platform.OS === "ios"
      ? `https://maps.apple.com/?daddr=${lat},${lon}&q=${nomEncode}`
      : `geo:${lat},${lon}?q=${lat},${lon}(${nomEncode})`;
  Linking.openURL(url).catch(() => {
    Linking.openURL(`https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`);
  });
}

function appeler(telephone: string) {
  Linking.openURL(`tel:${telephone.replace(/\s+/g, "")}`);
}

export default function CarteCourseActive({
  course,
  actionEnCours,
  erreurAction,
  onEnlever,
  onLivrer,
  onIncident,
}: Props) {
  const [codeSaisi, setCodeSaisi] = useState("");
  const [incidentOuvert, setIncidentOuvert] = useState(false);
  const estEnlevee = course.etat === "enlevee" || course.etat === "en_livraison";
  const occupe = actionEnCours !== null;

  const latCible = estEnlevee ? course.livraison.latitude : course.enlevement.latitude;
  const lonCible = estEnlevee ? course.livraison.longitude : course.enlevement.longitude;
  const adresseCible = estEnlevee ? course.adresse_livraison : course.adresse_enlevement;

  return (
    <View style={styles.carte}>
      {/* En-tête avec étape */}
      <View style={styles.entete}>
        <View style={styles.ligneBadge}>
          <Text style={[styles.badgeEtape, estEnlevee ? styles.badgeLivraison : styles.badgeEnlevement]}>
            {estEnlevee ? "ÉTAPE 2/2 : LIVRAISON" : "ÉTAPE 1/2 : ENLÈVEMENT"}
          </Text>
          {course.priorite === "urgente" && <Text style={styles.badgeUrgent}>URGENT</Text>}
        </View>
        {course.gain_estime_xof ? (
          <Text style={styles.gain}>{course.gain_estime_xof.toLocaleString("fr-FR")} F CFA</Text>
        ) : null}
      </View>

      {/* Destination courante */}
      <Text style={styles.reference}>Commande : {course.commande_id}</Text>
      <Text style={styles.etiquette}>Destination</Text>
      <Text style={styles.adresse}>{adresseCible}</Text>

      {/* Bouton GPS Itinéraire */}
      <Pressable
        accessibilityRole="button"
        onPress={() => ouvrirItineraire(latCible, lonCible, adresseCible)}
        style={({ pressed }) => [styles.boutonGps, pressed && styles.appuye]}
      >
        <Text style={styles.texteBoutonGps}>🧭 Ouvrir dans le GPS</Text>
      </Pressable>

      {/* Infos destinataire */}
      <View style={styles.blocDestinataire}>
        <View style={{ flex: 1 }}>
          <Text style={styles.etiquette}>Destinataire</Text>
          <Text style={styles.nomDestinataire}>{course.destinataire_nom}</Text>
          <Text style={styles.telDestinataire}>{course.destinataire_telephone}</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          onPress={() => appeler(course.destinataire_telephone)}
          style={({ pressed }) => [styles.boutonAppel, pressed && styles.appuye]}
        >
          <Text style={styles.texteBoutonAppel}>📞 Appeler</Text>
        </Pressable>
      </View>

      {course.consignes ? (
        <View style={styles.blocConsignes}>
          <Text style={styles.etiquette}>Consignes</Text>
          <Text style={styles.texteConsignes}>{course.consignes}</Text>
        </View>
      ) : null}

      {/* Message d'erreur éventuel */}
      {erreurAction ? <Text style={styles.erreur}>{erreurAction}</Text> : null}

      {/* Actions selon l'étape */}
      {!estEnlevee ? (
        <Pressable
          accessibilityRole="button"
          disabled={occupe}
          onPress={onEnlever}
          style={({ pressed }) => [
            styles.boutonAction,
            styles.boutonEnlever,
            occupe && styles.inactif,
            pressed && styles.appuye,
          ]}
        >
          <Text style={styles.texteAction}>
            {actionEnCours === "enlevement" ? "Validation…" : "📦 J'ai récupéré le colis"}
          </Text>
        </Pressable>
      ) : (
        <View style={styles.blocValidation}>
          <Text style={styles.etiquetteCode}>Code secret à 6 chiffres reçu par le client :</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="number-pad"
            maxLength={6}
            onChangeText={setCodeSaisi}
            placeholder="000000"
            style={styles.champCode}
            value={codeSaisi}
          />
          <Pressable
            accessibilityRole="button"
            disabled={occupe || codeSaisi.trim().length !== 6}
            onPress={() => onLivrer(codeSaisi)}
            style={({ pressed }) => [
              styles.boutonAction,
              styles.boutonLivrer,
              (occupe || codeSaisi.trim().length !== 6) && styles.inactif,
              pressed && styles.appuye,
            ]}
          >
            <Text style={styles.texteAction}>
              {actionEnCours === "livraison" ? "Validation…" : "✅ Valider la livraison"}
            </Text>
          </Pressable>
        </View>
      )}
      <Pressable
        accessibilityRole="button"
        disabled={occupe}
        onPress={() => setIncidentOuvert(true)}
        style={({ pressed }) => [styles.boutonIncident, occupe && styles.inactif, pressed && styles.appuye]}
      >
        <Text style={styles.texteIncident}>
          {actionEnCours === "incident" ? "Signalement…" : "⚠️ Signaler un incident"}
        </Text>
      </Pressable>
      <Modal
        animationType="fade"
        onRequestClose={() => setIncidentOuvert(false)}
        transparent
        visible={incidentOuvert}
      >
        <View style={styles.fondModal}>
          <View style={styles.dialogueIncident}>
            <Text style={styles.titreIncident}>Quel incident ?</Text>
            {MOTIFS_INCIDENT.map(({ valeur, libelle }) => (
              <Pressable
                accessibilityRole="button"
                key={valeur}
                onPress={() => {
                  setIncidentOuvert(false);
                  Alert.alert(
                    "Confirmer l'incident",
                    "Le signalement termine cette tentative de livraison.",
                    [
                      { text: "Retour", style: "cancel" },
                      { text: "Confirmer", style: "destructive", onPress: () => onIncident(valeur) },
                    ],
                  );
                }}
                style={styles.optionIncident}
              >
                <Text style={styles.texteOptionIncident}>{libelle}</Text>
              </Pressable>
            ))}
            <Pressable onPress={() => setIncidentOuvert(false)} style={styles.optionAnnuler}>
              <Text style={styles.texteOptionIncident}>Annuler</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
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
  entete: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  ligneBadge: { flexDirection: "row", alignItems: "center", gap: 6 },
  reference: { fontSize: 11, color: couleurs.discret, marginBottom: 4 },
  badgeEtape: {
    fontSize: 12,
    fontWeight: "800",
    color: "#fff",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    overflow: "hidden",
  },
  badgeEnlevement: { backgroundColor: couleurs.accent },
  badgeLivraison: { backgroundColor: couleurs.ok },
  badgeUrgent: {
    fontSize: 12,
    fontWeight: "800",
    color: "#fff",
    backgroundColor: couleurs.avertissement,
    paddingHorizontal: 6,
    paddingVertical: 4,
    borderRadius: 6,
    overflow: "hidden",
  },
  gain: { fontSize: 18, fontWeight: "800", color: couleurs.ok },
  etiquette: { fontSize: 12, color: couleurs.discret, marginTop: 4 },
  adresse: { fontSize: 18, fontWeight: "700", color: couleurs.texte, marginBottom: 8 },
  boutonGps: {
    backgroundColor: "#f0f4f9",
    borderWidth: 1,
    borderColor: "#c2d1e5",
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
    marginBottom: 12,
  },
  texteBoutonGps: { fontSize: 15, fontWeight: "700", color: couleurs.accent },
  blocDestinataire: {
    flexDirection: "row",
    alignItems: "center",
    borderTopWidth: 1,
    borderTopColor: "#e3e6ee",
    paddingTop: 10,
    marginBottom: 10,
  },
  nomDestinataire: { fontSize: 16, fontWeight: "700", color: couleurs.texte },
  telDestinataire: { fontSize: 14, color: couleurs.discret, marginTop: 2 },
  boutonAppel: {
    backgroundColor: couleurs.ok,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  texteBoutonAppel: { color: "#fff", fontWeight: "700", fontSize: 14 },
  blocConsignes: {
    backgroundColor: "#fffbe6",
    borderColor: "#ffe58f",
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
  },
  texteConsignes: { fontSize: 13, color: "#874d00", marginTop: 2 },
  erreur: { color: couleurs.erreur, fontSize: 14, fontWeight: "600", marginVertical: 6 },
  boutonAction: {
    minHeight: 56,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 10,
  },
  boutonEnlever: { backgroundColor: couleurs.accent },
  boutonLivrer: { backgroundColor: couleurs.ok },
  boutonIncident: {
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: couleurs.erreur,
    borderRadius: 8,
    marginTop: 10,
  },
  texteIncident: { color: couleurs.erreur, fontSize: 15, fontWeight: "700" },
  fondModal: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
    backgroundColor: "rgba(0, 0, 0, 0.45)",
  },
  dialogueIncident: { backgroundColor: "#fff", borderRadius: 12, padding: 18 },
  titreIncident: { fontSize: 19, fontWeight: "800", color: couleurs.texte, marginBottom: 8 },
  optionIncident: { paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: "#e3e6ee" },
  optionAnnuler: { paddingVertical: 13, marginTop: 4 },
  texteOptionIncident: { fontSize: 16, color: couleurs.texte },
  texteAction: { fontSize: 18, fontWeight: "800", color: "#fff" },
  blocValidation: { marginTop: 6 },
  etiquetteCode: { fontSize: 13, fontWeight: "600", color: couleurs.texte, marginBottom: 6 },
  champCode: {
    height: 52,
    borderWidth: 2,
    borderColor: couleurs.accent,
    borderRadius: 8,
    fontSize: 26,
    fontWeight: "800",
    textAlign: "center",
    letterSpacing: 8,
    backgroundColor: "#f9fbff",
    marginBottom: 4,
  },
  inactif: { opacity: 0.4 },
  appuye: { opacity: 0.8 },
});

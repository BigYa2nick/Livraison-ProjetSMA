import { Text, View } from "react-native";

import type { Ligne } from "../journal";
import { couleurNiveau, styles } from "./theme";

/** Les lignes les plus récentes en premier : sur un téléphone, on lit le haut de l'écran. */
export default function Journal({ lignes }: { lignes: Ligne[] }) {
  return (
    <View>
      <Text style={[styles.titre, { fontSize: 16 }]}>Journal</Text>
      {[...lignes].reverse().map((l) => (
        <Text key={l.id} style={[styles.texte, { color: couleurNiveau[l.niveau], fontSize: 12 }]} selectable>
          {l.heure}  {l.message}
        </Text>
      ))}
    </View>
  );
}

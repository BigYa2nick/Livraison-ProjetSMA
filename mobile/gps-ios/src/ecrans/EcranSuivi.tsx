/** Suivi GPS : passer en ligne, choisir la source de position, lire les mesures. */

import { Button, Text, View } from "react-native";

import type { ClientApi } from "../clientApi";
import type { Log } from "../journal";
import { MODES, type Mode, type Statistiques, useCollecte } from "../useCollecte";
import { couleurs, styles } from "../ui/theme";

interface Props {
  api: ClientApi;
  log: Log;
}

const fmt = (valeur: number | null, unite: string, decimales = 0) =>
  valeur === null ? "-" : `${valeur.toFixed(decimales)} ${unite}`;

function resume(s: Statistiques): string {
  return [
    `En ligne depuis ${s.dureeMin.toFixed(1)} min`,
    `Positions GPS reçues : ${s.recues}   trous > 15 s : ${s.trous}   plus long trou : ${s.trouMaxS.toFixed(0)} s`,
    `Envoyées : ${s.envoyees}   en attente : ${s.enAttente}   refusées : ${s.refusees}   perdues : ${s.perdues}`,
    `Espacées (< 5 s, non envoyées) : ${s.espacees}   échecs réseau : ${s.echecsReseau}`,
    `Horodatages GPS corrigés : ${s.horodatagesCorriges}`,
    `Dernier retard mesure->serveur : ${fmt(s.retardS, "s", 1)}   requête : ${fmt(s.latenceMs, "ms")}`,
    ...(s.derniereErreur ? [`Dernière erreur : ${s.derniereErreur}`] : []),
  ].join("\n");
}

export default function EcranSuivi({ api, log }: Props) {
  const { enLigne, mode, permission, stats, autoriser, ouvrirReglages, basculerEnLigne, choisirMode } =
    useCollecte(api, log);

  return (
    <View style={styles.bloc}>
      <Text style={styles.titre}>Suivi GPS</Text>

      <Text style={styles.etiquette}>Mode de collecte</Text>
      <View style={styles.rangee}>
        {(Object.keys(MODES) as Mode[]).map((cle) => (
          <Button
            key={cle}
            title={`${cle === mode ? "● " : "○ "}${MODES[cle]}`}
            onPress={() => choisirMode(cle)}
            color={cle === mode ? couleurs.accent : couleurs.discret}
          />
        ))}
      </View>

      <View style={styles.rangee}>
        <Button title="Autoriser la position" onPress={autoriser} color={couleurs.accent} />
        <Button title="Réglages" onPress={ouvrirReglages} color={couleurs.accent} />
      </View>
      <Text style={[styles.texte, { marginBottom: 8 }]}>Permission : {permission}</Text>

      <View style={styles.rangee}>
        <Button
          title={enLigne ? "Passer hors ligne" : "Passer en ligne"}
          onPress={basculerEnLigne}
          color={enLigne ? couleurs.erreur : couleurs.ok}
        />
      </View>

      {stats !== null && <Text style={styles.texte} selectable>{resume(stats)}</Text>}
    </View>
  );
}

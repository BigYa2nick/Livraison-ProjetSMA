/** Prototype GPS du livreur (React Native / Expo) : teste le suivi sur un vrai iPhone avec Expo Go. */

import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { AppState, ScrollView, StyleSheet } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

import type { ClientApi } from "./src/clientApi";
import EcranConnexion from "./src/ecrans/EcranConnexion";
import EcranSuivi from "./src/ecrans/EcranSuivi";
import { useJournal } from "./src/journal";
import Journal from "./src/ui/Journal";
import { couleurs } from "./src/ui/theme";

export default function App() {
  const { lignes, log } = useJournal();
  const [api, setApi] = useState<ClientApi | null>(null);

  useEffect(() => {
    log("Application prête.");
    const abonnement = AppState.addEventListener("change", (etat) => log(`Application : ${etat}`));
    return () => abonnement.remove();
  }, [log]);

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.racine}>
        <StatusBar style="dark" />
        <ScrollView contentContainerStyle={styles.contenu} keyboardShouldPersistTaps="handled">
          {api === null ? (
            <EcranConnexion
              log={log}
              onConnecte={(client, nouveauCompte) => {
                log(nouveauCompte ? "Compte créé et connecté." : "Connecté.", "ok");
                setApi(client);
              }}
            />
          ) : (
            <EcranSuivi api={api} log={log} />
          )}
          <Journal lignes={lignes} />
        </ScrollView>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  racine: { flex: 1, backgroundColor: couleurs.fond },
  contenu: { padding: 16 },
});

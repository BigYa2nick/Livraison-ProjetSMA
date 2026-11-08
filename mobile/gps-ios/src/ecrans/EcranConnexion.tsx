/** Connexion du livreur : numéro de téléphone, code SMS (affiché dans la console du serveur). */

import { useEffect, useRef, useState } from "react";
import { Button, Text, TextInput, View } from "react-native";

import { ClientApi } from "../clientApi";
import { PROFIL_PAR_DEFAUT, SERVEUR_PAR_DEFAUT, TELEPHONE_PAR_DEFAUT } from "../config";
import { ErreurReseau, messageErreur } from "../erreurs";
import type { Log } from "../journal";
import { lireConnexion, sauverConnexion } from "../stockage";
import { couleurs, styles } from "../ui/theme";

interface Props {
  log: Log;
  onConnecte: (api: ClientApi, nouveauCompte: boolean) => void;
}

export default function EcranConnexion({ log, onConnecte }: Props) {
  const [serveur, setServeur] = useState(SERVEUR_PAR_DEFAUT);
  const [telephone, setTelephone] = useState(TELEPHONE_PAR_DEFAUT);
  const [code, setCode] = useState("");
  const [occupe, setOccupe] = useState(false);
  const api = useRef<ClientApi | null>(null);

  useEffect(() => {
    lireConnexion().then((memorisee) => {
      if (memorisee) {
        setServeur(memorisee.serveur);
        setTelephone(memorisee.telephone);
      }
    });
  }, []);

  function signaler(e: unknown) {
    log(`Échec : ${messageErreur(e)}`, "erreur");
    if (e instanceof ErreurReseau) {
      log(
        "Vérifiez : même Wi-Fi que le PC, adresse IP du PC (ipconfig), uvicorn lancé avec --host 0.0.0.0, pare-feu.",
        "avertissement",
      );
    }
  }

  async function demanderCode() {
    setOccupe(true);
    try {
      api.current = new ClientApi(serveur);
      await api.current.demanderCode(telephone);
      log("Code demandé : il s'affiche dans la console du serveur (mode développement).");
    } catch (e) {
      signaler(e);
    } finally {
      setOccupe(false);
    }
  }

  async function valider() {
    if (api.current === null) {
      log("Demandez d'abord un code.", "avertissement");
      return;
    }
    setOccupe(true);
    try {
      const nouveau = await api.current.verifierCode(telephone, code);
      await api.current.definirProfil(PROFIL_PAR_DEFAUT.typeVehicule, PROFIL_PAR_DEFAUT.capaciteKg);
      await sauverConnexion({ serveur, telephone }).catch((e) =>
        log(`Adresse non mémorisée : ${messageErreur(e)}`, "avertissement"),
      );
      onConnecte(api.current, nouveau);
    } catch (e) {
      signaler(e);
    } finally {
      setOccupe(false);
    }
  }

  return (
    <View style={styles.bloc}>
      <Text style={styles.titre}>Connexion livreur</Text>
      <Text style={styles.etiquette}>Adresse du serveur</Text>
      <TextInput
        style={styles.champ}
        value={serveur}
        onChangeText={setServeur}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />
      <Text style={styles.etiquette}>Téléphone</Text>
      <TextInput
        style={styles.champ}
        value={telephone}
        onChangeText={setTelephone}
        keyboardType="phone-pad"
      />
      <View style={styles.rangee}>
        <Button title="Demander le code" onPress={demanderCode} disabled={occupe} color={couleurs.accent} />
      </View>
      <Text style={styles.etiquette}>Code reçu par SMS</Text>
      <TextInput
        style={styles.champ}
        value={code}
        onChangeText={setCode}
        keyboardType="number-pad"
        maxLength={6}
      />
      <View style={styles.rangee}>
        <Button title="Valider" onPress={valider} disabled={occupe || code.length !== 6} color={couleurs.accent} />
      </View>
    </View>
  );
}

/** Journal affiché à l'écran : sert à lire ce qui se passe pendant un test sur le terrain. */

import { useCallback, useRef, useState } from "react";

import { JOURNAL_MAX } from "./config";

export type Niveau = "info" | "ok" | "avertissement" | "erreur";
export type Log = (message: string, niveau?: Niveau) => void;

export interface Ligne {
  id: number;
  heure: string;
  message: string;
  niveau: Niveau;
}

export function useJournal(): { lignes: Ligne[]; log: Log } {
  const [lignes, setLignes] = useState<Ligne[]>([]);
  const compteur = useRef(0);

  const log = useCallback<Log>((message, niveau = "info") => {
    compteur.current += 1;
    const ligne: Ligne = {
      id: compteur.current,
      heure: new Date().toLocaleTimeString("fr-FR"),
      message,
      niveau,
    };
    setLignes((avant) => {
      const apres = [...avant, ligne];
      return apres.length > JOURNAL_MAX ? apres.slice(-(JOURNAL_MAX - 30)) : apres;
    });
  }, []);

  return { lignes, log };
}

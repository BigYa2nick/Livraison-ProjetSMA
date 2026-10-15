# Authentification et rôles

Exigences : F-01 (inscription et connexion par téléphone et code SMS), F-02 (quatre rôles),
NF-05 (jetons, mots de passe hachés, limitation des tentatives).

## Parcours

| Qui | Comment | Routes |
|---|---|---|
| Client, livreur | numéro de téléphone + code SMS à 6 chiffres | `POST /auth/code`, puis `POST /auth/verifier` |
| Répartiteur, administrateur | numéro de téléphone + mot de passe | `POST /auth/connexion` |
| Tous | renouveler les jetons | `POST /auth/rafraichir` |
| Tous | lire ou compléter son profil | `GET` et `PATCH /auth/moi` |

À la première connexion par SMS, le compte est créé avec le rôle demandé (`client` ou `livreur`
uniquement). Les comptes répartiteur et administrateur ne se créent jamais par l'API publique :
utiliser `python scripts/creer_personnel.py --telephone 0708091011 --role administrateur`.

Le livreur peut se connecter dès l'inscription, mais ne peut prendre aucune course avant la
validation de son dossier par l'administrateur (F-03, prochaine étape).

## Règles de sécurité

- **Code SMS** : 6 chiffres, valable 5 minutes, à usage unique, 5 essais maximum, 3 demandes de
  code maximum par fenêtre de 10 minutes et par numéro. Seul le dernier code demandé est valable.
  Il est conservé sous forme hachée (HMAC-SHA256 avec `JWT_SECRET`), jamais en clair.
- **Mot de passe** (personnel) : 10 caractères minimum, haché avec scrypt et un sel aléatoire.
  Après 5 échecs, le compte est verrouillé 15 minutes. Le personnel n'utilise pas le SMS, pour
  qu'un numéro détourné ne suffise pas à obtenir des droits étendus.
- **Jetons JWT** (HS256) : jeton d'accès de 15 minutes, jeton de rafraîchissement de 7 jours.
  Chaque requête relit le compte en base : un compte suspendu perd l'accès immédiatement (F-62).
- **Pas de fuite d'information** : demander un code répond pareil pour un numéro inscrit ou non ;
  une connexion échouée répond pareil pour un numéro inconnu, un client ou un mauvais mot de passe.
- **Configuration** : le serveur refuse de signer un jeton si `JWT_SECRET` est vide, trop court
  (moins de 32 caractères) ou laissé à la valeur d'exemple. L'envoi SMS simulé est refusé si
  `ENVIRONNEMENT` n'est pas `dev`.

## Tester à la main

```powershell
uvicorn livraison.api.main:app --reload
```

Ouvrir `http://127.0.0.1:8000/docs` : `POST /auth/code` avec un numéro, lire le **code dans la
console d'uvicorn** (ligne `[SMS SIMULÉ]`), puis `POST /auth/verifier`. Copier `access_token`, cliquer
sur **Authorize** en haut de la page et le coller : `GET /auth/moi` fonctionne alors.

## Protéger une route par rôle

```python
from fastapi import Depends
from livraison.api.dependances import exiger_roles, utilisateur_courant
from livraison.persistance.enums import Role

@app.get("/admin/livreurs")
async def lister(_=Depends(exiger_roles(Role.ADMINISTRATEUR))): ...

@app.get("/commandes")
async def mes_commandes(utilisateur=Depends(utilisateur_courant)): ...
```

## Limites connues (à traiter plus tard)

- Pas de déconnexion côté serveur : un jeton volé reste valable jusqu'à son expiration. Une liste
  de jetons révoqués dans Redis pourra être ajoutée.
- Pas d'envoi de vrais SMS : brancher un fournisseur dans `services/sms.py` avant le déploiement.
- Pas de limitation par adresse IP : seule la limitation par numéro existe.

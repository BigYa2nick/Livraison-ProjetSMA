# Serveur XMPP de développement (celui de SPADE) sous Linux.
#
# Sous Linux, sa boucle asyncio (uvloop) est fiable : les connexions des agents réussissent à chaque essai.
# Sous Windows, le même serveur bloque le passage en TLS d'une partie des connexions (docs/contract-net.md, § 9).
# Les versions sont celles du projet (requirements) : spade 4.1.4, pyjabber 0.4.5.
FROM python:3.13-slim

# Délais et nouvelles tentatives généreux : la construction échoue si le réseau du conteneur est lent.
# greenlet : SQLAlchemy 2.1 (utilisé par pyjabber) ne l'installe plus tout seul, et le serveur plante sans lui.
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 "spade==4.1.4" "pyjabber==0.4.5" greenlet

# Le domaine du serveur doit être identique à XMPP_DOMAINE (.env) : les agents s'y connectent par ce nom.
ENV XMPP_DOMAINE=127.0.0.1
EXPOSE 5222

# --purge : le serveur repart d'une base de comptes vide à chaque démarrage du conteneur.
CMD ["sh", "-c", "exec spade run --host \"$XMPP_DOMAINE\" --purge"]

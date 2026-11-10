"""Événements entre l'API et les agents (décision D-03, docs/conception/06-decisions.md).

L'API **annonce** ce qui vient de se passer (un livreur passe en ligne ou hors ligne, une commande est
validée, un livreur confirme une course) sur un canal Redis ; le gestionnaire d'agents l'écoute et
agit. Les agents annoncent à leur tour ce qui concerne l'extérieur (une course est proposée à un
livreur). Un événement n'est pas durable : si Redis est en panne, l'annonce est perdue, **jamais la
requête** : la base reste la vérité (D-04) et le gestionnaire rattrape l'écart en comparant
périodiquement la base et ses agents.
"""

import json
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

CANAL_EVENEMENTS = "evenements"

# De l'API vers les agents
LIVREUR_EN_LIGNE = "livreur_en_ligne"
LIVREUR_HORS_LIGNE = "livreur_hors_ligne"
COMMANDE_VALIDEE = "commande_validee"  # une commande est prête à être attribuée (F-31)
COURSE_CONFIRMEE = "course_confirmee"  # le livreur a accepté la course qu'on lui propose (F-34)
COURSE_DECLINEE = "course_declinee"  # le livreur a refusé la course qu'on lui propose (F-34)
COMMANDE_ENLEVEE = "commande_enlevee"  # le livreur a récupéré le colis (F-23)
COMMANDE_LIVREE = "commande_livree"  # le livreur a remis le colis avec preuve (F-23)

# Des agents vers l'API, l'application du livreur et la console du répartiteur. Le gestionnaire
# d'agents les voit passer sur le même canal et les ignore.
COURSE_PROPOSEE = "course_proposee"
COMMANDE_ATTRIBUEE = "commande_attribuee"
ATTRIBUTION_ECHOUEE = "attribution_echouee"
EVENEMENTS_DES_AGENTS = frozenset({COURSE_PROPOSEE, COMMANDE_ATTRIBUEE, ATTRIBUTION_ECHOUEE})

journal = logging.getLogger("livraison")


async def publier(redis: Redis | None, type_evenement: str, **donnees) -> bool:
    """Publie un événement. Renvoie False s'il n'est pas parti (pas de Redis, ou Redis en panne).

    Sans Redis (`redis=None`, par exemple dans un outil ou un test qui n'en a pas besoin), rien
    n'est publié et rien n'est signalé : c'est un choix de l'appelant. Une panne de Redis, elle, est
    signalée par un avertissement dans le journal.
    """
    if redis is None:
        return False
    message = {"type": type_evenement, "emis_le": datetime.now(UTC).isoformat(), **donnees}
    try:
        await redis.publish(CANAL_EVENEMENTS, json.dumps(message))
    except (RedisError, OSError) as e:
        journal.warning(
            "Événement %s non publié (%s : %s) : les agents le retrouveront par réconciliation",
            type_evenement,
            type(e).__name__,
            e,
        )
        return False
    return True

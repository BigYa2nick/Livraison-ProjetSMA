"""Service d'attribution : les écritures et lectures du Contract Net, sur la vraie base de test."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aides_commandes import COCODY, PLATEAU, creer_commande, creer_livreur
from livraison.config import parametres
from livraison.persistance.enums import (
    EtatCommande,
    Priorite,
    ResultatOffre,
    StatutValidation,
    TypeEvenement,
)
from livraison.persistance.modeles import Commande, Evenement, Offre
from livraison.services import attribution as svc

pytestmark = pytest.mark.asyncio


async def offre(session, commande_id, tour, livreur_id) -> Offre:
    return (
        await session.execute(
            select(Offre).where(
                Offre.commande_id == uuid.UUID(commande_id),
                Offre.tour == tour,
                Offre.livreur_id == uuid.UUID(livreur_id),
            )
        )
    ).scalar_one()


async def evenements_de(session, commande_id, type_):
    lignes = await session.execute(
        select(Evenement).where(Evenement.commande_id == uuid.UUID(commande_id), Evenement.type == type_)
    )
    return list(lignes.scalars())


# ======================================================================== lecture


async def test_lecture_d_une_commande(session):
    cid = await creer_commande(session, priorite=Priorite.URGENTE, poids_kg=4.25)
    c = await svc.lire_commande(session, cid)
    assert c.commande_id == cid and c.etat == EtatCommande.VALIDEE
    assert c.priorite == "urgente" and c.taille == "petit" and c.poids_kg == 4.25
    assert c.enlevement == pytest.approx(PLATEAU) and c.livraison == pytest.approx(COCODY)
    assert (c.tentatives, c.rayon_m) == (0, None)


async def test_commande_inconnue(session):
    assert await svc.lire_commande(session, str(uuid.uuid4())) is None


async def test_commandes_a_attribuer(session, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_tours_max", 3)
    a_faire = await creer_commande(session)
    en_recherche = await creer_commande(session, etat=EtatCommande.RECHERCHE_LIVREUR, tentatives_attribution=2)
    ecartees = [
        await creer_commande(session, etat=EtatCommande.RECHERCHE_LIVREUR, tentatives_attribution=3),  # épuisée
        await creer_commande(session, etat=EtatCommande.CREEE),
        await creer_commande(session, etat=EtatCommande.ANNULEE),
    ]
    trouvees = await svc.lire_commandes_a_attribuer(session)
    assert {a_faire, en_recherche} <= set(trouvees)
    assert not set(ecartees) & set(trouvees)


# ======================================================================== tours


async def test_premier_tour(session):
    cid = await creer_commande(session)
    tour = await svc.preparer_tour(session, cid)
    assert (tour.tour, tour.rayon_m) == (1, parametres.attribution_rayon_initial_m)
    c = await svc.lire_commande(session, cid)
    assert c.etat == EtatCommande.RECHERCHE_LIVREUR and c.tentatives == 1
    changements = await evenements_de(session, cid, TypeEvenement.CHANGEMENT_ETAT)
    assert [e.detail for e in changements] == [{"de": "validee", "vers": "recherche_livreur"}]


async def test_tour_vide_elargit_le_rayon_puis_refus_humain_le_garde(session, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_tours_max", 10)
    cid, livreur = await creer_commande(session), await creer_livreur(session)
    assert (await svc.preparer_tour(session, cid)).rayon_m == 3000
    # tour 1 sans offre retenue : le rayon double
    t2 = await svc.preparer_tour(session, cid)
    assert (t2.tour, t2.rayon_m) == (2, 6000)
    # tour 2 : une offre est retenue puis déclinée par le livreur humain
    await svc.enregistrer_reponses(session, cid, 2, propositions={livreur: (100.0, 90)}, refus={})
    assert await svc.retenir_offre(session, cid, 2, livreur)
    await svc.declarer_non_prise(session, cid, 2, livreur, "declinee")
    t3 = await svc.preparer_tour(session, cid)
    assert (t3.tour, t3.rayon_m) == (3, 6000)
    # le changement d'état n'est noté qu'une fois
    assert len(await evenements_de(session, cid, TypeEvenement.CHANGEMENT_ETAT)) == 1


async def test_les_tours_s_epuisent(session, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_tours_max", 2)
    cid = await creer_commande(session)
    await svc.preparer_tour(session, cid)
    await svc.preparer_tour(session, cid)
    with pytest.raises(svc.ToursEpuises):
        await svc.preparer_tour(session, cid)


@pytest.mark.parametrize("etat", [EtatCommande.CREEE, EtatCommande.ATTRIBUEE, EtatCommande.ANNULEE])
async def test_une_commande_hors_recherche_ne_se_prepare_pas(session, etat):
    cid = await creer_commande(session, etat=etat, livreur_id=None if etat != EtatCommande.ATTRIBUEE else await creer_livreur(session))
    with pytest.raises(svc.AttributionImpossible):
        await svc.preparer_tour(session, cid)


async def test_preparer_une_commande_inconnue(session):
    with pytest.raises(svc.AttributionImpossible):
        await svc.preparer_tour(session, str(uuid.uuid4()))


# ======================================================================== candidats


async def test_exclusion_des_livreurs_qui_ont_decline(session):
    cid = await creer_commande(session)
    a, b, c = [await creer_livreur(session) for _ in range(3)]
    await svc.enregistrer_reponses(
        session, cid, 1, propositions={a: (1.0, 1), b: (2.0, 2)}, refus={c: "sature"}
    )
    await svc.preparer_tour(session, cid)
    assert await svc.retenir_offre(session, cid, 1, a)
    await svc.declarer_non_prise(session, cid, 1, a, "expiree")
    assert await svc.livreurs_exclus(session, cid) == {a}  # b et c ne sont pas exclus


async def test_livreurs_eligibles(session):
    ok = await creer_livreur(session)
    hors_ligne = await creer_livreur(session, en_ligne=False)
    suspendu = await creer_livreur(session, statut=StatutValidation.SUSPENDU)
    en_attente = await creer_livreur(session, statut=StatutValidation.EN_ATTENTE)
    ok2 = await creer_livreur(session)
    demandes = [ok2, hors_ligne, "pas-un-uuid", suspendu, ok, en_attente, str(uuid.uuid4())]
    assert await svc.livreurs_eligibles(session, demandes) == [ok2, ok, en_attente]
    assert await svc.livreurs_eligibles(session, []) == []
    assert await svc.livreurs_eligibles(session, ["x", "y"]) == []


# ======================================================================== charge


async def test_charge_en_base(session):
    livreur = await creer_livreur(session)
    assert await svc.charge_en_base(session, livreur) == 0
    for etat in (EtatCommande.ATTRIBUEE, EtatCommande.ENLEVEE, EtatCommande.EN_LIVRAISON):
        await creer_commande(session, etat=etat, livreur_id=livreur)
    await creer_commande(session, etat=EtatCommande.LIVREE, livreur_id=livreur)  # terminée : ne compte pas
    assert await svc.charge_en_base(session, livreur) == 3


async def test_offre_retenue_en_attente_compte_puis_expire(session):
    livreur = await creer_livreur(session)
    cid = await creer_commande(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={livreur: (10.0, 10)}, refus={})
    assert await svc.retenir_offre(session, cid, 1, livreur)
    assert await svc.charge_en_base(session, livreur) == 1
    fenetre = parametres.attribution_confirmation_s + parametres.attribution_marge_s
    plus_tard = datetime.now(UTC) + timedelta(seconds=fenetre + 1)
    assert await svc.charge_en_base(session, livreur, maintenant=plus_tard) == 0  # abandonnée


# ======================================================================== offres


async def test_enregistrement_des_reponses_est_rejouable(session):
    cid = await creer_commande(session)
    a, b, c = [await creer_livreur(session) for _ in range(3)]
    for _ in range(2):  # rejouer ne duplique rien
        await svc.enregistrer_reponses(
            session, cid, 1, propositions={a: (420.4, 400)}, refus={b: "hors_zone"}, silencieux=[c]
        )
    oa, ob, oc = [await offre(session, cid, 1, x) for x in (a, b, c)]
    assert (oa.resultat, oa.cout, oa.eta_s) == (ResultatOffre.PROPOSEE, 420.4, 400)
    assert (ob.resultat, ob.motif_refus, ob.cout) == (ResultatOffre.REFUSEE, "hors_zone", None)
    assert oc.resultat == ResultatOffre.EXPIREE
    await svc.enregistrer_reponses(session, cid, 1, propositions={}, refus={})  # rien à écrire


async def test_retenir_une_offre_puis_rejeter_les_autres(session):
    cid = await creer_commande(session)
    a, b, c = [await creer_livreur(session) for _ in range(3)]
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(
        session, cid, 1, propositions={a: (1.0, 1), b: (2.0, 2)}, refus={}, silencieux=[c]
    )
    assert await svc.retenir_offre(session, cid, 1, a)
    assert await svc.rejeter_les_autres(session, cid, 1, a) == [b]
    assert (await offre(session, cid, 1, a)).resultat == ResultatOffre.RETENUE
    assert (await offre(session, cid, 1, a)).decide_le is not None
    assert (await offre(session, cid, 1, b)).resultat == ResultatOffre.REJETEE
    assert (await offre(session, cid, 1, c)).resultat == ResultatOffre.EXPIREE  # inchangée


async def test_une_offre_deja_retenue_ne_se_retient_pas_deux_fois(session):
    cid, a = await creer_commande(session), await creer_livreur(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={a: (1.0, 1)}, refus={})
    assert await svc.retenir_offre(session, cid, 1, a)
    assert not await svc.retenir_offre(session, cid, 1, a)  # elle n'est plus « proposée »


async def test_la_base_interdit_deux_offres_retenues_pour_un_tour(session):
    cid = await creer_commande(session)
    a, b = await creer_livreur(session), await creer_livreur(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={a: (1.0, 1), b: (2.0, 2)}, refus={})
    assert await svc.retenir_offre(session, cid, 1, a)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await svc.retenir_offre(session, cid, 1, b)


async def test_garde_fou_de_charge_refuse_un_livreur_sature(session, monkeypatch):
    monkeypatch.setattr(parametres, "livreur_charge_max", 2)
    livreur = await creer_livreur(session)
    for _ in range(2):
        await creer_commande(session, etat=EtatCommande.ENLEVEE, livreur_id=livreur)
    cid = await creer_commande(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={livreur: (1.0, 1)}, refus={})
    assert not await svc.retenir_offre(session, cid, 1, livreur)
    assert (await offre(session, cid, 1, livreur)).resultat == ResultatOffre.PROPOSEE


async def test_une_offre_retenue_pour_une_autre_commande_compte_dans_la_charge(session, monkeypatch):
    """Scénario S6 : le livreur retenu pour A en attente de confirmation n'est pas libre pour B."""
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    livreur = await creer_livreur(session)
    a, b = await creer_commande(session), await creer_commande(session)
    for cid in (a, b):
        await svc.preparer_tour(session, cid)
        await svc.enregistrer_reponses(session, cid, 1, propositions={livreur: (1.0, 1)}, refus={})
    assert await svc.retenir_offre(session, a, 1, livreur)
    assert not await svc.retenir_offre(session, b, 1, livreur)


async def test_on_ne_retient_pas_une_offre_d_une_commande_qui_n_est_plus_en_recherche(session):
    cid, a = await creer_commande(session), await creer_livreur(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={a: (1.0, 1)}, refus={})
    (await session.get(Commande, uuid.UUID(cid))).etat = EtatCommande.ANNULEE
    await session.flush()
    assert not await svc.retenir_offre(session, cid, 1, a)


@pytest.mark.parametrize(
    ("motif", "attendu"),
    [("declinee", ResultatOffre.DECLINEE), ("expiree", ResultatOffre.DECLINEE), ("sature", ResultatOffre.REFUSEE)],
)
async def test_offre_non_prise(session, motif, attendu):
    cid, a = await creer_commande(session), await creer_livreur(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={a: (1.0, 1)}, refus={})
    await svc.retenir_offre(session, cid, 1, a)
    await svc.declarer_non_prise(session, cid, 1, a, motif)
    o = await offre(session, cid, 1, a)
    assert (o.resultat, o.motif_refus) == (attendu, motif)


# ======================================================================== confirmation


async def test_confirmer_l_attribution(session):
    cid, a = await creer_commande(session), await creer_livreur(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={a: (1.0, 1)}, refus={})
    await svc.retenir_offre(session, cid, 1, a)
    assert await svc.confirmer_attribution(session, cid, 1, a)
    commande = await session.get(Commande, uuid.UUID(cid))
    assert commande.etat == EtatCommande.ATTRIBUEE and str(commande.livreur_id) == a
    assert commande.attribuee_le is not None
    vers = [e.detail["vers"] for e in await evenements_de(session, cid, TypeEvenement.CHANGEMENT_ETAT)]
    assert vers == ["recherche_livreur", "attribuee"]
    assert await svc.charge_en_base(session, a) == 1  # désormais comptée comme course en cours
    assert not await svc.confirmer_attribution(session, cid, 1, a)  # déjà attribuée


async def test_confirmation_refusee_si_offre_non_retenue_ou_commande_annulee(session):
    cid, a, b = await creer_commande(session), await creer_livreur(session), await creer_livreur(session)
    await svc.preparer_tour(session, cid)
    await svc.enregistrer_reponses(session, cid, 1, propositions={a: (1.0, 1), b: (2.0, 2)}, refus={})
    await svc.retenir_offre(session, cid, 1, a)
    assert not await svc.confirmer_attribution(session, cid, 1, b)  # b n'est pas le livreur retenu
    (await session.get(Commande, uuid.UUID(cid))).etat = EtatCommande.ANNULEE
    await session.flush()
    assert not await svc.confirmer_attribution(session, cid, 1, a)
    assert not await svc.confirmer_attribution(session, str(uuid.uuid4()), 1, a)


# ======================================================================== journal


async def test_journal_des_messages_et_alerte(session):
    cid = await creer_commande(session)
    n = await svc.journaliser_messages(
        session,
        cid,
        [
            svc.EntreeJournal("dispatcher@localhost", "livreur-x@localhost", "cfp", {"tour": 1}),
            svc.EntreeJournal("livreur-x@localhost", "dispatcher@localhost", "propose", {"cout": 12.5}),
        ],
    )
    assert n == 2 and await svc.journaliser_messages(session, cid, []) == 0
    messages = await evenements_de(session, cid, TypeEvenement.MESSAGE_AGENT)
    assert sorted(m.performative for m in messages) == ["cfp", "propose"]
    await svc.signaler_echec(session, cid, 6)
    (alerte,) = await evenements_de(session, cid, TypeEvenement.NOTIFICATION)
    assert alerte.detail == {"destinataire": "repartiteur", "motif": "aucun_livreur_trouve", "tours": 6}

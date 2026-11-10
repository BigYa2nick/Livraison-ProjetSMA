"""Verrou à priorité (F-35) : asyncio pur, aucune base ni aucun réseau."""

import asyncio

import pytest

from livraison.agents.verrou_priorite import VerrouPrioritaire

pytestmark = pytest.mark.asyncio

URGENT, NORMAL = 0, 1


async def passer(verrou, rang, nom, ordre):
    async with verrou(rang):
        ordre.append(nom)


async def attendre_file(verrou, nombre, delai=1.0):
    async with asyncio.timeout(delai):
        while verrou.en_attente < nombre:
            await asyncio.sleep(0)


async def test_verrou_libre_passe_tout_de_suite():
    verrou = VerrouPrioritaire()
    async with verrou(NORMAL):
        assert verrou.occupe
    assert not verrou.occupe


async def test_l_urgente_passe_avant_la_normale_arrivee_plus_tot():
    verrou, ordre = VerrouPrioritaire(), []
    async with verrou(NORMAL):  # le verrou est tenu : les deux suivantes attendent ensemble
        normale = asyncio.create_task(passer(verrou, NORMAL, "normale", ordre))
        await attendre_file(verrou, 1)
        urgente = asyncio.create_task(passer(verrou, URGENT, "urgente", ordre))
        await attendre_file(verrou, 2)
    await asyncio.gather(normale, urgente)
    assert ordre == ["urgente", "normale"]


async def test_a_rang_egal_premier_arrive_premier_servi():
    verrou, ordre = VerrouPrioritaire(), []
    async with verrou(NORMAL):
        taches = []
        for i, nom in enumerate("abcd"):
            taches.append(asyncio.create_task(passer(verrou, NORMAL, nom, ordre)))
            await attendre_file(verrou, i + 1)
    await asyncio.gather(*taches)
    assert ordre == ["a", "b", "c", "d"]


async def test_les_urgentes_gardent_entre_elles_l_ordre_d_arrivee():
    verrou, ordre = VerrouPrioritaire(), []
    async with verrou(NORMAL):
        taches = []
        for i, (nom, rang) in enumerate([("n1", NORMAL), ("u1", URGENT), ("n2", NORMAL), ("u2", URGENT)]):
            taches.append(asyncio.create_task(passer(verrou, rang, nom, ordre)))
            await attendre_file(verrou, i + 1)
    await asyncio.gather(*taches)
    assert ordre == ["u1", "u2", "n1", "n2"]


async def test_l_urgente_n_enleve_pas_la_place_a_celle_qui_decide_deja():
    verrou, ordre = VerrouPrioritaire(), []
    en_cours, fin = asyncio.Event(), asyncio.Event()

    async def decision_normale():
        async with verrou(NORMAL):
            en_cours.set()
            await fin.wait()
            ordre.append("normale")

    normale = asyncio.create_task(decision_normale())
    await en_cours.wait()
    urgente = asyncio.create_task(passer(verrou, URGENT, "urgente", ordre))
    await attendre_file(verrou, 1)
    assert ordre == []  # l'urgente attend : on ne reprend pas une décision en cours
    fin.set()
    await asyncio.gather(normale, urgente)
    assert ordre == ["normale", "urgente"]


async def test_jamais_deux_taches_en_meme_temps():
    verrou, dedans, maximum = VerrouPrioritaire(), 0, 0

    async def section(rang):
        nonlocal dedans, maximum
        async with verrou(rang):
            dedans += 1
            maximum = max(maximum, dedans)
            await asyncio.sleep(0)
            dedans -= 1

    await asyncio.gather(*(section(i % 2) for i in range(20)))
    assert maximum == 1 and not verrou.occupe


async def test_une_tache_annulee_en_attente_ne_bloque_personne():
    verrou, ordre = VerrouPrioritaire(), []
    async with verrou(NORMAL):
        annulee = asyncio.create_task(passer(verrou, URGENT, "annulee", ordre))
        await attendre_file(verrou, 1)
        annulee.cancel()
        suivante = asyncio.create_task(passer(verrou, NORMAL, "suivante", ordre))
        await attendre_file(verrou, 1)  # l'annulée ne compte plus
    with pytest.raises(asyncio.CancelledError):
        await annulee
    await asyncio.wait_for(suivante, 1.0)
    assert ordre == ["suivante"] and not verrou.occupe


async def test_annulee_juste_apres_avoir_recu_le_verrou_le_passe_au_suivant():
    verrou, ordre = VerrouPrioritaire(), []
    async with verrou(NORMAL):
        premiere = asyncio.create_task(passer(verrou, URGENT, "premiere", ordre))
        await attendre_file(verrou, 1)
        seconde = asyncio.create_task(passer(verrou, NORMAL, "seconde", ordre))
        await attendre_file(verrou, 2)
    premiere.cancel()  # le verrou lui a été donné, mais elle n'a pas encore repris la main
    with pytest.raises(asyncio.CancelledError):
        await premiere
    await asyncio.wait_for(seconde, 1.0)
    assert ordre == ["seconde"] and not verrou.occupe


async def test_une_erreur_dans_la_section_libere_le_verrou():
    verrou = VerrouPrioritaire()
    with pytest.raises(RuntimeError):
        async with verrou(NORMAL):
            raise RuntimeError("panne")
    assert not verrou.occupe
    async with asyncio.timeout(1.0):
        async with verrou(URGENT):
            pass

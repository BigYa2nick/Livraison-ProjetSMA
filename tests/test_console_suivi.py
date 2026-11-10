from livraison.interfaces.messages_suivi import message_refus_ws


def test_refus_ws_explique_quels_roles_sont_autorises():
    assert "répartiteur ou administrateur" in message_refus_ws(403)


def test_refus_ws_indique_quand_la_session_est_expiree():
    assert "Session expirée" in message_refus_ws(401)


def test_refus_ws_presente_les_autres_statuts():
    assert "HTTP 503" in message_refus_ws(503)

"""Comportement de la banque simulée selon le dernier chiffre du MSISDN.

    1 : refus métier (opt-in refusé, client non éligible)
    2 : éligible pour un montant inférieur au minimum
    3 : réponse plus lente que le timeout du mock Airtel
    4 : erreur HTTP 500 avec un corps non JSON
    5 : éligible, mais Apply Loan refusé (prêt déjà en cours)
    autre : succès
"""

REFUSED = "1"
BELOW_MINIMUM = "2"
SLOW = "3"
SERVER_ERROR = "4"
ACTIVE_LOAN = "5"

DESCRIPTIONS = {
    REFUSED: "Business refusal (opt-in refused, customer not eligible)",
    BELOW_MINIMUM: "Eligible amount below the minimum amount",
    SLOW: "Response slower than the Airtel mock timeout",
    SERVER_ERROR: "HTTP 500 with a non-JSON body",
    ACTIVE_LOAN: "Eligible, but Apply Loan refused (active loan)",
}


def scenario_for(msisdn: str) -> str | None:
    """Scénario associé au MSISDN, ou None pour le cas nominal."""
    last = msisdn[-1:] if msisdn else ""
    return last if last in DESCRIPTIONS else None

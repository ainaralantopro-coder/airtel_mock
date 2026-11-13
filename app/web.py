"""Configuration Jinja partagée par les écrans."""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


async def read_form(request: Request) -> FormData:
    """Formulaire lu par une dépendance asynchrone : les écrans qui appellent la banque restent synchrones.

    FastAPI exécute une route synchrone dans un thread. Une route async faisant un appel HTTP bloquant figerait
    la boucle d'uvicorn : la banque ne pourrait plus appeler le mock (User Enquiry pendant l'opt-in, par exemple)
    avant d'avoir répondu, et chacun attendrait l'autre jusqu'au timeout.
    """
    return await request.form()


def format_ariary(value) -> str:
    """50000 -> "Ar 50 000" ; les décimales ne sont affichées que si elles ne sont pas nulles."""
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    text = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    return "Ar " + text.replace(",", " ")


def format_amount(amount: Decimal) -> str:
    """Montant en chaîne pour les API : "1000", ou "1000.5" s'il y a des décimales."""
    if amount == amount.to_integral_value():
        return str(int(amount))
    return f"{amount.normalize():f}"


templates.env.filters["ariary"] = format_ariary

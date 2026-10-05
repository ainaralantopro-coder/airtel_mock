"""Configuration Jinja partagée par les écrans."""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


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


templates.env.filters["ariary"] = format_ariary

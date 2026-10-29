"""Écran Mini statement : appelle Mini Statement à la banque et affiche sa réponse JSON telle quelle."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.bank_client import send_mini_statement
from app.config import get_settings
from app.database import get_db
from app.web import templates

router = APIRouter(prefix="/mini-statement", include_in_schema=False)


@router.get("", response_class=HTMLResponse)
def mini_statement_form(request: Request):
    return templates.TemplateResponse(request, "mini_statement.html", _context(""))


@router.post("", response_class=HTMLResponse)
async def mini_statement(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    msisdn = str(form.get("msisdn", "")).strip()
    context = _context(msisdn)

    if context["url"] is None:
        context["error"] = "BANK_MINI_STATEMENT_PATH must be set in .env."
        return templates.TemplateResponse(request, "mini_statement.html", context, status_code=503)
    if not msisdn:
        context["error"] = "The MSISDN is required."
        return templates.TemplateResponse(request, "mini_statement.html", context, status_code=400)

    call = send_mini_statement(db, context["url"], msisdn)
    context["call"] = call
    if call.error:
        context["error"] = f"Bank call failed: {call.error}"
    else:
        try:
            context["response_json"] = json.dumps(json.loads(call.response_body or ""), indent=2, ensure_ascii=False)
        except ValueError:
            context["error"] = f"Unreadable bank response (HTTP {call.response_status}): not JSON."
    return templates.TemplateResponse(request, "mini_statement.html", context)


def _context(msisdn: str) -> dict:
    return {"msisdn": msisdn, "url": get_settings().bank_mini_statement_url}

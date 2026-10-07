"""TEMPORAIRE, à supprimer après le test : appel manuel de notificationSubscriptionStatus vers la banque.

Pour retirer : supprimer ce fichier et templates/subscription_test.html, puis l'import et l'include_router
dans main.py et le lien « Subscription test » dans base.html.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.bank_client import _post_json
from app.config import get_settings
from app.database import get_db
from app.web import templates

router = APIRouter(prefix="/subscription-test", include_in_schema=False)

PATH = "/api/merchant/notification/notificationSubscriptionStatus/{id}"


@router.get("", response_class=HTMLResponse)
def subscription_form(request: Request):
    return templates.TemplateResponse(request, "subscription_test.html", _context())


@router.post("", response_class=HTMLResponse)
async def subscription_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    subscription_id = str(form.get("subscription_id", "")).strip()
    context = _context(subscription_id)

    if not context["base_url"]:
        context["error"] = "BANK_BASE_URL must be set in .env."
        return templates.TemplateResponse(request, "subscription_test.html", context, status_code=503)
    if not subscription_id:
        context["error"] = "The subscription ID is required."
        return templates.TemplateResponse(request, "subscription_test.html", context, status_code=400)

    url = context["base_url"].rstrip("/") + PATH.format(id=quote(subscription_id, safe=""))
    context["call"] = _post_json(
        db, flow="subscription_status_test", url=url, msisdn=None, payload={"SubscriptionId": subscription_id}
    )
    return templates.TemplateResponse(request, "subscription_test.html", context)


def _context(subscription_id: str = "") -> dict:
    return {"subscription_id": subscription_id, "base_url": get_settings().bank_base_url, "path": PATH}

"""Prêts accordés par le bank-mock, conservés dans un fichier JSON pour survivre aux redémarrages.

Le fichier est un objet {transactionId: prêt}. Il est relu au démarrage et réécrit entièrement après
chaque modification, via un fichier temporaire renommé ensuite : un arrêt brutal ne peut pas le corrompre.
"""

import json
import os
from pathlib import Path

from bank_mock.config import get_settings

# transactionId -> données du prêt accordé
LOANS: dict[str, dict] = {}


def load() -> None:
    """Remplace LOANS par le contenu du fichier ; ne fait rien si le fichier n'existe pas encore."""
    path = Path(get_settings().loans_file)
    LOANS.clear()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"{path} is not valid JSON: fix or delete it ({exc})") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object keyed by transactionId")
    LOANS.update(data)


def save() -> None:
    path = Path(get_settings().loans_file)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(LOANS, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

"""Señales curadas a mano (vacantes, prensa, venta de cartera) desde un JSON con fuente."""
from __future__ import annotations

import json
from pathlib import Path

from . import Signal


def load(cfg: dict) -> dict:
    return json.loads(Path(cfg["archivo"]).read_text(encoding="utf-8"))


def fetch(cfg: dict, icp: dict) -> list[Signal]:
    out: list[Signal] = []
    for e in load(cfg)["empresas"]:
        # Perfil: nombre, dominio, decisores y gancho del mensaje viajan como una señal de tipo "perfil".
        perfil = {k: v for k, v in e.items() if k not in ("senales", "calificaciones")}
        out.append(Signal(e["id"], "perfil", e["nombre"], "data/signals.json", datos=perfil))
        for crit, (val, why) in e.get("calificaciones", {}).items():
            out.append(Signal(e["id"], "analista", why, "Criterio del analista", criterio=crit, calificacion=int(val)))
        for s in e.get("senales", []):
            out.append(Signal(e["id"], s["tipo"], s["texto"], s["fuente"], s.get("url"), s.get("fecha"), s.get("criterio")))
    return out

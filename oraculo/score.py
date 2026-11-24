"""Puntaje 0-100 ponderado según icp.yaml. Cada punto queda explicado."""
from __future__ import annotations

from collections import defaultdict

from .sources import Signal


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


def is_excluded(nombre: str, icp: dict) -> bool:
    n = _norm(nombre)
    return any(_norm(x) == n for x in icp.get("excluir", []))


def score_company(signals: list[Signal], icp: dict) -> dict:
    crits = icp["criterios"]
    total_w = sum(c["peso"] for c in crits) or 1
    by_crit: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        if s.criterio:
            by_crit[s.criterio].append(s)

    desglose, puntaje = [], 0.0
    for c in crits:
        ss = by_crit.get(c["clave"], [])
        analista = next((s for s in ss if s.tipo == "analista" and s.calificacion), None)
        cuanti = next((s for s in ss if s.cuantitativa and s.calificacion), None)
        if cuanti and icp.get("cuantitativo_prevalece", True):
            rating, origen, porque = cuanti.calificacion, cuanti.fuente, cuanti.texto
        elif analista:
            rating, origen, porque = analista.calificacion, analista.fuente, analista.texto
        else:
            rating, origen, porque = 1, "Sin señales", "No hay evidencia para este criterio."
        pts = c["peso"] * (rating - 1) / 4 / total_w * 100
        puntaje += pts
        desglose.append({
            "criterio": c["clave"], "nombre": c["nombre"], "peso": c["peso"],
            "calificacion": rating, "puntos": round(pts, 1), "max_puntos": round(c["peso"] / total_w * 100, 1),
            "origen": origen, "porque": porque,
            "calificacion_analista": analista.calificacion if analista else None,
            "evidencia": [s.texto for s in ss if s.tipo != "analista"],
        })
    return {"puntaje": round(puntaje), "desglose": desglose}

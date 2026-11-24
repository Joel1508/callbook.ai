"""Superfinanciera vía datos.gov.co (Socrata).

Dataset: "Distribución de cartera por producto" (id rvii-eis8), formato de cartera por entidad,
mensual, por unidad de captura (UNICAP). Se usa solo el renglón 5 (total de cada unidad).

Para cada entidad calcula, en el último corte y contra el mismo mes del año anterior:
  - crecimiento anual de cartera de consumo y de microcrédito
  - saldo vencido (saldo total - saldo vigente)
  - ICV (indicador de calidad de cartera) = vencido / saldo
y convierte crecimiento y tamaño en una calificación 1-5 del criterio configurado (umbrales en icp.yaml).

Para encontrar otros datasets: https://www.datos.gov.co/api/catalog/v1?q=cartera%20superintendencia%20financiera
"""
from __future__ import annotations

from datetime import date

import requests

from . import Signal

TIMEOUT = 30


def _q(cfg: dict, params: dict) -> list[dict]:
    url = f"https://{cfg.get('dominio', 'www.datos.gov.co')}/resource/{cfg['dataset']}.json"
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _esc(s: str) -> str:
    return s.replace("'", "''")


def _year_before(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    try:
        prev = d.replace(year=d.year - 1)
    except ValueError:  # 29 de febrero
        prev = d.replace(year=d.year - 1, day=28)
    return prev.isoformat()


def entity_metrics(cfg: dict, nombre: str) -> dict:
    ent = _esc(nombre)
    last = _q(cfg, {"$select": "max(fecha_corte) AS f", "$where": f"nombreentidad='{ent}'"})
    if not last or not last[0].get("f"):
        raise LookupError(f"entidad no encontrada: {nombre}")
    f1 = last[0]["f"][:10]
    f0 = _year_before(f1)
    codes = sorted({str(c) for g in cfg["grupos"].values() for c in g}, key=int)
    rows = _q(cfg, {
        "$select": "fecha_corte,unicap,_1_saldo_de_la_cartera_a,_2_vigente,_16_n_mero_de_clientes_mora",
        "$where": (f"nombreentidad='{ent}' AND renglon='5' AND unicap in({','.join(repr(c) for c in codes)})"
                   f" AND (fecha_corte='{f1}T00:00:00' OR fecha_corte='{f0}T00:00:00')"),
        "$limit": 5000,
    })
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        f = r["fecha_corte"][:10]
        for g, cs in cfg["grupos"].items():
            if int(r["unicap"]) in cs:
                a = agg.setdefault((g, f), {"saldo": 0.0, "vigente": 0.0, "clientes_mora": 0})
                a["saldo"] += float(r.get("_1_saldo_de_la_cartera_a") or 0)
                a["vigente"] += float(r.get("_2_vigente") or 0)
                a["clientes_mora"] += int(float(r.get("_16_n_mero_de_clientes_mora") or 0))
    out = {"entidad": nombre, "corte": f1, "corte_anterior": f0, "grupos": {}}
    for g in cfg["grupos"]:
        now, prev = agg.get((g, f1)), agg.get((g, f0))
        if not now or now["saldo"] <= 0:
            continue
        venc = now["saldo"] - now["vigente"]
        out["grupos"][g] = {
            "saldo": round(now["saldo"]), "saldo_vencido": round(venc),
            "icv_pct": round(venc / now["saldo"] * 100, 2), "clientes_mora": now["clientes_mora"],
            "saldo_anio_anterior": round(prev["saldo"]) if prev else None,
            "crecimiento_anual_pct": round((now["saldo"] / prev["saldo"] - 1) * 100, 1) if prev and prev["saldo"] > 0 else None,
        }
    tot_now = sum(g["saldo"] for g in out["grupos"].values())
    tot_prev = sum(g["saldo_anio_anterior"] or 0 for g in out["grupos"].values())
    tot_venc = sum(g["saldo_vencido"] for g in out["grupos"].values())
    out["total"] = {
        "saldo": tot_now, "saldo_vencido": tot_venc,
        "icv_pct": round(tot_venc / tot_now * 100, 2) if tot_now else None,
        "crecimiento_anual_pct": round((tot_now / tot_prev - 1) * 100, 1) if tot_prev else None,
    }
    return out


def rating_for(growth: float | None, umbrales: list) -> int | None:
    if growth is None:
        return None
    for th, val in umbrales:
        if growth >= th:
            return int(val)
    return 1


def _rating(t: dict, cfg: dict) -> int | None:
    """Calificación = la menor entre crecimiento y tamaño: una base pequeña que crece 1.000 % no es un 5."""
    g = rating_for(t["crecimiento_anual_pct"], cfg.get("umbrales_crecimiento", []))
    if g is None or not cfg.get("umbrales_saldo"):
        return g
    return min(g, rating_for(t["saldo"] / 1e9, cfg["umbrales_saldo"]))


def _cop(x: float) -> str:
    return f"${x / 1e12:,.2f} billones".replace(",", "X").replace(".", ",").replace("X", ".") if x >= 1e12 else \
        f"${x / 1e9:,.0f} mil millones".replace(",", ".")


def fetch(cfg: dict, icp: dict) -> list[Signal]:
    out: list[Signal] = []
    for pid, nombre in (cfg.get("entidades") or {}).items():
        m = entity_metrics(cfg, nombre)
        t = m["total"]
        partes = []
        for g, v in m["grupos"].items():
            cr = f"{v['crecimiento_anual_pct']:+.1f} % anual" if v["crecimiento_anual_pct"] is not None else "sin año anterior"
            partes.append(f"{g}: {_cop(v['saldo'])} ({cr}), ICV {v['icv_pct']:.1f} %")
        texto = (f"Superfinanciera, corte {m['corte']}: cartera de consumo y microcrédito {_cop(t['saldo'])}"
                 + (f", {t['crecimiento_anual_pct']:+.1f} % anual" if t["crecimiento_anual_pct"] is not None else "")
                 + f"; saldo vencido {_cop(t['saldo_vencido'])} (ICV {t['icv_pct']:.1f} %). " + "; ".join(partes) + ".")
        url = f"https://{cfg.get('dominio', 'www.datos.gov.co')}/d/{cfg['dataset']}"
        out.append(Signal(pid, "cartera_sfc", texto, "Superfinanciera (datos.gov.co)", url, m["corte"],
                          cfg.get("criterio", "cartera"),
                          _rating(t, cfg),
                          cuantitativa=True, datos=m))
    return out

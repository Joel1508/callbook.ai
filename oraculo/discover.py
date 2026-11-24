"""Descubre decisores por dominio con Hunter Domain Search y los compara con los ya conocidos.

Pasos: dominio -> Hunter Domain Search (con caché y tope de llamadas) -> filtro por palabras
clave del cargo (icp.yaml) -> rol (decisor / aprobador / influenciador) -> cruce con los
decisores de data/signals.json (coincidencia o persona nueva).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .enrich import _ascii, _level

CACHE = Path("out/.discover_cache.json")   # datos completos; git lo ignora
TIMEOUT = 20


def _norm(s: str | None) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in _ascii(s or "").lower()).split())


def _has(text: str, words: list[str]) -> bool:
    t = f" {_norm(text)} "
    return any(f" {_norm(w)} " in t for w in words)


class Budget:
    """Tope de llamadas reales a Hunter por corrida; las respuestas en caché no cuentan."""

    def __init__(self, max_calls: int):
        self.left = max_calls
        self.used = 0


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def domain_search(domain: str, cfg: dict, budget: Budget) -> tuple[list[dict], str]:
    """Devuelve (correos de Hunter, origen) donde origen es 'cache', 'hunter' o el motivo de omisión."""
    c = _cache()
    if domain in c:
        return c[domain], "cache"
    if cfg.get("solo_cache"):
        return [], "omitido (solo_cache)"
    key = os.getenv("HUNTER_API_KEY")
    if not key:
        return [], "omitido (sin HUNTER_API_KEY)"
    if budget.left <= 0:
        return [], "omitido (tope de llamadas)"
    budget.left -= 1
    budget.used += 1
    params = {"domain": domain, "api_key": key, "limit": cfg.get("limite", 10)}
    if cfg.get("departamentos"):
        params["department"] = ",".join(cfg["departamentos"])
    r = requests.get("https://api.hunter.io/v2/domain-search", params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"hunter domain-search {domain}: {r.status_code} {r.text[:200]}")
    emails = (r.json().get("data") or {}).get("emails") or []
    c[domain] = emails
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    return emails, "hunter"


def classify(cargo: str, roles: dict) -> str | None:
    """Primera regla que aplica, en el orden de icp.yaml."""
    for rol, regla in roles.items():
        if regla.get("todas"):
            if all(_has(cargo, grupo) for grupo in regla["todas"]):
                return rol
        elif _has(cargo, regla.get("alguna", [])):
            return rol
    return None


def _same_person(a: str, b: str) -> bool:
    ta, tb = _norm(a).split(), _norm(b).split()
    # mismo primer nombre y algún apellido en común
    return bool(ta and tb and ta[0] == tb[0] and set(ta[1:]) & set(tb[1:]))


def _same_first(a: str, b: str) -> bool:
    ta, tb = _norm(a).split(), _norm(b).split()
    return bool(ta and tb and ta[0] == tb[0])


def _dedupe(emails: list[dict]) -> list[dict]:
    """Hunter puede devolver a la misma persona dos veces (Suárez / Suarez): queda la de mayor confianza."""
    best: dict[str, dict] = {}
    for e in emails:
        k = _norm(f"{e.get('first_name') or ''} {e.get('last_name') or ''}")
        if k and (k not in best or (e.get("confidence") or 0) > (best[k].get("confidence") or 0)):
            best[k] = e
    return list(best.values())


def discover_company(perfil: dict, cfg: dict, budget: Budget, min_conf: int = 70) -> dict:
    domain = perfil.get("dominio")
    emails, origen = domain_search(domain, cfg, budget) if domain else ([], "sin dominio")
    emails = _dedupe(emails)
    conocidos = perfil.get("decisores", [])
    full = lambda e: " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x)
    # El cruce con los conocidos se hace contra todo lo que devolvió Hunter, no solo lo filtrado
    emparejados = {k["nombre"] for k in conocidos for e in emails if _same_person(k["nombre"], full(e))}
    personas = []
    for e in emails:
        cargo = e.get("position") or ""
        nombre = full(e)
        if not nombre or not _has(cargo, cfg["palabras_clave"]):
            continue
        match = next((k["nombre"] for k in conocidos if _same_person(k["nombre"], nombre)), None)
        # mismo primer nombre, otro apellido: puede ser la misma persona mal registrada en nuestros datos
        posible = None if match else next(
            (k["nombre"] for k in conocidos if k["nombre"] not in emparejados and _same_first(k["nombre"], nombre)), None)
        personas.append({
            "nombre": nombre, "cargo": cargo, "rol": classify(cargo, cfg["roles"]) or "influenciador",
            "correo": e.get("value"), "puntaje_confianza": e.get("confidence"),
            "confianza": _level(e.get("confidence"), min_conf),
            "verificacion": (e.get("verification") or {}).get("status"),
            "linkedin_url": e.get("linkedin"), "fuente": "hunter domain-search",
            "nuevo": match is None, "coincide_con": match, "posible_coincidencia": posible,
        })
    orden = {r: i for i, r in enumerate(cfg["roles"])}
    personas.sort(key=lambda p: (orden.get(p["rol"], 99), not p["nuevo"], p["nombre"]))
    return {
        "dominio": domain, "origen": origen, "encontrados_hunter": len(emails),
        "personas": personas,
        "conocidos_no_encontrados": [k["nombre"] for k in conocidos if k["nombre"] not in emparejados],
    }

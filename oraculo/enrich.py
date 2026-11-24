"""Decisores -> dominio (validado con MX) -> Hunter Email Finder o Apollo people/match.

Devuelve correo, nivel de confianza, fuente y linkedin_url. Nunca inventa correos:
si ningún proveedor responde, el correo queda en None. El único teléfono que se
expone es el PBX corporativo (Ley 1581: solo canales corporativos).
"""
from __future__ import annotations

import os
import unicodedata
import json
from functools import lru_cache
from pathlib import Path

import requests

TIMEOUT = 20
CACHE = Path("out/.enrich_cache.json")   # evita gastar créditos al repetir; contiene datos completos (git lo ignora)

# Palabras equivalentes para comparar el cargo que tenemos con el que reporta el proveedor
_SYN = {
    "cobranza": "collections", "cobranzas": "collections", "cobro": "collections", "collection": "collections",
    "riesgo": "risk", "riesgos": "risk", "operaciones": "operations", "operacion": "operations",
    "vicepresidente": "vp", "vice": "vp", "president": "vp", "director": "director", "directora": "director",
    "gerente": "manager", "lider": "lead", "head": "lead", "ceo": "ceo", "cofundador": "founder", "cofounder": "founder",
    "credito": "credit", "finanzas": "finance", "financiero": "finance",
}
_STOP = {"de", "del", "y", "la", "el", "of", "and", "the", "antes", "en", "a"}


@lru_cache(maxsize=None)
def mx_valid(domain: str) -> list[str]:
    try:
        import dns.resolver
        ans = dns.resolver.resolve(domain, "MX", lifetime=8)
        return sorted(str(r.exchange).rstrip(".") for r in ans)
    except Exception:
        return []


def _split(nombre: str) -> tuple[str, str]:
    parts = nombre.split()
    return parts[0], " ".join(parts[1:]) if len(parts) > 1 else ""


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _level(score: int | None, minimo: int) -> str:
    if score is None:
        return "desconocida"
    return "alta" if score >= 90 else "media" if score >= minimo else "baja"


def hunter(first: str, last: str, domain: str, key: str, minimo: int) -> dict | None:
    r = requests.get("https://api.hunter.io/v2/email-finder", timeout=TIMEOUT,
                     params={"domain": domain, "first_name": _ascii(first), "last_name": _ascii(last), "api_key": key})
    if r.status_code != 200:
        return {"error": f"hunter {r.status_code}: {r.text[:160]}"}
    d = r.json().get("data") or {}
    if not d.get("email"):
        return None
    return {"correo": d["email"], "puntaje_confianza": d.get("score"), "confianza": _level(d.get("score"), minimo),
            "fuente": "hunter", "verificacion": (d.get("verification") or {}).get("status"),
            "linkedin_url": d.get("linkedin_url"), "cargo_proveedor": d.get("position"),
            "evidencia": [s.get("uri") for s in d.get("sources", [])][:3]}


def apollo(first: str, last: str, domain: str, empresa: str, key: str) -> dict | None:
    r = requests.post("https://api.apollo.io/api/v1/people/match", timeout=TIMEOUT,
                      headers={"X-Api-Key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"},
                      json={"first_name": first, "last_name": last, "domain": domain, "organization_name": empresa,
                            "reveal_personal_emails": False})
    if r.status_code != 200:
        return {"error": f"apollo {r.status_code}: {r.text[:160]}"}
    p = r.json().get("person") or {}
    if not p:
        return None
    status = p.get("email_status")
    email = p.get("email")
    # Solo correos del dominio corporativo
    if email and not email.lower().endswith("@" + domain.lower()):
        email = None
    conf = {"verified": "alta", "likely_to_engage": "media", "guessed": "baja"}.get(status, "desconocida")
    return {"correo": email, "puntaje_confianza": None, "confianza": conf if email else "sin correo",
            "fuente": "apollo", "verificacion": status, "linkedin_url": p.get("linkedin_url"),
            "cargo_proveedor": p.get("title"), "evidencia": []}


def _tokens(cargo: str) -> set[str]:
    words = "".join(c if c.isalnum() else " " for c in _ascii(cargo).lower()).split()
    return {_SYN.get(w, w) for w in words if w not in _STOP and len(w) > 1}


def cargo_coincide(nuestro: str | None, proveedor: str | None) -> bool | None:
    """None si no hay con qué comparar; False si no comparten ninguna palabra clave (posible homónimo)."""
    if not nuestro or not proveedor:
        return None
    nuestro = nuestro.split("·")[0]  # solo el cargo actual
    return bool(_tokens(nuestro) & _tokens(proveedor))


def _cache_load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_save(key: str, val: dict) -> None:
    c = _cache_load()
    c[key] = val
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


def enrich_person(nombre: str, empresa: str, domain: str, cfg: dict, cargo: str | None = None) -> dict:
    key = f"{_ascii(nombre).lower()}@{domain}"
    hit = _cache_load().get(key)
    if hit is None and cfg.get("solo_cache"):
        hit = {"correo": None, "confianza": "no enriquecido", "puntaje_confianza": None, "fuente": None,
               "linkedin_url": None, "dominio": domain, "mx_valido": bool(mx_valid(domain)) if domain else False,
               "errores": ["solo_cache: no se consultó al proveedor"]}
        return _review(hit, cargo)
    if hit is not None:
        return _review(dict(hit), cargo)
    res = _lookup(nombre, empresa, domain, cfg)
    if res.get("fuente"):  # solo se cachean respuestas reales del proveedor
        _cache_save(key, res)
    return _review(res, cargo)


def _review(res: dict, cargo: str | None) -> dict:
    """Marca para revisión humana: cargo distinto (posible homónimo) o dominio accept_all (no verificable)."""
    res["cargo_coincide"] = cargo_coincide(cargo, res.get("cargo_proveedor"))
    notas = []
    if res["cargo_coincide"] is False:
        notas.append(f"El proveedor reporta otro cargo ({res.get('cargo_proveedor')}): posible homónimo; confirmar en LinkedIn")
        if res.get("correo"):
            res["confianza"] = "baja"
    if res.get("verificacion") == "accept_all":
        notas.append("El servidor acepta cualquier dirección: correo inferido por patrón, no verificado")
    res["revisar"] = notas
    return res


def _lookup(nombre: str, empresa: str, domain: str, cfg: dict) -> dict:
    first, last = _split(nombre)
    mx = mx_valid(domain) if domain else []
    res = {"correo": None, "confianza": "sin datos", "puntaje_confianza": None, "fuente": None,
           "linkedin_url": None, "dominio": domain, "mx_valido": bool(mx), "mx": mx[:2], "errores": []}
    if not mx:
        res["errores"].append(f"{domain}: sin registros MX; no se busca correo")
        return res
    hk, ak = os.getenv("HUNTER_API_KEY"), os.getenv("APOLLO_API_KEY")
    prov = cfg.get("proveedor", "auto")
    order = {"hunter": ["hunter"], "apollo": ["apollo"]}.get(prov, ["hunter", "apollo"])
    tried = False
    for p in order:
        try:
            if p == "hunter" and hk:
                tried, out = True, hunter(first, last, domain, hk, cfg.get("confianza_minima", 70))
            elif p == "apollo" and ak:
                tried, out = True, apollo(first, last, domain, empresa, ak)
            else:
                continue
        except requests.RequestException as e:
            res["errores"].append(f"{p}: {e}")
            continue
        if out and out.get("error"):
            res["errores"].append(out["error"])
            continue
        if out:
            linkedin = res["linkedin_url"] or out.get("linkedin_url")
            res.update({k: v for k, v in out.items() if v is not None})
            res["linkedin_url"] = linkedin
            if res.get("correo"):
                break
    if not tried:
        res["errores"].append("Sin HUNTER_API_KEY ni APOLLO_API_KEY en el entorno")
    return res


def enrich_company(perfil: dict, cfg: dict) -> list[dict]:
    out = []
    for d in perfil.get("decisores", []):
        r = enrich_person(d["nombre"], perfil["nombre"], perfil.get("dominio"), cfg, d.get("cargo"))
        out.append({**d, **r, "telefono": perfil.get("pbx"), "telefono_tipo": "PBX corporativo" if perfil.get("pbx") else None})
    return out


def mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    user, dom = email.split("@", 1)
    return f"{user[0]}***@{dom}"


if __name__ == "__main__":
    # python -m oraculo.enrich "Jessica Alvira:Addi:addi.com[:cargo]" ...
    import json
    import sys
    from .run import load_env
    load_env()
    for arg in sys.argv[1:]:
        n, e, d, *c = arg.split(":")
        print(json.dumps({"nombre": n, **enrich_person(n, e, d, {}, c[0] if c else None)}, ensure_ascii=False))

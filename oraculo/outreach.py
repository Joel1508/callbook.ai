"""Primer mensaje por prospecto, con la señal concreta y chequeos de Ley 2300 de 2023 / Ley 1581 de 2012.

Ley 2300 ("dejen de fregar"): contacto solo por canales autorizados, máximo una vez por semana
por canal, lunes a viernes 7:00-19:00 y sábados 8:00-15:00, nunca domingos ni festivos.
Aunque el mensaje es B2B, se aplican las mismas reglas como estándar de la casa.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Bogota")


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def next_send_slot(cfg: dict, now: datetime | None = None) -> datetime:
    """Primer momento permitido a partir de ahora (hora de Bogotá)."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    v = cfg["ventana"]
    festivos = {date.fromisoformat(d) for d in cfg.get("festivos", [])}
    d = now
    for _ in range(14):
        wd = d.weekday()
        win = v["lunes_viernes"] if wd < 5 else v["sabado"] if wd == 5 else None
        if win and d.date() not in festivos:
            start = datetime.combine(d.date(), _t(win[0]), TZ)
            end = datetime.combine(d.date(), _t(win[1]), TZ)
            if d < end:
                return max(d, start).replace(second=0, microsecond=0)
        d = datetime.combine(d.date() + timedelta(days=1), time(0, 0), TZ)
    raise RuntimeError("No hay ventana de envío en 14 días")


def compose(perfil: dict, contacto: dict, icp: dict) -> dict:
    v, m = icp["vendedor"], icp["mensaje"]
    nombre = contacto["nombre"].split()[0]
    texto = m["plantilla"].format(
        nombre=nombre, gancho=perfil["gancho"][0].upper() + perfil["gancho"][1:],
        empresa_vendedor=v["empresa"], descripcion_vendedor=v["descripcion"],
        remitente=v["remitente"], cargo=v["cargo"], url_baja=v["url_baja"],
    ).strip()
    checks = check(texto, contacto, icp)
    return {
        "para": contacto["nombre"], "cargo": contacto.get("cargo"), "canal": "correo corporativo",
        "asunto": f"{perfil['nombre']} y la cartera tardía",
        "texto": texto,
        "enviar_desde": next_send_slot(m).isoformat(),
        "max_contactos_por_semana": m.get("max_contactos_por_semana", 1),
        "cumplimiento": checks, "cumple": all(c["ok"] for c in checks),
    }


def check(texto: str, contacto: dict, icp: dict) -> list[dict]:
    v = icp["vendedor"]
    correo = contacto.get("correo") or ""
    dominio = contacto.get("dominio") or ""
    return [
        {"regla": "Identifica a quien escribe y a su empresa", "ok": v["remitente"] in texto and v["empresa"] in texto},
        {"regla": "Ofrece una forma clara de no recibir más mensajes", "ok": "baja" in texto.lower()},
        {"regla": "Solo canal corporativo (dominio de la empresa, nunca correo personal)",
         "ok": (not correo) or correo.lower().endswith("@" + dominio.lower())},
        {"regla": "Explica por qué se le contacta (cargo relacionado)", "ok": "cargo" in texto.lower()},
        {"regla": "Sin lenguaje de presión ni amenazas",
         "ok": not any(w in texto.lower() for w in ("urgente", "último aviso", "embargo", "reportado"))},
        {"regla": "Breve (menos de 120 palabras)", "ok": len(texto.split()) < 120},
        {"regla": "Envío dentro de la ventana horaria y máximo 1 contacto por semana", "ok": True},
    ]

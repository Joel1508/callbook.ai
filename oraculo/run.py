"""CLI: python -m oraculo.run --icp oraculo/icp.yaml

Escribe:
  out/prospects.json       versión pública (correos enmascarados); se copia a public/prospects.json
  out/prospects_full.json  datos completos (ignorado por git)
  out/prospects.csv        un renglón por decisor, datos completos (ignorado por git)
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from . import enrich, outreach, score
from .sources import Signal


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def collect(icp: dict, only: list[str] | None) -> tuple[list[Signal], list[dict]]:
    signals, log = [], []
    for name, cfg in icp.get("fuentes", {}).items():
        if only and name not in only:
            continue
        try:
            mod = importlib.import_module(f"oraculo.sources.{name}")
            got = mod.fetch(cfg or {}, icp)
            signals += got
            log.append({"fuente": name, "ok": True, "senales": len(got)})
        except Exception as e:  # una fuente caída no tumba el pipeline
            log.append({"fuente": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            print(f"[aviso] fuente {name} falló: {e}", file=sys.stderr)
    return signals, log


def build(icp: dict, signals: list[Signal], do_enrich: bool) -> tuple[list[dict], list[str]]:
    by_co: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        by_co[s.empresa].append(s)
    prospects, excluded = [], []
    for cid, ss in by_co.items():
        perfil = next((s.datos for s in ss if s.tipo == "perfil"), None)
        if not perfil:
            continue  # una fuente cuantitativa sin prospecto declarado no crea prospectos
        if score.is_excluded(perfil["nombre"], icp):
            excluded.append(perfil["nombre"])
            continue
        sc = score.score_company(ss, icp)
        decisores = enrich.enrich_company(perfil, icp.get("enriquecimiento", {})) if do_enrich else [
            {**d, "correo": None, "confianza": "no enriquecido", "fuente": None, "linkedin_url": None,
             "dominio": perfil.get("dominio"), "telefono": perfil.get("pbx")} for d in perfil.get("decisores", [])]
        principal = next((d for d in decisores if d.get("principal")), decisores[0] if decisores else None)
        prospects.append({
            "id": cid, "nombre": perfil["nombre"], "tipo": perfil.get("tipo"), "dominio": perfil.get("dominio"),
            "puntaje": sc["puntaje"], "desglose": sc["desglose"],
            "senales": [s.to_dict() for s in ss if s.tipo not in ("perfil", "analista")],
            "decisores": decisores,
            "mensaje": outreach.compose(perfil, principal, icp) if principal else None,
        })
    prospects.sort(key=lambda p: -p["puntaje"])
    for i, p in enumerate(prospects, 1):
        p["rank"] = i
    return prospects, excluded


def public_copy(doc: dict) -> dict:
    pub = json.loads(json.dumps(doc))
    for p in pub["prospectos"]:
        for d in p["decisores"]:
            d["correo"] = enrich.mask(d.get("correo"))
            d.pop("evidencia", None)
            d.pop("errores", None)
            d.pop("mx", None)
    return pub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Oráculo de prospección B2B")
    ap.add_argument("--icp", default="oraculo/icp.yaml")
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-enrich", action="store_true", help="no llama a Hunter/Apollo")
    ap.add_argument("--solo-cache", action="store_true",
                    help="enriquece solo con resultados ya guardados en out/.enrich_cache.json (no gasta créditos)")
    ap.add_argument("--public", default="public", help="carpeta de la demo; recibe una copia de prospects.json")
    ap.add_argument("--sources", help="lista separada por comas; por defecto todas las de icp.yaml")
    a = ap.parse_args(argv)

    load_env()
    icp_path = Path(a.icp)
    if not icp_path.exists() and (Path("oraculo") / icp_path).exists():
        icp_path = Path("oraculo") / icp_path  # permite --icp icp.yaml desde la raíz
    icp = yaml.safe_load(icp_path.read_text(encoding="utf-8"))
    signals, log = collect(icp, a.sources.split(",") if a.sources else None)
    if a.solo_cache:
        icp.setdefault("enriquecimiento", {})["solo_cache"] = True
    prospects, excluded = build(icp, signals, not a.no_enrich)

    doc = {
        "generado": datetime.now(outreach.TZ).isoformat(timespec="seconds"),
        "vendedor": icp["vendedor"]["empresa"], "vertical": icp.get("vertical"), "pais": icp.get("pais"),
        "criterios": icp["criterios"], "excluidos": icp.get("excluir", []), "excluidos_encontrados": excluded,
        "fuentes": log, "prospectos": prospects,
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prospects_full.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    pub = json.dumps(public_copy(doc), ensure_ascii=False, indent=2)
    (out / "prospects.json").write_text(pub, encoding="utf-8")
    if a.public and Path(a.public).is_dir():
        (Path(a.public) / "prospects.json").write_text(pub, encoding="utf-8")  # lo que se despliega
    with open(out / "prospects.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "empresa", "puntaje", "decisor", "cargo", "correo", "confianza", "fuente_correo",
                    "linkedin_url", "dominio", "mx_valido", "cargo_proveedor", "revisar", "telefono_pbx",
                    "principal", "mensaje"])
        for p in prospects:
            for d in p["decisores"]:
                w.writerow([p["rank"], p["nombre"], p["puntaje"], d["nombre"], d.get("cargo"), d.get("correo"),
                            d.get("confianza"), d.get("fuente"), d.get("linkedin_url"), d.get("dominio"),
                            d.get("mx_valido"), d.get("cargo_proveedor"), " | ".join(d.get("revisar") or []),
                            d.get("telefono"), bool(d.get("principal")),
                            p["mensaje"]["texto"] if d.get("principal") and p["mensaje"] else ""])

    for x in log:
        print(f"fuente {x['fuente']}: " + (f"{x['senales']} señales" if x["ok"] else f"ERROR {x['error']}"))
    for p in prospects:
        print(f"{p['rank']}. {p['nombre']:<18} {p['puntaje']:>3}/100")
    print(f"-> {out}/prospects.json, {out}/prospects.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

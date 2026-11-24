"""Fuentes de señales. Cada módulo expone fetch(cfg: dict, icp: dict) -> list[Signal]."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Signal:
    empresa: str                      # id del prospecto
    tipo: str                         # prensa, vacante, venta_cartera, cartera_sfc, perfil...
    texto: str
    fuente: str
    url: str | None = None
    fecha: str | None = None
    criterio: str | None = None       # criterio del ICP al que aporta evidencia
    calificacion: int | None = None   # 1-5 si la fuente califica el criterio
    cuantitativa: bool = False
    datos: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

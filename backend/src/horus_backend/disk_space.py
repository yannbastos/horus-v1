"""Verificação de espaço em disco para produzir (HOR-094). Somente leitura: nunca cria pastas nem altera o sistema.

O limiar vem do tamanho medido de uma produção do piloto local (Kokoro, 4 seções, ≈ 48 s): ≈ 493 MiB no diretório da
produção (≈ 430 MiB são a cópia local das dependências do Remotion; o MP4 de rascunho tem ≈ 40 MiB). Arredondado para
512 MiB e multiplicado por 4 para dar folga ao Final 1080×1920, à miniatura, à exportação e a uma segunda produção:
**2 GiB livres por volume**. A mensagem usa rótulos, nunca caminhos absolutos.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

MIB = 1024 * 1024
PILOT_PRODUCTION_ESTIMATE_BYTES = 512 * MIB
HEADROOM_FACTOR = 4
REQUIRED_FREE_BYTES = PILOT_PRODUCTION_ESTIMATE_BYTES * HEADROOM_FACTOR


def _existing_ancestor(path: Path) -> Path | None:
    current = Path(path)
    for candidate in (current, *current.parents):
        if candidate.exists():
            return candidate
    return None


def _gib(value: int) -> str:
    return f"{value / 1024 ** 3:.1f}".replace(".", ",") + " GiB"


def locations(settings) -> list[tuple[str, Path]]:
    data = Path(settings.data_dir)
    return [("pasta de dados", data), ("saída de produção", data / "productions"), ("temporários", Path(tempfile.gettempdir()))]


def disk_space_check(settings, *, usage=shutil.disk_usage, device=lambda p: os.stat(p).st_dev,
                     required: int = REQUIRED_FREE_BYTES) -> dict:
    """Item de diagnóstico (`id`, `name`, `available`, `detail`, `required_for`), no mesmo formato dos demais."""
    volumes: dict[object, dict] = {}
    for label, path in locations(settings):
        anchor = _existing_ancestor(path)
        try:
            free = usage(anchor).free if anchor is not None else None
            key = device(anchor) if anchor is not None else label
        except OSError:
            free, key = None, label
        volume = volumes.setdefault(key, {"labels": [], "free": free})
        volume["labels"].append(label)
    low = [v for v in volumes.values() if v["free"] is not None and v["free"] < required]
    unknown = [v for v in volumes.values() if v["free"] is None]
    summary = "; ".join(f"{' / '.join(v['labels'])}: {_gib(v['free'])} livres" if v["free"] is not None
                        else f"{' / '.join(v['labels'])}: não foi possível medir" for v in volumes.values())
    if low:
        where = " e ".join(" / ".join(v["labels"]) for v in low)
        detail = (f"Espaço insuficiente em {where}. {summary}. Uma produção do piloto ocupa ≈ {_gib(PILOT_PRODUCTION_ESTIMATE_BYTES)} "
                  f"e o aplicativo recomenda pelo menos {_gib(required)} livres por volume. Libere espaço (esvazie a Lixeira, "
                  "remova produções e pacotes antigos) ou aponte HORUS_DATA_DIR para outro disco. Nada é apagado automaticamente.")
    elif unknown:
        detail = (f"Não foi possível medir o espaço livre em {' e '.join(' / '.join(v['labels']) for v in unknown)}. {summary}. "
                  "Confira se as pastas existem e são acessíveis.")
    else:
        detail = f"Espaço livre suficiente (mínimo {_gib(required)} por volume). {summary}."
    # Medição impossível avisa no texto, mas não bloqueia a produção por si só.
    return dict(id="disk_space", name="Espaço em disco", available=not low, detail=detail,
                required_for=["draft", "final"])

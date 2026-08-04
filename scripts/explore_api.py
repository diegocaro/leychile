"""Throwaway exploration script: probe BCN's real response shapes for one
small, well-known norm before building the real pipeline on assumptions.

Usage: .venv/bin/python scripts/explore_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from leyeschile.client import BcnClient  # noqa: E402

# Ley 19.846 "Calificacion de la produccion cinematografica" - used as BCN's
# own worked example in accesoLeyesChilenas4.pdf, so idNorma is confirmed
# real. Small law, good smoke-test candidate before touching anything huge
# like the Codigo Civil.
ID_NORMA = 206396

OBTXML_URL = f"https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma={ID_NORMA}"
VINCULACIONES_URL = (
    "https://nuevo.leychile.cl/servicios/Vinculaciones/get_vinculaciones_descarga"
    f"?idNorma={ID_NORMA}&npagina=1&itemsporpagina=1000&formato=csv"
)


def main() -> None:
    client = BcnClient(min_delay_seconds=8.0)

    print(f"== Fetching obtxml for idNorma={ID_NORMA} ==")
    result = client.get(OBTXML_URL)
    print(f"status={result.status_code} from_cache={result.from_cache} bytes={len(result.content)}")
    out = Path("scripts/_sample_obtxml.xml")
    out.write_bytes(result.content)
    print(f"saved to {out}")
    print(result.text()[:1500])

    print()
    print(f"== Fetching vinculaciones for idNorma={ID_NORMA} ==")
    result2 = client.get(VINCULACIONES_URL)
    print(f"status={result2.status_code} from_cache={result2.from_cache} bytes={len(result2.content)}")
    out2 = Path("scripts/_sample_vinculaciones.csv")
    out2.write_bytes(result2.content)
    print(f"saved to {out2}")
    print(result2.text()[:1500])


if __name__ == "__main__":
    main()

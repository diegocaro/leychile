"""Script de exploración desechable: sondea las respuestas reales de BCN sobre
una norma pequeña y conocida, para no construir el pipeline sobre supuestos.

Útil cada vez que se quiera confirmar la forma de un endpoint antes de escribir
código que dependa de ella.

Uso: uv run python scripts/explore_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from leyeschile.client import BcnClient  # noqa: E402

# Ley 19.846, "Calificación de la producción cinematográfica". Es el ejemplo
# que usa la propia BCN en su documento accesoLeyesChilenas4.pdf, así que el
# idNorma está confirmado. Es una ley corta: buena candidata para probar antes
# de tocar algo enorme como el Código Civil.
ID_NORMA = 206396

OBTXML_URL = f"https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma={ID_NORMA}"
VINCULACIONES_URL = (
    "https://nuevo.leychile.cl/servicios/Vinculaciones/get_vinculaciones_descarga"
    f"?idNorma={ID_NORMA}&npagina=1&itemsporpagina=1000&formato=csv"
)


def main() -> None:
    client = BcnClient(min_delay_seconds=8.0)

    print(f"== Descargando obtxml de idNorma={ID_NORMA} ==")
    result = client.get(OBTXML_URL)
    print(f"estado={result.status_code} desde_cache={result.from_cache} bytes={len(result.content)}")
    out = Path("scripts/_sample_obtxml.xml")
    out.write_bytes(result.content)
    print(f"guardado en {out}")
    print(result.text()[:1500])

    print()
    print(f"== Descargando vinculaciones de idNorma={ID_NORMA} ==")
    result2 = client.get(VINCULACIONES_URL)
    print(f"estado={result2.status_code} desde_cache={result2.from_cache} bytes={len(result2.content)}")
    out2 = Path("scripts/_sample_vinculaciones.csv")
    out2.write_bytes(result2.content)
    print(f"guardado en {out2}")
    print(result2.text()[:1500])


if __name__ == "__main__":
    main()

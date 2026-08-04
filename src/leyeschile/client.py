"""Cliente HTTP para los servicios de LeyChile (BCN).

BCN limita las peticiones de forma agresiva: durante el desarrollo bastaron
unas pocas peticiones seguidas para recibir `HTTP 429` con `Retry-After: 300`.
Por eso este cliente es deliberadamente conservador:

- **Todo se cachea en disco para siempre**, indexado por URL (bytes crudos +
  metadatos de la descarga). Reejecutar el pipeline nunca vuelve a pedir una
  URL ya descargada, así que corregir un bug de parseo y reconstruir el
  repositorio completo no cuesta ni una sola petición nueva.
- Se respeta un **retardo mínimo entre peticiones reales** a la red (las
  respuestas servidas desde caché no cuentan).
- Ante 429 o errores 5xx se reintenta con backoff exponencial, respetando la
  cabecera `Retry-After` cuando viene.

Todo el pipeline debe pasar por esta clase: no hacer llamadas directas con
`requests` en otros módulos (ver CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("cache")
DEFAULT_MIN_DELAY_SECONDS = 4.0
DEFAULT_USER_AGENT = "leyes-chile-research/0.1 (+https://github.com; contacto ciudadano)"
MAX_RETRIES = 6


@dataclass(frozen=True)
class FetchResult:
    """Respuesta de BCN, venga de la red o de la caché en disco."""

    url: str
    content: bytes
    status_code: int
    fetched_at: str  # marca de tiempo ISO 8601 UTC de la petición HTTP real
    from_cache: bool

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


class BcnClient:
    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        min_delay_seconds: float = DEFAULT_MIN_DELAY_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_delay_seconds = min_delay_seconds
        self._last_request_ts: float | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        """Rutas (cuerpo, metadatos) de una URL, indexadas por su hash."""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.body", self.cache_dir / f"{digest}.meta.json"

    def _read_cache(self, url: str) -> FetchResult | None:
        body_path, meta_path = self._cache_paths(url)
        if not (body_path.exists() and meta_path.exists()):
            return None
        meta = json.loads(meta_path.read_text())
        return FetchResult(
            url=url,
            content=body_path.read_bytes(),
            status_code=meta["status_code"],
            fetched_at=meta["fetched_at"],
            from_cache=True,
        )

    def _write_cache(self, url: str, content: bytes, status_code: int, fetched_at: str) -> None:
        body_path, meta_path = self._cache_paths(url)
        body_path.write_bytes(content)
        meta_path.write_text(
            json.dumps(
                {"url": url, "status_code": status_code, "fetched_at": fetched_at},
                ensure_ascii=False,
                indent=2,
            )
        )

    def _throttle(self) -> None:
        """Duerme lo necesario para respetar el retardo mínimo entre peticiones."""
        if self._last_request_ts is None:
            return
        elapsed = time.monotonic() - self._last_request_ts
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str, *, force_refresh: bool = False) -> FetchResult:
        """Descarga una URL usando la caché en disco de forma transparente.

        Lanza `requests.HTTPError` si la respuesta final (tras los reintentos)
        no es exitosa, y `RuntimeError` si se agotan los reintentos.
        """
        if not force_refresh:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        backoff = 5.0
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            self._last_request_ts = time.monotonic()
            fetched_at = datetime.now(timezone.utc).isoformat()
            try:
                resp = self._session.get(url, timeout=30)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("request error for %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, exc)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 429:
                # BCN suele responder con Retry-After: 300. Respetarlo es más
                # rápido que insistir con nuestro propio backoff, y evita que
                # nos limiten todavía más.
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                logger.warning(
                    "429 rate limited on %s (attempt %d/%d), waiting %.0fs", url, attempt, MAX_RETRIES, wait
                )
                time.sleep(wait)
                backoff *= 2
                continue

            if resp.status_code >= 500:
                logger.warning(
                    "server error %d for %s (attempt %d/%d), waiting %.0fs",
                    resp.status_code, url, attempt, MAX_RETRIES, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue

            # Se cachea incluso un 4xx: si BCN responde "no existe", volver a
            # preguntar mañana tampoco va a cambiar la respuesta.
            self._write_cache(url, resp.content, resp.status_code, fetched_at)
            result = FetchResult(
                url=url,
                content=resp.content,
                status_code=resp.status_code,
                fetched_at=fetched_at,
                from_cache=False,
            )
            if resp.status_code >= 400:
                raise requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
            return result

        raise RuntimeError(f"exhausted retries fetching {url}") from last_exc

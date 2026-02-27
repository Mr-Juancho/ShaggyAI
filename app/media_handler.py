"""
Cliente asíncrono para la API de Radarr.
Fase 6: Búsqueda de películas con confirmación visual.
Fase 7: Descarga y notificación vía webhook.
Fase 6.5: Selección de calidad (4K, 1080p, etc.) antes de descargar.
"""

import httpx
import re
from typing import Any, Optional

from app.config import RADARR_URL, RADARR_API_KEY, logger


def _format_size(size_bytes: int) -> str:
    """Convierte bytes a formato legible (GB/MB)."""
    if size_bytes <= 0:
        return "? GB"
    gb = size_bytes / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.0f} MB"


def _classify_quality(quality_name: str) -> str:
    """Clasifica un nombre de calidad en una categoría simplificada."""
    q = quality_name.lower()
    if any(tag in q for tag in ("2160", "4k", "uhd")):
        return "4K"
    if any(tag in q for tag in ("1080",)):
        return "1080p"
    if any(tag in q for tag in ("720",)):
        return "720p"
    if any(tag in q for tag in ("480", "sd", "dvd")):
        return "480p"
    return quality_name


def _extract_genres(raw_genres: Any) -> list[str]:
    """Normaliza el campo de géneros de Radarr en lista de texto."""
    if not raw_genres:
        return []

    genres: list[str] = []
    if isinstance(raw_genres, list):
        for entry in raw_genres:
            name = ""
            if isinstance(entry, str):
                name = entry.strip()
            elif isinstance(entry, dict):
                name = str(
                    entry.get("name")
                    or entry.get("label")
                    or entry.get("value")
                    or ""
                ).strip()
            if name and name not in genres:
                genres.append(name)
    elif isinstance(raw_genres, str):
        parts = [part.strip() for part in raw_genres.split(",")]
        genres = [part for part in parts if part]

    return genres[:4]


def _extract_runtime_minutes(item: dict[str, Any]) -> Optional[int]:
    """Extrae runtime en minutos desde posibles variantes de payload."""
    for key in ("runtime", "runtimeMinutes", "duration", "durationMinutes"):
        value = item.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw.isdigit():
                minutes = int(raw)
                if minutes > 0:
                    return minutes

            # Acepta formatos como "2h 34m", "2h", "154m"
            match = re.match(r"^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?$", raw)
            if match:
                hours = int(match.group(1) or 0)
                mins = int(match.group(2) or 0)
                total = (hours * 60) + mins
                if total > 0:
                    return total

    return None


def _format_runtime(minutes: Optional[int]) -> str:
    """Devuelve duración legible para UI/Telegram."""
    if not minutes or minutes <= 0:
        return "No especificada"

    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}min"
    if hours:
        return f"{hours}h"
    return f"{mins}min"


def _short_summary(text: str, max_len: int = 170) -> str:
    """Resume overview en una línea corta para tarjetas/captions."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "Sin resumen disponible."
    if len(cleaned) <= max_len:
        return cleaned
    return f"{cleaned[:max_len].rstrip()}..."


class RadarrClient:
    """Gestiona la comunicación con la API de Radarr."""

    def __init__(
        self,
        base_url: str = RADARR_URL,
        api_key: str = RADARR_API_KEY,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

        if not self.api_key:
            logger.warning("RADARR_API_KEY no configurada. RadarrClient deshabilitado.")

    @property
    def enabled(self) -> bool:
        """Indica si el cliente tiene configuración válida."""
        return bool(self.api_key and self.base_url)

    async def _get_client(self) -> httpx.AsyncClient:
        """Retorna o crea el cliente HTTP reutilizable."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-Api-Key": self.api_key},
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def check_health(self) -> bool:
        """Verifica la conexión con Radarr."""
        if not self.enabled:
            return False
        try:
            client = await self._get_client()
            resp = await client.get("/api/v3/health")
            return resp.status_code == 200
        except Exception as exc:
            logger.error(f"Error al verificar salud de Radarr: {exc}")
            return False

    async def search_movie(self, query: str) -> list[dict[str, Any]]:
        """
        Busca películas en Radarr (vía TMDB lookup).

        Args:
            query: Término de búsqueda (título de la película).

        Returns:
            Lista de resultados con: title, year, tmdbId, overview, poster_url.
        """
        if not self.enabled:
            logger.warning("RadarrClient no habilitado. Saltando búsqueda.")
            return []

        try:
            client = await self._get_client()
            resp = await client.get(
                "/api/v3/movie/lookup",
                params={"term": query},
            )
            resp.raise_for_status()
            raw_results = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(f"Error HTTP en búsqueda Radarr: {exc.response.status_code}")
            return []
        except Exception as exc:
            logger.error(f"Error en búsqueda Radarr: {exc}")
            return []

        movies: list[dict[str, Any]] = []
        for item in raw_results[:5]:
            # Extraer URL de poster con fallback robusto.
            poster_url = ""
            poster_candidates = [
                item.get("remotePoster", ""),
                item.get("remotePosterUrl", ""),
                item.get("posterUrl", ""),
                item.get("poster", ""),
            ]
            for candidate in poster_candidates:
                if isinstance(candidate, str) and candidate.strip():
                    poster_url = candidate.strip()
                    break

            if poster_url.startswith("/"):
                poster_url = f"{self.base_url}{poster_url}"

            if not poster_url:
                # Fallback: buscar en images[]
                for img in item.get("images", []):
                    if img.get("coverType") != "poster":
                        continue
                    candidate = img.get("remoteUrl") or img.get("url") or ""
                    if not candidate:
                        continue
                    if isinstance(candidate, str) and candidate.startswith("/"):
                        candidate = f"{self.base_url}{candidate}"
                    poster_url = candidate
                    if poster_url:
                        break

            # Fallback final: construir URL pública de TMDB si existe posterPath.
            if not poster_url:
                poster_path = item.get("posterPath") or item.get("remotePosterPath")
                if isinstance(poster_path, str) and poster_path.startswith("/"):
                    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"

            genres = _extract_genres(item.get("genres"))
            runtime_minutes = _extract_runtime_minutes(item)
            overview = (item.get("overview") or "").strip()
            summary = _short_summary(overview)

            movies.append({
                "title": item.get("title", "Desconocido"),
                "year": item.get("year", 0),
                "tmdbId": item.get("tmdbId", 0),
                "overview": overview[:400],
                "summary": summary,
                "poster_url": poster_url,
                "genres": genres,
                "genre_text": ", ".join(genres) if genres else "No especificado",
                "runtime_minutes": runtime_minutes,
                "runtime_text": _format_runtime(runtime_minutes),
                "hasFile": item.get("hasFile", False),
                "isExisting": item.get("id", 0) > 0,
            })

        logger.info(f"Búsqueda Radarr para '{query}': {len(movies)} resultados.")
        return movies

    async def get_root_folders(self) -> list[dict[str, Any]]:
        """Obtiene las carpetas raíz configuradas en Radarr."""
        if not self.enabled:
            return []
        try:
            client = await self._get_client()
            resp = await client.get("/api/v3/rootfolder")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error(f"Error al obtener root folders de Radarr: {exc}")
            return []

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        """Obtiene los perfiles de calidad configurados en Radarr."""
        if not self.enabled:
            return []
        try:
            client = await self._get_client()
            resp = await client.get("/api/v3/qualityprofile")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error(f"Error al obtener quality profiles de Radarr: {exc}")
            return []

    async def add_movie(
        self,
        tmdb_id: int,
        title: str,
        year: int = 0,
        root_folder_path: Optional[str] = None,
        quality_profile_id: Optional[int] = None,
        search_for_movie: bool = False,
    ) -> dict[str, Any]:
        """
        Añade una película a Radarr. Verifica primero si ya existe para
        evitar duplicados.

        Args:
            tmdb_id: ID de TMDB de la película.
            title: Título de la película.
            year: Año de estreno.
            root_folder_path: Ruta de la carpeta raíz. Si None, usa la primera disponible.
            quality_profile_id: ID del perfil de calidad. Si None, usa el primero disponible.
            search_for_movie: Si True, Radarr busca releases automáticamente al agregar.

        Returns:
            Diccionario con datos de la película añadida o error.
        """
        if not self.enabled:
            return {"error": "RadarrClient no habilitado."}

        # ── Verificar si la película ya existe en Radarr ──
        existing = await self.get_movie_by_tmdb(tmdb_id)
        if existing:
            radarr_id = existing.get("id")
            logger.info(
                f"Película ya existe en Radarr: {title} "
                f"(tmdbId={tmdb_id}, radarrId={radarr_id})"
            )
            return {
                "success": True,
                "already_exists": True,
                "radarr_id": radarr_id,
                "title": existing.get("title", title),
                "year": existing.get("year", year),
                "tmdbId": tmdb_id,
                "message": "La película ya está en tu biblioteca de Radarr.",
            }

        try:
            # Auto-detectar root_folder si no se proporcionó
            if not root_folder_path:
                folders = await self.get_root_folders()
                if not folders:
                    return {"error": "No hay carpetas raíz configuradas en Radarr."}
                root_folder_path = folders[0].get("path", "/movies")
                logger.info(f"Root folder auto-detectado: {root_folder_path}")

            # Auto-detectar quality profile si no se proporcionó
            if not quality_profile_id:
                profiles = await self.get_quality_profiles()
                if not profiles:
                    return {"error": "No hay perfiles de calidad configurados en Radarr."}
                quality_profile_id = profiles[0].get("id", 1)
                logger.info(f"Quality profile auto-detectado: {quality_profile_id}")

            # Agregar como NO monitoreada para evitar que Radarr
            # auto-descargue un release mientras el usuario elige manualmente.
            payload = {
                "tmdbId": tmdb_id,
                "title": title,
                "year": year,
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder_path,
                "monitored": not search_for_movie,  # False en flujo manual
                "minimumAvailability": "released",
                "addOptions": {
                    "searchForMovie": search_for_movie,
                },
            }

            client = await self._get_client()
            resp = await client.post("/api/v3/movie", json=payload)
            resp.raise_for_status()
            result = resp.json()

            logger.info(
                f"Película añadida a Radarr: {title} (tmdbId={tmdb_id}, "
                f"radarrId={result.get('id', '?')})"
            )
            return {
                "success": True,
                "radarr_id": result.get("id"),
                "title": result.get("title", title),
                "year": result.get("year", year),
                "tmdbId": tmdb_id,
                "monitored": result.get("monitored", True),
            }

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = ""
            try:
                body = exc.response.json()
                if isinstance(body, list) and body:
                    detail = body[0].get("errorMessage", "")
                elif isinstance(body, dict):
                    detail = body.get("message", "")
            except Exception:
                detail = exc.response.text[:200]

            # 400 con "already been added" = película ya existe (doble seguridad)
            if status == 400 and "already" in detail.lower():
                logger.info(f"Película ya existe en Radarr (error 400): {title}")
                existing = await self.get_movie_by_tmdb(tmdb_id)
                return {
                    "success": True,
                    "already_exists": True,
                    "radarr_id": existing.get("id") if existing else None,
                    "title": title,
                    "tmdbId": tmdb_id,
                    "message": "La película ya está en tu biblioteca de Radarr.",
                }

            logger.error(f"Error HTTP al añadir película: {status} - {detail}")
            return {"error": f"Error de Radarr ({status}): {detail or 'Error desconocido'}"}

        except Exception as exc:
            logger.error(f"Error al añadir película a Radarr: {exc}")
            return {"error": f"Error de conexión con Radarr: {exc}"}

    # ------------------------------------------------------------------
    # Fase 6.5: Búsqueda y selección de releases por calidad
    # ------------------------------------------------------------------

    async def get_movie_by_tmdb(self, tmdb_id: int) -> Optional[dict[str, Any]]:
        """
        Busca una película existente en Radarr por su tmdbId.

        Returns:
            Diccionario con datos de la película o None si no existe.
        """
        if not self.enabled:
            return None
        try:
            client = await self._get_client()
            resp = await client.get("/api/v3/movie", params={"tmdbId": tmdb_id})
            resp.raise_for_status()
            movies = resp.json()
            if movies and isinstance(movies, list):
                return movies[0]
            return None
        except Exception as exc:
            logger.error(f"Error buscando película tmdbId={tmdb_id} en Radarr: {exc}")
            return None

    async def update_movie(self, movie_data: dict[str, Any]) -> dict[str, Any]:
        """
        Actualiza una película en Radarr (PUT completo).
        Se usa principalmente para activar/desactivar el monitoreo.

        Args:
            movie_data: Diccionario completo de la película (obtenido de get_movie_by_tmdb).
        """
        if not self.enabled:
            return {"error": "RadarrClient no habilitado."}
        movie_id = movie_data.get("id")
        if not movie_id:
            return {"error": "Falta el ID de la película."}
        try:
            client = await self._get_client()
            resp = await client.put(f"/api/v3/movie/{movie_id}", json=movie_data)
            resp.raise_for_status()
            result = resp.json()
            logger.info(
                f"Película actualizada en Radarr: {result.get('title', '?')} "
                f"(radarrId={movie_id}, monitored={result.get('monitored')})"
            )
            return {"success": True, "radarr_id": movie_id}
        except Exception as exc:
            logger.error(f"Error al actualizar película radarrId={movie_id}: {exc}")
            return {"error": str(exc)}

    async def set_monitored(self, tmdb_id: int, monitored: bool = True) -> dict[str, Any]:
        """
        Activa o desactiva el monitoreo de una película por tmdbId.
        """
        existing = await self.get_movie_by_tmdb(tmdb_id)
        if not existing:
            return {"error": f"Película tmdbId={tmdb_id} no encontrada en Radarr."}
        existing["monitored"] = monitored
        return await self.update_movie(existing)

    async def search_releases(self, movie_id: int) -> list[dict[str, Any]]:
        """
        Busca releases disponibles (torrents/nzb) para una película en Radarr.
        Esto desencadena la búsqueda en Prowlarr y devuelve los resultados.

        Args:
            movie_id: ID interno de Radarr de la película.

        Returns:
            Lista de releases con: title, quality, size, seeders, indexer, guid, etc.
        """
        if not self.enabled:
            return []

        try:
            client = await self._get_client()
            resp = await client.get(
                "/api/v3/release",
                params={"movieId": movie_id},
                timeout=60.0,  # La búsqueda puede tardar
            )
            resp.raise_for_status()
            raw = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(f"Error HTTP buscando releases: {exc.response.status_code}")
            return []
        except Exception as exc:
            logger.error(f"Error buscando releases para movieId={movie_id}: {exc}")
            return []

        releases: list[dict[str, Any]] = []
        for item in raw:
            quality_info = item.get("quality", {}).get("quality", {})
            quality_name = quality_info.get("name", "Desconocida")
            resolution = quality_info.get("resolution", 0)

            # Extraer info de idiomas
            languages = [
                lang.get("name", "")
                for lang in item.get("languages", [])
                if lang.get("name")
            ]

            # Detectar características especiales
            custom_formats = [
                cf.get("name", "")
                for cf in item.get("customFormats", [])
                if cf.get("name")
            ]

            releases.append({
                "title": item.get("title", ""),
                "quality": quality_name,
                "quality_category": _classify_quality(quality_name),
                "resolution": resolution,
                "size": item.get("size", 0),
                "size_formatted": _format_size(item.get("size", 0)),
                "seeders": item.get("seeders", 0),
                "leechers": item.get("leechers", 0),
                "indexer": item.get("indexer", "Desconocido"),
                "guid": item.get("guid", ""),
                "indexerId": item.get("indexerId", 0),
                "languages": languages,
                "custom_formats": custom_formats,
                "rejected": bool(item.get("rejected", False)),
                "rejections": item.get("rejections", []),
                "protocol": item.get("protocol", "unknown"),
            })

        logger.info(
            f"Releases encontrados para movieId={movie_id}: "
            f"{len(releases)} total, {len([r for r in releases if not r['rejected']])} aprobados"
        )
        return releases

    def get_grouped_releases(
        self, releases: list[dict[str, Any]], max_per_group: int = 1
    ) -> list[dict[str, Any]]:
        """
        Agrupa releases por categoría de calidad y devuelve el mejor de cada grupo.
        'Mejor' = más seeders entre los no rechazados.

        Args:
            releases: Lista de releases de search_releases().
            max_per_group: Máximo de opciones por grupo de calidad.

        Returns:
            Lista ordenada (4K > 1080p > 720p > 480p) con el mejor release por categoría.
        """
        # Filtrar rechazados y agrupar
        groups: dict[str, list[dict[str, Any]]] = {}
        for rel in releases:
            if rel.get("rejected"):
                continue
            cat = rel["quality_category"]
            groups.setdefault(cat, []).append(rel)

        # Orden de prioridad
        quality_order = ["4K", "1080p", "720p", "480p"]
        result: list[dict[str, Any]] = []

        for cat in quality_order:
            candidates = groups.pop(cat, [])
            if not candidates:
                continue
            # Ordenar por seeders (desc) y luego por tamaño (desc para mejor calidad)
            candidates.sort(key=lambda r: (r.get("seeders", 0), r.get("size", 0)), reverse=True)
            for pick in candidates[:max_per_group]:
                result.append(pick)

        # Añadir categorías restantes no estándar
        for cat in sorted(groups.keys()):
            candidates = groups[cat]
            candidates.sort(key=lambda r: (r.get("seeders", 0), r.get("size", 0)), reverse=True)
            for pick in candidates[:max_per_group]:
                result.append(pick)

        return result

    async def get_all_movies(self) -> list[dict[str, Any]]:
        """Obtiene todas las películas registradas en Radarr."""
        if not self.enabled:
            return []
        try:
            client = await self._get_client()
            resp = await client.get("/api/v3/movie")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error(f"Error al obtener todas las películas de Radarr: {exc}")
            return []

    async def delete_movie(
        self, radarr_id: int, delete_files: bool = True
    ) -> dict[str, Any]:
        """
        Elimina una película de Radarr.

        Args:
            radarr_id: ID interno de Radarr.
            delete_files: Si True, elimina también los archivos descargados.
        """
        if not self.enabled:
            return {"error": "RadarrClient no habilitado."}
        try:
            client = await self._get_client()
            resp = await client.delete(
                f"/api/v3/movie/{radarr_id}",
                params={"deleteFiles": str(delete_files).lower()},
            )
            resp.raise_for_status()
            logger.info(f"Película eliminada de Radarr: radarrId={radarr_id}")
            return {"success": True, "radarr_id": radarr_id}
        except Exception as exc:
            logger.error(f"Error al eliminar película radarrId={radarr_id}: {exc}")
            return {"error": str(exc)}

    async def remove_duplicates(self) -> dict[str, Any]:
        """
        Busca y elimina películas duplicadas en Radarr.
        Mantiene la primera entrada (menor radarr_id) de cada tmdbId y
        elimina las copias adicionales.

        Returns:
            Diccionario con resumen: duplicados encontrados y eliminados.
        """
        movies = await self.get_all_movies()
        if not movies:
            return {"duplicates_found": 0, "deleted": []}

        # Agrupar por tmdbId
        by_tmdb: dict[int, list[dict[str, Any]]] = {}
        for m in movies:
            tmdb_id = m.get("tmdbId", 0)
            if tmdb_id <= 0:
                continue
            by_tmdb.setdefault(tmdb_id, []).append(m)

        deleted: list[dict[str, Any]] = []
        for tmdb_id, entries in by_tmdb.items():
            if len(entries) <= 1:
                continue

            # Ordenar por id (mantener el menor = el original)
            entries.sort(key=lambda x: x.get("id", 0))
            keep = entries[0]
            for dup in entries[1:]:
                dup_id = dup.get("id")
                dup_title = dup.get("title", "?")
                logger.info(
                    f"Eliminando duplicado: {dup_title} "
                    f"(radarrId={dup_id}, tmdbId={tmdb_id}) — "
                    f"conservando radarrId={keep.get('id')}"
                )
                result = await self.delete_movie(dup_id, delete_files=True)
                deleted.append({
                    "title": dup_title,
                    "radarr_id": dup_id,
                    "tmdb_id": tmdb_id,
                    "result": result,
                })

        logger.info(f"Deduplicación completada: {len(deleted)} duplicados eliminados.")
        return {"duplicates_found": len(deleted), "deleted": deleted}

    async def grab_release(self, guid: str, indexer_id: int) -> dict[str, Any]:
        """
        Descarga (graba) un release específico en Radarr/Transmission.

        Args:
            guid: GUID único del release.
            indexer_id: ID del indexer que tiene el release.

        Returns:
            Diccionario con resultado de la operación.
        """
        if not self.enabled:
            return {"error": "RadarrClient no habilitado."}

        try:
            client = await self._get_client()
            payload = {
                "guid": guid,
                "indexerId": indexer_id,
            }
            resp = await client.post("/api/v3/release", json=payload)
            resp.raise_for_status()

            logger.info(f"Release grabado exitosamente: guid={guid[:50]}...")
            return {"success": True, "guid": guid}

        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                if isinstance(body, dict):
                    detail = body.get("message", "")
                elif isinstance(body, list) and body:
                    detail = body[0].get("errorMessage", "")
            except Exception:
                detail = exc.response.text[:200]
            logger.error(f"Error HTTP al grabar release: {exc.response.status_code} - {detail}")
            return {"error": f"Error de Radarr ({exc.response.status_code}): {detail or 'Error desconocido'}"}

        except Exception as exc:
            logger.error(f"Error al grabar release: {exc}")
            return {"error": f"Error de conexión: {exc}"}

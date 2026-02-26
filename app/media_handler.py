"""
Cliente asíncrono para la API de Radarr.
Fase 6: Búsqueda de películas con confirmación visual.
Fase 7: Descarga y notificación vía webhook.
"""

import httpx
from typing import Any, Optional

from app.config import RADARR_URL, RADARR_API_KEY, logger


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
            # Extraer la URL del póster remoto
            poster_url = ""
            remote_poster = item.get("remotePoster", "")
            if remote_poster:
                poster_url = remote_poster
            else:
                # Fallback: buscar en images[]
                for img in item.get("images", []):
                    if img.get("coverType") == "poster" and img.get("remoteUrl"):
                        poster_url = img["remoteUrl"]
                        break

            movies.append({
                "title": item.get("title", "Desconocido"),
                "year": item.get("year", 0),
                "tmdbId": item.get("tmdbId", 0),
                "overview": (item.get("overview") or "")[:300],
                "poster_url": poster_url,
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
    ) -> dict[str, Any]:
        """
        Añade una película a Radarr y activa la búsqueda automática
        (Prowlarr + Transmission).

        Args:
            tmdb_id: ID de TMDB de la película.
            title: Título de la película.
            year: Año de estreno.
            root_folder_path: Ruta de la carpeta raíz. Si None, usa la primera disponible.
            quality_profile_id: ID del perfil de calidad. Si None, usa el primero disponible.

        Returns:
            Diccionario con datos de la película añadida o error.
        """
        if not self.enabled:
            return {"error": "RadarrClient no habilitado."}

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

            payload = {
                "tmdbId": tmdb_id,
                "title": title,
                "year": year,
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder_path,
                "monitored": True,
                "minimumAvailability": "released",
                "addOptions": {
                    "searchForMovie": True,  # Activa búsqueda inmediata en Prowlarr
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
                # Radarr devuelve errores en body[0].errorMessage o body.message
                if isinstance(body, list) and body:
                    detail = body[0].get("errorMessage", "")
                elif isinstance(body, dict):
                    detail = body.get("message", "")
            except Exception:
                detail = exc.response.text[:200]

            # 400 con "already been added" = película ya existe
            if status == 400 and "already" in detail.lower():
                logger.info(f"Película ya existe en Radarr: {title}")
                return {
                    "success": True,
                    "already_exists": True,
                    "title": title,
                    "tmdbId": tmdb_id,
                    "message": "La película ya está en tu biblioteca de Radarr.",
                }

            logger.error(f"Error HTTP al añadir película: {status} - {detail}")
            return {"error": f"Error de Radarr ({status}): {detail or 'Error desconocido'}"}

        except Exception as exc:
            logger.error(f"Error al añadir película a Radarr: {exc}")
            return {"error": f"Error de conexión con Radarr: {exc}"}

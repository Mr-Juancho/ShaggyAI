"""
Motor de busqueda web usando DuckDuckGo.
Sin API key, resultados de busqueda general y noticias.
"""

import asyncio
from typing import Optional

import httpx
from duckduckgo_search import DDGS

from app.config import BRAVE_API_KEY, logger


class WebSearchEngine:
    """Busqueda web usando DuckDuckGo (sin API key)."""

    def __init__(self, brave_api_key: str = BRAVE_API_KEY):
        self.brave_api_key = brave_api_key.strip()
        self.brave_web_url = "https://api.search.brave.com/res/v1/web/search"
        self.brave_news_url = "https://api.search.brave.com/res/v1/news/search"
        self._brave_client: Optional[httpx.Client] = None
        if self.brave_api_key:
            logger.info("WebSearchEngine inicializado (Brave principal + DuckDuckGo fallback)")
        else:
            logger.info("WebSearchEngine inicializado (DuckDuckGo)")

    def _get_brave_client(self) -> httpx.Client:
        """Retorna un cliente HTTP reutilizable para Brave."""
        if self._brave_client is None or self._brave_client.is_closed:
            self._brave_client = httpx.Client(
                timeout=20,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.brave_api_key,
                },
            )
        return self._brave_client

    async def search(
        self,
        query: str,
        max_results: int = 5
    ) -> list[dict]:
        """
        Busqueda web general.
        Retorna: [{title, snippet, url}]
        """
        if self.brave_api_key:
            try:
                brave_results = await asyncio.to_thread(
                    self._sync_search_brave_web, query, max_results
                )
                if brave_results:
                    logger.info(
                        f"Busqueda web '{query}': {len(brave_results)} resultados (Brave)"
                    )
                    return brave_results
                logger.warning(
                    "Brave no devolvio resultados. Intentando DuckDuckGo como fallback."
                )
            except Exception as e:
                logger.error(f"Error en busqueda web con Brave '{query}': {e}")
                logger.warning("Intentando DuckDuckGo como fallback.")

        try:
            # duckduckgo-search es sincrono, lo ejecutamos en un thread
            results = await asyncio.to_thread(
                self._sync_search, query, max_results
            )
            logger.info(f"Busqueda web '{query}': {len(results)} resultados")
            return results

        except Exception as e:
            logger.error(f"Error en busqueda web '{query}': {e}")
            return []

    def _sync_search(self, query: str, max_results: int) -> list[dict]:
        """Busqueda sincrona (se ejecuta en thread)."""
        results = []
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("href", "")
                    })
        except Exception as e:
            logger.error(f"Error en DuckDuckGo search: {e}")
        if not results:
            logger.warning(
                "DuckDuckGo no devolvio resultados. "
                "Si persiste, configura BRAVE_API_KEY como fallback."
            )
        return results

    async def search_news(
        self,
        query: str,
        max_results: int = 5
    ) -> list[dict]:
        """
        Busqueda de noticias.
        Retorna: [{title, snippet, url}]
        """
        if self.brave_api_key:
            try:
                brave_results = await asyncio.to_thread(
                    self._sync_search_brave_news, query, max_results
                )
                if brave_results:
                    logger.info(
                        f"Busqueda noticias '{query}': {len(brave_results)} resultados (Brave)"
                    )
                    return brave_results
                logger.warning(
                    "Brave news no devolvio resultados. Intentando DuckDuckGo como fallback."
                )
            except Exception as e:
                logger.error(f"Error en busqueda de noticias con Brave '{query}': {e}")
                logger.warning("Intentando DuckDuckGo news como fallback.")

        try:
            results = await asyncio.to_thread(
                self._sync_search_news, query, max_results
            )
            logger.info(f"Busqueda noticias '{query}': {len(results)} resultados")
            return results

        except Exception as e:
            logger.error(f"Error en busqueda de noticias '{query}': {e}")
            return []

    def _sync_search_news(self, query: str, max_results: int) -> list[dict]:
        """Busqueda de noticias sincrona (se ejecuta en thread)."""
        results = []
        try:
            with DDGS() as ddgs:
                for r in ddgs.news(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("url", "")
                    })
        except Exception as e:
            logger.error(f"Error en DuckDuckGo news: {e}")
        if not results:
            logger.warning(
                "DuckDuckGo news no devolvio resultados. "
                "Si persiste, configura BRAVE_API_KEY como fallback."
            )
        return results

    def _sync_search_brave_web(self, query: str, max_results: int) -> list[dict]:
        """Fallback opcional usando Brave Search API."""
        if not self.brave_api_key:
            return []
        results: list[dict] = []
        try:
            client = self._get_brave_client()
            params = {
                "q": query,
                "count": max_results,
                "search_lang": "es",
            }
            response = client.get(self.brave_web_url, params=params)
            response.raise_for_status()
            data = response.json()
            web = data.get("web", {}).get("results", [])
            for item in web:
                results.append(
                    {
                        "title": item.get("title", ""),
                        "snippet": item.get("description", ""),
                        "url": item.get("url", ""),
                    }
                )
        except Exception as e:
            logger.error(f"Error en Brave web search: {e}")
        return results

    def _sync_search_brave_news(self, query: str, max_results: int) -> list[dict]:
        """Fallback opcional de noticias usando Brave Search API."""
        if not self.brave_api_key:
            return []
        results: list[dict] = []
        try:
            client = self._get_brave_client()
            params = {
                "q": query,
                "count": max_results,
                "search_lang": "es",
            }
            response = client.get(self.brave_news_url, params=params)
            response.raise_for_status()
            data = response.json()
            news = data.get("results", [])
            for item in news:
                results.append(
                    {
                        "title": item.get("title", ""),
                        "snippet": item.get("description", ""),
                        "url": item.get("url", ""),
                    }
                )
        except Exception as e:
            logger.error(f"Error en Brave news search: {e}")
        return results

    def format_results(self, results: list[dict]) -> str:
        """Formatea resultados para incluir en respuesta del agente."""
        if not results:
            return "No se encontraron resultados."

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"{i}. **{r['title']}**\n"
                f"   {r['snippet']}\n"
                f"   Fuente: {r['url']}"
            )
        return "\n\n".join(formatted)

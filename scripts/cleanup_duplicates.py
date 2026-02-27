#!/usr/bin/env python3
"""
Script para limpiar películas duplicadas en Radarr.
Ejecutar desde la raíz del proyecto:
    python scripts/cleanup_duplicates.py

También borra los archivos duplicados del disco.
"""

import os
import sys
import httpx

# Cargar .env si existe
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

RADARR_URL = os.getenv("RADARR_URL", "http://localhost:7878").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")

if not RADARR_API_KEY:
    print("ERROR: RADARR_API_KEY no configurada. Revisa tu .env")
    sys.exit(1)


def main():
    headers = {"X-Api-Key": RADARR_API_KEY}

    print(f"Conectando a Radarr en {RADARR_URL}...")

    # 1) Obtener todas las películas
    resp = httpx.get(f"{RADARR_URL}/api/v3/movie", headers=headers, timeout=30)
    resp.raise_for_status()
    movies = resp.json()
    print(f"Total películas en Radarr: {len(movies)}")

    # 2) Agrupar por tmdbId
    by_tmdb: dict[int, list[dict]] = {}
    for m in movies:
        tmdb_id = m.get("tmdbId", 0)
        if tmdb_id > 0:
            by_tmdb.setdefault(tmdb_id, []).append(m)

    # 3) Encontrar duplicados
    duplicates = {k: v for k, v in by_tmdb.items() if len(v) > 1}

    if not duplicates:
        print("\nNo se encontraron duplicados. Todo limpio!")
        return

    print(f"\nDuplicados encontrados: {len(duplicates)} películas con copias extra\n")

    total_deleted = 0
    for tmdb_id, entries in duplicates.items():
        # Ordenar por ID — conservar el menor (original)
        entries.sort(key=lambda x: x.get("id", 0))
        keep = entries[0]
        print(f"  [{keep['title']} ({keep.get('year', '?')})] tmdbId={tmdb_id}")
        print(f"    CONSERVAR: radarrId={keep['id']} path={keep.get('path', '?')}")

        for dup in entries[1:]:
            dup_id = dup.get("id")
            dup_path = dup.get("path", "?")
            print(f"    ELIMINAR:  radarrId={dup_id} path={dup_path}")

            try:
                del_resp = httpx.delete(
                    f"{RADARR_URL}/api/v3/movie/{dup_id}",
                    headers=headers,
                    params={"deleteFiles": "true"},
                    timeout=30,
                )
                del_resp.raise_for_status()
                print(f"      -> Eliminado OK (archivos incluidos)")
                total_deleted += 1
            except Exception as exc:
                print(f"      -> ERROR al eliminar: {exc}")

    print(f"\nLimpieza completada: {total_deleted} duplicado(s) eliminado(s).")


if __name__ == "__main__":
    main()

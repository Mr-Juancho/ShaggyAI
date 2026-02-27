# RUFÜS - Agente de IA Local

Asistente personal en local con:
- FastAPI (API + interfaz web)
- Ollama (`gpt-oss:20b`)
- Memoria persistente con ChromaDB
- Bot de Telegram
- Recordatorios y busqueda web

## Plan Maestro (mejoras integradas)

Se incorporaron mejoras del plan por fases:

- Fase 1:
  - `PRODUCT_SCOPE.md` (contrato de producto con capacidades permitidas).
  - `app/CAPABILITIES.yaml` (registry con schema de entrada/salida y fallback por capability).
- Fase 2:
  - `app/semantic_router.py` (clasificador semantico con salida JSON estricta).
  - `app/time_policy.py` (inyeccion obligatoria de fecha/hora para intenciones temporales).
- Fase 3:
  - `app/json_guard.py` (pipeline Generacion -> Validacion schema -> Reparacion, max 2 reintentos).
  - Escalera de fallback web integrada en `app/main.py` (primaria -> alternativa -> general -> aclaracion).
- Fase 4:
  - `app/response_verifier.py` (verificador final de coherencia temporal y uso de fuentes).
  - `app/evals.py` (metricas y regla de gateo por SLO).

## Pruebas por fase

Se incluyen tests unitarios en `tests/`:

- `tests/test_phase1_scope_registry.py`
- `tests/test_phase2_semantic_router.py`
- `tests/test_phase2_memory_semantic.py`
- `tests/test_phase3_json_pipeline.py`
- `tests/test_phase4_verifier_evals.py`

Ejecutar:

```bash
cd mi-agente-ia
PYTHONPATH=. PYTHONPYCACHEPREFIX=/tmp/pythoncache python3 -m unittest discover -s tests -v
```

## 1) Prerequisitos

- Python 3.10 o superior
- Ollama instalado y corriendo
- Bot de Telegram creado con BotFather (token)

## 2) Instalacion

```bash
git clone <tu-repo>
cd mi-agente-ia
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Configura `.env`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_USER_ID`
- (opcional) `BRAVE_API_KEY` para fallback de busqueda web si DuckDuckGo no devuelve resultados
- (opcional) `OLLAMA_MODEL`, `HOST`, `PORT`, etc.

## 3) Descargar modelos de Ollama

```bash
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

Si no esta corriendo Ollama:

```bash
ollama serve
```

## 4) Iniciar el agente

Opcion A:

```bash
python -m app.main
```

Opcion B:

```bash
bash scripts/start.sh
```

Por defecto, RUFÜS inicia solo (sin stack multimedia).
Si quieres autoiniciar Radarr/Prowlarr/Transmission/Jellyfin junto con RUFÜS:

```bash
export RUFUS_START_MEDIA_STACK=true
```

Modo recomendado (manual):
- Escribe en el chat de RUFÜS: `inicia protocolo peliculas`
- Se levantan en segundo plano (headless) sin abrir sus webs.
- Puedes consultar estado con: `estado del protocolo peliculas`
- Para detenerlo y matar procesos: `apaga protocolo peliculas`

Luego abre:
- Web desktop: `http://localhost:8000`
- Salud API: `http://localhost:8000/health`

## 5) Personalizar personalidad

Edita:

`app/personality.yaml`

Puedes cambiar:
- `name`
- `tone`
- `language`
- `custom_instructions`

## 6) Crear acceso directo

```bash
python scripts/create_shortcut.py
```

Genera un acceso directo del launcher:
- Windows: en el Escritorio (`.bat` y `.url`)
- macOS: en `~/Library/Application Support/RUFUS/launcher` (`.app` y `.command`, sin ensuciar Escritorio)
- Linux: en el Escritorio (`.desktop`)

## 7) Inicio automatico con el sistema

Opciones recomendadas:
- Linux: `systemd` (servicio de usuario)
- macOS: `launchd` (`~/Library/LaunchAgents`)
- Windows: Task Scheduler

## Endpoints principales

- `POST /chat`
- `GET /health`
- `POST /remember`
- `GET /reminders`
- `POST /reminders`
- `DELETE /reminders/{id}`

## Comandos de Telegram

- `/start`
- `/remember [texto]`
- `/profile`
- `/clear`
- `/reminders`
- `/remind [texto con fecha]`
- `/search [consulta]`

## Publicar En GitHub (Seguro)

Antes de subir el proyecto:

1. Verifica que **NO** se suban archivos privados:
   - `.env`
   - `data/` (memoria, recordatorios, logs)
   - `venv/`
   - `Library/`
2. Este repo ya incluye `.gitignore` para bloquear esos archivos.
3. Si alguna clave/token se uso en local o se compartio en capturas, **rotala**:
   - Token de Telegram (BotFather)
   - API key de Brave

Chequeo rapido recomendado:

```bash
git init
git add .
git status --short
```

Si aparece algun archivo sensible en `git status`, quitale seguimiento antes de hacer commit.

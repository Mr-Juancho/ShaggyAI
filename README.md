# Shaggy - Agente de IA Local

Asistente personal en local con:
- FastAPI (API + interfaz web)
- Ollama (`gpt-oss:20b`)
- Memoria persistente con ChromaDB
- Bot de Telegram
- Recordatorios y busqueda web

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

Genera un acceso directo en el escritorio:
- Windows: `.bat` y `.url`
- macOS: `.command`
- Linux: `.desktop`

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

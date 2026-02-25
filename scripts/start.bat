@echo off
REM Script de inicio para el Agente de IA Local (Windows)
REM Uso: scripts\start.bat

cd /d "%~dp0\.."
echo === Agente de IA Local ===
echo Directorio: %CD%

REM Activar venv si existe
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo Entorno virtual activado
)
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo Entorno virtual activado
)

REM Iniciar servidor
echo Iniciando servidor en http://localhost:8000 ...
python -m app.main
pause

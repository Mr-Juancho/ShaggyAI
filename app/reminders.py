"""
Sistema de recordatorios proactivos.
Almacena recordatorios en JSON, parsea fechas en lenguaje natural,
y notifica via Telegram cuando llega la hora.
"""

import json
import re
import threading
import uuid
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

import dateparser
from dateparser.search import search_dates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import DATA_DIR, logger


REMINDERS_FILE = DATA_DIR / "reminders.json"


class ReminderManager:
    """
    Gestiona recordatorios del usuario.
    Estructura: {id, text, datetime, recurring, interval, status}
    """
    _TASK_STOPWORDS = {
        "a",
        "al",
        "de",
        "del",
        "el",
        "en",
        "la",
        "las",
        "los",
        "para",
        "por",
        "un",
        "una",
        "recordatorio",
        "recordarme",
        "recuerdame",
        "recuérdame",
        "crear",
        "crea",
        "activar",
        "activa",
        "programar",
        "programa",
        "poner",
        "pon",
        "hoy",
        "manana",
        "mañana",
        "tarde",
        "noche",
        "am",
        "pm",
    }

    def __init__(self):
        self.reminders: list[dict] = []
        self._lock = threading.Lock()
        self.scheduler = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1}
        )
        self.telegram_send_fn = None  # Se asigna despues de inicializar el bot
        self._load_reminders()

    def _load_reminders(self) -> None:
        """Carga recordatorios desde el archivo JSON."""
        try:
            if REMINDERS_FILE.exists():
                with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                    self.reminders = json.load(f)
                logger.info(f"Cargados {len(self.reminders)} recordatorios")
            else:
                self.reminders = []
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Error al cargar recordatorios: {e}")
            self.reminders = []

    def _save_reminders(self) -> None:
        """Guarda recordatorios en el archivo JSON (thread-safe)."""
        try:
            REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.reminders, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error al guardar recordatorios: {e}")

    def _parse_datetime(self, text: str) -> Optional[datetime]:
        """Parsea una fecha/hora en lenguaje natural (espanol)."""
        try:
            parsed = dateparser.parse(
                text,
                languages=["es", "en"],
                settings={
                    "PREFER_DATES_FROM": "future",
                    "RELATIVE_BASE": datetime.now(),
                    "RETURN_AS_TIMEZONE_AWARE": False,
                },
            )
            return parsed
        except Exception as e:
            logger.error(f"Error al parsear fecha '{text}': {e}")
            return None

    def format_datetime_for_user(self, dt_str: str) -> str:
        """Formatea fecha ISO a un formato legible para mostrar al usuario."""
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return dt_str

    def _normalize_reminder_task(self, text: str) -> str:
        """
        Limpia residuos de fecha/hora para quedarse con la accion real del recordatorio.
        """
        task = " ".join((text or "").split()).strip(" .!?")
        if not task:
            return ""

        task = re.sub(
            r"^(?:a\s+)?las?\s+\d{1,2}(?::\d{2})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|am|pm)?\b",
            "",
            task,
            flags=re.IGNORECASE,
        ).strip()

        task = re.sub(
            r"^(?:hoy|mañana|manana|esta\s+(?:tarde|noche)|pasado\s+mañana)\b",
            "",
            task,
            flags=re.IGNORECASE,
        ).strip()

        while True:
            new_task = re.sub(
                r"^(?:para|a|al|de|del|en|el|la|las|los)\b[\s,:;-]*",
                "",
                task,
                flags=re.IGNORECASE,
            ).strip()
            if new_task == task:
                break
            task = new_task

        task = re.sub(r"\s*[,;:\-]+\s*", " ", task)
        task = " ".join(task.split()).strip(" .!?")
        if not task:
            return ""

        tokens = re.findall(r"[a-zA-Z0-9áéíóúñü]+", task.lower())
        meaningful_tokens = [
            token for token in tokens if token not in self._TASK_STOPWORDS and len(token) > 1
        ]
        if not meaningful_tokens:
            return ""

        return task

    def _build_notification_text(self, reminder: dict) -> str:
        """
        Genera un mensaje natural para la notificacion del recordatorio.
        """
        task = str(reminder.get("text", "")).strip().rstrip(".")
        if not task:
            task = "tu pendiente"

        lower_task = task.lower()
        starts_with_action = bool(
            re.match(
                r"^(ir|hacer|tomar|beber|comer|correr|entrenar|leer|estudiar|"
                r"comprar|pagar|llamar|enviar|revisar|terminar|empezar)\b",
                lower_task,
            )
        )

        if starts_with_action:
            templates = [
                f"Recuerda, tienes que {task}.",
                f"Hey, no te olvides de {task}.",
            ]
        else:
            templates = [
                f"Recuerda esto: {task}.",
                f"Hey, no olvides: {task}.",
            ]

        reminder_id = str(reminder.get("id", ""))
        idx = sum(ord(ch) for ch in reminder_id) % len(templates)
        body = templates[idx]
        return f"⏰ {body}\nFecha: {self.format_datetime_for_user(reminder['datetime'])}"

    def _extract_text_and_datetime(self, text: str) -> tuple[str, Optional[datetime]]:
        """
        Intenta separar texto del recordatorio y la fecha detectada.
        """
        clean = " ".join(text.strip().split())
        if not clean:
            return "", None

        parsed_dt: Optional[datetime] = None
        date_fragment = ""

        # Fallback rapido para tiempos relativos comunes: "en/dentro de 5 minutos".
        rel_match = re.search(
            r"\b(?:en|dentro\s+de)\s+(\d+)\s*(minuto|minutos|hora|horas|dia|dias)\b",
            clean,
            flags=re.IGNORECASE,
        )
        if rel_match:
            amount = int(rel_match.group(1))
            unit = rel_match.group(2).lower()
            now = datetime.now()
            if "minuto" in unit:
                parsed_dt = now + timedelta(minutes=amount)
            elif "hora" in unit:
                parsed_dt = now + timedelta(hours=amount)
            else:
                parsed_dt = now + timedelta(days=amount)
            date_fragment = rel_match.group(0)

        if parsed_dt is None:
            try:
                matches = search_dates(
                    clean,
                    languages=["es", "en"],
                    settings={
                        "PREFER_DATES_FROM": "future",
                        "RELATIVE_BASE": datetime.now(),
                        "RETURN_AS_TIMEZONE_AWARE": False,
                    },
                )
                if matches:
                    # Filtrar fragmentos demasiado cortos que son falsos positivos
                    # (e.g., "a", "de", "en" sueltos que dateparser interpreta como fechas).
                    valid_matches = [
                        (frag, dt) for frag, dt in matches
                        if len(frag.strip()) >= 4
                    ]
                    if valid_matches:
                        # Tomar la ultima coincidencia suele capturar la fecha completa.
                        date_fragment, parsed_dt = valid_matches[-1]
            except Exception as e:
                logger.debug(f"search_dates no encontro coincidencias: {e}")

        if parsed_dt is None:
            parsed_dt = self._parse_datetime(clean)

        reminder_text = clean
        if date_fragment and len(date_fragment.strip()) >= 4:
            # Solo reemplazar la primera ocurrencia para no corromper el texto.
            reminder_text = reminder_text.replace(date_fragment, " ", 1)

        # Limpiar verbos de activacion al inicio.
        reminder_text = re.sub(
            r"^(recuerdame|recu[eé]rdame|recordarme|recordar|avisame|av[ií]same)\s+",
            "",
            reminder_text,
            flags=re.IGNORECASE,
        )
        reminder_text = re.sub(
            r"^(puedes|podrias|podr[ií]as)\s+(crear\s+)?(un\s+)?recordatorio\s+",
            "",
            reminder_text,
            flags=re.IGNORECASE,
        )
        reminder_text = re.sub(
            r"^(activa|activar|crea|crear|pon|programa)\s+(un\s+)?recordatorio(\s+para)?\s+",
            "",
            reminder_text,
            flags=re.IGNORECASE,
        )
        reminder_text = re.sub(
            r"\s+que\s+se\s+active\s+((?:unicamente|únicamente)|solo)\s*$",
            "",
            reminder_text,
            flags=re.IGNORECASE,
        )
        reminder_text = re.sub(r"^para\s+", "", reminder_text, flags=re.IGNORECASE)
        reminder_text = re.sub(r"\s*[,;:\-]+\s*", " ", reminder_text)
        reminder_text = " ".join(reminder_text.split()).strip(" .!?")

        return reminder_text, parsed_dt

    async def create_from_natural_language(self, text: str) -> Optional[dict]:
        """
        Crea un recordatorio a partir de texto en lenguaje natural.
        Intenta extraer la fecha/hora y el texto del recordatorio.
        """
        reminder_text, parsed_dt = self._extract_text_and_datetime(text)
        if not parsed_dt:
            raise ValueError("missing_datetime")

        reminder_text = self._normalize_reminder_task(reminder_text)
        if not reminder_text:
            raise ValueError("missing_task")

        # Si la fecha ya paso, ajustamos al siguiente dia.
        if parsed_dt <= datetime.now():
            parsed_dt += timedelta(days=1)

        reminder = {
            "id": str(uuid.uuid4())[:8],
            "text": reminder_text,
            "datetime": parsed_dt.isoformat(),
            "recurring": False,
            "interval": None,
            "status": "active",
        }
        self.reminders.append(reminder)
        self._save_reminders()
        logger.info(f"Recordatorio creado: {reminder['text']} -> {reminder['datetime']}")
        return reminder

    def create_reminder(
        self,
        text: str,
        dt: str,
        recurring: bool = False,
        interval: Optional[str] = None,
    ) -> dict:
        """Crea un recordatorio con fecha/hora especifica."""
        normalized_text = self._normalize_reminder_task(text)
        if not normalized_text:
            raise ValueError("missing_task")

        parsed_dt: Optional[datetime] = None
        try:
            parsed_dt = datetime.fromisoformat(dt)
        except ValueError:
            parsed_dt = self._parse_datetime(dt)

        if parsed_dt is None:
            raise ValueError("Formato de fecha invalido.")

        if parsed_dt <= datetime.now() and not recurring:
            parsed_dt += timedelta(days=1)

        reminder = {
            "id": str(uuid.uuid4())[:8],
            "text": normalized_text,
            "datetime": parsed_dt.isoformat(),
            "recurring": recurring,
            "interval": interval,
            "status": "active",
        }
        self.reminders.append(reminder)
        self._save_reminders()
        return reminder

    def get_active_reminders(self) -> list[dict]:
        """Retorna solo recordatorios activos."""
        now = datetime.now()
        active = []
        for r in self.reminders:
            if r["status"] == "active":
                try:
                    r_dt = datetime.fromisoformat(r["datetime"])
                    if r_dt > now or r.get("recurring"):
                        active.append(r)
                except (ValueError, TypeError):
                    active.append(r)
        return active

    def get_active_reminders_text(self) -> str:
        """Retorna texto formateado de recordatorios activos para el system prompt."""
        active = self.get_active_reminders()
        if not active:
            return ""

        lines = ["Recordatorios pendientes del usuario:"]
        for r in active:
            lines.append(
                f"  - {r['text']} (fecha: {self.format_datetime_for_user(r['datetime'])})"
            )
        return "\n".join(lines)

    def delete_reminder(self, reminder_id: str) -> bool:
        """Elimina (marca como completado) un recordatorio."""
        for r in self.reminders:
            if r["id"] == reminder_id:
                r["status"] = "completed"
                self._save_reminders()
                logger.info(f"Recordatorio completado: {r['text']}")
                return True
        return False

    def _normalize_for_match(self, text: str) -> str:
        """Normaliza texto para comparaciones flexibles sin acentos."""
        normalized = unicodedata.normalize("NFD", text.lower())
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        return " ".join(normalized.split()).strip()

    def find_active_reminders_by_text(self, query: str) -> list[dict]:
        """
        Busca recordatorios activos por coincidencia flexible de texto.
        Retorna candidatos ordenados por fecha (mas cercano primero).
        """
        query_norm = self._normalize_for_match(query)
        if not query_norm:
            return []

        tokens = [token for token in query_norm.split() if len(token) >= 3]
        candidates: list[dict] = []

        for reminder in self.get_active_reminders():
            reminder_norm = self._normalize_for_match(reminder.get("text", ""))
            if not reminder_norm:
                continue

            if query_norm in reminder_norm or reminder_norm in query_norm:
                candidates.append(reminder)
                continue

            if tokens and all(token in reminder_norm for token in tokens):
                candidates.append(reminder)

        def _sort_key(reminder: dict) -> datetime:
            try:
                return datetime.fromisoformat(reminder["datetime"])
            except Exception:
                return datetime.max

        candidates.sort(key=_sort_key)
        return candidates

    def delete_reminder_by_text(self, query: str) -> Optional[dict]:
        """Completa el primer recordatorio activo que coincida con el texto."""
        matches = self.find_active_reminders_by_text(query)
        if not matches:
            return None

        reminder = matches[0]
        reminder["status"] = "completed"
        self._save_reminders()
        logger.info(f"Recordatorio completado por texto: {reminder['id']} - {reminder['text']}")
        return reminder

    def delete_all_active(self) -> int:
        """Marca como completados todos los recordatorios activos."""
        count = 0
        for reminder in self.reminders:
            if reminder.get("status") == "active":
                reminder["status"] = "completed"
                count += 1
        if count > 0:
            self._save_reminders()
            logger.info(f"Recordatorios completados en lote: {count}")
        return count

    def format_active_reminders_for_chat(self) -> str:
        """Genera un listado legible de recordatorios activos para respuestas de chat."""
        active = self.get_active_reminders()
        if not active:
            return "No tienes recordatorios activos."

        lines = ["Tus recordatorios activos son:"]
        for reminder in active:
            lines.append(
                f"- [{reminder['id']}] {reminder['text']} ({self.format_datetime_for_user(reminder['datetime'])})"
            )
        return "\n".join(lines)

    def get_all_reminders(self) -> list[dict]:
        """Retorna todos los recordatorios."""
        return self.reminders

    # ==========================================
    # SCHEDULER
    # ==========================================

    def _get_due_reminders(self) -> list[dict]:
        """Obtiene recordatorios activos cuya fecha ya llego."""
        now = datetime.now()
        due: list[dict] = []
        for reminder in self.reminders:
            if reminder.get("status") != "active":
                continue
            try:
                reminder_dt = datetime.fromisoformat(reminder["datetime"])
            except Exception:
                continue
            if reminder_dt <= now:
                due.append(reminder)
        return due

    async def _check_due_reminders(self) -> None:
        """Revisa recordatorios vencidos cada minuto y dispara notificaciones."""
        due = self._get_due_reminders()
        if due:
            logger.info(f"Scheduler: {len(due)} recordatorio(s) pendiente(s) de disparar.")
        for reminder in due:
            try:
                await self._fire_reminder(reminder["id"])
            except Exception as e:
                logger.error(f"Error al disparar recordatorio [{reminder.get('id')}]: {e}")

    def _next_recurring_datetime(self, current_dt: datetime, interval: Optional[str]) -> datetime:
        """Calcula la siguiente fecha de un recordatorio recurrente."""
        if not interval:
            return current_dt + timedelta(days=1)

        interval_clean = interval.strip().lower()
        if interval_clean in {"hourly", "cada hora"}:
            return current_dt + timedelta(hours=1)
        if interval_clean in {"daily", "diario", "cada dia"}:
            return current_dt + timedelta(days=1)
        if interval_clean in {"weekly", "semanal", "cada semana"}:
            return current_dt + timedelta(weeks=1)
        if interval_clean in {"monthly", "mensual", "cada mes"}:
            return current_dt + timedelta(days=30)

        match = re.match(r"^(\d+)\s*(m|min|minuto|minutos|h|hora|horas|d|dia|dias)$", interval_clean)
        if not match:
            return current_dt + timedelta(days=1)

        amount = int(match.group(1))
        unit = match.group(2)
        if unit in {"m", "min", "minuto", "minutos"}:
            return current_dt + timedelta(minutes=amount)
        if unit in {"h", "hora", "horas"}:
            return current_dt + timedelta(hours=amount)
        return current_dt + timedelta(days=amount)

    async def _fire_reminder(self, reminder_id: str) -> None:
        """Se ejecuta cuando un recordatorio llega a su hora."""
        reminder = None
        for r in self.reminders:
            if r["id"] == reminder_id:
                reminder = r
                break

        if not reminder or reminder["status"] != "active":
            return

        logger.info(f"Recordatorio disparado: [{reminder['id']}] {reminder['text']}")

        # Enviar por Telegram (con reintentos)
        sent = False
        notification_text = self._build_notification_text(reminder)
        if self.telegram_send_fn:
            for attempt in range(3):
                try:
                    if attempt == 0:
                        logger.info(
                            f"Texto de notificacion [{reminder['id']}]: {notification_text[:140]}"
                        )
                    result = await self.telegram_send_fn(notification_text)
                    if result:
                        sent = True
                        logger.info(f"Notificacion enviada para recordatorio [{reminder['id']}]")
                        break
                    logger.warning(
                        f"telegram_send_fn retorno False para [{reminder['id']}] "
                        f"(intento {attempt + 1}/3)"
                    )
                except Exception as e:
                    logger.error(
                        f"Error al enviar recordatorio [{reminder['id']}] "
                        f"(intento {attempt + 1}/3): {e}"
                    )
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(2)
        else:
            logger.warning(
                f"Sin telegram_send_fn para recordatorio [{reminder['id']}]. "
                "Verificar que el bot de Telegram este configurado."
            )

        if not sent:
            # No marcar completado: reintentar en el proximo ciclo del scheduler.
            fail_count = reminder.get("_send_failures", 0) + 1
            reminder["_send_failures"] = fail_count
            logger.error(
                f"No se pudo notificar recordatorio [{reminder['id']}] "
                f"(fallos acumulados: {fail_count})"
            )
            # Tras 5 ciclos fallidos (~5 min), marcar completado para no reintentar
            # indefinidamente.
            if fail_count >= 5:
                logger.error(
                    f"Recordatorio [{reminder['id']}] marcado completado "
                    f"tras {fail_count} fallos de envio."
                )
                reminder["status"] = "completed"
            self._save_reminders()
            return

        # Limpiar contador de fallos si existia
        reminder.pop("_send_failures", None)

        # Marcar como completado si no es recurrente
        if not reminder.get("recurring"):
            reminder["status"] = "completed"
            self._save_reminders()
            return

        try:
            current_dt = datetime.fromisoformat(reminder["datetime"])
        except Exception:
            current_dt = datetime.now()

        next_dt = self._next_recurring_datetime(current_dt, reminder.get("interval"))
        while next_dt <= datetime.now():
            next_dt = self._next_recurring_datetime(next_dt, reminder.get("interval"))

        reminder["datetime"] = next_dt.isoformat()
        self._save_reminders()

    def start_scheduler(self) -> None:
        """Inicia scheduler y revisa recordatorios activos cada minuto."""
        try:
            self.scheduler.add_job(
                self._check_due_reminders,
                "interval",
                minutes=1,
                id="reminder-checker",
                replace_existing=True,
                next_run_time=datetime.now(),
            )

            if not self.scheduler.running:
                self.scheduler.start()
                logger.info("Scheduler de recordatorios iniciado")

        except Exception as e:
            logger.error(f"Error al iniciar scheduler: {e}")

    def stop_scheduler(self) -> None:
        """Detiene el scheduler."""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                logger.info("Scheduler detenido")
        except Exception as e:
            logger.error(f"Error al detener scheduler: {e}")

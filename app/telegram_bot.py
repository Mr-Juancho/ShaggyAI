"""
Bot de Telegram para el agente de IA.
Comparte backend y memoria con la interfaz desktop.
"""

import asyncio
import re
from typing import Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode, ChatAction

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, logger


class TelegramBot:
    """Gestiona el bot de Telegram del agente."""

    def __init__(
        self,
        chat_handler,
        memory_manager=None,
        reminder_manager=None,
        web_search=None,
        clear_history_handler=None,
    ):
        """
        Args:
            chat_handler: Funcion async(message, user_id, source) -> str
            memory_manager: Instancia de MemoryManager (Fase 2)
            reminder_manager: Instancia de ReminderManager (Fase 5)
            web_search: Instancia de WebSearchEngine (Fase 5)
            clear_history_handler: Funcion clear_history(user_id) -> bool
        """
        self.chat_handler = chat_handler
        self.memory = memory_manager
        self.reminders = reminder_manager
        self.web_search = web_search
        self.clear_history_handler = clear_history_handler
        self.app: Optional[Application] = None
        self.allowed_user_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else None

        if not TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN no configurado. Bot deshabilitado.")

    def is_running(self) -> bool:
        """Indica si el bot esta realmente corriendo en polling."""
        if not self.app:
            return False
        updater = getattr(self.app, "updater", None)
        updater_running = bool(updater and getattr(updater, "running", False))
        app_running = bool(getattr(self.app, "running", False))
        return app_running and updater_running

    def _is_authorized(self, update: Update) -> bool:
        """Verifica que el mensaje venga del usuario autorizado."""
        if not self.allowed_user_id:
            return True  # Sin restriccion si no se configura
        user_id = update.effective_user.id
        if user_id != self.allowed_user_id:
            logger.warning(f"Acceso no autorizado de user_id={user_id}")
            return False
        return True

    def _normalize_telegram_text(self, text: str) -> str:
        """Limpia formatos no compatibles con Telegram (tablas HTML/Markdown complejo)."""
        normalized = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        normalized = re.sub(r"</?(div|span|p|table|tbody|thead|tr|td|th)>", "", normalized, flags=re.IGNORECASE)

        lines = normalized.splitlines()
        out_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            # Convertir filas de tablas markdown en lineas legibles.
            if stripped.count("|") >= 2:
                cells = [c.strip() for c in stripped.split("|")]
                if all(not c or set(c) <= {"-", ":", " "} for c in cells):
                    continue
                cells = [c for c in cells if c]
                if len(cells) >= 2:
                    out_lines.append(f"- {cells[0]}: {' | '.join(cells[1:])}")
                elif cells:
                    out_lines.append(f"- {cells[0]}")
                continue

            if stripped == "|":
                continue
            out_lines.append(line.rstrip("|").rstrip())

        normalized = "\n".join(out_lines)
        normalized = normalized.replace("### ", "").replace("## ", "")
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    async def _split_and_send(
        self,
        update: Update,
        text: str,
        max_length: int = 4096
    ) -> None:
        """Divide mensajes largos y los envia con soporte Markdown."""
        text = self._normalize_telegram_text(text)
        if len(text) <= max_length:
            try:
                await update.message.reply_text(
                    text, parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                # Si falla el markdown, enviar como texto plano
                await update.message.reply_text(text)
            return

        # Dividir en chunks respetando saltos de linea
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_length:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)

        for chunk in chunks:
            try:
                await update.message.reply_text(
                    chunk, parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                await update.message.reply_text(chunk)
            await asyncio.sleep(0.3)

    # ==========================================
    # COMANDOS
    # ==========================================

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /start - Bienvenida."""
        if not self._is_authorized(update):
            await update.message.reply_text("No estas autorizado para usar este bot.")
            return

        await update.message.reply_text(
            "Hola! Soy *Shaggy*, tu asistente personal de IA.\n\n"
            "Puedes hablarme directamente o usar estos comandos:\n"
            "/remember [texto] — Guardar informacion\n"
            "/profile — Ver tu perfil guardado\n"
            "/reminders — Ver recordatorios activos\n"
            "/remind [texto] [fecha] — Crear recordatorio\n"
            "/search [consulta] — Buscar en internet\n"
            "/clear — Limpiar historial de chat",
            parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_remember(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /remember — Guardar info manualmente."""
        if not self._is_authorized(update):
            return

        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text("Uso: /remember [informacion a guardar]")
            return

        if self.memory:
            stored = await self.memory.store_user_info(
                text, metadata={"source": "telegram_manual"}
            )
            if stored:
                await update.message.reply_text(f"Guardado: {text}")
            else:
                await update.message.reply_text(
                    "Esa informacion ya la tengo guardada."
                )
        else:
            await update.message.reply_text("Sistema de memoria no disponible.")

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /profile — Ver perfil del usuario."""
        if not self._is_authorized(update):
            return

        if self.memory:
            summary = await self.memory.get_user_profile_summary()
            await self._split_and_send(update, summary)
        else:
            await update.message.reply_text("Sistema de memoria no disponible.")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /clear — Limpiar historial de conversacion."""
        if not self._is_authorized(update):
            return

        user_id = str(update.effective_user.id)
        cleared = False

        if self.clear_history_handler:
            try:
                cleared = bool(self.clear_history_handler(user_id))
            except Exception as e:
                logger.error(f"Error al limpiar historial de {user_id}: {e}")

        if cleared:
            await update.message.reply_text(
                "Historial de conversacion limpiado. La memoria a largo plazo se mantiene."
            )
        else:
            await update.message.reply_text(
                "No habia historial reciente para limpiar. La memoria a largo plazo se mantiene."
            )

    async def cmd_reminders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /reminders — Listar recordatorios activos."""
        if not self._is_authorized(update):
            return

        if self.reminders:
            active = self.reminders.get_active_reminders()
            if not active:
                await update.message.reply_text("No tienes recordatorios activos.")
                return

            text = "*Recordatorios activos:*\n\n"
            for r in active:
                dt_text = r["datetime"]
                if hasattr(self.reminders, "format_datetime_for_user"):
                    dt_text = self.reminders.format_datetime_for_user(r["datetime"])
                text += f"- {r['text']} — {dt_text}\n"
            await self._split_and_send(update, text)
        else:
            await update.message.reply_text("Sistema de recordatorios no disponible.")

    async def cmd_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /remind — Crear recordatorio."""
        if not self._is_authorized(update):
            return

        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text(
                "Uso: /remind comprar leche manana a las 9am"
            )
            return

        if self.reminders:
            try:
                result = await self.reminders.create_from_natural_language(text)
            except ValueError as exc:
                reason = str(exc).strip().lower()
                if reason == "missing_task":
                    await update.message.reply_text(
                        "Tengo la hora, pero me falta la accion del recordatorio. "
                        "Ejemplo: /remind para las 10:11 para ir a comer"
                    )
                    return
                await update.message.reply_text(
                    "No pude entender la fecha. Intenta ser mas especifico."
                )
                return

            if result:
                dt_text = result["datetime"]
                if hasattr(self.reminders, "format_datetime_for_user"):
                    dt_text = self.reminders.format_datetime_for_user(result["datetime"])
                await update.message.reply_text(
                    f"Recordatorio creado: {result['text']}\n"
                    f"Fecha: {dt_text}"
                )
            else:
                await update.message.reply_text(
                    "No pude entender la fecha. Intenta ser mas especifico."
                )
        else:
            await update.message.reply_text("Sistema de recordatorios no disponible.")

    async def cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Comando /search — Buscar en internet."""
        if not self._is_authorized(update):
            return

        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Uso: /search [consulta]")
            return

        if self.web_search:
            await update.message.chat.send_action(ChatAction.TYPING)
            results = await self.web_search.search(query)
            if results:
                text = f"*Resultados para:* {query}\n\n"
                for r in results:
                    text += f"*{r['title']}*\n{r['snippet']}\n{r['url']}\n\n"
                await self._split_and_send(update, text)
            else:
                await update.message.reply_text("No se encontraron resultados.")
        else:
            await update.message.reply_text("Sistema de busqueda no disponible.")

    # ==========================================
    # MENSAJES DE TEXTO
    # ==========================================

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Maneja mensajes de texto normales."""
        if not self._is_authorized(update):
            return

        user_message = update.message.text
        user_id = str(update.effective_user.id)

        logger.info(f"[telegram] Mensaje de {user_id}: {user_message[:100]}...")

        # Mostrar typing action
        await update.message.chat.send_action(ChatAction.TYPING)

        try:
            # Usar el mismo flujo que /chat
            response = await self.chat_handler(
                message=user_message,
                user_id=user_id,
                source="telegram"
            )
            await self._split_and_send(update, response)

        except Exception as e:
            logger.error(f"Error procesando mensaje de Telegram: {e}")
            await update.message.reply_text(
                "Hubo un error procesando tu mensaje. Intenta de nuevo."
            )

    # ==========================================
    # INICIALIZACION Y EJECUCION
    # ==========================================

    async def setup(self) -> Optional[Application]:
        """Configura y retorna la aplicacion del bot."""
        if not TELEGRAM_BOT_TOKEN:
            logger.info("Bot de Telegram deshabilitado (sin token)")
            return None

        try:
            self.app = (
                Application.builder()
                .token(TELEGRAM_BOT_TOKEN)
                .build()
            )

            # Registrar comandos
            self.app.add_handler(CommandHandler("start", self.cmd_start))
            self.app.add_handler(CommandHandler("remember", self.cmd_remember))
            self.app.add_handler(CommandHandler("profile", self.cmd_profile))
            self.app.add_handler(CommandHandler("clear", self.cmd_clear))
            self.app.add_handler(CommandHandler("reminders", self.cmd_reminders))
            self.app.add_handler(CommandHandler("remind", self.cmd_remind))
            self.app.add_handler(CommandHandler("search", self.cmd_search))

            # Mensajes de texto
            self.app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
            )

            logger.info("Bot de Telegram configurado correctamente")
            return self.app

        except Exception as e:
            logger.error(f"Error al configurar bot de Telegram: {e}")
            return None

    async def start_polling(self) -> None:
        """Inicia el bot en modo polling (dentro del proceso FastAPI)."""
        if not self.app:
            await self.setup()

        if not self.app:
            return

        if self.is_running():
            logger.debug("Bot de Telegram ya estaba corriendo")
            return

        try:
            if not getattr(self.app, "initialized", False):
                await self.app.initialize()
            if not getattr(self.app, "running", False):
                await self.app.start()

            # Establecer comandos del bot
            try:
                await self.app.bot.set_my_commands([
                    BotCommand("start", "Iniciar el bot"),
                    BotCommand("remember", "Guardar informacion"),
                    BotCommand("profile", "Ver perfil guardado"),
                    BotCommand("reminders", "Ver recordatorios"),
                    BotCommand("remind", "Crear recordatorio"),
                    BotCommand("search", "Buscar en internet"),
                    BotCommand("clear", "Limpiar historial"),
                ])
            except Exception:
                pass

            if self.app.updater and not getattr(self.app.updater, "running", False):
                await self.app.updater.start_polling(
                    drop_pending_updates=True
                )
            logger.info("Bot de Telegram iniciado (polling)")

        except Exception as e:
            logger.error(f"Error al iniciar bot de Telegram: {e}")
            # Limpieza defensiva para permitir reintentos limpios.
            try:
                if self.app and self.app.updater and getattr(self.app.updater, "running", False):
                    await self.app.updater.stop()
            except Exception:
                pass
            try:
                if self.app and getattr(self.app, "running", False):
                    await self.app.stop()
            except Exception:
                pass
            try:
                if self.app and getattr(self.app, "initialized", False):
                    await self.app.shutdown()
            except Exception:
                pass

    async def stop(self) -> None:
        """Detiene el bot de forma defensiva."""
        if not self.app:
            return

        try:
            if self.app.updater and getattr(self.app.updater, "running", False):
                await self.app.updater.stop()
        except Exception as e:
            logger.error(f"Error al detener updater de Telegram: {e}")

        try:
            if getattr(self.app, "running", False):
                await self.app.stop()
        except Exception as e:
            logger.error(f"Error al detener app de Telegram: {e}")

        try:
            if getattr(self.app, "initialized", False):
                await self.app.shutdown()
        except Exception as e:
            logger.error(f"Error al hacer shutdown de Telegram: {e}")

        logger.info("Bot de Telegram detenido")

    async def send_message(self, text: str) -> bool:
        """Envia un mensaje proactivo al usuario (para recordatorios)."""
        if not self.app or not self.allowed_user_id:
            return False

        try:
            await self.app.bot.send_message(
                chat_id=self.allowed_user_id,
                text=text
            )
            return True
        except Exception as e:
            logger.error(f"Error al enviar mensaje proactivo: {e}")
            return False

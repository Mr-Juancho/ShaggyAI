# PRODUCT_SCOPE

Contrato de producto para RUFÜS. Define un alcance estricto y auditable.

## Capacidades core permitidas (v1)

1. `chat_general`: conversación general y explicación técnica.
2. `web_search_general`: búsqueda web general para información variable.
3. `web_search_news`: búsqueda de noticias recientes.
4. `get_current_datetime`: obtención de fecha/hora actual para contexto temporal.
5. `reminder_create`: creación de recordatorios por lenguaje natural.
6. `reminder_list`: listado de recordatorios activos.
7. `reminder_delete`: eliminación de recordatorios por ID o texto.
8. `memory_store_user_fact`: guardado explícito de datos personales en memoria larga.
9. `memory_retrieval`: recuperación de contexto relevante de memoria.
10. `memory_recall_profile`: respuesta explícita a "qué recuerdas de mí/sobre X".
11. `memory_store_summary`: guardado de resumen de conversación útil.
12. `memory_update_user_fact`: edición semántica de recuerdos existentes del usuario.
13. `memory_delete_user_fact`: borrado semántico de recuerdos específicos.
14. `memory_purge_all`: borrado total de memoria larga (perfil + conversaciones).
15. `media_stack_start`: inicio del stack multimedia en background.
16. `media_stack_status`: consulta de estado del stack multimedia.
17. `media_stack_stop`: apagado del stack multimedia.
18. `movie_search_radarr`: búsqueda de películas en Radarr para flujo web/desktop.

## Reglas de gobernanza

- No se pueden ejecutar capacidades fuera de esta lista.
- Toda nueva capacidad debe añadirse primero a este archivo y a `app/CAPABILITIES.yaml`.

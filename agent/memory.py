"""Short-term session memory: Postgres checkpointer (Docker) with in-memory fallback."""
import os

from langgraph.checkpoint.memory import MemorySaver

from security.settings import get_settings

STATUS = {"backend": "memory", "detail": "In-memory (lost when the app restarts)"}


def _fallback(reason: str):
    STATUS.update(backend="memory", detail=f"In-memory fallback: {reason}")
    return MemorySaver()


def _delete_old_threads(saver, pool, days: int) -> int:
    """Drop whole conversations whose latest checkpoint is older than `days`."""
    if days <= 0:
        return 0
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT thread_id FROM checkpoints GROUP BY thread_id "
            "HAVING MAX((checkpoint->>'ts')::timestamptz) < now() - make_interval(days => %s)",
            (days,),
        ).fetchall()
    for row in rows:
        saver.delete_thread(row["thread_id"])
    return len(rows)


def get_checkpointer():
    get_settings()  # makes sure .env is loaded
    if os.getenv("MEMORY_BACKEND", "postgres").lower() != "postgres":
        STATUS.update(backend="memory", detail="In-memory (MEMORY_BACKEND=memory)")
        return MemorySaver()

    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        return _fallback("POSTGRES_PASSWORD is not set in .env")

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "agent_memory")
    try:
        days = int(os.getenv("MEMORY_RETENTION_DAYS", "7"))
    except ValueError:
        days = 7

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.conninfo import make_conninfo
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        conninfo = make_conninfo(host=host, port=port, dbname=database,
                                 user=os.getenv("POSTGRES_USER", "agent"),
                                 password=password, connect_timeout=5)
        pool = ConnectionPool(conninfo, min_size=1, max_size=5, open=False,
                              kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        pool.open(wait=True, timeout=10)
        saver = PostgresSaver(pool)
        saver.setup()
        removed = _delete_old_threads(saver, pool, days)
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"Postgres unavailable ({type(exc).__name__}: {str(exc)[:120]})")

    STATUS.update(backend="postgres", detail=f"Postgres {host}:{port}/{database} (chats kept {days} days)"
                  + (f", removed {removed} old chat(s)" if removed else ""))
    return saver
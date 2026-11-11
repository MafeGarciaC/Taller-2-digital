"""Conexiones a PostgreSQL (pool que se recupera solo si la BD se reinicia o se restaura)."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None


def iniciar() -> None:
    """Abre el pool. Se llama al arrancar (no al importar) para que un secreto faltante
    se reporte como error en /api/estado en lugar de tumbar el backend."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.dsn(), min_size=1, max_size=10, open=False,
                               check=ConnectionPool.check_connection, kwargs={"row_factory": dict_row})
        _pool.open(wait=False)


def cerrar() -> None:
    if _pool is not None:
        _pool.close()


@contextmanager
def conexion():
    if _pool is None:
        raise RuntimeError("El backend no tiene conexión a la base de datos (revise los secretos)")
    with _pool.connection(timeout=5) as con:
        yield con


def evento(tipo: str, detalle: str, documento_id=None, con=None) -> None:
    sql = "INSERT INTO eventos (tipo, documento_id, detalle) VALUES (%s, %s, %s)"
    if con is not None:
        con.execute(sql, (tipo, documento_id, detalle))
        return
    try:
        with conexion() as c:
            c.execute(sql, (tipo, documento_id, detalle))
    except Exception:
        pass  # la bitácora nunca debe tumbar una operación

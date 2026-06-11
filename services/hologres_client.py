from contextlib import contextmanager
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.settings import settings


class HologresConfigError(ValueError):
    pass


def build_hologres_url() -> str:
    errors = settings.validate_hologres()
    if errors:
        raise HologresConfigError(" ".join(errors))

    resolved = settings.resolved_hologres_url()
    if resolved:
        return resolved

    user = quote_plus(settings.hologres_user)
    password = quote_plus(settings.hologres_password)
    host = settings.hologres_host
    port = settings.hologres_port
    db = quote_plus(settings.hologres_db)
    sslmode = quote_plus(settings.hologres_sslmode)

    return (
        f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
        f"?sslmode={sslmode}"
    )


class HologresClient:
    def __init__(self) -> None:
        self.engine: Engine = create_engine(build_hologres_url(), pool_pre_ping=True)

    @contextmanager
    def _connection(self):
        with self.engine.connect() as conn:
            conn.execute(text(f"SET statement_timeout = {settings.app_query_timeout_sec * 5000}"))
            yield conn

    def run_select(self, sql: str, max_rows: int | None = None) -> pd.DataFrame:
        effective_max_rows = max_rows or settings.app_max_rows
        with self._connection() as conn:
            result = conn.execute(text(sql))
            rows = result.fetchmany(effective_max_rows)
            columns = list(result.keys())
            return pd.DataFrame(rows, columns=columns)

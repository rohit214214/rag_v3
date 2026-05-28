import pytest

from services.sql_guard import SqlGuard, SqlGuardError


def test_validate_allows_select():
    guard = SqlGuard()
    sql = guard.validate("SELECT * FROM public.fact_sales")
    assert sql.lower().startswith("select")


def test_validate_blocks_delete():
    guard = SqlGuard()
    with pytest.raises(SqlGuardError):
        guard.validate("DELETE FROM public.fact_sales")


def test_limit_is_applied_when_missing():
    guard = SqlGuard()
    sql = guard.apply_limit("SELECT * FROM public.fact_sales", max_rows=100)
    assert "LIMIT 100" in sql

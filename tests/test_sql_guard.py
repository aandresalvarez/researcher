from uamm.security.sql_guard import (
    is_read_only_select,
    referenced_tables,
    tables_allowed,
)


def test_sql_guard_allows_select():
    assert is_read_only_select("SELECT 1") is True
    assert is_read_only_select(" select * from demo ") is True


def test_sql_guard_blocks_ddl_dml():
    assert is_read_only_select("DROP TABLE x") is False
    assert is_read_only_select("UPDATE x SET a=1") is False
    assert (
        is_read_only_select("WITH cte AS (SELECT * FROM demo) SELECT * FROM cte")
        is False
    )
    assert is_read_only_select("SELECT * FROM demo UNION SELECT * FROM demo2") is False
    assert is_read_only_select("SELECT 1;") is False


def test_referenced_tables_and_allowlist():
    sql = "select x from demo where x > 1"
    assert referenced_tables(sql) == ["demo"]
    assert tables_allowed(sql, ["demo"]) is True
    assert tables_allowed(sql, ["other"]) is False

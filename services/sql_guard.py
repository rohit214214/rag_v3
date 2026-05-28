import re

from config.settings import settings


BLOCKED_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "merge",
    "call",
    "execute",
    "vacuum",
    "analyze",
}


class SqlGuardError(ValueError):
    pass


class SqlGuard:
    @staticmethod
    def _normalize(sql: str) -> str:
        return re.sub(r"\s+", " ", sql.strip()).strip()

    def validate(self, sql: str) -> str:
        normalized = self._normalize(sql)
        lower_sql = normalized.lower()
        if ";" in lower_sql.rstrip(";"):
            raise SqlGuardError("Only one SQL statement is allowed.")
        if not lower_sql.startswith(("select", "with")):
            raise SqlGuardError("Only SELECT statements are allowed.")
        for keyword in BLOCKED_KEYWORDS:
            if re.search(rf"\b{keyword}\b", lower_sql):
                raise SqlGuardError(f"Blocked keyword detected: {keyword}")
        return normalized

    def enforce_table_whitelist(self, sql: str) -> None:
        whitelist = settings.whitelist_tables
        if not whitelist:
            return
        lower_sql = sql.lower()
        referenced = re.findall(r"\b(?:from|join)\s+([a-zA-Z0-9_\.]+)", lower_sql)
        allowed = {item.lower() for item in whitelist}
        for table in referenced:
            if table not in allowed and table.split(".")[-1] not in allowed:
                allowed_list = ", ".join(settings.whitelist_tables)
                raise SqlGuardError(
                    f"Table not whitelisted: {table}. "
                    f"Allowed tables: {allowed_list}. "
                    "The model must use only whitelisted tables — check APP_TABLE_WHITELIST and business_rules.json."
                )

    def apply_limit(self, sql: str, max_rows: int | None = None) -> str:
        row_cap = max_rows or settings.app_max_rows
        sql = sql.rstrip().rstrip(";")  # strip trailing semicolon before appending LIMIT
        lower_sql = sql.lower()
        has_limit = re.search(r"\blimit\s+\d+\b", lower_sql)
        if not has_limit:
            return f"{sql} LIMIT {row_cap}"

        limit_match = re.search(r"\blimit\s+(\d+)\b", lower_sql)
        if not limit_match:
            return sql
        current_limit = int(limit_match.group(1))
        if current_limit > row_cap:
            return re.sub(r"\blimit\s+\d+\b", f"LIMIT {row_cap}", sql, flags=re.IGNORECASE)
        return sql

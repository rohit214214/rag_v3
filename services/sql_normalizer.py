import re


def uppercase_string_literals(sql: str) -> str:
    """
    Uppercase every single-quoted string literal in SQL.

    Applies to country, brand, and any other dimension text the model puts in filters
  (e.g. 'Singapore' -> 'SINGAPORE'). Dates like '2026-05-13' are unchanged in meaning.
    """

    def repl(match: re.Match[str]) -> str:
        return f"'{match.group(1).upper()}'"

    return re.sub(r"'([^']*)'", repl, sql)


# Backward-compatible alias used by app.py
def normalize_country_filters(sql: str) -> str:
    return uppercase_string_literals(sql)

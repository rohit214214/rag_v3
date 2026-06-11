import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import settings
from services.hologres_client import HologresClient

# Common English words removed from schema search keywords so only domain
# terms (e.g. "customer", "nationality") drive table matching.
_SCHEMA_STOP_WORDS = frozenset({
    "are", "can", "the", "any", "for", "and", "get", "how",
    "where", "what", "show", "give", "tell", "find", "have", "from",
    "with", "this", "that", "there", "which", "about", "into",
    "also", "some", "all", "not", "but", "was", "has", "did",
    "use", "per", "who", "will", "does", "more", "most", "much",
    "when", "here", "then", "please", "could", "would", "should",
    "data", "table", "tables", "column", "columns", "list",
    "just", "only", "want", "need", "like", "know", "you", "your",
    "our", "its", "his", "her", "they", "them", "than", "now",
    "very", "still", "look", "make", "take", "see", "info",
})


@dataclass
class MetadataContext:
    user_question: str
    tables: list[dict[str, Any]]
    business_rules: dict[str, Any]

    def to_prompt_context(self) -> str:
        table_lines: list[str] = []
        for table in self.tables:
            cols = ", ".join([f"{col['column_name']} ({col['data_type']})" for col in table["columns"]])
            table_lines.append(f"- {table['table_schema']}.{table['table_name']}: {cols}")
        glossary = self.business_rules.get("glossary", {})
        rules = self.business_rules.get("rules", [])
        table_columns = self.business_rules.get("table_columns", {})
        allowed_only = self.business_rules.get("allowed_tables_only", [])
        default_sales = self.business_rules.get("default_sales_table", "")
        default_stock = self.business_rules.get("default_stock_table", "")
        parts = [
            "User question:\n",
            f"{self.user_question}\n\n",
        ]
        if allowed_only:
            parts.append(
                "ALLOWED TABLES — you MUST use ONLY these tables in FROM/JOIN "
                "(do not use any other table name):\n"
                f"{chr(10).join(f'- {t}' for t in allowed_only)}\n\n"
            )
        if default_sales:
            parts.append(f"Default table for sales/revenue questions: {default_sales}\n\n")
        if default_stock:
            parts.append(f"Default table for stock/inventory questions: {default_stock}\n\n")
        if table_columns:
            parts.append(
                "Table column mapping (use these exact column names):\n"
                f"{json.dumps(table_columns, ensure_ascii=True, indent=2)}\n\n"
            )
        parts.extend(
            [
                "Relevant schema:\n",
                f"{chr(10).join(table_lines) if table_lines else '- none'}\n\n",
                f"Business glossary:\n{json.dumps(glossary, ensure_ascii=True, indent=2)}\n\n",
                f"Business rules:\n{json.dumps(rules, ensure_ascii=True, indent=2)}",
            ]
        )
        return "".join(parts)


class MetadataRetriever:
    def __init__(self, db_client: HologresClient, rules_path: str | None = None) -> None:
        self.db_client = db_client
        self.rules_path = Path(rules_path or settings.app_business_rules_path)

    def _load_rules(self) -> dict[str, Any]:
        if not self.rules_path.exists():
            return {"glossary": {}, "rules": []}
        with self.rules_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _fetch_schema_catalog(self) -> pd.DataFrame:
        sql = """
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name, ordinal_position
        """
        return self.db_client.run_select(sql, max_rows=50000)

    @staticmethod
    def _parse_whitelist_entry(entry: str) -> tuple[str | None, str]:
        """Return (schema, table). Schema may be None if only table name given."""
        entry = entry.strip()
        if "." in entry:
            schema, table = entry.split(".", 1)
            return schema.lower(), table.lower()
        return None, entry.lower()

    @staticmethod
    def _resolve_catalog_key(
        grouped: dict[tuple[str, str], list[dict[str, str]]],
        schema: str | None,
        table: str,
        default_schema: str,
    ) -> tuple[str, str] | None:
        table_l = table.lower()
        schema_l = (schema or default_schema).lower()
        for schema_name, table_name in grouped:
            if table_name.lower() == table_l and schema_name.lower() == schema_l:
                return schema_name, table_name
        return None

    def _table_is_whitelisted(
        self, schema_name: str, table_name: str, whitelist: list[str]
    ) -> bool:
        schema_lower = schema_name.lower()
        table_lower = table_name.lower()
        for entry in whitelist:
            allowed_schema, allowed_table = self._parse_whitelist_entry(entry)
            if allowed_schema is None:
                if table_lower == allowed_table:
                    return True
            elif schema_lower == allowed_schema and table_lower == allowed_table:
                return True
        return False

    def get_context(self, question: str, max_tables: int = 12, ignore_whitelist: bool = False) -> MetadataContext:
        rules = self._load_rules()
        # ignore_whitelist=True is used for schema discovery questions so all
        # tables are visible, not just the SQL query whitelist.
        whitelist = [] if ignore_whitelist else settings.whitelist_tables
        if whitelist:
            rules = {
                **rules,
                "allowed_tables_only": whitelist,
            }
            if not rules.get("default_sales_table") and any(
                "sales" in t.lower() for t in whitelist
            ):
                rules["default_sales_table"] = next(
                    t for t in whitelist if "sales" in t.lower()
                )

        catalog = self._fetch_schema_catalog()
        keywords = set(token.lower() for token in question.replace(",", " ").split() if len(token) > 2)
        glossary = rules.get("glossary", {})
        for key in glossary.keys():
            keywords.add(key.lower())

        grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
        for _, row in catalog.iterrows():
            key = (row["table_schema"], row["table_name"])
            if whitelist and not self._table_is_whitelisted(key[0], key[1], whitelist):
                continue
            grouped.setdefault(key, []).append(
                {"column_name": row["column_name"], "data_type": row["data_type"]}
            )

        scored: list[tuple[int, dict[str, Any]]] = []
        for (schema_name, table_name), columns in grouped.items():
            col_names = {col["column_name"].lower() for col in columns}
            score = 0
            for token in keywords:
                if token in table_name.lower() or token in schema_name.lower() or token in col_names:
                    score += 2
                else:
                    if any(token in c for c in col_names):
                        score += 1
            if score > 0:
                scored.append(
                    (
                        score,
                        {
                            "table_schema": schema_name,
                            "table_name": table_name,
                            "columns": columns,
                        },
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        if whitelist:
            # Whitelist mode: expose all allowed tables (prioritize keyword matches).
            selected = [item[1] for item in scored]
            for entry in whitelist:
                schema, table = self._parse_whitelist_entry(entry)
                key = self._resolve_catalog_key(
                    grouped, schema, table, settings.app_default_schema
                )
                if key and not any(
                    t["table_schema"] == key[0] and t["table_name"] == key[1] for t in selected
                ):
                    selected.append(
                        {
                            "table_schema": key[0],
                            "table_name": key[1],
                            "columns": grouped.get(key, []),
                        }
                    )
            if not selected:
                for entry in whitelist[:max_tables]:
                    schema, table = self._parse_whitelist_entry(entry)
                    selected.append(
                        {
                            "table_schema": schema or settings.app_default_schema,
                            "table_name": table,
                            "columns": [],
                        }
                    )
            else:
                selected = selected[:max_tables]
        else:
            selected = [item[1] for item in scored[:max_tables]]
            if not selected:
                selected = [
                    {"table_schema": k[0], "table_name": k[1], "columns": v}
                    for k, v in list(grouped.items())[:max_tables]
                ]
        return MetadataContext(user_question=question, tables=selected, business_rules=rules)

    def get_schema_context(self, question: str, max_tables: int = 8) -> MetadataContext:
        """Schema discovery: find the most relevant tables for a catalog question.
        - Removes stop words so only domain keywords drive matching.
        - Scores tables: table-name match (+3) ranks higher than column match (+1).
        - Returns top max_tables by score — no LLM involved.
        """
        rules = self._load_rules()
        catalog = self._fetch_schema_catalog()

        # Extract meaningful keywords (strip punctuation, remove stop words)
        raw = question.lower()
        for ch in (",", "?", "!", ".", "(", ")", "'", "\""):
            raw = raw.replace(ch, " ")
        keywords = {
            t for t in raw.split()
            if len(t) > 2 and t not in _SCHEMA_STOP_WORDS
        }
        # Also include glossary terms
        for key in rules.get("glossary", {}).keys():
            for part in key.lower().split():
                if len(part) > 2 and part not in _SCHEMA_STOP_WORDS:
                    keywords.add(part)

        if not keywords:
            return MetadataContext(user_question=question, tables=[], business_rules=rules)

        # Group catalog rows by (schema, table)
        grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
        for _, row in catalog.iterrows():
            key = (row["table_schema"], row["table_name"])
            grouped.setdefault(key, []).append(
                {"column_name": row["column_name"], "data_type": row["data_type"]}
            )

        # Score each table
        scored: list[tuple[int, dict[str, Any]]] = []
        for (schema_name, table_name), columns in grouped.items():
            col_names = [c["column_name"].lower() for c in columns]
            score = 0
            for kw in keywords:
                if kw in table_name.lower():
                    score += 3          # keyword in table name = highly relevant
                for cn in col_names:
                    if kw in cn:
                        score += 1      # keyword in a column name
                        break           # count each keyword once per table
            if score > 0:
                scored.append((score, {
                    "table_schema": schema_name,
                    "table_name": table_name,
                    "columns": columns,
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return MetadataContext(
            user_question=question,
            tables=[t for _, t in scored[:max_tables]],
            business_rules=rules,
        )
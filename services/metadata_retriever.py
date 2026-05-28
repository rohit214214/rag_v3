import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import settings
from services.hologres_client import HologresClient


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

    def get_context(self, question: str, max_tables: int = 12) -> MetadataContext:
        rules = self._load_rules()
        whitelist = settings.whitelist_tables
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

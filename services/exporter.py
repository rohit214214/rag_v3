from datetime import datetime, timezone
from io import BytesIO

import pandas as pd

from config.settings import settings


class Exporter:
    @staticmethod
    def to_xlsx_bytes(data: pd.DataFrame, question: str, sql: str) -> bytes:
        clipped = data.head(settings.app_max_rows)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            clipped.to_excel(writer, index=False, sheet_name="result")
            meta = pd.DataFrame(
                [
                    {"key": "timestamp_utc", "value": datetime.now(timezone.utc).isoformat()},
                    {"key": "question", "value": question},
                    {"key": "sql", "value": sql},
                    {"key": "row_count", "value": len(clipped)},
                ]
            )
            meta.to_excel(writer, index=False, sheet_name="metadata")
        output.seek(0)
        return output.read()

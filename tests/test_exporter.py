import pandas as pd

from services.exporter import Exporter


def test_exporter_generates_xlsx_bytes():
    df = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    blob = Exporter.to_xlsx_bytes(df, question="q", sql="SELECT 1")
    assert isinstance(blob, bytes)
    assert len(blob) > 100

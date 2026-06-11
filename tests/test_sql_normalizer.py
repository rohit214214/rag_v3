from services.sql_normalizer import uppercase_string_literals


def test_country_and_brand_uppercased():
    sql = (
        "SELECT SUM(sales_qty) FROM ads.ads_ckg_sales_di "
        "WHERE country = 'Singapore' AND brand = 'Nike' AND sales_dt = '2026-05-13'"
    )
    out = uppercase_string_literals(sql)
    assert "country = 'SINGAPORE'" in out
    assert "brand = 'NIKE'" in out
    assert "sales_dt = '2026-05-13'" in out


def test_in_list_uppercased():
    sql = "SELECT 1 FROM t WHERE country IN ('Singapore', 'malaysia')"
    out = uppercase_string_literals(sql)
    assert "('SINGAPORE', 'MALAYSIA')" in out

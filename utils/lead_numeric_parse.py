"""Parse de números em linhas de lead (CSV BR, milhar com ponto) para INSERT em campaign_leads."""

import math
import re


def parse_loose_float(val):
    """
    Converte string/número para float aceitando formato BR (1.234,56) e milhar com pontos
    (-253.999.866). Valores inválidos retornam None.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
        return float(val)
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null", "", "nat"):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 and re.match(r"^-?\d+(\.\d{3})+$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def coerce_lead_numeric_fields(lead: dict) -> None:
    """Garante tipos aceitos pelo Postgres para colunas FLOAT em campaign_leads."""
    rc = parse_loose_float(lead.get("reviews_count"))
    rr = parse_loose_float(lead.get("reviews_rating"))
    lat = parse_loose_float(lead.get("latitude"))
    lon = parse_loose_float(lead.get("longitude"))
    lead["reviews_count"] = rc
    lead["reviews_rating"] = rr
    lead["latitude"] = lat if lat is not None and -90.0 <= lat <= 90.0 else None
    lead["longitude"] = lon if lon is not None and -180.0 <= lon <= 180.0 else None

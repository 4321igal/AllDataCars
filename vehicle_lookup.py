"""Look up an Israeli license plate (mispar_rechev) in the public vehicle
registries on data.gov.il.

Two open datasets are queried:
  * vehicles                  — private & commercial registry
                                (resource 053cea08-09bc-40ec-8f7a-156f0677aff3)
  * vehicles_personal_import  — personal-import registry
                                (resource 03adc637-b6fe-402b-9937-7c3d3afc9140)

Both expose the plate in the ``mispar_rechev`` field. A given car appears in
exactly one of them depending on how it was imported, so we check both.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://data.gov.il/api/3/action/datastore_search"

RESOURCES: dict[str, str] = {
    "vehicles": "053cea08-09bc-40ec-8f7a-156f0677aff3",
    "vehicles_personal_import": "03adc637-b6fe-402b-9937-7c3d3afc9140",
}

SOURCE_LABELS = {
    "vehicles": "מאגר רכב פרטי/מסחרי",
    "vehicles_personal_import": "מאגר יבוא אישי",
}

# Hebrew labels for the fields we care about (union of both datasets).
FIELD_LABELS: dict[str, str] = {
    "mispar_rechev": "מספר רכב",
    "tozeret_nm": "יצרן",
    "kinuy_mishari": "דגם מסחרי",
    "degem_nm": "דגם",
    "ramat_gimur": "רמת גימור",
    "shnat_yitzur": "שנת ייצור",
    "degem_manoa": "דגם מנוע",
    "nefah_manoa": "נפח מנוע",
    "sug_delek_nm": "סוג דלק",
    "tzeva_rechev": "צבע",
    "tzeva": "צבע",
    "misgeret": "מספר שלדה",
    "tokef_dt": "תוקף רישיון",
    "mivchan_acharon_dt": "טסט אחרון",
    "baalut": "בעלות",
    "moed_aliya_lakvish": "עלייה לכביש",
    "zmig_kidmi": "צמיג קדמי",
    "zmig_ahori": "צמיג אחורי",
}


def normalize_plate(raw: str) -> str:
    """Keep digits only (users may type 12-345-67 or with spaces)."""
    return "".join(ch for ch in str(raw) if ch.isdigit())


def _pick_match(records: list[dict[str, Any]], plate: str) -> Optional[dict[str, Any]]:
    for rec in records:
        if normalize_plate(rec.get("mispar_rechev", "")) == plate:
            return rec
    return records[0] if records else None


async def _query_resource(
    client: httpx.AsyncClient, resource_id: str, plate: str
) -> Optional[dict[str, Any]]:
    params = {"resource_id": resource_id, "q": plate, "limit": 5}
    resp = await client.get(API_URL, params=params)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return None
    records = data.get("result", {}).get("records", [])
    return _pick_match(records, plate)


async def lookup_plate(plate: str, timeout: float = 20.0) -> dict[str, Any]:
    """Query both registries for a plate.

    Returns ``{source: {"found": bool, "record": dict|None, "error": str|None}}``.
    """
    plate = normalize_plate(plate)
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for source, resource_id in RESOURCES.items():
            try:
                record = await _query_resource(client, resource_id, plate)
                out[source] = {"found": record is not None, "record": record, "error": None}
            except Exception as exc:  # noqa: BLE001 — surface the reason, keep going
                logger.warning("Lookup failed for %s: %s", source, exc)
                out[source] = {"found": False, "record": None, "error": str(exc)}
    return out


def record_to_vehicle_fields(record: dict[str, Any]) -> dict[str, str]:
    """Map a registry record to our vehicle columns."""
    def g(*keys: str) -> str:
        for k in keys:
            v = record.get(k)
            if v not in (None, ""):
                return str(v).strip()
        return ""

    make = g("tozeret_nm")
    model = g("kinuy_mishari", "degem_nm")
    year = g("shnat_yitzur")
    plate = normalize_plate(g("mispar_rechev"))
    engine = g("degem_manoa", "nefah_manoa")
    color = g("tzeva_rechev", "tzeva")
    fuel = g("sug_delek_nm")

    name_parts = [p for p in (make, model, year) if p]
    name = " ".join(name_parts) if name_parts else f"רכב {plate}"
    notes_parts = [p for p in (f"צבע: {color}" if color else "", f"דלק: {fuel}" if fuel else "") if p]

    return {
        "name": name,
        "make": make,
        "model": model,
        "year": year,
        "engine": engine,
        "plate": plate,
        "vin": g("misgeret"),
        "notes": " | ".join(notes_parts),
    }


def format_record(record: dict[str, Any]) -> str:
    """Human-readable summary of a registry record (known fields first)."""
    lines = []
    for key, label in FIELD_LABELS.items():
        val = record.get(key)
        if val not in (None, ""):
            lines.append(f"• {label}: {val}")
    return "\n".join(lines) if lines else "(אין שדות מוכרים בתשובה)"

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BRT = timezone(timedelta(hours=-3))

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "cambio_cache.json"
HISTORICO_PATH = ROOT / "historico.csv"

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CNY", "AUD", "CAD", "CHF", "HKD", "SGD", "AED", "ZAR"]
FIELDNAMES = (
    ["data", "hora"]
    + [f"{c}_mercado" for c in CURRENCIES]
    + [f"{c}_klas" for c in CURRENCIES]
    + [f"{c}_spread" for c in CURRENCIES]
)


def main() -> None:
    if not CACHE_PATH.exists():
        print("Cache não encontrado.")
        return

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    if cache.get("error"):
        print(f"Cache com erro, linha não gravada: {cache['error']}")
        return

    updated_at = cache.get("fetched_at") or cache.get("updated_at") or cache.get("fetched_on")
    if not updated_at:
        print("Cache sem data de atualização, linha não gravada.")
        return

    try:
        dt = datetime.fromisoformat(updated_at)
        if dt.tzinfo is not None:
            dt = dt.astimezone(BRT)
        else:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(BRT)
    except ValueError:
        dt = datetime.now(BRT)

    rates_by_code = {r["code"]: r for r in cache.get("rates", [])}
    if not any(rates_by_code.get(c, {}).get("market_brl") for c in CURRENCIES):
        print("Cache sem nenhuma cotação, linha não gravada.")
        return

    row: dict[str, str] = {
        "data": dt.strftime("%d/%m/%Y"),
        "hora": dt.strftime("%H:%M"),
    }
    for code in CURRENCIES:
        r = rates_by_code.get(code, {})
        row[f"{code}_mercado"] = str(r.get("market_brl") or "")
        row[f"{code}_klas"]    = str(r.get("rate") or "")
        row[f"{code}_spread"]  = str(r.get("spread") or "")

    tem_cabecalho = False
    if HISTORICO_PATH.exists():
        with HISTORICO_PATH.open(newline="", encoding="utf-8-sig") as f:
            tem_cabecalho = (f.readline().strip().split(",")[:2] == ["data", "hora"])

    # Mesma consulta já gravada (execução que não chamou a API) — não duplica
    if tem_cabecalho:
        with HISTORICO_PATH.open(newline="", encoding="utf-8-sig") as f:
            last = None
            for last in csv.DictReader(f):
                pass
        if last and last.get("data") == row["data"] and last.get("hora") == row["hora"]:
            print(f"Consulta de {row['data']} {row['hora']} já está no histórico, linha não gravada.")
            return

    with HISTORICO_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not tem_cabecalho:
            writer.writeheader()
        writer.writerow(row)

    print(f"Linha gravada: {row['data']} {row['hora']}")


if __name__ == "__main__":
    main()

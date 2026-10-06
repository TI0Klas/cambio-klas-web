"""
Confere o cambio_cache.json recém-publicado contra a PTAX (BCB) e o mercado em tempo real (AwesomeAPI).
Não bloqueia nada: só informa. Código de saída 0 = ok, 1 = alerta, 2 = falha ao consultar as fontes,
3 = inconclusivo (nenhum teste de moeda pôde rodar).

Testes por execução:
  1. Frescor      - a ExchangeRate-API atualizou há menos de LIMITE_IDADE_H horas
                    (mede se a API está viva, não se o dado está certo)
  4. Cruzado      - EUR/USD do cache bate com o mercado (se bate e o BRL não, o erro é do real)
Testes por moeda:
  2. PTAX         - diferença para o último boletim PTAX de hoje até LIMITE_PTAX
  3. Tempo real   - diferença para a AwesomeAPI (média compra/venda) até LIMITE_REALTIME;
                    print com mais de IDADE_MERCADO_MIN minutos não conta e vira alerta
  5. Dia anterior - variação do cache ou do mercado contra a PTAX de fechamento do dia útil anterior
                    acima de LIMITE_VAR_DIA, ou as duas variações separadas por mais de LIMITE_REALTIME
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "cambio_cache.json"

MOEDAS = [m.strip().upper() for m in os.getenv("CAMBIO_CONFERIR_MOEDAS", "USD,EUR,GBP,CAD,JPY,AUD,CHF").split(",") if m.strip()]

BRT = timezone(timedelta(hours=-3))

LIMITE_IDADE_H = 2.0
LIMITE_PTAX = 0.01
LIMITE_REALTIME = 0.015   # com 1% foram 8 falsos em 80 dias (desvio do teste 3 é 0,5%, 1% = 2 sigma)
LIMITE_CRUZADO = 0.005
LIMITE_VAR_DIA = 0.02     # teste 5: movimento de um dia útil para o outro
IDADE_MERCADO_MIN = 30.0  # minutos: print da AwesomeAPI mais velho que isso não serve de referência

URL_AWESOME = "https://economia.awesomeapi.com.br/json/last/{pares}"
URL_PTAX = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoMoedaDia(moeda=@moeda,dataCotacao=@dataCotacao)"
    "?@moeda='{moeda}'&@dataCotacao='{data}'&$format=json"
)


def _get_json(url: str):
    with urlopen(Request(url, headers={"User-Agent": "cambio-web-conferencia/1.0"}), timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _boletins(moeda: str, dia: date) -> list[dict]:
    return _get_json(URL_PTAX.format(moeda=moeda, data=dia.strftime("%m-%d-%Y"))).get("value", [])


def _ptax_hoje(moeda: str) -> dict | None:
    bol = _boletins(moeda, date.today())
    if not bol:
        return None
    b = bol[-1]
    return {"venda": b["cotacaoVenda"], "tipo": b["tipoBoletim"], "hora": b["dataHoraCotacao"][11:16]}


def _ptax_fechamento_anterior(moeda: str) -> dict | None:
    dia = date.today()
    for _ in range(10):
        dia -= timedelta(days=1)
        fech = [b for b in _boletins(moeda, dia) if b["tipoBoletim"].startswith("Fechamento")]
        if fech:
            return {"venda": fech[-1]["cotacaoVenda"], "dia": dia}
    return None


def _parse_data(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return parsedate_to_datetime(valor)  # "Mon, 05 Oct 2026 18:00:01 +0000"
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(valor)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dif(a: float, b: float) -> float:
    return a / b - 1


def _idade_mercado_min(a: dict) -> float | None:
    """Idade, em minutos, do print da AwesomeAPI. None se não der para ler."""
    try:
        t = datetime.strptime(a["create_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT)
    except (KeyError, TypeError, ValueError):
        return None
    return (datetime.now(BRT) - t).total_seconds() / 60


def main() -> int:
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if cache.get("error"):
        print(f"Cache com erro, nada a conferir: {cache['error']}")
        return 2
    mercado = {r["code"]: r.get("market_brl") for r in cache.get("rates", [])}

    try:
        pares = ",".join([f"{m}-BRL" for m in MOEDAS] + ["EUR-USD"])
        aw = _get_json(URL_AWESOME.format(pares=pares))
        ptax = {m: _ptax_hoje(m) for m in MOEDAS}
        ptax_ant = {m: _ptax_fechamento_anterior(m) for m in MOEDAS}
    except Exception as exc:  # rede, HTTP, JSON
        print(f"Falha ao consultar as fontes de conferência: {exc}")
        return 2

    linhas: list[str] = []
    alertas: list[str] = []

    # Teste 1 - frescor
    atualizado = _parse_data(cache.get("updated_at"))
    if atualizado:
        idade_h = (datetime.now(timezone.utc) - atualizado).total_seconds() / 3600
        t1 = idade_h <= LIMITE_IDADE_H
        if not t1:
            alertas.append(f"1. ExchangeRate-API sem atualizar há {idade_h:.1f} h")
        linhas.append(f"Teste 1 (frescor): API atualizada em {atualizado:%d/%m %H:%M} UTC, há {idade_h:.1f} h - {'ok' if t1 else 'ALERTA'}")
    else:
        alertas.append("1. cache sem updated_at: não foi possível medir o frescor da API")
        linhas.append("Teste 1 (frescor): cache sem updated_at - ALERTA")

    # Teste 4 - par cruzado sem real
    t4 = True
    if mercado.get("EUR") and mercado.get("USD") and "EURUSD" in aw:
        eurusd_cache = mercado["EUR"] / mercado["USD"]
        eurusd_aw = (float(aw["EURUSD"]["bid"]) + float(aw["EURUSD"]["ask"])) / 2
        d4 = _dif(eurusd_cache, eurusd_aw)
        t4 = abs(d4) <= LIMITE_CRUZADO
        if not t4:
            alertas.append(f"4. EUR/USD do cache {d4:+.2%} fora do mercado: a API inteira está desalinhada")
        linhas.append(f"Teste 4 (EUR/USD): cache {eurusd_cache:.4f} x mercado {eurusd_aw:.4f} ({d4:+.2%}) - {'ok' if t4 else 'ALERTA'}")

    linhas.append("")
    linhas.append("| Moeda | Cache | PTAX hoje | dif | Tempo real | dif | PTAX D-1 | var cache | var mercado | Resultado |")
    linhas.append("|---|---|---|---|---|---|---|---|---|---|")

    brl_errado: list[str] = []
    testes_rodados = 0
    for m in MOEDAS:
        api = mercado.get(m)
        if not api or f"{m}BRL" not in aw:
            linhas.append(f"| {m} | sem dado | | | | | | | | - |")
            continue
        a = aw[f"{m}BRL"]
        rt = (float(a["bid"]) + float(a["ask"])) / 2
        idade_rt = _idade_mercado_min(a)
        rt_serve = idade_rt is not None and idade_rt <= IDADE_MERCADO_MIN
        if not rt_serve:
            idade_txt = "sem data" if idade_rt is None else f"{round(idade_rt)} min"
            alertas.append(f"{m}: print do mercado com {idade_txt} - teste 3 não pode ser aplicado")
        p, pa = ptax[m], ptax_ant[m]
        falhas: list[str] = []

        if p:
            d2 = _dif(api, p["venda"])
            testes_rodados += 1
            if abs(d2) > LIMITE_PTAX:
                falhas.append("2")
            p_txt, d2_txt = f"{p['venda']:.4f} ({p['tipo'][:5]} {p['hora']})", f"{d2:+.2%}"
        else:
            p_txt, d2_txt = "sem boletim", "-"

        d3 = _dif(api, rt)
        if rt_serve:
            testes_rodados += 1
            if abs(d3) > LIMITE_REALTIME:
                falhas.append("3")

        if pa:
            var_api, var_merc = _dif(api, pa["venda"]), _dif(rt, pa["venda"])
            if (abs(var_api) > LIMITE_VAR_DIA or abs(var_merc) > LIMITE_VAR_DIA
                    or abs(var_api - var_merc) > LIMITE_REALTIME):
                falhas.append("5")
            pa_txt, va_txt, vm_txt = f"{pa['venda']:.4f}", f"{var_api:+.2%}", f"{var_merc:+.2%}"
        else:
            pa_txt = va_txt = vm_txt = "-"

        if falhas:
            alertas.append(f"{m}: falhou nos testes {', '.join(falhas)}")
            if t4 and ("2" in falhas or "3" in falhas):
                brl_errado.append(m)

        resultado = "ok" if not falhas else f"**ALERTA ({','.join(falhas)})**"
        linhas.append(f"| {m} | {api:.4f} | {p_txt} | {d2_txt} | {rt:.4f} | {d3:+.2%} | {pa_txt} | {va_txt} | {vm_txt} | {resultado} |")

    linhas.append("")
    if brl_errado:
        linhas.append(f"Diagnóstico: EUR/USD bate e {', '.join(brl_errado)}/BRL não. O erro está no dado do real na API.")
    if alertas:
        linhas.append("**ALERTA: o câmbio publicado não bate com a referência. Conferir antes de usar.**")
        linhas.extend(f"- {a}" for a in alertas)
    elif testes_rodados == 0:
        linhas.append("**INCONCLUSIVO: nenhuma moeda pôde ser conferida. Isto não é um 'ok'.**")
    else:
        linhas.append(f"Conferência ok em {testes_rodados} teste(s) de moeda: "
                      f"o câmbio publicado bate com a PTAX e com o mercado.")

    texto = "\n".join(linhas)
    print(texto)
    resumo = os.getenv("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(f"## Conferência do câmbio - {datetime.now(BRT):%d/%m/%Y %H:%M} BRT\n\n{texto}\n")
    if alertas:
        return 1
    return 3 if testes_rodados == 0 else 0


if __name__ == "__main__":
    sys.exit(main())

"""
Confere o cambio_cache.json recém-publicado contra a PTAX (BCB) e o mercado em tempo real (AwesomeAPI).
Não bloqueia nada: só informa. Código de saída 0 = ok, 1 = alerta, 2 = fonte fora sem nenhum teste de moeda
(as duas fontes, ou a que sobrou não tinha dado), 3 = inconclusivo (fontes no ar e nenhum teste de moeda rodou).
Cada fonte é consultada à parte, com nova tentativa em 429/5xx/rede: se uma cair, os testes rodam com a outra
e a fonte que caiu sai como aviso.

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
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
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
PAUSAS_S = (5, 15)        # novas tentativas em 429/5xx/rede; Retry-After do servidor vale até 30 s

URL_AWESOME = "https://economia.awesomeapi.com.br/json/last/{pares}"
URL_PTAX = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoMoedaDia(moeda=@moeda,dataCotacao=@dataCotacao)"
    "?@moeda='{moeda}'&@dataCotacao='{data}'&$format=json"
)


def _get_json(url: str):
    for tentativa in range(len(PAUSAS_S) + 1):
        try:
            with urlopen(Request(url, headers={"User-Agent": "cambio-web-conferencia/1.0"}), timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            temporario = not isinstance(exc, HTTPError) or exc.code == 429 or exc.code >= 500
            if not temporario or tentativa == len(PAUSAS_S):
                raise
            pausa = PAUSAS_S[tentativa]
            espera = exc.headers.get("Retry-After", "") if isinstance(exc, HTTPError) else ""
            if espera.isdigit():
                pausa = min(int(espera), 30)
            time.sleep(pausa)


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

    fontes_fora: list[str] = []
    aw_ok = ptax_ok = True
    try:
        pares = ",".join([f"{m}-BRL" for m in MOEDAS] + ["EUR-USD"])
        aw = _get_json(URL_AWESOME.format(pares=pares))
    except Exception as exc:  # rede, HTTP, JSON
        aw, aw_ok = {}, False
        fontes_fora.append(f"AwesomeAPI (mercado em tempo real): {exc}")
    try:
        ptax = {m: _ptax_hoje(m) for m in MOEDAS}
        ptax_ant = {m: _ptax_fechamento_anterior(m) for m in MOEDAS}
    except Exception as exc:
        ptax = ptax_ant = dict.fromkeys(MOEDAS)
        ptax_ok = False
        fontes_fora.append(f"PTAX (BCB): {exc}")
    for f in fontes_fora:
        print(f"::warning::Fonte de conferência fora - {f}")
    if len(fontes_fora) == 2:
        print("Falha ao consultar as duas fontes de conferência:\n- " + "\n- ".join(fontes_fora))
        return 2

    linhas: list[str] = []
    alertas: list[str] = []
    if fontes_fora:
        linhas.append(f"**Conferência parcial.** Fonte fora: {fontes_fora[0]}")
        if not aw_ok:
            linhas.append("Sem o mercado em tempo real, os testes 3 e 4 não rodam e o teste 5 só olha o cache; "
                          "a referência fica sendo a PTAX do dia (teste 2), que só existe depois do 1º boletim (~10h).")
        linhas.append("")

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
    t4 = None  # None = não rodou; o diagnóstico "erro no real" só sai com o teste 4 ok
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
        p, pa = ptax[m], ptax_ant[m]
        if not api or (f"{m}BRL" not in aw and not p and not pa):
            linhas.append(f"| {m} | sem dado | | | | | | | | - |")
            continue
        rt = rt_serve = None
        if f"{m}BRL" in aw:
            a = aw[f"{m}BRL"]
            rt = (float(a["bid"]) + float(a["ask"])) / 2
            idade_rt = _idade_mercado_min(a)
            rt_serve = idade_rt is not None and idade_rt <= IDADE_MERCADO_MIN
            if not rt_serve:
                idade_txt = "sem data" if idade_rt is None else f"{round(idade_rt)} min"
                alertas.append(f"{m}: print do mercado com {idade_txt} - teste 3 não pode ser aplicado")
        falhas: list[str] = []
        testes_antes = testes_rodados

        if p:
            d2 = _dif(api, p["venda"])
            testes_rodados += 1
            if abs(d2) > LIMITE_PTAX:
                falhas.append("2")
            p_txt, d2_txt = f"{p['venda']:.4f} ({p['tipo'][:5]} {p['hora']})", f"{d2:+.2%}"
        else:
            p_txt, d2_txt = "sem boletim", "-"

        if rt is not None:
            d3 = _dif(api, rt)
            if rt_serve:
                testes_rodados += 1
                if abs(d3) > LIMITE_REALTIME:
                    falhas.append("3")
            rt_txt, d3_txt = f"{rt:.4f}", f"{d3:+.2%}"
        else:
            rt_txt = d3_txt = "-"

        if pa:
            var_api = _dif(api, pa["venda"])
            va_txt, vm_txt = f"{var_api:+.2%}", "-"
            if rt is not None:
                var_merc = _dif(rt, pa["venda"])
                vm_txt = f"{var_merc:+.2%}"
                if (abs(var_api) > LIMITE_VAR_DIA or abs(var_merc) > LIMITE_VAR_DIA
                        or abs(var_api - var_merc) > LIMITE_REALTIME):
                    falhas.append("5")
            elif abs(var_api) > LIMITE_VAR_DIA:  # sem mercado: só o movimento do cache
                falhas.append("5")
            pa_txt = f"{pa['venda']:.4f}"
        else:
            pa_txt = va_txt = vm_txt = "-"

        if falhas:
            alertas.append(f"{m}: falhou nos testes {', '.join(falhas)}")
            if t4 and ("2" in falhas or "3" in falhas):
                brl_errado.append(m)

        if falhas:
            resultado = f"**ALERTA ({','.join(falhas)})**"
        else:
            resultado = "ok" if testes_rodados > testes_antes else "não conferido"
        linhas.append(f"| {m} | {api:.4f} | {p_txt} | {d2_txt} | {rt_txt} | {d3_txt} | {pa_txt} | {va_txt} | {vm_txt} | {resultado} |")

    linhas.append("")
    if brl_errado:
        linhas.append(f"Diagnóstico: EUR/USD bate e {', '.join(brl_errado)}/BRL não. O erro está no dado do real na API.")
    if alertas:
        linhas.append("**ALERTA: o câmbio publicado não bate com a referência. Conferir antes de usar.**")
        linhas.extend(f"- {a}" for a in alertas)
    elif testes_rodados == 0:
        linhas.append("**INCONCLUSIVO: nenhuma moeda pôde ser conferida. Isto não é um 'ok'.**")
    else:
        refs = " e ".join(r for r, ok in (("a PTAX", ptax_ok), ("o mercado", aw_ok)) if ok)
        linhas.append(f"Conferência ok em {testes_rodados} teste(s) de moeda: "
                      f"o câmbio publicado bate com {refs}.")

    texto = "\n".join(linhas)
    print(texto)
    resumo = os.getenv("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(f"## Conferência do câmbio - {datetime.now(BRT):%d/%m/%Y %H:%M} BRT\n\n{texto}\n")
    if alertas:
        return 1
    if testes_rodados == 0:
        return 2 if fontes_fora else 3  # nada conferido por causa da fonte fora = aviso, não vermelho
    return 0


if __name__ == "__main__":
    sys.exit(main())

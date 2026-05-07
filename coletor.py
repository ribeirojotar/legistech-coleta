"""
LegisTech Intelligence — Coletor PNCP
Busca licitações na API pública do PNCP e salva no Supabase.

Uso:
  python coletor.py                        # coleta PI, hoje
  python coletor.py --ufs PI MA --dias 7   # últimos 7 dias, PI e MA
  python coletor.py --ufs PI --dias 1      # só hoje, só PI
"""

import os
import time
import argparse
import logging
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

# ─── Carregar .env com override (garante que mudanças no .env são lidas) ──────
load_dotenv(find_dotenv(), override=True)

url: str = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

if not url or not key:
    raise ValueError(f"Credenciais não encontradas. URL: {bool(url)}, KEY: {bool(key)}")

supabase: Client = create_client(url, key)

# ─── Configurações ────────────────────────────────────────────────────────────

PNCP_URL    = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
PAGE_SIZE   = 50
TIMEOUT     = 30   # segundos por requisição (era 90, aumentamos)
MAX_RETRY   = 2     # tentativas antes de desistir de uma página
SLEEP_RETRY = 20    # segundos entre tentativas após falha
SLEEP_PAGE  = 1.5   # segundos entre páginas (respeita o servidor)

# Modalidades a coletar
# 6 = Dispensa | 8 = Pregão Eletrônico | 1 = Concorrência
MODALIDADES = [8, 1, 6]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("legistech.coletor")


# ─── Funções ──────────────────────────────────────────────────────────────────

def extrair_uf(item: dict) -> str:
    """Extrai a UF com 3 níveis de fallback (corrigido)."""
    unidade = item.get("unidadeOrgao")
    if unidade and isinstance(unidade, dict):
        uf = unidade.get("ufSigla") or unidade.get("ufNome")
        if uf:
            return uf.strip().upper()[:2]

    orgao = item.get("orgaoEntidade") or {}
    municipio = orgao.get("municipio") or {}
    uf = municipio.get("uf") or municipio.get("ufSigla")
    if uf:
        return uf.strip().upper()[:2]

    uf = item.get("uf") or item.get("ufSigla")
    if uf:
        return uf.strip().upper()[:2]

    return "PI"  # fallback final


def buscar_pagina_com_retry(uf: str, data_alvo: str, modalidade: int, pagina: int) -> dict:
    """
    Busca uma página da API do PNCP.
    Tenta até MAX_RETRY vezes antes de desistir.
    """
    params = {
        "dataInicial": data_alvo,
        "dataFinal":   data_alvo,
        "codigoModalidadeContratacao": str(modalidade),
        "uf":          uf,
        "pagina":      pagina,
        "tamanhoPagina": PAGE_SIZE,
    }

    for tentativa in range(1, MAX_RETRY + 1):
        try:
            log.info(f"  [{uf}] Modalidade {modalidade} | Página {pagina} | Tentativa {tentativa}/{MAX_RETRY}")
            response = requests.get(
                PNCP_URL,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            if response.status_code == 200:
                return response.json()

            elif response.status_code in (400, 204):
                # 400 ou 204 = sem dados para essa combinação — não é erro
                log.info(f"  [{uf}] Sem dados para modalidade {modalidade} em {data_alvo}.")
                return {}

            elif response.status_code == 429:
                # Rate limit — esperar mais
                log.warning(f"  Rate limit atingido. Aguardando 60s...")
                time.sleep(60)

            else:
                log.warning(f"  Status inesperado: {response.status_code}")

        except requests.exceptions.Timeout:
            log.warning(f"  Timeout na tentativa {tentativa}. Aguardando {SLEEP_RETRY}s...")
            if tentativa < MAX_RETRY:
                time.sleep(SLEEP_RETRY)

        except requests.exceptions.ConnectionError:
            log.warning(f"  Sem conexão na tentativa {tentativa}. Aguardando {SLEEP_RETRY}s...")
            if tentativa < MAX_RETRY:
                time.sleep(SLEEP_RETRY)

        except Exception as e:
            log.error(f"  Erro inesperado: {e}")
            break

    log.error(f"  [{uf}] Desistindo após {MAX_RETRY} tentativas.")
    return {}


def salvar_no_supabase(lista: list) -> tuple[int, int]:
    """
    Insere licitações no Supabase.
    Usa upsert para evitar duplicatas (baseado em link_pncp como chave).
    Retorna (salvos, erros).
    """
    if not lista:
        return 0, 0

    salvos = 0
    erros  = 0

    for lic in lista:
        item = {
            "orgao_nome":      lic.get("orgaoEntidade", {}).get("razaoSocial"),
            "objeto":          lic.get("objetoCompra"),
            "data_publicacao": lic.get("dataPublicacaoPncp", "").split("T")[0] or None,
            "uf":              extrair_uf(lic),
            "link_pncp":       lic.get("linkSistemaOrigem"),
        }

        # Ignorar registros sem link (não conseguimos identificar duplicatas)
        if not item["link_pncp"]:
            continue

        try:
            # upsert: insere se não existe, ignora se já existe (pelo link_pncp)
            supabase.table("licitacoes_pncp").upsert(
                item,
                on_conflict="link_pncp"
            ).execute()
            salvos += 1
        except Exception as e:
            log.warning(f"  Erro ao salvar: {e}")
            erros += 1

    return salvos, erros


def coletar_uf_data(uf: str, data_alvo: str):
    """Coleta todas as modalidades e páginas para uma UF e data."""
    total_salvos = 0

    for modalidade in MODALIDADES:
        pagina = 1

        while True:
            dados = buscar_pagina_com_retry(uf, data_alvo, modalidade, pagina)

            if not dados:
                break

            lista = dados.get("data", [])
            if not lista:
                break

            salvos, erros = salvar_no_supabase(lista)
            total_salvos += salvos
            log.info(f"  [{uf}] Modalidade {modalidade} | Pág {pagina}: {len(lista)} encontrados, {salvos} salvos, {erros} erros")

            # Verificar se há mais páginas
            total_paginas = dados.get("totalPaginas", 1)
            if pagina >= total_paginas:
                break

            pagina += 1
            time.sleep(SLEEP_PAGE)

    return total_salvos


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LegisTech — Coletor PNCP")
    parser.add_argument(
        "--ufs",
        nargs="+",
        default=["PI"],
        help="Siglas dos estados (padrão: PI)"
    )
    parser.add_argument(
        "--dias",
        type=int,
        default=1,
        help="Quantos dias passados buscar (padrão: 1 = só hoje)"
    )
    args = parser.parse_args()

    hoje   = datetime.now(timezone.utc).date()
    datas  = [
        (hoje - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(args.dias - 1, -1, -1)
    ]

    log.info("=" * 55)
    log.info(f"LegisTech — Coleta PNCP")
    log.info(f"UFs    : {', '.join(args.ufs)}")
    log.info(f"Datas  : {datas[0]} a {datas[-1]} ({len(datas)} dia(s))")
    log.info(f"Modals : {MODALIDADES}")
    log.info("=" * 55)

    total_geral = 0

    for uf in args.ufs:
        for data in datas:
            log.info(f"[{uf}] Coletando {data}...")
            salvos = coletar_uf_data(uf.upper(), data)
            total_geral += salvos
            time.sleep(SLEEP_PAGE)

    log.info("=" * 55)
    log.info(f"COLETA CONCLUÍDA — {total_geral} registros salvos no Supabase")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
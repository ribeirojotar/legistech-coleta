"""
LegisTech Intelligence — Motor de Análise Semântica (IA)
Refina os matches criados pelo motor_matching.py com scoring via Gemini.
Fluxo: consulta matches sem resumo_ia → chama Gemini → atualiza ou remove o match.
"""

import os
import time
import logging
import argparse
from google import genai
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

load_dotenv(find_dotenv(), override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("legistech.ia")

GEMINI_KEY   = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Credenciais Supabase não encontradas.")

gemini: genai.Client = genai.Client(api_key=GEMINI_KEY)
supabase: Client     = create_client(SUPABASE_URL, SUPABASE_KEY)

SCORE_MINIMO_IA      = 70
SLEEP_ENTRE_REQUESTS = 13   # respeita rate limit do Gemini free tier
SLEEP_COTA           = 60   # espera ao atingir cota
MAX_RETRY_COTA       = 2


def analisar_licitacao_com_ia(objeto: str, perfil_nome: str, palavras_chave: list):
    prompt = f"""
    Voce e um consultor especialista em Licitacoes Publicas (Lei 14.133/2021).
    Avalie a seguinte licitacao para a empresa '{perfil_nome}', cujo foco principal e: {', '.join(palavras_chave)}.

    Objeto do Edital: "{objeto}"

    Responda EXATAMENTE neste formato de duas linhas:
    SCORE: [nota de 0 a 100]
    RESUMO: [Um resumo executivo de 1 frase]
    """
    try:
        response = gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        if not response.text:
            return 0, "IA bloqueou a resposta."

        linhas = response.text.strip().split("\n")
        score_val  = int(linhas[0].upper().replace("SCORE:", "").strip())
        resumo_val = linhas[1].upper().replace("RESUMO:", "").strip()
        return score_val, resumo_val

    except Exception as e:
        err = str(e)
        if "429" in err:
            return "COTA", "Cota Gemini atingida."
        if "503" in err or "502" in err or "UNAVAILABLE" in err:
            return "ERRO_TEMP", "Serviço temporariamente indisponível."
        log.warning(f"Erro ao chamar Gemini: {e}")
        return 0, "Erro ao processar item."


def executar_analise(max_matches: int = 0):
    log.info("=== Motor de Análise Semântica (IA) ===")

    try:
        q = (
            supabase.table("matches")
            .select("id, perfil_id, licitacao_id")
            .filter("resumo_ia", "is", "null")
            .order("id", desc=True)  # prioriza matches mais recentes
        )
        if max_matches > 0:
            q = q.limit(max_matches)
        matches = q.execute().data or []
    except Exception as e:
        log.error(f"Erro ao buscar matches pendentes: {e}")
        return

    if not matches:
        log.info("Nenhum match pendente para análise de IA.")
        return

    log.info(f"{len(matches)} match(es) para analisar.")

    # Pré-carrega licitações e perfis em dicionários para evitar N+1 queries
    ids_lics   = list({m["licitacao_id"] for m in matches})
    ids_perfis = list({m["perfil_id"]    for m in matches})

    try:
        licitacoes = {
            l["id"]: l
            for l in supabase.table("licitacoes_pncp")
            .select("id, objeto, orgao_nome")
            .in_("id", ids_lics)
            .execute()
            .data
        }
        perfis = {
            p["id"]: p
            for p in supabase.table("perfis_empresa")
            .select("id, nome, palavras_chave")
            .in_("id", ids_perfis)
            .execute()
            .data
        }
    except Exception as e:
        log.error(f"Erro ao pré-carregar dados relacionados: {e}")
        return

    for i, match in enumerate(matches, 1):
        lic    = licitacoes.get(match["licitacao_id"])
        perfil = perfis.get(match["perfil_id"])

        if not lic or not perfil:
            log.warning(f"Dados incompletos para match {match['id']}, pulando.")
            continue

        log.info(f"[{i}/{len(matches)}] {perfil['nome'][:30]} — {(lic.get('orgao_nome') or '')[:30]}")

        # Chama Gemini com retry automático em caso de cota atingida
        cota_tentativas = 0
        while True:
            score, resumo = analisar_licitacao_com_ia(
                lic["objeto"],
                perfil["nome"],
                perfil["palavras_chave"],
            )
            if score == "ERRO_TEMP":
                log.warning(f"  Erro temporário Gemini. Pulando match {match['id']} (não deletado).")
                score = None
                break
            if score != "COTA":
                break
            cota_tentativas += 1
            if cota_tentativas >= MAX_RETRY_COTA:
                log.error("Cota Gemini esgotada após retries. Encerrando análise.")
                return
            log.warning(f"Cota atingida. Aguardando {SLEEP_COTA}s (retry {cota_tentativas}/{MAX_RETRY_COTA})...")
            time.sleep(SLEEP_COTA)

        if score is None:
            if i < len(matches):
                time.sleep(SLEEP_ENTRE_REQUESTS)
            continue

        log.info(f"  Score: {score} | {str(resumo)[:70]}")

        try:
            if score >= SCORE_MINIMO_IA:
                # Atualiza o match existente com score e resumo da IA
                supabase.table("matches").update({
                    "score_calculado": score,
                    "resumo_ia":       resumo,
                }).eq("id", match["id"]).execute()
                log.info(f"  ✓ Match {match['id']} atualizado (score {score}).")
            else:
                # Score abaixo do mínimo — remove o match para não poluir a fila
                supabase.table("matches").delete().eq("id", match["id"]).execute()
                log.info(f"  Score {score} < {SCORE_MINIMO_IA}. Match {match['id']} removido.")
        except Exception as e:
            log.error(f"  Erro ao atualizar match {match['id']}: {e}")

        if i < len(matches):
            time.sleep(SLEEP_ENTRE_REQUESTS)

    log.info("Análise IA concluída.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=0,
                        help="Máximo de matches a processar por execução (0 = sem limite)")
    args = parser.parse_args()
    executar_analise(max_matches=args.max)

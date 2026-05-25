import os
import logging
import unicodedata
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

load_dotenv(find_dotenv(), override=True)

url: str = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

if not url or not key:
    raise ValueError(f"Credenciais não encontradas. URL: {bool(url)}, KEY: {bool(key)}")

supabase: Client = create_client(url, key)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("legistech.matching")


def remover_acentos(texto):
    if not texto:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower().strip()


def executar_matching():
    log.info("=== Motor de Matching LegisTech ===")

    try:
        perfis = supabase.table("perfis_empresa").select("*").execute().data or []
        licitacoes = supabase.table("licitacoes_pncp").select("*").execute().data or []
    except Exception as e:
        log.error(f"Erro ao carregar dados do banco: {e}")
        return

    if not perfis or not licitacoes:
        log.warning("Sem perfis ou licitações no banco.")
        return

    log.info(f"{len(perfis)} perfis | {len(licitacoes)} licitações")

    total_matches = 0

    for perfil in perfis:
        log.info(f"Perfil: {perfil['nome']}")
        palavras = [remover_acentos(p) for p in perfil["palavras_chave"]]
        ufs_alvo = perfil["uf_interesse"]
        contagem_perfil = 0

        for lic in licitacoes:
            if lic.get("uf") not in ufs_alvo:
                continue

            objeto_limpo = remover_acentos(lic.get("objeto") or "")

            for p in palavras:
                if p in objeto_limpo:
                    match_data = {
                        "perfil_id": perfil["id"],
                        "licitacao_id": lic["id"],
                        "score_calculado": 100,
                        "notificado": False,
                        "status": "novo",
                    }
                    try:
                        # ignore_duplicates evita reset de matches já processados
                        supabase.table("matches").upsert(
                            match_data,
                            ignore_duplicates=True,
                        ).execute()
                        contagem_perfil += 1
                    except Exception as e:
                        log.warning(f"Erro ao salvar match ({perfil['id']}, {lic['id']}): {e}")
                    break

        log.info(f"  {contagem_perfil} novo(s) match(es).")
        total_matches += contagem_perfil

    log.info(f"Matching concluído — {total_matches} novo(s) match(es) no total.")


if __name__ == "__main__":
    executar_matching()

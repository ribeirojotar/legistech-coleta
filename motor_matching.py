import os
import unicodedata
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client
load_dotenv(find_dotenv(), override=True)

url: str = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

if not url or not key:
    raise ValueError(f"Credenciais não encontradas. URL: {bool(url)}, KEY: {bool(key)}")

supabase: Client = create_client(url, key)

def remover_acentos(texto):
    if not texto: return ""
    return "".join(c for c in unicodedata.normalize('NFD', texto)
                   if unicodedata.category(c) != 'Mn').lower().strip()

def executar_matching():
    print("--- 🤖 Iniciando Motor de Matching LegisTech ---")
    
    perfis = supabase.table("perfis_empresa").select("*").execute().data
    licitacoes = supabase.table("licitacoes_pncp").select("*").execute().data

    if not perfis or not licitacoes:
        print("❌ Sem perfis ou licitações no banco.")
        return

    for perfil in perfis:
        print(f"\n🎯 Perfil: {perfil['nome']}")
        # Normaliza palavras-chave (remove acentos e espaços)
        palavras = [remover_acentos(p) for p in perfil['palavras_chave']]
        ufs_alvo = perfil['uf_interesse']
        
        contagem_perfil = 0

        for lic in licitacoes:
            # Filtro de UF
            if lic['uf'] not in ufs_alvo:
                continue

            objeto_limpo = remover_acentos(lic['objeto'])
            
            # Busca por cada palavra
            for p in palavras:
                if p in objeto_limpo:
                    print(f"   ✅ MATCH! Palavra '{p}' encontrada em: {lic['objeto'][:50]}...")
                    
                    match_data = {
                        "perfil_id": perfil['id'],
                        "licitacao_id": lic['id'],
                        "score_calculado": 100
                    }
                    supabase.table("matches").upsert(match_data).execute()
                    contagem_perfil += 1
                    break # Encontrou uma palavra, pula para a próxima licitação

        print(f"   📊 Total para este cliente: {contagem_perfil} matches.")

if __name__ == "__main__":
    executar_matching()
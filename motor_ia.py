import os
import time
from google import genai
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

load_dotenv(find_dotenv(), override=True)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

url: str = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

if not url or not key:
    raise ValueError(f"Credenciais não encontradas. URL: {bool(url)}, KEY: {bool(key)}")

supabase: Client = create_client(url, key)

url: str = os.environ.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

if not url or not key:
    raise ValueError(f"Credenciais não encontradas. URL: {bool(url)}, KEY: {bool(key)}")

supabase: Client = create_client(url, key)
print("--- 🚀 Iniciando Motor de Analise Semantica (IA) ---")

def analisar_licitacao_com_ia(objeto, perfil_nome, palavras_chave):
    prompt = f"""
    Voce e um consultor especialista em Licitacoes Publicas (Lei 14.133/2021).
    Avalie a seguinte licitacao para a empresa '{perfil_nome}', cujo foco principal e: {', '.join(palavras_chave)}.
    
    Objeto do Edital: "{objeto}"
    
    Responda EXATAMENTE neste formato de duas linhas:
    SCORE: [nota de 0 a 100]
    RESUMO: [Um resumo executivo de 1 frase]
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        if not response.text:
            return 0, "IA BLOQUEOU A RESPOSTA POR SEGURANCA."

        linhas = response.text.strip().split('\n')
        score_val = linhas[0].upper().replace('SCORE:', '').strip()
        resumo_val = linhas[1].upper().replace('RESUMO:', '').strip()

        return int(score_val), resumo_val

    except Exception as e:
        if "429" in str(e):
            return "COTA", "⚠️ COTA ATINGIDA! AGUARDANDO LIBERACAO..."
        else:
            print(f"⚠️ ERRO TECNICO NO ITEM: {e}")
            return 0, "ERRO AO PROCESSAR ITEM."


def salvar_match(perfil_id, licitacao_id, score, resumo):
    try:
        data = {
            "perfil_id": perfil_id,
            "licitacao_id": licitacao_id,
            "score_calculado": score,
            "resumo_ia": resumo,
            "notificado": False
        }
        supabase.table("matches").insert(data).execute()
        print(f"   ✅ Oportunidade de Score {score} salva com sucesso!")
    except Exception as e:
        print(f"   ❌ Erro ao salvar match: {e}")


def executar_analise():
    print("1. Buscando clientes ativos...\n")
    perfis = supabase.table("perfis_empresa").select("*").execute().data

    print("2. Buscando licitacoes pendentes...\n")
    licitacoes = supabase.table("licitacoes_pncp").select("*").limit(5).execute().data

    if not licitacoes:
        print("Nenhuma licitacao nova para analisar.")
        return

    for perfil in perfis:
        print(f"👔 ANALISANDO PARA: {perfil['nome'].upper()}")
        print("-" * 50)

        for lic in licitacoes:
            print(f"Item: {lic['orgao_nome'][:30]}...")

            score, resumo = analisar_licitacao_com_ia(
                lic['objeto'],
                perfil['nome'],
                perfil['palavras_chave']
            )

            if score == "COTA":
                print(f"   ↳ {resumo}")
                return

            print(f"   ↳ 🎯 Score: {score}")
            print(f"   ↳ 📝 Resumo: {resumo}")

            if score >= 70:
                salvar_match(perfil['id'], lic['id'], score, resumo)
            else:
                print("   ℹ️ Score baixo, descartando match.")

            print("   (Aguardando 13s...)")
            time.sleep(13)

        print("\n" + "=" * 50 + "\n")


if __name__ == "__main__":
    executar_analise()
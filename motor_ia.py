import os
import time
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client, Client

# ==========================================
# 1. CONFIGURAÇÕES INICIAIS
# ==========================================
load_dotenv(override=True)

# Usando o modelo que está ATIVO na sua conta (Gemini 2.5 Flash)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('models/gemini-1.5-flash')

# Configuração Supabase
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

print("--- 🚀 Iniciando Motor de Analise Semantica (IA) ---")

# ==========================================
# 2. FUNÇÕES AUXILIARES (AS FERRAMENTAS)
# ==========================================

def analisar_licitacao_com_ia(objeto, perfil_nome, palavras_chave):
    """
    Usa a IA para avaliar a aderência entre o edital e o cliente.
    """
    prompt = f"""
    Voce e um consultor especialista em Licitacoes Publicas (Lei 14.133/2021).
    Avalie a seguinte licitacao para a empresa '{perfil_nome}', cujo foco principal e: {', '.join(palavras_chave)}.
    
    Objeto do Edital: "{objeto}"
    
    Responda EXATAMENTE neste formato de duas linhas:
    SCORE: [nota de 0 a 100]
    RESUMO: [Um resumo executivo de 1 frase]
    """
    
    try:
        response = model.generate_content(prompt)
        
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
    """
    Grava o match encontrado no Supabase com os nomes exatos das colunas.
    """
    try:
        data = {
            "perfil_id": perfil_id,
            "licitacao_id": licitacao_id,
            "score_calculado": score,  # Nome conforme seu print
            "resumo_ia": resumo,       # A coluna que você está criando agora
            "notificado": False        # Coluna que vi no seu banco
        }
        supabase.table("matches").insert(data).execute()
        print(f"   ✅ Oportunidade de Score {score} salva com sucesso!")
    except Exception as e:
        print(f"   ❌ Erro ao salvar match: {e}")

# ==========================================
# 3. ROTINA PRINCIPAL (O FLUXO DE TRABALHO)
# ==========================================

def executar_analise():
    print("1. Buscando clientes ativos...\n")
    resp_perfis = supabase.table("perfis_empresa").select("*").execute()
    perfis = resp_perfis.data
    
    print("2. Buscando licitacoes pendentes...\n")
    # Limitamos a 5 para testar sem estourar a cota diária
    resp_licitacoes = supabase.table("licitacoes_pncp").select("*").limit(5).execute()
    licitacoes = resp_licitacoes.data
    
    if not licitacoes:
        print("Nenhuma licitacao nova para analisar.")
        return

    # Varredura por Cliente
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
            
            # Se for erro de cota, paramos para não queimar requisições à toa
            if score == "COTA":
                print(f"   ↳ {resumo}")
                return 

            print(f"   ↳ 🎯 Score: {score}")
            print(f"   ↳ 📝 Resumo: {resumo}")

            # Salva no banco apenas o que for "filé" (Score 70+)
            if score >= 70:
                salvar_match(perfil['id'], lic['id'], score, resumo)
            else:
                print("   ℹ️ Score baixo, descartando match.")
            
            # Pausa de segurança para a API gratuita
            print("   (Aguardando 13s...)")
            time.sleep(13) 
            
        print("\n" + "="*50 + "\n")

# Disparo do sistema
if __name__ == "__main__":
    executar_analise()
"""
LegisTech Intelligence — Alertas via Telegram

Uso:
  python alertas_telegram.py              # envia alertas pendentes
  python alertas_telegram.py --simular    # mostra mensagens sem enviar
  python alertas_telegram.py --score 75   # só matches com score >= 75
"""

import os
import argparse
import logging
from datetime import datetime

import requests
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv(find_dotenv(), override=True)

SUPABASE_URL  = os.environ.get("SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8662926278:AAEBz-wbtItR_lh109Eqdl6H3AUXLTANH28")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SCORE_MINIMO_ALERTA = 65

# ─── Chat IDs dos clientes ────────────────────────────────────────────────────
# Para adicionar um cliente:
# 1. O cliente manda /start para o bot
# 2. Roda: python -c "import requests; r = requests.get('https://api.telegram.org/botSEU_TOKEN/getUpdates'); print(r.text)"
# 3. Pega o "id" dentro de "chat" e adiciona aqui

CHAT_IDS_CLIENTES = {
    "Engenharia Total":                    "804078121",
    "Gráfica & Brindes":                   "804078121",
    "Soluções em TI":                      "804078121",
    "C K M Distribuidora":                 "804078121",
    "GF INFRAESTRUTURA E PAVIMENTACAO LTDA": "804078121",
}
}

SEU_CHAT_ID = "804078121"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("legistech.telegram")


# ─── Escape MarkdownV2 ────────────────────────────────────────────────────────

def escape_md(texto: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 do Telegram."""
    if not texto:
        return ""
    caracteres = r'_*[]()~`>#+-=|{}.!'
    for c in caracteres:
        texto = texto.replace(c, f'\\{c}')
    return texto


# ─── Formatar mensagem ────────────────────────────────────────────────────────

def formatar_mensagem(cliente: str, matches: list[dict]) -> str:
    hoje  = datetime.now().strftime("%d/%m/%Y")
    total = len(matches)

    linhas = [
        f"🏆 *LegisTech — Oportunidades de {escape_md(hoje)}*",
        f"Encontrei *{total} licitação\\(ões\\)* para {escape_md(cliente)}:",
        "",
    ]

    for i, m in enumerate(matches, 1):
        lic       = m.get("licitacao", {})
        score     = m.get("score_calculado", 0)
        objeto    = (lic.get("objeto") or "")[:100]
        orgao     = lic.get("orgao_nome") or "Órgão não informado"
        uf        = lic.get("uf") or ""
        valor     = lic.get("valor_estimado")
        link      = lic.get("link_pncp") or ""
        resumo    = lic.get("resumo_ia") or ""

        emoji = "🔥" if score >= 85 else "✅" if score >= 70 else "🔎"

        valor_fmt = (
            f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if valor else "Valor não informado"
        )

        linhas += [
            f"*{i}\\. {emoji} Score: {score}/100*",
            f"📋 {escape_md(objeto)}",
            f"🏛️ {escape_md(orgao)} \\({uf}\\)",
            f"💰 {escape_md(valor_fmt)}",
        ]

        if resumo:
            linhas.append(f"💡 _{escape_md(resumo[:120])}_")

        if link:
            linhas.append(f"🔗 [Ver edital]({link})")

        linhas.append("")

    linhas += [
        "─────────────────────",
        "⚠️ _Verifique os prazos com antecedência\\._",
        "_Dúvidas? Fale com sua assessoria LegisTech\\._",
    ]

    return "\n".join(linhas)


# ─── Envio via Telegram ───────────────────────────────────────────────────────

def enviar_telegram(chat_id: str, mensagem: str) -> bool:
    try:
        resp = requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       mensagem,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            log.info(f"  Telegram enviado para chat_id {chat_id}")
            return True
        else:
            log.error(f"  Erro Telegram {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"  Erro de conexão Telegram: {e}")
        return False


# ─── Buscar matches pendentes ─────────────────────────────────────────────────

def buscar_matches_pendentes(supabase: Client, score_minimo: int) -> list[dict]:
    matches = supabase.table("matches").select(
        "id, perfil_id, licitacao_id, score_calculado, notificado"
    ).eq("notificado", False)\
     .gte("score_calculado", score_minimo)\
     .order("score_calculado", desc=True)\
     .execute().data or []

    if not matches:
        return []

    ids_lics   = list({m["licitacao_id"] for m in matches})
    ids_perfis = list({m["perfil_id"]    for m in matches})

    licitacoes = supabase.table("licitacoes_pncp").select(
        "id, objeto, orgao_nome, uf, valor_estimado, link_pncp, resumo_ia"
    ).in_("id", ids_lics).execute().data or []

    perfis = supabase.table("perfis_empresa").select(
        "id, nome"
    ).in_("id", ids_perfis).execute().data or []

    mapa_lics = {l["id"]: l for l in licitacoes}
    mapa_perf = {p["id"]: p for p in perfis}

    for m in matches:
        m["licitacao"] = mapa_lics.get(m["licitacao_id"], {})
        m["perfil"]    = mapa_perf.get(m["perfil_id"], {})

    return matches


def agrupar_por_cliente(matches: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for m in matches:
        nome = m.get("perfil", {}).get("nome", "Desconhecido")
        grupos.setdefault(nome, []).append(m)
    return grupos


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LegisTech — Alertas Telegram")
    parser.add_argument("--simular", action="store_true", help="Mostrar sem enviar")
    parser.add_argument("--score",   type=int, default=SCORE_MINIMO_ALERTA)
    args = parser.parse_args()

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    log.info(f"Buscando matches com score >= {args.score}...")
    matches = buscar_matches_pendentes(supabase, args.score)
    log.info(f"  {len(matches)} match(es) para notificar.")

    if not matches:
        log.info("Nenhum alerta para enviar.")
        return

    grupos = agrupar_por_cliente(matches)
    ids_notificados = []

    for cliente, matches_cliente in grupos.items():
        chat_id = CHAT_IDS_CLIENTES.get(cliente)
        if not chat_id:
            log.warning(f"  Chat ID não configurado para '{cliente}'. Pulando.")
            continue

        matches_top = matches_cliente[:5]
        mensagem = formatar_mensagem(cliente, matches_top)

        if args.simular:
            print(f"\n{'='*50}")
            print(f"SIMULAÇÃO — {cliente} (chat_id: {chat_id})")
            print(f"{'='*50}")
            print(mensagem)
            ids_notificados.extend(m["id"] for m in matches_cliente)
        else:
            log.info(f"Enviando para {cliente}...")
            ok = enviar_telegram(chat_id, mensagem)
            if ok:
                ids_notificados.extend(m["id"] for m in matches_cliente)

    if ids_notificados and not args.simular:
        supabase.table("matches").update({
            "notificado": True,
        }).in_("id", ids_notificados).execute()
        log.info(f"{len(ids_notificados)} match(es) marcado(s) como notificado.")

    log.info("Concluído.")


if __name__ == "__main__":
    main()

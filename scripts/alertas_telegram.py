"""
LegisTech Intelligence — Alertas Telegram com Botões Interativos

Fluxo:
1. Bot envia oportunidades com botões [Participar] [Dispensar] [Ver edital]
2. Cliente clica no botão
3. Sistema atualiza o status no Supabase automaticamente
4. Dashboard reflete a escolha em tempo real

Uso:
  python alertas_telegram.py          # envia alertas pendentes
  python alertas_telegram.py --listen # fica escutando respostas (modo webhook)
  python alertas_telegram.py --simular
"""

import os
import time
import argparse
import logging
from datetime import datetime

import requests
from dotenv import load_dotenv, find_dotenv
from supabase import create_client, Client

load_dotenv(find_dotenv(), override=True)

SUPABASE_URL  = os.environ.get("SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SCORE_MINIMO  = 65

# Chat IDs — todos apontam para você por enquanto
CHAT_IDS_CLIENTES = {
    "Engenharia Total":                      "804078121",
    "Gráfica & Brindes":                     "804078121",
    "Soluções em TI":                        "804078121",
    "C K M Distribuidora":                   "804078121",
    "GF INFRAESTRUTURA E PAVIMENTACAO LTDA": "804078121",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("legistech.telegram")


def escape_md(texto: str) -> str:
    if not texto:
        return ""
    for c in r'_*[]()~`>#+-=|{}.!':
        texto = texto.replace(c, f'\\{c}')
    return texto


def enviar_oportunidade(chat_id: str, match: dict, lic: dict, perf: dict) -> bool:
    """Envia uma oportunidade individual com botões inline."""
    score  = match.get("score_calculado", 0)
    objeto = (lic.get("objeto") or "")[:150]
    orgao  = lic.get("orgao_nome") or "Órgão não informado"
    uf     = lic.get("uf") or ""
    valor  = lic.get("valor_estimado")
    link   = lic.get("link_pncp") or ""
    match_id = match.get("id")

    emoji = "🔥" if score >= 85 else "✅" if score >= 70 else "🔎"

    valor_fmt = (
        f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if valor else "Valor não informado"
    )

    texto = (
        f"🏆 *LegisTech — Nova Oportunidade*\n"
        f"Cliente: *{escape_md(perf.get('nome', ''))}*\n\n"
        f"{emoji} *Score: {score}/100*\n\n"
        f"📋 {escape_md(objeto)}{'...' if len(objeto)==150 else ''}\n\n"
        f"🏛️ {escape_md(orgao)} \\({uf}\\)\n"
        f"💰 {escape_md(valor_fmt)}\n\n"
        f"_Clique abaixo para registrar sua decisão:_"
    )

    # Botões inline — callback_data codifica a ação e o ID do match
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Participar",  "callback_data": f"participar:{match_id}"},
                {"text": "❌ Dispensar",  "callback_data": f"dispensar:{match_id}"},
            ],
        ]
    }

    # Adiciona botão Ver edital se tiver link
    if link:
        keyboard["inline_keyboard"].append([
            {"text": "🔗 Ver edital", "url": link}
        ])

    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       texto,
                "parse_mode": "MarkdownV2",
                "reply_markup": keyboard,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return True
        else:
            log.error(f"  Erro Telegram {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"  Erro conexão: {e}")
        return False


def buscar_matches_pendentes(supabase: Client, score_minimo: int) -> list:
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


def processar_callbacks(supabase: Client):
    """
    Fica em loop processando respostas dos botões.
    Roda por 5 minutos depois do envio dos alertas.
    """
    log.info("Aguardando respostas dos botões (5 minutos)...")
    offset = 0
    fim = time.time() + 300  # 5 minutos

    while time.time() < fim:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": offset, "timeout": 10, "allowed_updates": ["callback_query"]},
                timeout=15,
            )
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                callback = update.get("callback_query")
                if not callback:
                    continue

                callback_id   = callback["id"]
                callback_data = callback.get("data", "")
                chat_id       = callback["message"]["chat"]["id"]

                if ":" not in callback_data:
                    continue

                acao, match_id = callback_data.split(":", 1)

                if acao == "participar":
                    novo_status = "participando"
                    resposta    = "✅ Registrado! Boa sorte na licitação!"
                elif acao == "dispensar":
                    novo_status = "descartado"
                    resposta    = "❌ Oportunidade dispensada."
                else:
                    continue

                # Atualiza no Supabase
                supabase.table("matches").update({
                    "status": novo_status
                }).eq("id", int(match_id)).execute()

                # Confirma para o usuário no Telegram
                requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={
                    "callback_query_id": callback_id,
                    "text": resposta,
                    "show_alert": False,
                }, timeout=10)

                # Edita a mensagem para remover os botões
                requests.post(f"{TELEGRAM_API}/editMessageReplyMarkup", json={
                    "chat_id":    chat_id,
                    "message_id": callback["message"]["message_id"],
                    "reply_markup": {"inline_keyboard": []},
                }, timeout=10)

                log.info(f"  Match {match_id} → {novo_status}")

        except Exception as e:
            log.warning(f"  Erro ao processar callback: {e}")
            time.sleep(2)

    log.info("Processamento de callbacks encerrado.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simular", action="store_true")
    parser.add_argument("--score",   type=int, default=SCORE_MINIMO)
    parser.add_argument("--listen",  action="store_true", help="Só processar callbacks")
    args = parser.parse_args()

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Modo só escutar respostas
    if args.listen:
        processar_callbacks(supabase)
        return

    log.info(f"Buscando matches com score >= {args.score}...")
    matches = buscar_matches_pendentes(supabase, args.score)
    log.info(f"  {len(matches)} match(es) para notificar.")

    if not matches:
        log.info("Nenhum alerta para enviar.")
        return

    # Agrupar por cliente
    grupos: dict = {}
    for m in matches:
        nome = m.get("perfil", {}).get("nome", "Desconhecido")
        grupos.setdefault(nome, []).append(m)

    ids_notificados = []

    for cliente, matches_cliente in grupos.items():
        chat_id = CHAT_IDS_CLIENTES.get(cliente)
        if not chat_id:
            log.warning(f"  Chat ID não configurado para '{cliente}'. Pulando.")
            continue

        log.info(f"Enviando {len(matches_cliente)} oportunidade(s) para {cliente}...")

        # Envia as top 5 oportunidades individualmente (uma por mensagem com botões)
        enviados = 0
        for m in matches_cliente[:5]:
            if args.simular:
                lic  = m.get("licitacao", {})
                perf = m.get("perfil", {})
                print(f"\n{'='*50}")
                print(f"SIMULAÇÃO — {cliente}")
                print(f"Match ID: {m['id']} | Score: {m['score_calculado']}")
                print(f"Objeto: {(lic.get('objeto') or '')[:80]}")
                print(f"Botões: [✅ Participar] [❌ Dispensar] [🔗 Ver edital]")
                enviados += 1
            else:
                ok = enviar_oportunidade(
                    chat_id,
                    m,
                    m.get("licitacao", {}),
                    m.get("perfil", {})
                )
                if ok:
                    enviados += 1
                time.sleep(0.5)  # pequena pausa entre mensagens

        if enviados > 0:
            ids_notificados.extend(m["id"] for m in matches_cliente[:5])
            log.info(f"  {enviados} oportunidade(s) enviada(s) para {cliente}.")

    # Marcar como notificado
    if ids_notificados and not args.simular:
        supabase.table("matches").update({
            "notificado": True,
        }).in_("id", ids_notificados).execute()
        log.info(f"{len(ids_notificados)} match(es) marcado(s) como notificado.")

    # Processar respostas dos botões por 5 minutos
    if not args.simular and ids_notificados:
        processar_callbacks(supabase)

    log.info("Concluído.")


if __name__ == "__main__":
    main()

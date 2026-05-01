# Prompt para próxima tech-spec — Desconexão WhatsApp, pausa de campanha e retoma após reconexão

Copiar o bloco **Prompt principal** para o fluxo BMAD quick-spec ou para um agente de especificação.

---

## Prompt principal

**Contexto:** Stack atual — Flask, PostgreSQL, campanhas Uazapi com **fila outbox** (`campaign_message_outbox`) e legado com pastas/chunks (`campaign_stage_sends`, `waiting_reconnect` no worker). Hoje, falhas de envio no outbox tendem a marcar linhas como **`failed`** terminal; não há fluxo unificado “instância desligou → pausa campanha → reconectou → retoma sem queimar leads”.

**Objetivo de produto:**

1. Quando o **WhatsApp / instância Uazapi desliga**, as **campanhas activas** que dependem dessa instância devem **parar de disparar**, **registar erro visível** ao utilizador e, idealmente, **pausar automaticamente** (ou entrar num estado explícito tipo “pausada por desligação”).
2. Quando a **ligação volta**, **informar** o utilizador que há **campanhas pausadas** por desligação e permitir **retomar de onde parou** — isto é: **não reenviar** o que já foi **confirmado como enviado** (HTTP 200 / regra actual); **voltar a tentar** apenas intenções **não confirmadas** (filas `pending` ou linhas reclassificadas por política de erro transitório).

**Desenho de alto nível a especificar e fechar na spec:**

| Área | Direcção |
| ---- | -------- |
| **Detecção de desligação** | Polling periódico do estado da instância (`get_status` / equivalente) **e/ou** evento do fornecedor (se existir). Transição para estado “desligado” dispara o fluxo de pausa. |
| **Âmbito da pausa** | Associar **instância** ↔ campanhas/outbox que usam essa instância. Decidir: pausar **`campaigns.status`**, flag por campanha (`paused_reason`), ou só bloquear envios no worker sem mudar status da UI. |
| **Outbox — erro transitório** | Erros classificados como **disconnect / 503 / instância indisponível** **não** devem fechar a linha como **`failed`** definitivo sem política; preferir manter **`pending`** com `next_run_at` ou estado dedicado (ex. `waiting_instance`) + métrica/tentativa. |
| **Notificação** | Canal acordado (in-app, email, integração existente de “disconnect support” no repo). Mensagem clara: **qual instância**, **quantas campanhas** afectadas. |
| **Reconexão** | Detectar transição **desligado → ligado**. Listar campanhas em pausa por desligação. Opções de produto: **retoma automática** (só se pausa foi sistema) vs **CTA “Retomar”** no utilizador. |
| **Retoma “de onde parou”** | SSOT: envio confirmado = política actual pós-200. Leads **`sent`** intactos. Linhas **`failed`** só por disconnect podem precisar **re-enfileirar** ou voltar a **`pending`** conforme política. **Idempotência** (`track_id`) como mitigação a duplo envio. |
| **Legado advanced** | Alinhar semântica com `waiting_reconnect` em chunks onde fizer sentido; evitar dois comportamentos incongruentes entre legado e outbox (documentar dual-run). |

**Fora de âmbito sugerido para MVP (explicitar na spec):** alterar contrato Uazapi além do que já existe; SSE no browser; prioridade entre campanhas.

**Critérios de aceitação (rascunho para a spec):**

- Dado instância **desligada**, quando o worker ou job de saúde corre, então campanhas elegíveis ficam **pausadas** (ou estado definido) e o utilizador **vê** aviso.
- Dado envio outbox que falha só por **disconnect**, quando não há confirmação de envio, então o lead **não** é marcado como enviado com sucesso e a fila **pode** retentar após reconexão conforme política.
- Dado **reconexão**, quando o utilizador **retoma** (ou auto-retoma), então processamento continua **sem duplicar** envios já confirmados.

**Referências no codebase para ancorar a investigação:** `worker_message_outbox.py` (`_persist_outcome`, estados terminal `failed`), `worker_cadence.py` (`_resume_waiting_reconnect_stage_sends`), `utils/uazapi_support_notify.py`, `services/uazapi.py` (`get_status`), `instances` no `app.py`.

**Idioma da spec:** pt-BR.

---

## Uma linha para colar no chat

“Elabora uma tech-spec **ready-for-dev** para: ao desligar WhatsApp numa instância Uazapi, pausar e notificar campanhas activas afectadas; ao reconectar, informar e retomar envios outbox **sem** confirmar envios não 200 e **sem** duplicar idempotência — usando o desenho de alto nível no ficheiro `prompt-next-spec-desconexao-whatsapp-retoma-campanhas.md`.”

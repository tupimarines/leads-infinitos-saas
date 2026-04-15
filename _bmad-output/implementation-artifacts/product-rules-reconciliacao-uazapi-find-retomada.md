# Regras de produto: reconciliação UAZAPI (find) + retomada + failed vs pending

**Contexto:** campanhas com `create_advanced_campaign` (pastas), `listfolders` como SSOT de agregados, `list_messages` **não** confiável para “quem recebeu”. Objetivo: eliminar (1) lead errado no follow-up e (2) reenvio da inicial para quem já recebeu, especialmente com restrição/desconexão no meio do disparo.

**Data:** 2026-04-15  
**Idioma:** decisões em PT-BR para engenharia e produto.

---

## 1. Princípios

1. **Contador da pasta (`listfolders`)** continua válido para **progresso agregado** da pasta e para **estado da pasta** (`sending`, `done`, `failed`, etc.), não para **atribuir** “quem é o N-ésimo enviado” na lista de leads do app.
2. **Estado por lead** (`campaign_leads.status`, etc.) para “enviado na etapa X” exige **evidência por destinatário**, obtida preferencialmente por **`message_find`** (mensagem com `send_folder_id` coerente com o send) enquanto a API de listagem por pasta for incompleta.
3. **`list_messages` (Sent/Failed / Scheduled): não usar** neste fluxo de campanha UAZAPI (`create_advanced_campaign` + pastas). **Não** chamar para marcar `campaign_leads` (`sent` / `failed`), **não** usar em `needs_reconcile` / `fetch_all_phones_by_status` / contagens por lead, **não** usar como prova para follow-up ou retomada. Respostas vazias ou `totalRecords` enganoso na prática tornam o endpoint inútil para SSOT; qualquer ramo legado que dependa disso deve ser **removido ou desligado por padrão** (ex.: variável de ambiente documentada, default = off).
4. **Fontes permitidas para estado por lead:** **`message_find`** (evidência no chat + `send_folder_id`) e, no futuro, webhooks/eventos do provedor se existirem. **Fonte da pasta (agregado + status da pasta):** apenas **`listfolders`**.
5. Reconciliação **percorre todos os leads pendentes / não confirmados** pertencentes ao **escopo do send** (`campaign_stage_sends.lead_ids`), não janela “últimos N” por heurística.

---

## 2. Definições

| Termo | Significado |
|--------|-------------|
| **Send** | Linha `campaign_stage_sends` (chunk): `uazapi_folder_id`, `lead_ids`, `planned_count`, `status`, contagens sincronizadas. |
| **Confirmado enviado (etapa)** | Lead com evidência `message_find` (ou política futura equivalente) de mensagem da campanha na pasta correta **ou** ACK de envio unitário se no futuro existir outro modo. |
| **Pendente reconciliável** | Lead em `lead_ids` do send, etapa correta (`current_step` + guardas de cadência), `status` ainda não finalizado para aquele envio **ou** marcado de forma que precise validação antes de FU / novo disparo. |
| **Agregado failed (`listfolders`)** | `log_failed` > 0 ou estado da pasta indicando falhas; **não** implica que **todo** lead do chunk falhou no aparelho. |

---

## 3. Gatilhos obrigatórios de `message_find` (sondar todos no escopo)

Rodar **`reconcile_leads_via_message_find`** (ou equivalente) sobre **todo** conjunto:

`{ id ∈ lead_ids do send ∧ elegível ∧ (status pending OU marcado failed pela pasta mas ainda sem find pós-falha OU “não confirmado” explícito no modelo de dados) }`

**Obrigatório antes de:**

- **A)** Qualquer novo **`create_advanced_campaign`** que inclua lead que já pertenceu a um send **incompleto** ou **ambiguo** da mesma etapa (retomada / segundo chunk com sobreposição de risco).
- **B)** Transição para **follow-up** (rollover / `current_step` / criação de campanha FU na API) para leads originados desse send.
- **C)** Marcar o send como **encerrado para efeito de FU** (`fu_rollover_done` ou equivalente) quando a pasta estiver `done` / política de `partial` aceita.

**Ordem sugerida:** find → atualizar estados de lead → decidir novo advanced / FU / retomada.

---

## 4. Política: `failed` (pasta / listfolders) vs WhatsApp cai

1. **Incremento de `log_failed` ou `failed` na pasta** significa “a API/pasta contabilizou falhas”, **não** “este número N não recebeu” sem correlacionar ao lead.
2. **Antes** de mover lead para **retry como `pending`** para novo envio da **mesma** mensagem inicial:
   - Rodar **find** para esse lead (e, em batch, para todos os candidatos do send).
   - Se **find positivo** (mensagem da pasta no chat): marcar **`sent`** (ou equivalente confirmado), **não** reenviar inicial.
   - Se **find negativo** e política de falha explícita: `failed` no lead ou `pending` para retry conforme regra de negócio (ver §5).
3. **Queda de sessão / instância** no meio do disparo: tratar como **evento operacional**; ao reconectar, executar **§3** nos pendentes daquele send **antes** de retomar chunk ou criar próximo.

---

## 5. Retry e `pending` após falha agregada

- Leads que a pasta marcou como slot “failed” mas **find mostrou entrega**: ficam **`sent`**; **não** entram em retry de inicial.
- Leads **sem** evidência de entrega e com falha tratável: **`pending`** para nova tentativa (ou `failed` terminal se política disser que não retry).
- **Nunca** promover ao FU só com `log_sucess` + ordem de `lead_ids`; só após **§3** concluído para o escopo do send (ou política documentada de exceção).

---

## 6. Follow-up e chunks

1. **Follow-up (rollover inicial → FU1)** só após: pasta em estado aceite (`done` ou `partial` conforme spec) **e** `success_count + failed_count` coerente com `planned_count` **na API**, **e** reconciliação **§3** aplicada aos leads desse send.
2. **Próximo chunk** da mesma campanha/dia: só depois de **§3** no send anterior do mesmo contexto de instância quando houver **ambiguidade** (queda, `partial`, ou `failed` > 0 com pendentes).
3. Chunk “fechado” para efeito de produto: **todos** os `lead_ids` estão em estado terminal permitido (`sent` confirmado, `failed` confirmado, ou `pending` só se política permitir re-disparo) **e** decisão explícita de avanço.

---

## 7. Observabilidade (mínimo desejável)

- Log estruturado: `event`, `campaign_id`, `send_id`, `folder_id`, `find_scope_count`, `find_positive_count`, `find_negative_count`.
- Opcional futuro: `last_error` / motivo no send (Task 8 backlog já citado na spec n8n-sync).

---

## 8. Fora de escopo deste documento

- Mudança de transporte para “só envio unitário pela VPS” (decisão arquitetural separada).
- Webhooks UAZAPI (fase posterior).

---

## Prompt para outro chat (Quick Spec / quick-dev)

Copie o bloco abaixo **inteiro** para um novo chat (com o workflow **quick-spec** ou **quick-dev** do BMAD, se usar).

```
Você é o redator de um QUICK TECH SPEC para o repositório leads-infinitos-saas.

CONTEXTO
- Campanhas UAZAPI usam create_advanced_campaign (pastas), sync com GET /sender/listfolders.
- **Política:** não usar mais `list_messages` neste fluxo (nem para marcação de lead, nem needs_reconcile / fetch_all_phones_by_status); ver product-rules §1.3–1.4.
- Hoje _sync_folder_via_listfolders marca os primeiros N leads como sent com base em log_sucess, o que gera falsos positivos/negativos e risco de follow-up errado ou reenvio da inicial.
- message_find (POST /message/find por chat + send_folder_id) existe em utils/sync_uazapi.py (reconcile_leads_via_message_find) mas é limitado por _should_reconcile_via_message_find (principalmente pasta quase final / done).

OBJETIVO DA FEATURE
1. Reconciliar TODOS os leads pendentes / não confirmados no escopo de campaign_stage_sends.lead_ids com message_find antes de: (a) novo create_advanced_campaign em retomada, (b) rollover/follow-up, (c) marcar encerramento do send para FU.
2. Tratar log_failed / estado failed da pasta: não assumir que todo mundo falhou; antes de retry (pending), find para não reenviar a quem já recebeu.
3. Manter listfolders para agregados e estado da pasta; **remover ou desligar por padrão** ramos que dependam de `list_messages` para leads ou reconciliação.

ENTREGÁVEIS DO SPEC
- User stories + ACs mensuráveis.
- Fluxos: (i) pasta done, (ii) partial / restrição no meio, (iii) pasta failed agregada, (iv) reconexão instância, (v) pasta órfã (já coberta parcialmente — referenciar tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md).
- Mudanças de arquivo prováveis: utils/sync_uazapi.py, worker_cadence.py (rollover), testes em tests/test_sync_uazapi.py.
- Flags de env opcionais (ex.: UAZAPI_RECONCILE_FIND_BEFORE_RESUME) se fizer sentido.
- Riscos: rate limit message_find (N chamadas), tempo de worker; mitigação (sleep existente, batch, limite paralelo).
- Fora de escopo: refator completo para envio unitário só pela VPS.

REFERÊNCIA LOCAL
- Ler: _bmad-output/implementation-artifacts/product-rules-reconciliacao-uazapi-find-retomada.md (regras de produto acordadas).
- Cruzar com: _bmad-output/implementation-artifacts/tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md

Saída: um único markdown de quick tech spec em _bmad-output/implementation-artifacts/ com nome slug claro (ex.: tech-spec-uazapi-reconcile-find-before-fu-resume.md), pronto para quick-dev.
```

---

_Fim do artefato._

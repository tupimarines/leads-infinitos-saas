# Regras de comunicação (IA / docs) — nomenclatura do app (campanhas Uazapi)

Objetivo: respostas **curtas**, **operacionais** e alinhadas aos **nomes reais** do código e da BD, para reduzir ambiguidade ao implementar ou rever PRs.

---

## 1. Preferir termos canônicos

Use estes tokens como vocabulário estável (não traduzir para sinónimos vagos):

- `create_advanced_campaign` — chamada HTTP ao provedor; não dizer apenas “disparar campanha”.
- `campaign_stage_sends` — tabela de estado por pasta/etapa; não “fila genérica”.
- `folder_id` / `uazapi_folder_id` — ID da pasta no Uazapi.
- `enable_cadence` — flag booleana na campanha.
- `use_uazapi_sender` — campanha usa pipeline Uazapi.
- `schedule_next_initial_chunk` — função que agenda o próximo chunk **initial**.
- `_materialize_scheduled_stage_sends` — cria pasta a partir de linha `scheduled`.
- `per_instance_limit` / `total_limit` — saída de `uazapi_initial_chunk_distribution_limits`.
- `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` — bloqueio de novo chunk na instância.

---

## 2. Estrutura de frase sugerida

- **Contexto (opcional):** ficheiro ou tabela em backticks, uma vez.
- **Verbo:** “grava”, “chama”, “atualiza”, “bloqueia”.
- **Objeto:** nome de função/tabela/coluna exato.
- **Efeito:** uma só frase (ex.: “não marca `campaign_leads` como `sent` no INSERT”).

Exemplo bom:  
`app._create_campaign_core` chama `UazapiService.create_advanced_campaign` e insere `campaign_stage_sends` com `stage='initial'` e `status='running'`.

Exemplo fraco:  
“A API envia as mensagens e o sistema regista tudo.”

---

## 3. Evitar

- Sinónimos para a mesma coisa (`lote`, `batch`, `chunk`, `pasta`) na mesma resposta sem equivalência explícita.
- “Worker” sem nome: dizer `worker_cadence.process_cadence` ou `schedule_next_initial_chunk`.
- Supor estados da API Uazapi iguais a `campaign_stage_sends.status` (o código documenta divergência `running` vs `queued` remoto).

---

## 4. Quando falar de utilizador vs admin

- **`created_by_admin_id`** / fluxos superadmin: citar quando a regra for específica de admin.
- **`SUPER_ADMIN_EMAILS`** (`utils/config.py`): apenas para gate de email, não misturar com “plano” do utilizador alvo.

---

## 5. Idioma

- **Documentação de produto:** pt-BR (frases completas).
- **Identificadores:** sempre como no código (`snake_case`, nomes de ficheiros exatos).

---

## 6. Checklist antes de fechar uma análise

1. O caminho citado (`app.py`, `worker_cadence.py`, …) existe e bate com a afirmação?
2. A tabela/coluna existe no DDL do projeto?
3. Distinguiu **primeiro lote na criação** de **chunks agendados** (`scheduled` → materialize)?

-- =============================================================================
-- Consulta status de chunks (campaign_stage_sends) para debug "Já existe chunk em andamento"
-- Instância 68: aurora-4966
-- Folders: r1f544247aef49f (Campaign 140 follow1), r6d2051ad056822 (Ag Mkt CWB 01)
-- =============================================================================

-- 1) Todos os campaign_stage_sends da instância 68 (o que o sync e o botão Continuar enxergam)
SELECT
    css.id,
    css.campaign_id,
    c.name AS campaign_name,
    css.stage,
    css.instance_id,
    i.name AS instance_name,
    css.uazapi_folder_id,
    css.status,
    css.success_count,
    css.failed_count,
    css.planned_count,
    css.last_sync_at,
    css.scheduled_for,
    css.created_at
FROM campaign_stage_sends css
JOIN campaigns c ON c.id = css.campaign_id
JOIN instances i ON i.id = css.instance_id
WHERE css.instance_id = 68
ORDER BY css.campaign_id, css.stage, css.created_at DESC;

-- 2) Chunks que BLOQUEIAM o botão Continuar (stage=initial, status ativo)
-- O botão verifica: stage='initial' AND status IN ('scheduled','running','partial','queued')
SELECT
    css.id,
    css.campaign_id,
    c.name AS campaign_name,
    css.stage,
    css.instance_id,
    css.uazapi_folder_id,
    css.status,
    css.last_sync_at
FROM campaign_stage_sends css
JOIN campaigns c ON c.id = css.campaign_id
WHERE css.instance_id = 68
  AND css.stage = 'initial'
  AND css.status IN ('scheduled', 'running', 'partial', 'queued');

-- 3) Chunks com os folder_ids específicos (r1f544247aef49f, r6d2051ad056822)
SELECT
    css.id,
    css.campaign_id,
    c.name AS campaign_name,
    css.stage,
    css.instance_id,
    css.uazapi_folder_id,
    css.status,
    css.success_count,
    css.failed_count,
    css.last_sync_at
FROM campaign_stage_sends css
JOIN campaigns c ON c.id = css.campaign_id
WHERE css.uazapi_folder_id IN ('r1f544247aef49f', 'r6d2051ad056822')
   OR css.uazapi_folder_id LIKE '%r1f544247aef49f%'
   OR css.uazapi_folder_id LIKE '%r6d2051ad056822%';

-- 4) Campanhas 140 e "Ag Mkt CWB 01"
SELECT id, name, enable_cadence, uazapi_folder_id
FROM campaigns
WHERE id = 140 OR name ILIKE '%Ag Mkt CWB 01%';

-- 5) LIMPEZA: Marcar chunks órfãos (do antigo loop) como done em TODAS as instâncias
--    Desbloqueia o botão Continuar. Execute em transação para poder dar ROLLBACK se necessário.
--    Ver: scripts/cleanup_stale_chunks.py para versão com --dry-run

-- Preview (quantos serão afetados):
SELECT COUNT(*) AS total, campaign_id, c.name
FROM campaign_stage_sends css
JOIN campaigns c ON c.id = css.campaign_id
WHERE css.status IN ('scheduled', 'running', 'partial')
GROUP BY campaign_id, c.name
ORDER BY campaign_id;

-- Executar (descomente para aplicar):
-- BEGIN;
-- UPDATE campaign_stage_sends
-- SET status = 'done', updated_at = NOW()
-- WHERE status IN ('scheduled', 'running', 'partial');
-- -- Verifique: SELECT COUNT(*) FROM campaign_stage_sends WHERE status IN ('scheduled','running','partial');
-- COMMIT;

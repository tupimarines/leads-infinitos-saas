-- ========================================
-- Migração: Corrigir Status de Campaign Leads
-- Data: 2026-01-28
-- Objetivo: Definir status='pending' para todos os leads não processados
-- ========================================

-- Passo 1: Verificar estado atual (ANTES da correção)
SELECT 
    campaign_id,
    COALESCE(status, 'NULL') as status,
    COUNT(*) as count
FROM campaign_leads
GROUP BY campaign_id, status
ORDER BY campaign_id DESC;

-- Passo 2: Garantir que a coluna status tenha DEFAULT correto
ALTER TABLE campaign_leads 
ALTER COLUMN status SET DEFAULT 'pending';

-- Passo 3: Atualizar leads sem status ou com status inválido para 'pending'
UPDATE campaign_leads 
SET status = 'pending' 
WHERE status IS NULL 
   OR status NOT IN ('pending', 'sent', 'failed', 'invalid');

-- Passo 4: Verificar resultado (DEPOIS da correção)
SELECT 
    campaign_id,
    status,
    COUNT(*) as count
FROM campaign_leads
GROUP BY campaign_id, status
ORDER BY campaign_id DESC;

-- Passo 5: Verificar quantos leads estão prontos para envio
SELECT 
    c.id as campaign_id,
    c.name as campaign_name,
    c.status as campaign_status,
    COUNT(cl.id) as pending_leads
FROM campaigns c
LEFT JOIN campaign_leads cl ON c.id = cl.campaign_id AND cl.status = 'pending'
WHERE c.status IN ('running', 'pending')
GROUP BY c.id, c.name, c.status
ORDER BY c.id DESC;

-- ========================================
-- INSTRUÇÕES DE USO
-- ========================================
-- 1. Conectar ao PostgreSQL de produção via Dokploy ou SSH
-- 2. Executar este script completo
-- 3. Verificar que os leads agora têm status='pending'
-- 4. Monitorar logs do container 'sender' para confirmar que disparos iniciaram
-- ========================================

"""
Validação automática de lista CSV via Uazapi check_phone.
Usado no upload de CSV e pós-extração (worker_scraper).
"""

import os
import re
import time

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from dotenv import load_dotenv

load_dotenv()


def _get_db_connection():
    """Conexão DB usando env DB_* (compatível com worker_scraper)."""
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432'),
    )


def _get_uazapi_token_for_user(conn, user_id):
    """
    Obtém apikey da primeira instância Uazapi do usuário.
    Retorna None se não houver.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT apikey FROM instances
            WHERE user_id = %s AND COALESCE(api_provider, 'megaapi') = 'uazapi'
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _normalize_phone_for_api(phone):
    """
    Normaliza número para API Uazapi.
    Extrai dígitos; se 10–11 dígitos sem 55, adiciona 55.
    Retorna string ou None se inválido.
    """
    if not phone:
        return None
    raw_str = str(phone).split("@")[0]
    clean = re.sub(r"\D", "", raw_str)
    if len(clean) < 10:
        return None
    if 10 <= len(clean) <= 11 and not clean.startswith("55"):
        return "55" + clean
    return clean


def _extract_phone_from_link(value):
    """
    Extrai número de um link WhatsApp (wa.me, phone=, whatsapp.com).
    Retorna string de dígitos ou None.
    """
    if not value or not pd.notna(value):
        return None
    link = str(value).strip()
    if not link:
        return None
    patterns = [
        r'wa\.me/([0-9]+)',
        r'phone=([0-9]+)',
        r'whatsapp\.com/send\?phone=([0-9]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, link, re.IGNORECASE)
        if match:
            return match.group(1)
    digits = re.sub(r'\D', '', link)
    return digits if len(digits) >= 10 else None


def _extract_phone_from_row(row, phone_col, whatsapp_link_col, website_col=None):
    """
    Extrai telefone de uma linha do DataFrame.
    Prioridade: whatsapp_link > website (se contiver wa.me) > phone_number.
    Apify às vezes coloca link WhatsApp na coluna website.
    Retorna string ou None.
    """
    # 1. Primeiro: whatsapp_link
    raw_link = row.get(whatsapp_link_col) if whatsapp_link_col else None
    phone = _extract_phone_from_link(raw_link)
    if phone:
        return phone

    # 2. Fallback: website (Apify pode colocar wa.me no website)
    if website_col:
        raw_web = row.get(website_col)
        phone = _extract_phone_from_link(raw_web)
        if phone:
            return phone

    # 3. Fallback: phone_number
    raw_phone = row.get(phone_col) if phone_col else None
    if raw_phone and pd.notna(raw_phone):
        digits = re.sub(r'\D', '', str(raw_phone))
        if len(digits) >= 10:
            return digits
    return None


def _check_phone_with_retry(uazapi, token, numbers, max_retries=2, backoff=1, timeout=90):
    """
    Chama check_phone com retry: 2x se None/Timeout, 1s backoff.
    429: 3x, 2s backoff. 400: sem retry.
    Retorna (result_list, error_msg). result_list é None em falha.
    """
    import requests
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            result = uazapi.check_phone(token, numbers, timeout=timeout)
            if result is not None:
                return result, None
            last_err = "Timeout ou resposta vazia"
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                if attempt < 3:
                    time.sleep(2)
                    continue
            last_err = str(e)
            break
        except Exception as e:
            last_err = str(e)
        if attempt < max_retries:
            time.sleep(backoff)
    return None, last_err


def validate_job_csv(job_id, user_id, file_path=None):
    """
    Valida lista CSV via check_phone (batch 50).
    Lê CSV, extrai telefones, chama Uazapi, remove inválidos, sobrescreve CSV.
    Retorna {valid, invalid, batches_skipped, partial} ou None em skip/falha.

    file_path: se fornecido, usa (worker); senão lê results_path do job (upload).
    """
    conn = _get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, results_path FROM scraping_jobs WHERE id = %s",
                (job_id,),
            )
            job = cur.fetchone()
        if not job:
            print(f"[validate_job_csv] job_id={job_id} skip: job not found")
            return None
        if job['user_id'] != user_id:
            print(f"[validate_job_csv] job_id={job_id} skip: ownership mismatch")
            return None

        path = file_path or job['results_path']
        if not path or not os.path.exists(path):
            print(f"[validate_job_csv] job_id={job_id} skip: path not found or empty")
            return None

        df = pd.read_csv(path, dtype=str, encoding='utf-8', errors='replace')
        cols = [c.lower() for c in df.columns]
        df.columns = cols

        # Prioridade Apify: whatsapp_link (1º) > phone_number (fallback)
        whatsapp_link_col = next((c for c in cols if c == 'whatsapp_link'), None)
        phone_col = next((c for c in cols if 'phone' in c or 'tel' in c or 'cel' in c), None)
        website_col = next((c for c in cols if c == 'website'), None)  # Apify às vezes coloca wa.me no website
        status_col = next((c for c in cols if c == 'status'), None)

        if not phone_col and not whatsapp_link_col:
            print(f"[validate_job_csv] job_id={job_id} skip: no phone column")
            return None

        if status_col:
            df_filtered = df[df[status_col].astype(str).str.strip() == '1']
        else:
            df_filtered = df

        rows = []
        for df_idx, row in df_filtered.iterrows():
            raw = _extract_phone_from_row(row, phone_col, whatsapp_link_col, website_col)
            phone = _normalize_phone_for_api(raw) if raw else None
            if phone:
                rows.append((df_idx, row, phone))

        print(f"[validate_job_csv] job_id={job_id} user_id={user_id} path={path} rows_with_phone={len(rows)}")

        if not rows:
            print(f"[validate_job_csv] job_id={job_id} skip: no rows with valid phone")
            return None

        token = _get_uazapi_token_for_user(conn, user_id)
        if not token:
            print(f"[validate_job_csv] job_id={job_id} skip: no Uazapi instance")
            return None

        from services.uazapi import UazapiService
        uazapi = UazapiService()
        BATCH_SIZE = 50
        indices_drop = set()
        batches_skipped = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            numbers = [p for _, _, p in batch]
            result, err = _check_phone_with_retry(uazapi, token, numbers, timeout=90)
            if result is None:
                batches_skipped += 1
                continue
            for j, item in enumerate(result):
                if j < len(batch) and not item.get('isInWhatsapp', True):
                    df_idx = batch[j][0]
                    indices_drop.add(df_idx)
            if i + BATCH_SIZE < len(rows):
                time.sleep(0.5)

        df_valid = df_filtered[~df_filtered.index.isin(indices_drop)]
        valid = len(df_valid)
        invalid = len(indices_drop)
        partial = batches_skipped > 0

        temp_path = path + '.tmp'
        df_valid.to_csv(temp_path, index=False, encoding='utf-8')
        os.replace(temp_path, path)

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scraping_jobs SET lead_count = %s WHERE id = %s",
                (valid, job_id),
            )
        conn.commit()

        print(f"[validate_job_csv] job_id={job_id} valid={valid} invalid={invalid} batches_skipped={batches_skipped} partial={partial}")
        return {
            'valid': valid,
            'invalid': invalid,
            'batches_skipped': batches_skipped,
            'partial': partial,
        }
    finally:
        conn.close()

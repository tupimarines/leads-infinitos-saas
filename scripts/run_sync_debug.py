#!/usr/bin/env python
"""Script para rodar sync-debug sem Flask. Uso: python scripts/run_sync_debug.py [campaign_id]"""
import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def main():
    campaign_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not campaign_id:
        # Try to find teste225 or similar
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            database=os.environ.get('DB_NAME', 'leads_infinitos'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD'),
            port=os.environ.get('DB_PORT', '5432'),
            cursor_factory=RealDictCursor
        )
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM campaigns WHERE use_uazapi_sender = TRUE AND uazapi_folder_id IS NOT NULL ORDER BY id DESC LIMIT 5")
            rows = cur.fetchall()
        conn.close()
        print("Campanhas Uazapi disponíveis:")
        for r in rows:
            print(f"  {r['id']}: {r['name']}")
        campaign_id = rows[0]['id'] if rows else None
        if not campaign_id:
            print("Nenhuma campanha Uazapi encontrada.")
            return 1
        print(f"\nUsando campanha {campaign_id}\n")

    import psycopg2
    from psycopg2.extras import RealDictCursor
    def get_db_connection():
        return psycopg2.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            database=os.environ.get('DB_NAME', 'leads_infinitos'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD', ''),
            port=os.environ.get('DB_PORT', '5432'),
            cursor_factory=RealDictCursor
        )
    from utils.sync_uazapi import _fetch_all_phones_by_status
    from services.uazapi import UazapiService

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT c.id, c.uazapi_folder_id, c.use_uazapi_sender, c.name
               FROM campaigns c
               WHERE c.id = %s""",
            (campaign_id,)
        )
        campaign = cur.fetchone()
    conn.close()

    if not campaign or not campaign.get('use_uazapi_sender') or not campaign.get('uazapi_folder_id'):
        print(f"Campanha {campaign_id} não usa Uazapi ou sem folder_id")
        return 1

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.apikey FROM campaign_instances ci
            JOIN instances i ON i.id = ci.instance_id
            WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
            LIMIT 1
        """, (campaign_id,))
        inst = cur.fetchone()
    conn.close()

    if not inst or not inst.get('apikey'):
        print("Instância Uazapi não encontrada")
        return 1

    uazapi = UazapiService()
    token = inst['apikey']
    folder_id = campaign['uazapi_folder_id']

    print(f"Campanha: {campaign['name']} (id={campaign_id})")
    print(f"Folder: {folder_id}\n")

    sent_phones = list(_fetch_all_phones_by_status(uazapi, token, folder_id, "Sent"))
    failed_phones = list(_fetch_all_phones_by_status(uazapi, token, folder_id, "Failed"))

    print(f"API Sent: {len(sent_phones)} phones -> {sent_phones}")
    print(f"API Failed: {len(failed_phones)} phones -> {failed_phones}\n")

    raw_sent = uazapi.list_messages(token, folder_id, message_status="Sent", page=1, page_size=1)
    first_msg = None
    if raw_sent:
        msgs = raw_sent.get("messages") or raw_sent.get("data")
        if isinstance(msgs, list) and msgs:
            first_msg = msgs[0]
        elif isinstance(msgs, dict):
            first_msg = msgs

    print("Primeira mensagem (estrutura):")
    print(json.dumps(first_msg, indent=2, default=str) if first_msg else "  (vazio)\n")

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, phone, whatsapp_link, status, current_step,
                   regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g') as phone_norm,
                   regexp_replace(COALESCE(whatsapp_link, ''), '[^0-9]', '', 'g') as wa_norm
            FROM campaign_leads
            WHERE campaign_id = %s
        """, (campaign_id,))
        db_leads = cur.fetchall()
    conn.close()

    print("Leads no DB:")
    for l in db_leads:
        print(f"  id={l['id']} phone={l['phone']!r} wa_link={l['whatsapp_link']!r} -> phone_norm={l['phone_norm']!r} wa_norm={l['wa_norm']!r} status={l['status']}")

    def _match_params(ph):
        if len(ph) <= 11 and not ph.startswith("55"):
            return (ph, "55" + ph)
        return (ph, ph)

    matched = []
    unmatched_api = []
    for ph in sent_phones:
        p1, p2 = _match_params(ph)
        found = any(
            (lead.get("phone_norm") or "") in (p1, p2) or (lead.get("wa_norm") or "") in (p1, p2)
            for lead in db_leads
        )
        if found:
            matched.append(ph)
        else:
            unmatched_api.append(ph)

    unmatched_db = []
    for lead in db_leads:
        pn = (lead.get("phone_norm") or "").strip()
        wn = (lead.get("wa_norm") or "").strip()
        if not pn and not wn:
            continue
        found = any(
            pn == ph or wn == ph or pn == ("55" + ph) or wn == ("55" + ph)
            or ("55" + pn) == ph or ("55" + wn) == ph
            for ph in sent_phones
        )
        if not found and lead.get("status") != "sent":
            unmatched_db.append(lead)

    print(f"\n--- MATCH ---")
    print(f"Matched: {len(matched)}")
    print(f"Unmatched from API (não achou no DB): {unmatched_api}")
    print(f"Unmatched from DB (status!=sent e não achou na API): {len(unmatched_db)}")
    for l in unmatched_db:
        print(f"  id={l['id']} phone_norm={l['phone_norm']!r} wa_norm={l['wa_norm']!r}")

    return 0

if __name__ == "__main__":
    sys.exit(main())

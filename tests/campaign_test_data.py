# -*- coding: utf-8 -*-
"""
CSV de leads válido para integração com ``POST /api/admin/campaigns`` (app.py lê CSV/XLSX).
"""

# Janela de envio (horas locais da campanha / BRT): a partir das 5h até 23h, para testes
# não ficarem à espera de ``is_campaign_send_window`` fora do intervalo 8–20.
DEFAULT_TEST_SEND_HOUR_START = 5
DEFAULT_TEST_SEND_HOUR_END = 23

SAMPLE_LEADS_CSV = """name,phone_number,status
Joao Silva,4137982599,1
Maria Santos,4137982908,1
Pedro Oliveira,4137982960,1
josé,4137982595,1
marcos,4137981318,1
vilena,4137981306,1
mauricio,4137981158,1
josias,4137981130,1
oberval,4137980902,1
juao,4137984981,1
mariua,4137984966,1
gustavio,4137984019,1
leocadio,4137984061,1
"""

SAMPLE_LEADS_ROW_COUNT = len(
    [ln for ln in SAMPLE_LEADS_CSV.strip().splitlines()[1:] if ln.strip()]
)


def first_connected_uazapi_instance_id(db_conn, user_id: int) -> int:
    """
    Primeira instância Uazapi com status ``connected`` do utilizador (dono da campanha).

    A API admin exige ``instance_ids`` pertencentes ao ``user_id`` do payload; alinha-se
    à listagem por utilizador alvo. Se não existir nenhuma, cria uma mínima.
    """
    from psycopg2.extras import RealDictCursor

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id FROM instances
            WHERE user_id = %s
              AND status = 'connected'
              AND COALESCE(api_provider, 'megaapi') = 'uazapi'
            ORDER BY id ASC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur.execute(
            """
            INSERT INTO instances (user_id, name, apikey, status, api_provider)
            VALUES (%s, %s, %s, 'connected', 'uazapi')
            RETURNING id
            """,
            (user_id, "test-uazapi-connected", "fake-token"),
        )
        new_id = int(cur.fetchone()["id"])
        db_conn.commit()
        return new_id

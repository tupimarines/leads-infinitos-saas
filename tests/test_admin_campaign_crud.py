#!/usr/bin/env python3
"""
Integration tests for admin campaign CRUD (Sprint 4).
Tests the full flow: admin creates campaign for another user via POST /api/admin/campaigns.
Mocks UazapiService to avoid real API calls.
"""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def db_conn():
    from app import get_db_connection
    conn = get_db_connection()
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def ensure_admin_user(db_conn):
    """Ensure the superadmin user exists and return its id."""
    from psycopg2.extras import RealDictCursor
    email = 'augustogumi@gmail.com'
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return row['id']
        cur.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, true) RETURNING id",
            (email, 'fakehash')
        )
        uid = cur.fetchone()['id']
        db_conn.commit()
        return uid


@pytest.fixture
def ensure_target_user(db_conn):
    """Create a regular target user and return its id."""
    from psycopg2.extras import RealDictCursor
    email = 'target_user_test@example.com'
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return row['id']
        cur.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, false) RETURNING id",
            (email, 'fakehash')
        )
        uid = cur.fetchone()['id']
        db_conn.commit()
        return uid


@pytest.fixture
def ensure_instance(db_conn, ensure_target_user):
    """Create a Uazapi instance for the target user."""
    from psycopg2.extras import RealDictCursor
    user_id = ensure_target_user
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO instances (user_id, name, apikey, status, api_provider)
               VALUES (%s, 'test-instance', 'fake-token', 'connected', 'uazapi')
               RETURNING id""",
            (user_id,)
        )
        inst_id = cur.fetchone()['id']
        db_conn.commit()
        return inst_id


@pytest.fixture
def ensure_scraping_job(db_conn, ensure_target_user):
    """Create a scraping job with a temp CSV of leads for the target user."""
    from psycopg2.extras import RealDictCursor
    user_id = ensure_target_user

    leads_data = [
        {"title": "Empresa A", "phone": "5511999990001", "address": "Rua A"},
        {"title": "Empresa B", "phone": "5511999990002", "address": "Rua B"},
        {"title": "Empresa C", "phone": "5511999990003", "address": "Rua C"},
        {"title": "Empresa D", "phone": "5511999990004", "address": "Rua D"},
        {"title": "Empresa E", "phone": "5511999990005", "address": "Rua E"},
    ]

    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(leads_data, tmp)
    tmp.close()

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO scraping_jobs (user_id, keyword, locations, total_results, status, results_path, created_at)
               VALUES (%s, 'test-keyword', 'SP', %s, 'completed', %s, NOW())
               RETURNING id""",
            (user_id, len(leads_data), tmp.name)
        )
        job_id = cur.fetchone()['id']
        db_conn.commit()

    yield job_id
    os.unlink(tmp.name)


def test_admin_create_campaign(
    db_conn, ensure_admin_user, ensure_target_user,
    ensure_instance, ensure_scraping_job
):
    """Superadmin cria campanha para outro usuário via POST /api/admin/campaigns."""
    from psycopg2.extras import RealDictCursor

    admin_id = ensure_admin_user
    target_user_id = ensure_target_user
    instance_id = ensure_instance
    job_id = ensure_scraping_job

    with patch('services.uazapi.UazapiService.create_advanced_campaign') as mock_uazapi:
        mock_uazapi.return_value = {
            'folder_id': 'test_folder_123',
            'status': 'queued',
            'count': 5
        }

        from app import app
        app.config['TESTING'] = True
        app.config['LOGIN_DISABLED'] = True

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(admin_id)

            payload = {
                'user_id': target_user_id,
                'name': 'Test Admin Campaign',
                'job_id': job_id,
                'message_templates': ['Olá {nome}, teste admin!'],
                'instance_ids': [instance_id],
                'use_uazapi_sender': True,
                'rotation_mode': 'single',
                'send_hour_start': 8,
                'send_hour_end': 20,
                'send_saturday': False,
                'send_sunday': False,
            }

            res = client.post(
                '/api/admin/campaigns',
                data=json.dumps(payload),
                content_type='application/json'
            )

            assert res.status_code == 200 or res.status_code == 201, \
                f"Expected 2xx, got {res.status_code}: {res.get_data(as_text=True)}"

            data = json.loads(res.get_data(as_text=True))
            campaign_id = data.get('campaign_id') or data.get('id')
            assert campaign_id, f"Response missing campaign_id: {data}"

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
        campaign = cur.fetchone()
        assert campaign is not None, "Campaign not found in DB"
        assert campaign['user_id'] == target_user_id
        assert campaign.get('created_by_admin_id') == admin_id

        cur.execute(
            "SELECT COUNT(*) as cnt FROM campaign_instances WHERE campaign_id = %s",
            (campaign_id,)
        )
        assert cur.fetchone()['cnt'] >= 1, "No campaign_instances rows"

        cur.execute(
            "SELECT COUNT(*) as cnt FROM campaign_leads WHERE campaign_id = %s",
            (campaign_id,)
        )
        leads_count = cur.fetchone()['cnt']
        assert leads_count > 0, "No leads populated"

    print(f"✅ test_admin_create_campaign passed — campaign {campaign_id}, {leads_count} leads")


def test_admin_new_campaign_page_loads(db_conn, ensure_admin_user):
    """GET /admin/campaigns/new retorna 200."""
    admin_id = ensure_admin_user

    from app import app
    app.config['TESTING'] = True
    app.config['LOGIN_DISABLED'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)

        res = client.get('/admin/campaigns/new')
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        html = res.get_data(as_text=True)
        assert 'Nova Campanha (Admin)' in html

    print("✅ test_admin_new_campaign_page_loads passed")


def test_admin_edit_campaign_page_loads(
    db_conn, ensure_admin_user, ensure_target_user,
    ensure_instance, ensure_scraping_job
):
    """GET /admin/campaigns/<id>/edit retorna 200 com dados preenchidos."""
    from psycopg2.extras import RealDictCursor

    admin_id = ensure_admin_user
    target_user_id = ensure_target_user
    instance_id = ensure_instance

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO campaigns (user_id, name, message_template, status, created_at)
               VALUES (%s, 'Edit Test Campaign', '["Hello"]', 'pending', NOW())
               RETURNING id""",
            (target_user_id,)
        )
        campaign_id = cur.fetchone()['id']
        cur.execute(
            "INSERT INTO campaign_instances (campaign_id, instance_id) VALUES (%s, %s)",
            (campaign_id, instance_id)
        )
        db_conn.commit()

    from app import app
    app.config['TESTING'] = True
    app.config['LOGIN_DISABLED'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)

        res = client.get(f'/admin/campaigns/{campaign_id}/edit')
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        html = res.get_data(as_text=True)
        assert 'Edit Test Campaign' in html
        assert 'Editar Campanha' in html

    print("✅ test_admin_edit_campaign_page_loads passed")


if __name__ == '__main__':
    test_admin_new_campaign_page_loads(None, None)
    print("Run with pytest for full integration tests.")

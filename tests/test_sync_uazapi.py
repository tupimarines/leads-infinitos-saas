"""Testes para utils/sync_uazapi - match de telefone API ↔ DB."""
import json

import pytest

from utils.sync_uazapi import (
    normalize_phone_for_match,
    _extract_phones_from_message,
    _message_matches_folder,
    _reconcile_send_by_messages,
    sync_campaign_leads_from_uazapi,
)


class TestNormalizePhoneForMatch:
    def test_remotejid_remove_suffix(self):
        assert "554137984966" in normalize_phone_for_match("554137984966@s.whatsapp.net")
        assert "554137984966" in normalize_phone_for_match("554137984966@g.us")

    def test_adds_55_variant(self):
        variants = normalize_phone_for_match("4137984966")
        assert "4137984966" in variants
        assert "554137984966" in variants

    def test_strips_non_digits(self):
        assert "554137984966" in normalize_phone_for_match("55 41 37984-966")

    def test_empty_or_short_returns_empty(self):
        assert normalize_phone_for_match("") == set()
        assert normalize_phone_for_match("123") == set()


class TestExtractPhonesFromMessage:
    def test_chatid_remotejid(self):
        m = {"chatid": "554137984966@s.whatsapp.net"}
        assert _extract_phones_from_message(m) == "554137984966"

    def test_number_plain(self):
        m = {"number": "554137984966"}
        assert _extract_phones_from_message(m) == "554137984966"

    def test_senderpn(self):
        m = {"senderpn": "554137984966@s.whatsapp.net"}
        assert _extract_phones_from_message(m) == "554137984966"

    def test_jid(self):
        m = {"jid": "554137984966@s.whatsapp.net"}
        assert _extract_phones_from_message(m) == "554137984966"

    def test_order_priority_number_first(self):
        m = {"number": "5511999999999", "chatid": "554137984966@s.whatsapp.net"}
        assert _extract_phones_from_message(m) == "5511999999999"

    def test_nested_dict(self):
        """Valor dict em chatid: parse recursivo extrai number do objeto aninhado."""
        m = {"chatid": {"number": "554137984966"}}
        assert _extract_phones_from_message(m) == "554137984966"

    def test_wa_id_phoneNumber(self):
        m = {"wa_id": "554137984966"}
        assert _extract_phones_from_message(m) == "554137984966"
        m2 = {"phoneNumber": "554137984966"}
        assert _extract_phones_from_message(m2) == "554137984966"

    def test_empty_or_invalid_returns_none(self):
        assert _extract_phones_from_message({}) is None
        assert _extract_phones_from_message({"foo": "bar"}) is None
        assert _extract_phones_from_message({"number": "123"}) is None  # < 10 dígitos


class TestMessageMatchesFolder:
    def test_send_folder_id(self):
        assert _message_matches_folder({"send_folder_id": "abc123"}, "abc123") is True
        assert _message_matches_folder({"send_folder_id": "x"}, "y") is False

    def test_sendFolderId_alias(self):
        assert _message_matches_folder({"sendFolderId": "f1"}, "f1") is True

    def test_empty_send_folder_id_no_match(self):
        assert _message_matches_folder({"send_folder_id": ""}, "r373bae082f0849") is False

    def test_case_insensitive(self):
        assert _message_matches_folder({"send_folder_id": "r373bae082f0849"}, "R373BAE082F0849") is True


class TestReconcileSendByMessages:
    def test_reconcile_matches_sent_and_failed_by_phone_variants(self):
        class _FakeCursor:
            def __init__(self):
                self.rowcount = 0
                self._next = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params=None):
                if "SELECT id, phone, whatsapp_link" in query:
                    self._next = [
                        {"id": 101, "phone": "41999998888", "whatsapp_link": None},
                        {"id": 202, "phone": "41911112222", "whatsapp_link": None},
                    ]

            def fetchall(self):
                return self._next

            def fetchone(self):
                return {}

        class _FakeConn:
            def cursor(self, cursor_factory=None):
                return _FakeCursor()

        sent_ids, failed_ids = _reconcile_send_by_messages(
            conn=_FakeConn(),
            campaign_id=77,
            lead_ids=[101, 202],
            sent_phones={"5541999998888"},
            failed_phones={"5541911112222"},
        )

        assert sent_ids == {101}
        assert failed_ids == {202}


class TestSyncCampaignLeadsFromUazapi:
    def test_sync_listfolders_updates_leads_when_folder_present(self):
        """SSOT listfolders: pasta encontrada na lista → atualiza leads (sem ramo pesado list_messages)."""

        class _FakeCursor:
            def __init__(self, conn):
                self.conn = conn
                self.rowcount = 0
                self._fetchall = []
                self._fetchone = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params=None):
                compact = " ".join(query.split())
                self.conn.executed.append((compact, params))
                self.rowcount = 0
                self._fetchall = []
                self._fetchone = {}

                if "SELECT uazapi_folder_id, uazapi_last_send_lead_ids, cadence_config, enable_cadence" in compact and "use_uazapi_sender" in compact:
                    self._fetchone = {"enable_cadence": True, "use_uazapi_sender": True, "uazapi_folder_id": "folder-fu1"}
                elif "FROM campaign_stage_sends css" in compact:
                    self._fetchall = [{
                        "id": 1,
                        "stage": "follow1",
                        "instance_id": 12,
                        "instance_remote_jid": "5511999999999@s.whatsapp.net",
                        "uazapi_folder_id": "folder-fu1",
                        "lead_ids": [55],
                        "planned_count": 1,
                        "status": "running",
                        "apikey": "token-1",
                    }]
                elif "SELECT id, phone, whatsapp_link FROM campaign_leads" in compact:
                    self._fetchall = [{"id": 55, "phone": "11999999999", "whatsapp_link": None}]
                elif "UPDATE campaign_leads SET status = 'sent'" in compact:
                    assert params[0] == 3
                    assert params[1] == "follow1"
                    assert list(params[5]) == [55]
                    self.rowcount = 1
                elif "UPDATE campaign_stage_sends SET success_count = %s, failed_count = %s, status = %s" in compact:
                    self.rowcount = 1

            def fetchall(self):
                return self._fetchall

            def fetchone(self):
                return self._fetchone

        class _FakeConn:
            def __init__(self):
                self.executed = []
                self.committed = False

            def cursor(self, cursor_factory=None):
                return _FakeCursor(self)

            def commit(self):
                self.committed = True

        class _FakeUazapiService:
            def list_folders(self, _token, context=None):
                return [{
                    "id": "folder-fu1",
                    "status": "done",
                    "log_sucess": 1,
                    "log_failed": 0,
                    "log_total": 1,
                }]

            def list_messages(self, *_a, **_kw):
                raise AssertionError("list_messages não deve ser chamado neste cenário só-listfolders")

            def message_find(self, *_a, **_kw):
                return {"messages": []}

        conn = _FakeConn()
        result = sync_campaign_leads_from_uazapi(
            conn=conn,
            campaign_id=501,
            token="main-token",
            folder_id="folder-fu1",
            uazapi_service=_FakeUazapiService(),
            debug=False,
        )

        assert result["updated_sent"] == 1
        assert result["updated_failed"] == 0
        assert conn.committed is True

    def test_sync_orphan_folder_listmessages_error_marks_send_failed(self, capsys):
        """Pasta ausente em listfolders + listmessages None (ex. 400) → failed, liberta instância."""

        class _FakeCursor:
            def __init__(self, conn):
                self.conn = conn
                self.rowcount = 0
                self._fetchall = []
                self._fetchone = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params=None):
                compact = " ".join(query.split())
                self.conn.executed.append((compact, params))
                self.rowcount = 0
                self._fetchall = []
                self._fetchone = {}

                if "SELECT uazapi_folder_id, uazapi_last_send_lead_ids, cadence_config, enable_cadence" in compact and "use_uazapi_sender" in compact:
                    self._fetchone = {"enable_cadence": True, "use_uazapi_sender": True, "uazapi_folder_id": "orphan-folder"}
                elif "FROM campaign_stage_sends css" in compact:
                    self._fetchall = [{
                        "id": 99,
                        "stage": "initial",
                        "instance_id": 108,
                        "instance_remote_jid": "5511999999999@s.whatsapp.net",
                        "uazapi_folder_id": "r34orphan",
                        "lead_ids": [1, 2],
                        "planned_count": 22,
                        "status": "running",
                        "apikey": "tok-orphan",
                    }]
                elif "UPDATE campaign_stage_sends" in compact and "status = 'failed'" in compact:
                    assert params == (99,)
                    self.rowcount = 1

            def fetchall(self):
                return self._fetchall

            def fetchone(self):
                return self._fetchone

        class _FakeConn:
            def __init__(self):
                self.executed = []
                self.committed = False

            def cursor(self, cursor_factory=None):
                return _FakeCursor(self)

            def commit(self):
                self.committed = True

        class _FakeUazapiService:
            def list_folders(self, _token, context=None):
                return [{"id": "other-folder", "status": "done", "log_sucess": 1, "log_total": 1}]

            def list_messages(self, _token, _folder_id, message_status=None, page=1, page_size=500, context=None):
                return None

        conn = _FakeConn()
        result = sync_campaign_leads_from_uazapi(
            conn=conn,
            campaign_id=187,
            token="tok-orphan",
            folder_id="orphan-folder",
            uazapi_service=_FakeUazapiService(),
            debug=False,
        )

        assert result["updated_sent"] == 0
        assert result["updated_failed"] == 0
        assert conn.committed is True
        orphan_updates = [
            p for q, p in conn.executed if "UPDATE campaign_stage_sends" in q and "status = 'failed'" in q
        ]
        assert orphan_updates == [(99,)]

        out = capsys.readouterr().out
        orphan_line = next(
            (
                ln
                for ln in out.splitlines()
                if '"event": "uazapi_stage_send_orphan_probe_null"' in ln
            ),
            None,
        )
        assert orphan_line is not None
        payload = json.loads(orphan_line)
        assert payload == {
            "event": "uazapi_stage_send_orphan_probe_null",
            "campaign_id": 187,
            "send_id": 99,
            "instance_id": 108,
            "folder_id": "r34orphan",
        }

    def test_sync_listfolders_none_does_not_mark_failed_even_if_probe_would_be_none(self):
        """Task 7 / AC5: falha em list_folders (None) não deve marcar failed nem chamar probe."""

        class _FakeCursor:
            def __init__(self, conn):
                self.conn = conn
                self.rowcount = 0
                self._fetchall = []
                self._fetchone = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params=None):
                compact = " ".join(query.split())
                self.conn.executed.append((compact, params))
                self.rowcount = 0
                self._fetchall = []
                self._fetchone = {}

                if "SELECT uazapi_folder_id, uazapi_last_send_lead_ids, cadence_config, enable_cadence" in compact and "use_uazapi_sender" in compact:
                    self._fetchone = {"enable_cadence": True, "use_uazapi_sender": True, "uazapi_folder_id": "x"}
                elif "FROM campaign_stage_sends css" in compact:
                    self._fetchall = [{
                        "id": 100,
                        "stage": "initial",
                        "instance_id": 1,
                        "instance_remote_jid": None,
                        "uazapi_folder_id": "folder-net-err",
                        "lead_ids": [1],
                        "planned_count": 1,
                        "status": "running",
                        "apikey": "tok",
                    }]
                elif "UPDATE campaign_stage_sends" in compact and "last_sync_at" in compact:
                    self.rowcount = 1

            def fetchall(self):
                return self._fetchall

            def fetchone(self):
                return self._fetchone

        class _FakeConn:
            def __init__(self):
                self.executed = []
                self.committed = False

            def cursor(self, cursor_factory=None):
                return _FakeCursor(self)

            def commit(self):
                self.committed = True

        class _FakeUazapiService:
            def list_folders(self, _token, context=None):
                return None

            def list_messages(self, *_a, **_kw):
                raise AssertionError("list_messages não deve ser chamado quando list_folders falhou")

        conn = _FakeConn()
        sync_campaign_leads_from_uazapi(
            conn=conn,
            campaign_id=200,
            token="tok",
            folder_id="x",
            uazapi_service=_FakeUazapiService(),
            debug=False,
        )

        failed_updates = [
            p for q, p in conn.executed if "UPDATE campaign_stage_sends" in q and "status = 'failed'" in q
        ]
        assert failed_updates == []
        last_sync_only = [
            (q, p)
            for q, p in conn.executed
            if "UPDATE campaign_stage_sends" in q
            and "last_sync_at" in q
            and "status = 'failed'" not in q
        ]
        assert any(p == (100,) for _q, p in last_sync_only)

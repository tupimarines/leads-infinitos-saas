"""Testes para utils/sync_uazapi - match de telefone API ↔ DB."""
import pytest

from utils.sync_uazapi import (
    normalize_phone_for_match,
    _extract_phones_from_message,
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

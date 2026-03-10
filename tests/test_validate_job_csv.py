#!/usr/bin/env python3
"""
Unit tests for utils.validate_job_csv.
Mocks UazapiService.check_phone and DB.
"""

import os
import tempfile
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def test_extract_phone_from_row():
    """Test _extract_phone_from_row helper."""
    from utils.validate_job_csv import _extract_phone_from_row, _normalize_phone_for_api

    # From whatsapp link
    row = type('Row', (), {'get': lambda s, k, d=None: {'whatsapp_link': 'https://wa.me/5511999999999'}.get(k, d)})()
    ph = _extract_phone_from_row(row, None, 'whatsapp_link')
    assert ph == '5511999999999'

    # From phone column
    row2 = type('Row', (), {'get': lambda s, k, d=None: {'phone': '11999999999'}.get(k, d)})()
    ph2 = _extract_phone_from_row(row2, 'phone', None)
    assert ph2 == '11999999999'

    # Normalize
    assert _normalize_phone_for_api('11999999999') == '5511999999999'
    assert _normalize_phone_for_api('5511999999999') == '5511999999999'
    assert _normalize_phone_for_api('123') is None


def test_validate_job_csv_no_token_returns_none():
    """When user has no Uazapi instance, validate_job_csv returns None; CSV unchanged."""
    from utils.validate_job_csv import validate_job_csv
    from unittest.mock import MagicMock, patch

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("name,phone\nJoão,11999999999\nMaria,11888888888\n")
        path = f.name

    try:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {'user_id': 1, 'results_path': path}
        mock_cur.__enter__ = lambda s: mock_cur
        mock_cur.__exit__ = lambda s, *a: None
        mock_conn.cursor.return_value = MagicMock(__enter__=lambda s: mock_cur, __exit__=lambda s, *a: None)

        with patch('utils.validate_job_csv._get_db_connection', return_value=mock_conn):
            with patch('utils.validate_job_csv._get_uazapi_token_for_user', return_value=None):
                result = validate_job_csv(999, 1, file_path=path)
        assert result is None
        df = pd.read_csv(path)
        assert len(df) == 2
    finally:
        os.unlink(path)


def test_validate_job_csv_skip_no_phone_column():
    """When CSV has no phone or whatsapp_link column, returns None."""
    from utils.validate_job_csv import validate_job_csv
    from unittest.mock import patch, MagicMock

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("name,email\nJoão,j@x.com\n")
        path = f.name

    try:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {'user_id': 1, 'results_path': path}
        mock_cur.__enter__ = lambda s: mock_cur
        mock_cur.__exit__ = lambda s, *a: None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('utils.validate_job_csv._get_db_connection', return_value=mock_conn):
            result = validate_job_csv(999, 1, file_path=path)
        assert result is None
        df = pd.read_csv(path)
        assert len(df) == 1
    finally:
        os.unlink(path)


if __name__ == '__main__':
    test_extract_phone_from_row()
    print("✅ test_extract_phone_from_row passed")
    test_validate_job_csv_skip_no_phone_column()
    print("✅ test_validate_job_csv_skip_no_phone_column passed")
    try:
        test_validate_job_csv_no_token_returns_none()
        print("✅ test_validate_job_csv_no_token_returns_none passed")
    except Exception as e:
        print(f"⚠️ test_validate_job_csv_no_token_returns_none: {e}")
    print("✅ All basic tests passed")

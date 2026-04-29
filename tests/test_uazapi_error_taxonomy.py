from utils.uazapi_error_taxonomy import classify_create_advanced_error, format_last_error_for_db


def test_classify_no_session_message():
    r = {
        "uazapi_request_failed": True,
        "http_status": 500,
        "error_body": {"error": "No session"},
    }
    cat, msg, http = classify_create_advanced_error(r)
    assert cat == "no_session"
    assert http == 500


def test_classify_transient_503():
    r = {
        "uazapi_request_failed": True,
        "http_status": 503,
        "error_body": {"message": "unavailable"},
    }
    cat, _m, _h = classify_create_advanced_error(r)
    assert cat == "transient_http"


def test_format_last_error_json():
    s = format_last_error_for_db(
        {"uazapi_request_failed": True, "http_status": 500, "error_body": {"a": 1}},
        "no_session",
    )
    assert "no_session" in s

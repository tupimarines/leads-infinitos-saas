"""Pytest: hooks partilhados."""


def pytest_configure(config):
    """Evita que `.env` inválido (ex.: UTF-16 / null em chaves) quebre imports que chamam `load_dotenv()` no import."""
    try:
        import dotenv
    except ImportError:
        return
    _real = dotenv.load_dotenv

    def _load_dotenv_safe(*args, **kwargs):
        try:
            return _real(*args, **kwargs)
        except ValueError:
            return False

    dotenv.load_dotenv = _load_dotenv_safe

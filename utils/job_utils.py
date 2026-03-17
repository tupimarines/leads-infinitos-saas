"""Utilitários compartilhados para jobs de scraping."""


class JobCancelledError(Exception):
    """Levantada quando o usuário cancela o job durante execução."""

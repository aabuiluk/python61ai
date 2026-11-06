"""Спільний SSL-контекст для запитів до зовнішніх сервісів.

На macOS Python із python.org часто не має системних сертифікатів, і
urllib видає CERTIFICATE_VERIFY_FAILED. Тоді беремо сертифікати з пакета
certifi (якщо він встановлений). Перевірку сертифікатів НЕ вимикаємо.
"""

from __future__ import annotations

import ssl


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

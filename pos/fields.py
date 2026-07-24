"""
Custom encrypted model fields.

FLAG-009 Option B: replaces django-fernet-fields (unmaintained since 2019) with
a thin wrapper around cryptography.fernet using the FERNET_KEY from settings.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


_FERNET_TOKEN_PREFIX = 'gAAAAA'


def _fernet():
    key = getattr(settings, 'FERNET_KEY', None)
    if not key:
        raise ImproperlyConfigured('FERNET_KEY is required to use FernetEncryptedField')
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class FernetEncryptedField(models.CharField):
    """CharField that transparently encrypts at rest using Fernet (AES-128-CBC + HMAC).

    NULL/empty values pass through untouched. On read, values that don't look like
    a Fernet token (no 'gAAAAA' prefix) are returned as-is to tolerate legacy plain
    text rows during migration. Decryption failures raise ValueError instead of
    returning corrupted bytes.
    """

    description = 'Fernet-encrypted character field'

    def from_db_value(self, value, expression, connection):
        if value is None or value == '':
            return value
        if not isinstance(value, str) or not value.startswith(_FERNET_TOKEN_PREFIX):
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                f'Failed to decrypt {self.name}: invalid token or wrong FERNET_KEY'
            ) from exc

    def to_python(self, value):
        if value is None or value == '':
            return value
        if isinstance(value, str) and value.startswith(_FERNET_TOKEN_PREFIX):
            try:
                return _fernet().decrypt(value.encode()).decode()
            except InvalidToken:
                return value
        return value

    def get_prep_value(self, value):
        if value is None or value == '':
            return value
        if isinstance(value, str) and value.startswith(_FERNET_TOKEN_PREFIX):
            return value
        token = _fernet().encrypt(str(value).encode())
        return token.decode()

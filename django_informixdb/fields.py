from base64 import b64encode
from django.db import models
from django.core.exceptions import ValidationError


class IfxBlobField(models.BinaryField):
    description = "BinaryField that support JDBC Informix driver. Accepts bytearray()"
    
    def _to_bytearray(self, obj):
        if isinstance(obj, bytearray):
            return obj
        if isinstance(obj, bytes):
            return bytearray(obj)
        obj_size = obj.blobSize if hasattr(obj, 'blobSize') else int(obj.length())
        obj_bytes = obj.getBytes(1, obj_size)
        return bytearray([x % 256 for x in obj_bytes])

    def value_to_string(self, obj):
        """Binary data is serialized as base64"""
        return b64encode(self._to_bytearray(self.value_from_object(obj))).decode('ascii')

    def to_python(self, value):
        return self._to_bytearray(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        if not prepared:
            value = self.get_prep_value(value)
        if value is not None:
            new_value = self._to_bytearray(value)
            return connection.Database.Binary(new_value)
        return value
    
    # TODO should this field just return bytearray?
    # Currently accepts bytearray but returns BlobObject that needs
    # to be converted to bytearray using for example Field.to_python()


class TrimCharField(models.CharField):
    description = "CharField that ignores trailing spaces in data"

    def from_db_value(self, value, expression, connection, *ignore):
        if value:
            return value.rstrip()
        return value


class CharToBooleanField(models.BooleanField):
    def __init__(self, *args, null=True, **kwargs):
        kwargs['max_length'] = 1
        super().__init__(*args, null=null, **kwargs)

    def get_internal_type(self):
        return "CharField"

    def from_db_value(self, value, expression, connection, *ignore):
        if self.null and value is None:
            return None
        return value == 'Y'

    def get_db_prep_value(self, value, connection, prepared=False):
        if value is True:
            return 'Y'
        elif value is False:
            return 'N'

        elif value is None and self.null:
            return None

        # - Not sure if this is the right place/thing to do here
        raise ValidationError(
            self.error_messages['invalid_nullable' if self.null else 'null'],
            code='invalid',
            params={'value': value},
        )

    def to_python(self, value):
        return value

    def get_prep_value(self, value):
        return value

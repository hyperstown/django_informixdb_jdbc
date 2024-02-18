import datetime
import decimal
import uuid

from django.conf import settings
from django.utils import timezone
from django.utils.encoding import force_str
from django.db.backends.base.operations import BaseDatabaseOperations
from django.db.models import Aggregate
from django.db.backends import utils as backend_utils
from django.utils.dateparse import parse_date, parse_datetime, parse_time

from .fields import CharToBooleanField


class DatabaseOperations(BaseDatabaseOperations):

    compiler_module = "django_informixdb.compiler"
    cast_char_field_without_max_length = "LVARCHAR" # No text equivalent

    def quote_name(self, name):
        return name

    def last_insert_id(self, cursor, table_name, pk_name):
        operation = "SELECT DBINFO('sqlca.sqlerrd1') FROM SYSTABLES WHERE TABID=1"
        cursor.execute(operation)
        row = cursor.fetchone()
        last_identity_val = None
        if row is not None:
            last_identity_val = int(row[0])
        return last_identity_val

    def fulltext_search_sql(self, field_name):
        return "LIKE '%%%s%%'" % field_name

    def lookup_cast(self, lookup_type, internal_type=None):
        if lookup_type in ('iexact', 'icontains', 'istartswith', 'iendswith'):
            return "LOWER(CAST(%s as lvarchar))"
        return "%s"

    def check_expression_support(self, expression):
        if isinstance(expression, Aggregate):
            if expression.function in ['STDDEV_POP', 'STDDEV_SAMP']:
                expression.function = 'STDDEV'
            if expression.function in ['VAR_POP', 'VAR_SAMP']:
                expression.function = 'VARIANCE'

    def date_extract_sql(self, lookup_type, field_name):
        sqlmap = {
            'week_day': 'WEEKDAY',
            'month': 'MONTH',
            'day': 'DAY'
        }
        return "%s(%s)" % (sqlmap[lookup_type], field_name)

    def start_transaction_sql(self):
        return "BEGIN WORK"

    def end_transaction_sql(self, success=True):
        return "COMMIT WORK"

    def savepoint_create_sql(self, sid):
        return "SAVEPOINT %s" % sid

    def savepoint_commit_sql(self, sid):
        return "RELEASE SAVEPOINT %s" % sid

    def get_db_converters(self, expression):
        converters = super(DatabaseOperations, self).get_db_converters(expression)
        internal_type = expression.output_field.get_internal_type()
        if internal_type == 'BooleanField':
            converters.append(lambda value, *_: True if value == 1 else False)
        elif internal_type == 'NullBooleanField':
            converters.append(lambda value, *_: True if value == 1 else False if value == 0 else None)
        elif internal_type == 'DateTimeField':
            converters.append(self.convert_datetimefield_value)
        elif internal_type == 'DateField':
            converters.append(self.convert_datefield_value)
        elif internal_type == 'TimeField':
            converters.append(self.convert_timefield_value)
        elif internal_type == 'DecimalField':
            converters.append(self.convert_decimalfield_value)
        elif internal_type == 'UUIDField':
            converters.append(self.convert_uuidfield_value)
        return converters

    def convert_decimalfield_value(self, value, expression, connection, *ignore):
        value = backend_utils.format_number(value, expression.output_field.max_digits,
                                            expression.output_field.decimal_places)
        if value is not None:
            return decimal.Decimal(value)

    def convert_datefield_value(self, value, expression, connection, *ignore):
        if value is not None and not isinstance(value, datetime.date):
            value = parse_date(value)
        return value

    def convert_datetimefield_value(self, value, expression, connection, *ignore):
        if value is not None and not isinstance(value, datetime.datetime):
            value = parse_datetime(value)
        return value

    def convert_timefield_value(self, value, expression, connection, *ignore):
        if value is not None and not isinstance(value, datetime.time):
            value = parse_time(value)
        return value

    def convert_uuidfield_value(self, value, expression, connection, *ignore):
        if value is not None:
            value = uuid.UUID(value)
        return value

    def adapt_datefield_value(self, value):
        # default db format?
        return value.strftime('%d/%m/%Y') if value else value

    def adapt_datetimefield_value(self, value):
        # TODO: fix this, convert to DATETIME YEAR TO FRACTION(5)
        # value is like '2016-05-23 12:26:56.111909+00:00',
        # since informix only support fraction(5),
        # we need remove the last digit for micro-seconds
        if settings.USE_TZ and value:
            tz = timezone.get_current_timezone()
            value = timezone.make_naive(value, tz)
        return value.strftime('%Y-%m-%d %H:%M:%S.f')[:-1] if value else value

    def adapt_timefield_value(self, value):
        return value

    def sql_flush(self, style, tables, sequences=(), reset_sequences=True, allow_cascade=False):
        # The reset_sequences keyword arg is provided by Django 3.1 and later,
        # but like the sequences arg, it is ignored by this driver.

        # NB: The generated SQL below is specific to Informix
        sql = ['%s %s %s;' % (
            style.SQL_KEYWORD('DELETE'),
            style.SQL_KEYWORD('FROM'),
            style.SQL_FIELD(self.quote_name(table))
        ) for table in tables]
        return sql

    def last_executed_query(self, cursor, sql, params):
        """
        Return a string of the query last executed by the given cursor, with
        placeholders replaced with actual values.

        `sql` is the raw query containing placeholders and `params` is the
        sequence of parameters.
        NOTE: not really sure if that's the best way to do that but it works
        """

        # Convert params to contain string values.
        def to_string(s):
            return force_str(s, strings_only=True, errors="replace")

        if isinstance(params, (list, tuple)):
            u_params = tuple(to_string(val) for val in params)
        elif params is None:
            u_params = ()
        else:
            u_params = {to_string(k): to_string(v) for k, v in params.items()}

        formatted_sql = sql.replace('?', "%r")
        return formatted_sql % u_params
    
    def conditional_expression_supported_in_where_clause(self, expression):
        return not isinstance(expression.output_field, CharToBooleanField)

    #def bulk_insert_sql(self, fields, placeholder_rows):
    #    placeholder_rows_sql = (", ".join(row) for row in placeholder_rows)
    #    values_sql = ", ".join("(%s)" % sql for sql in placeholder_rows_sql)
    #    return "VALUES " + values_sql

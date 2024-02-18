"""
informix database backend for Django.

Requires informixdb
"""
import os
import time
import atexit
import logging
import platform

from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.base.validation import BaseDatabaseValidation
from django.core.exceptions import ImproperlyConfigured
from django.core import signals
from django.utils.encoding import smart_str

from .client import DatabaseClient
from .creation import DatabaseCreation
from .introspection import DatabaseIntrospection
from .operations import DatabaseOperations
from .features import DatabaseFeatures
from .schema import DatabaseSchemaEditor

import jpype
import jaydebeapi

logger = logging.getLogger(__name__)


def decoder(value, encodings=('utf-8',)):
    """This decoder tries multiple encodings before giving up"""

    if not isinstance(value, bytes):
        raise ValueError(f"Not a binary type: {value} {type(value)}")

    for enc in encodings:
        try:
            return value.decode(enc)
        except UnicodeDecodeError:
            pass

    raise UnicodeDecodeError("unable to decode `{value}`")


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = 'informixdb'
    Database = jaydebeapi

    data_types = {
        'AutoField': 'serial',
        'BigAutoField': 'bigserial',
        'BinaryField': 'blob',
        'BooleanField': 'boolean',
        'CharField': 'lvarchar(%(max_length)s)',
        'CommaSeparatedIntegerField': 'lvarchar(%(max_length)s)',
        'DateField': 'date',
        'DateTimeField': 'datetime year to fraction(5)',
        'DecimalField': 'decimal',
        'DurationField': 'interval',
        'FileField': 'lvarchar(%(max_length)s)',
        'FilePathField': 'lvarchar(%(max_length)s)',
        'FloatField': 'smallfloat',
        'IntegerField': 'integer',
        'BigIntegerField': 'bigint',
        'IPAddressField': 'char(15)',
        'GenericIPAddressField': 'char(39)',
        'NullBooleanField': 'boolean',
        'OneToOneField': 'integer',
        'PositiveIntegerField': 'integer',
        'PositiveSmallIntegerField': 'smallint',
        'SlugField': 'lvarchar(%(max_length)s)',
        'SmallIntegerField': 'smallint',
        'TextField': 'lvarchar(%(max_length)s)',
        'TimeField': 'datetime hour to second',
        'UUIDField': 'char(32)',
    }

    data_type_check_constraints = {
        'PositiveIntegerField': '%(column)s >= 0',
        'PositiveSmallIntegerField': '%(column)s >= 0',
    }

    operators = {
        'exact': '= %s',
        'iexact': "= LOWER(%s)",
        'contains': "LIKE %s ESCAPE '\\'",
        'icontains': "LIKE LOWER(%s) ESCAPE '\\'",
        'gt': '> %s',
        'gte': '>= %s',
        'lt': '< %s',
        'lte': '<= %s',
        'startswith': "LIKE %s ESCAPE '\\'",
        'endswith': "LIKE %s ESCAPE '\\'",
        'istartswith': "LIKE LOWER(%s) ESCAPE '\\'",
        'iendswith': "LIKE LOWER(%s) ESCAPE '\\'",
        'regex': 'LIKE %s',
        'iregex': 'LIKE %s',
    }

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an expression
    # or the result of a bilateral transformation).
    # In those cases, special characters for LIKE operators (e.g. \, *, _) should be
    # escaped on database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a wildcard for
    # the LIKE operator.
    pattern_esc = r"REPLACE(REPLACE(REPLACE({}, '\', '\\'), '%%', '\%%'), '_', '\_')"
    pattern_ops = {
        'contains': "LIKE '%%' ESCAPE '\\' || {} || '%%'",
        'icontains': "LIKE '%%' ESCAPE '\\' || UPPER({}) || '%%'",
        'startswith': "LIKE {} ESCAPE '\\' || '%%'",
        'istartswith': "LIKE UPPER({}) ESCAPE '\\' || '%%'",
        'endswith': "LIKE '%%' ESCAPE '\\' || {}",
        'iendswith': "LIKE '%%' ESCAPE '\\' || UPPER({})",
    }
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations
    SchemaEditorClass = DatabaseSchemaEditor
    validation_class = BaseDatabaseValidation

    def __init__(self, *args, **kwargs):
        super(DatabaseWrapper, self).__init__(*args, **kwargs)

        options = self.settings_dict.get('OPTIONS', {})

        self._validation_enabled = options.get("VALIDATE_CONNECTION", False)
        self._validation_interval = options.get("VALIDATION_INTERVAL", 300)
        self._next_validation = time.time() + self._validation_interval
        self._validation_query = options.get("VALIDATION_QUERY", "SELECT 1 FROM sysmaster:sysdual")
        self.encodings = options.get('encodings', ('utf-8', 'cp1252', 'iso-8859-1'))
        # make lookup operators to be collation-sensitive if needed
        self.collation = options.get('collation', None)
        if self.collation:
            self.operators = dict(self.__class__.operators)
            ops = {}
            for op in self.operators:
                sql = self.operators[op]
                if sql.startswith('LIKE '):
                    ops[op] = '%s COLLATE %s' % (sql, self.collation)
            self.operators.update(ops)

        self.features = self.features_class(self)
        self.ops = self.ops_class(self)
        self.client = self.client_class(self)
        self.creation = self.creation_class(self)
        self.introspection = self.introspection_class(self)
        self.validation = self.validation_class(self)

        atexit.register(self.shut_down_connection)

    def validate_connection(self):
        """
        This method is invoked at the start of a request to verify an existing
        connection is still functional. This is achieved by doing a simple query
        against the database.
        """
        if not self._validation_enabled or time.time() < self._next_validation:
            return

        self._next_validation = time.time() + self._validation_interval

        # We call close_if_unusable_or_obsolete to ensure obsolete connections
        # are closed before we consider validating them. This will result in
        # close_if_unusable_or_obsolete being called twice since it is also
        # called automatically by django. This is ok since the second call is
        # essentially a no-op.
        self.close_if_unusable_or_obsolete()
        if self.connection is not None and not self.is_usable():
            self.close()

    def get_driver_path(self):
        system = platform.system().upper()
        if system == 'WINDOWS':
            system = system + platform.architecture()[0]
        try:
            return self.DRIVER_MAP[system]
        except KeyError:
            raise ImproperlyConfigured('cannot locate informix driver, please specify')

    def get_connection_params(self):
        settings = self.settings_dict

        if 'DSN' not in settings:
            for k in ['NAME', 'SERVER', 'USER', 'PASSWORD', 'DRIVERS']:
                if k not in settings:
                    raise ImproperlyConfigured('{} is a required setting for an informix connection'.format(k))
        conn_params = settings.copy()

        # Ensure the driver is set in the options
        # options = conn_params.get('OPTIONS', {})
        # if 'DRIVER' not in options or options['DRIVER'] is None:
        #     raise ImproperlyConfigured('DRIVER is a required setting for an informix connection')
            # options['DRIVER'] = self.get_driver_path()
        # if platform.system().upper() != 'WINDOWS':
            # sqlhosts = os.environ.get('INFORMIXSQLHOSTS')
            # if not sqlhosts or not os.path.exists(sqlhosts):
            #     raise ImproperlyConfigured('Cannot find Informix sqlhosts at {}'.format(sqlhosts))
            # if not os.path.exists(options['DRIVER']):
            #     raise ImproperlyConfigured('cannot find Informix driver at {}'.format(options['DRIVER']))
        # conn_params['OPTIONS'] = options
        
        if 'DRIVERS' not in conn_params or conn_params['DRIVERS'] is None:
            raise ImproperlyConfigured('DRIVER is a required setting for an informix connection')
        
        for driver in conn_params['DRIVERS']:
            if not os.path.exists(driver):
                raise ImproperlyConfigured('cannot find Informix driver at {}'.format(driver))

        conn_params['AUTOCOMMIT'] = conn_params.get("AUTOCOMMIT", False)

        return conn_params

    def _normalize_pv(self, param_value):
        if isinstance(param_value, list):
            return ",".join(param_value)
        return param_value

    def get_new_connection(self, conn_params):
        driver_name = "com.informix.jdbc.IfxDriver"
        url = "jdbc:informix-sqli://{0}:{1}/{2}:INFORMIXSERVER={3}".format(
            conn_params['HOST'], conn_params['PORT'], conn_params['NAME'], conn_params['SERVER']
        )

        for param_key, param_value in conn_params.get('PARAMETERS', {}).items():
            url += f";{param_key.upper()}={self._normalize_pv(param_value)}"

        username = conn_params['USER']
        password = conn_params['PASSWORD']
        jars = conn_params['DRIVERS']

        if jpype.isJVMStarted() and not jpype.isThreadAttachedToJVM():
            jpype.attachThreadToJVM()
            jpype.java.lang.Thread.currentThread().setContextClassLoader(
                jpype.java.lang.ClassLoader.getSystemClassLoader()
            )
        
        self.connection = jaydebeapi.connect(driver_name, url, [username, password], jars=jars)
        
        #self.connection.setencoding(encoding='UTF-8')

        # This will set SQL_C_CHAR, SQL_C_WCHAR and SQL_BINARY to 32000
        # this max length is actually just what the database internally
        # supports. e.g. the biggest `LONGVARCHAR` field in informix is
        # 32000, you would need to split anything bigger over multiple fields
        # This limit will not effect schema defined lengths, which will just
        # truncate values greater than the limit.
        self.connection.maxwrite = 32000

        # self.connection.add_output_converter(-101, lambda r: r.decode('utf-8'))  # Constraints
        # self.connection.add_output_converter(-391, lambda r: r.decode('utf-16-be'))  # Integrity Error

        # self.connection.add_output_converter(pyodbc.SQL_CHAR, self._output_converter)
        # self.connection.add_output_converter(pyodbc.SQL_WCHAR, self._output_converter)
        # self.connection.add_output_converter(pyodbc.SQL_VARCHAR, self._output_converter)
        # self.connection.add_output_converter(pyodbc.SQL_WVARCHAR, self._output_converter)
        # self.connection.add_output_converter(pyodbc.SQL_LONGVARCHAR, self._output_converter)
        # self.connection.add_output_converter(pyodbc.SQL_WLONGVARCHAR, self._output_converter)

        if 'LOCK_MODE_WAIT' in conn_params['OPTIONS']:
            self.set_lock_mode(wait=conn_params['OPTIONS']['LOCK_MODE_WAIT'])

        return self.connection

    def shut_down_connection(self):
        if jpype.isJVMStarted():
            logger.debug("Shutting down JVM")
            #jpype.shutdownJVM() # hangs after many searches..
            os._exit(3) # it's not recommended but only this works reliably 

    def _unescape(self, raw):
        """
        For some reason the Informix ODBC driver seems to double escape new line characters.

        This little handler converts them back.

        @todo: See if this applies to other escape characters
        """
        return raw.replace(b'\\n', b'\n')

    def _output_converter(self, raw):
        return decoder(self._unescape(raw), self.encodings)

    def init_connection_state(self):
        pass

    def create_cursor(self, name=None):
        logging.debug('Creating Informix cursor')
        return CursorWrapper(self.connection.cursor(), self)

    def _set_autocommit(self, autocommit):
        with self.wrap_database_errors:
            self.connection.autocommit = autocommit

    def check_constraints(self, table_names=None):
        """
        To check constraints, we set constraints to immediate. Then, when, we're done we must ensure they
        are returned to deferred.
        """
        self.cursor().execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.cursor().execute('SET CONSTRAINTS ALL DEFERRED')

    def _start_transaction_under_autocommit(self):
        """
        Start a transaction explicitly in autocommit mode.
        """
        start_sql = self.ops.start_transaction_sql()
        self.cursor().execute(start_sql)

    def is_usable(self):
        # We create a cursor and then explicitly close it as there is a bug
        # that is encountered when relying on garbage collection to close the
        # cursor: https://github.com/mkleehammer/pyodbc/issues/585
        try:
            cursor = self.connection.cursor()
        except BaseException as exc: # TODO replace with jdbc sql exceptions
            logger.info(f"error creating cursor: {exc}")
            return False

        try:
            cursor.execute(self._validation_query)
            return True
        except BaseException as exc: # TODO replace with jdbc sql exceptions
            logger.info(f"error executing query: {exc}")
            return False
        finally:
            # We close the cursor explicitly to work around the pyodbc bug
            # described at the top of this function. If closing the cursor
            # fails we set the return value to `False`. Otherwise it
            # remains what was returned in the `try` or `except` block, which
            # depends on whether `cursor.execute` succeeded or not.
            try:
                cursor.close()
            except BaseException as exc: # TODO replace with jdbc sql exceptions
                logger.info(f"error closing cursor: {exc}")
                return False

    def read_dirty(self):
        self.cursor().execute('set isolation to dirty read;')

    def read_committed(self):
        self.cursor().execute('set isolation to committed read;')

    def read_repeatable(self):
        self.cursor().execute('set isolation to repeatable read;')

    def read_committed_with_update_locks(self):
        self.cursor().execute('set isolation to committed read retain update locks;')

    def set_lock_mode(self, wait=None):
        """
        This will set database LOCK MODE WAIT at connection level
        Application can use this property to override the default server
        process for accessing a locked row or table.
        The default value is 0 (do not wait for the lock).
        Possible values:
           -1 - WAIT until the lock is released.
           0 - DO NOT WAIT, end the operation, and return with error.
           nn - WAIT for nn seconds for the lock to be released.
        """
        if wait == 0:
            sql = 'SET LOCK MODE TO NOT WAIT'
        elif wait == -1:
            sql = 'SET LOCK MODE TO WAIT'
        else:
            sql = 'SET LOCK MODE TO WAIT {}'.format(wait)

        self.cursor().execute(sql)

    def _commit(self):
        if self.connection is not None:
            with self.wrap_database_errors:
                return self.cursor().execute("COMMIT WORK")

    def _rollback(self):
        if self.connection is not None:
            with self.wrap_database_errors:
                return self.cursor().execute("ROLLBACK WORK")


class CursorWrapper(object):
    """
    A wrapper around the pyodbc's cursor that takes in account a) some pyodbc
    DB-API 2.0 implementation and b) some common ODBC driver particularities.
    """
    def __init__(self, cursor, connection):
        self.active = True
        self.cursor = cursor
        self.connection = connection
        self.driver_charset = False  # connection.driver_charset
        self.last_sql = ''
        self.last_params = ()

    def close(self):
        if self.active:
            self.active = False
            self.cursor.close()

    def format_sql(self, sql, params):
        if isinstance(sql, str):
            # FreeTDS (and other ODBC drivers?) doesn't support Unicode
            # yet, so we need to encode the SQL clause itself in utf-8
            sql = smart_str(sql, self.driver_charset)

        # pyodbc uses '?' instead of '%s' as parameter placeholder.
        if params is not None:
            pass
            # sql = sql % tuple('?' * len(params))

        return sql

    def format_params(self, params):
        fp = []
        if params is not None:
            for p in params:
                if isinstance(p, str):
                    if self.driver_charset:
                        # FreeTDS (and other ODBC drivers?) doesn't support Unicode
                        # yet, so we need to encode parameters in utf-8
                        fp.append(smart_str(p, self.driver_charset))
                    else:
                        fp.append(p)

                elif isinstance(p, bytes):
                    fp.append(p)

                elif isinstance(p, type(True)):
                    if p:
                        fp.append(1)
                    else:
                        fp.append(0)

                else:
                    fp.append(p)

        return tuple(fp)

    def execute(self, sql, params=None):
        self.last_sql = sql
        sql = self.format_sql(sql, params)
        params = self.format_params(params)
        self.last_params = params
        return self.cursor.execute(sql, params)

    def executemany(self, sql, params_list=()):
        if not params_list:
            return None
        raw_pll = [p for p in params_list]
        sql = self.format_sql(sql, raw_pll[0])
        params_list = [self.format_params(p) for p in raw_pll]
        return self.cursor.executemany(sql, params_list)

    def format_rows(self, rows):
        return list(map(self.format_row, rows))

    def format_row(self, row):
        """
        Decode data coming from the database if needed and convert rows to tuples
        (pyodbc Rows are not sliceable).
        """
        if self.driver_charset:
            for i in range(len(row)):
                f = row[i]
                # FreeTDS (and other ODBC drivers?) doesn't support Unicode
                # yet, so we need to decode utf-8 data coming from the DB
                if isinstance(f, bytes):
                    row[i] = f.decode(self.driver_charset)
        return tuple(row)

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            row = self.format_row(row)
        # Any remaining rows in the current set must be discarded
        # before changing autocommit mode when you use FreeTDS
        #self.cursor.nextset()
        return row

    def fetchmany(self, chunk):
        return self.format_rows(self.cursor.fetchmany(chunk))

    def fetchall(self):
        return self.format_rows(self.cursor.fetchall())

    def __getattr__(self, attr):
        if attr in self.__dict__:
            return self.__dict__[attr]
        return getattr(self.cursor, attr)

    def __iter__(self):
        return iter(self.cursor)


def _validate_connection(**kwargs):
    for conn in connections.all():
        if isinstance(conn, DatabaseWrapper):
            conn.validate_connection()


signals.request_started.connect(_validate_connection)

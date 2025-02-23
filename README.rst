Django InformixDB (JDBC edition)
==================

A database driver for Django to connect to an Informix database via JDBC.

**Some limitations**:

- Does not support default values
- Informix automatically creates indexes on foreign keys, but Django attempts to do that
  manually; the current implementation here just attempts to catch the error on index
  creation. It may unintentionally catch other index creation errors where the index
  already exists.


Configure settings.py
---------------------

Django’s settings.py uses the following to connect to an Informix database:

.. code-block:: python

    'default': {
        'ENGINE': 'django_informixdb',
        'NAME': 'myproject',
        'USER': 'ifxserver',
        'PASSWORD': 'passw0rd',
        'HOST': 'localhost',
        'PORT': '61012',
        'SERVER': 'informix',
        'DRIVERS' : [
            "/path/to/jdbc-driver.jar",
            # ... and other JAR extensions (if needed)
        ],
        'PARAMETERS': {
            'CLIENT_LOCALE': 'en_us.57372',
        },
        'CONNECTION_RETRY': {
            'MAX_ATTEMPTS': 10,
        },
        'TEST': {
            'NAME': 'myproject',
            'CREATE_DB': False
        }
    }

Using with the Docker Informix Dev Database
-------------------------------------------

The docker image from IBM for the Informix developer database image behaves a little differently compared to other images. As such it needs a little extra handling, and doesn't seem to work with docker-compose

Firstly we need to download and getting it running:

.. code-block:: bash

    $ docker run -itd --name iif_developer_edition --privileged -p 9088:9088 -p 9089:9089 -p 27017:27017 \
    -p 27018:27018 -p 27883:27883 -e LICENSE=accept ibmcom/informix-developer-database:latest

This will download the image if it doesn't exist, and then run it with the name ``iif_developer_edition``. The first time this run, the image will do a bunch of initial setup stuff. As we used the ``-d`` option, it will run in the background as a detached process. So don't be concerned that you do not see anything in the output.

You can stop and restart the container with:

.. code-block:: bash

    $ docker stop iif_developer_edition
    $ docker start iif_developer_edition

It seems that the Informix ODBC driver does not currently support creating databases. So we will need to do
that manually, by attaching to the running container

.. code-block:: bash

    $ docker attach iif_developer_edition


This will give you a shell on the running container, and you can therefore use dbaccess to create your database.
You can exit this shell using ``Ctrl-p`` ``Ctrl-q`` without shutting down the whole container.

This Django database adaptor for Informix requires transaction support to be enabled in our database.
This is not the default within the Informix Developer image.  So you need to enable it on a per database basis:

.. code-block:: bash

    $ docker attach iif_developer_edition
    $ ontape -s -B <DB_NAME>

Again, you can detach using ``Ctrl-p`` ``Ctrl-q``.

Finally you need to ensure that our local dev database is included in the ``sqlhosts`` file. e.g.:

.. code-block:: bash

    dev    onsoctcp    localhost    9088

You should now be able to point Django to our local test database using the syntax detailed above.


Using Django InformixDB with docker-compose
-------------------------------------------

It is possible to use the Informix developer docker image with docker-compose with a little effort.

Example docker-compose.yml

.. code-block:: yaml

    version: '3'

    services:
        db:
            image: ibmcom/informix-developer-database
            tty: true # Needed to ensure container doesn't self terminate
            environment:
                LICENSE: accept
            privileged: true
            ports:
                - "9088:9088"
                - "9089:9089"
                - "27017:27017"
                - "27018:27018"
                - "27883:27883"


The key entry in the compose file which is out of the ordinary is `tty: true`. This allocates a (virtual) TTY to the container. The Informix developer database container expects a `tty` and terminates without one when run inside docker-compose.

Once it is up and running with `docker-compose up` you can run a `bash` shell on the running container with:

.. code-block:: bash

    docker exec -it informix_db_1 bash


Where `informix_db_1` is the name of the running container. From this shell you can create your DB with `dbaccess` etc.

.. warning::

    This approach still requires the SDK to installed locally and the appropriate environmental variables to be set up. Along with entries in `sqlhosts` and `/etc/services`


Testing against an Informix Database
------------------------------------

Due to a bug in the Informix ODBC driver, it is not currently possible to run Django tests normally. Specifically, it is not possible for Django to create a test database. As such, you will need to do it manually. By default Django will attempt to create a database with a name equal to the default database name with a ``test_`` prefix. e.g. if you database name is ``my_database``, the test database name would be ``test_my_database``.  This can be overridden with the ``NAME`` option under ``TEST``.

To prevent Django from attempting to create a test database, set the ``CREATE_DB`` option
under ``TEST`` to ``False``: see 'Configure settings.py' above.

You can follow the steps above, in the section on using Informix locally with Docker to create a test database. Then when running the test you can tell Django to re-use an existing database, rather than trying to create a new one with the ``-k`` parameter:

.. code-block:: bash

    ./manage.py test -k


For django_informixdb Developers
--------------------------------

To run the django_informixdb test suite, you need to set the INFORMIXDIR environment variable, and the tests
expect an Informix database at host "informix". Change that host in `test/conftest.py` if you need to.
Then run the test suite with:

    tox

This will run the tests under Django 3 and 4.


Docker based testing
^^^^^^^^^^^^^^^^^^^^

If you don't want to install the Informix libraries and multiple versions of Python locally, then you can test within
Docker containers.

Try using the helper script `test-in-docker.sh`, or inspect the script and adapt for your own purposes.

Requirements: Docker 19.03.2 or newer and Docker Compose 1.24.1 or newer.


Upstream Release History
---------------

Version 1.11.4

- Update pyproject.toml / setup.cfg and update docker tests to use Rocky9 instead of Centos7

Version 1.11.3

- Switch from setup.py to pyproject.toml / setup.cfg

Version 1.11.2

- Begin support for Python 3.10

Version 1.11.1

- Convert from TravisCI to GitHub Actions

Version 1.11.0

- Begin support for Django 4.x
- End support for Django 2.x
- End support for Python 3.6

Version 1.10.1

- Fix for https://github.com/reecetech/django_informixdb/issues/31

Version 1.10.0

- Begin support for Django 3.x
- Begin support for Python 3.9

Version 1.9.1

- Begin support for Python 3.7 and 3.8
- End support for Django 1.x and Python 3.5

Version 1.9.0

- Enable setting a validation interval.

Version 1.8.0

- Enable validating connections at start of request.

Version 1.7.0

- Add CONN_TIMEOUT setting.

Version 1.5.0

- Enable retrying if get connection fails.

Version 1.3.3

- Compability fix for Django 2+ to remove old "context" argument from
  custom fields

Version 1.3.0

- Addressing deprecation warning for conversion functions in Django 2+
- Detect incorrect INFORMIXSQLHOSTS setting earlier for better error message

Version 1.2.0

- Fix bug in DecimalField handling under Django 2+

Version 1.1.0

- Added LOCK_MODE_WAIT option

Version 1.0.0

- Initial public release

**FAQ**:

Q: Boolean fields doesn't work. How to fix it?

A: Import `CharToBooleanField` or `CharToBooleanField2` from `django_informixdb.fields`
Depending on your Informix config it will accept t/f or Y/N values. Choose accordingly.

Q: Can't save anything that contains date field.

A: Depending of your locale Informix will accept different date formats. 
Set your date format in settings like this `DEFAULT_DATE_FORMAT='%d/%m/%Y'`


**TODO**:

- Update tests from pyodbc to jdbc 


**NOTE**:

Contributions and suggestions are very welcome.

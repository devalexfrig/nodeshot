.. _manual-install-label:
**************
Manual Install
**************

.. warning::
    This file describes how to install nodeshot on **Ubuntu Server 13.10**,
    **Ubuntu Server 14.04 LTS** and **Debian 7**
    (other versions of ubuntu and debian should work as well with minor tweaks).

    Other Linux distributions will be good as well but you will have to use the
    package names according to your distribution package manager.

Other linux distributions will work as well but you will need to find the right
package names to install for the specific distribution you are using.

If you are installing for a **development environment** you need to follow the
instructions until the section :ref:`project-configuration`.

If you already have the required dependencies installed you can skip to
:ref:`install-python-packages` and follow until :ref:`project-configuration`.

If you are installing for a **production environment** you need to follow all the
instructions including :ref:`production-instructions`.

**Required dependencies**:

* Postgresql 9.1+
* Geospatial libraries and plugins (GEOS, Proj, Postgresql Contrib, ecc)
* Postgis 2.0+
* Python 2.7+
* Python Libraries (Virtualenv, setuptools, python-dev)

**Required python packages**:

* Django 1.6
* Django Rest Framework 2.4

A full list is available in the `requirements.txt file`_.

.. _requirements.txt file: https://github.com/ninuxorg/nodeshot/blob/master/requirements.txt

**Recommended stack for production environment**:

* Nginx
* uWSGI
* Supervisor
* Redis

.. _install-dependencies:

====================
Install dependencies
====================

First of all I suggest to become ``root`` to avoid typing sudo each time::

    sudo -s

Install the dependencies (Ubuntu 13 and Debian 7)::

    apt-get install python-software-properties software-properties-common build-essential postgresql-9.1 postgresql-server-dev-9.1 postgresql-contrib libxml2-dev python-setuptools python-virtualenv python-dev binutils libproj-dev gdal-bin libpq-dev libgdal1-dev wget checkinstall libjson0-dev python-gdal

If you are using Ubuntu 14 LTS, use this command instead::

    apt-get install python-software-properties software-properties-common build-essential postgresql postgresql-server-dev-9.3 postgresql-contrib libxml2-dev python-setuptools python-virtualenv python-dev binutils libproj-dev gdal-bin libpq-dev libgdal1-dev wget checkinstall libjson0-dev python-gdal

Download and compile **Postgis 2.1.3**::

    wget http://download.osgeo.org/postgis/source/postgis-2.1.3.tar.gz
    tar xfvz postgis-2.1.3.tar.gz
    cd postgis-2.1.3
    ./configure
    make
    checkinstall -y  # if you need to uninstall you can do it  with "dpkg -r postgis"
    cd ..
    rm -rf postgis-2.1.3

If on **debian 7** you get an error complaining about the fact that ``/usr/share/postgresql/9.1/contrib/`` does not exist run this command before ``checkinstall -y``::

    mkdir -p '/usr/share/postgresql/9.1/contrib/postgis-2.1'

.. _create-database:

===============
Create database
===============

Set ``postgres`` user password::

    passwd postgres

Become ``postgres`` user::

    su postgres

Create database, create required postgresql extensions,
create a user and grant all privileges to the newly created DB::

    createdb  nodeshot
    psql nodeshot
    CREATE EXTENSION postgis;
    CREATE EXTENSION postgis_topology;
    CREATE EXTENSION hstore;
    CREATE USER nodeshot WITH PASSWORD 'your_password';
    GRANT ALL PRIVILEGES ON DATABASE "nodeshot" to nodeshot;

exit (press CTRL+D) and go back to being root::

    exit

.. _install-python-packages:

=======================
Install python packages
=======================

First of all, install virtualenvwrapper::

    pip install --use-mirrors virtualenvwrapper
    mkdir /usr/local/lib/virtualenvs
    echo 'echo 'export WORKON_HOME=/usr/local/lib/virtualenvs' >> /usr/local/bin/virtualenvwrapper.sh'
    echo 'source /usr/local/bin/virtualenvwrapper.sh' >> ~/.bashrc
    source ~/.bashrc

Create a **python virtual environment**, a self-contained python installation
which will store all our python packages indipendently from the packages installed systemwide::

    mkvirtualenv nodeshot

Install all the necessary python packages::

    pip install --use-mirrors -U distribute
    pip install --use-mirrors https://github.com/ninuxorg/nodeshot/tarball/master

Now create the directory structure that will contain the project,
a typical web app is usually installed in ``/var/www/``::

    mkdir -p /var/www/nodeshot && cd /var/www/

Create the nodeshot settings folder:

.. code-block:: bash

    nodeshot startproject myproject nodeshot
    cd nodeshot
    chown -R <user>:www-data .  # set group to www-data
    adduser www-data <user>
    chmod 775 . log myproject  # permit www-data to write logs, pid files and static directory
    chmod 750 manage.py myproject/*.py  # do not permit www-data to write on python files*

Replace ``myproject`` with your project name. Avoid names which used by popular python packages, prefer a short and simple name.

Replace ``<user>`` with your current non-root user (the one which created the virtualenv).

.. _project-configuration:

=====================
Project configuration
=====================

Open ``settings.py``::

    vim myproject/settings.py

And edit the following settings:

* ``DOMAIN`` (domain or ip address)
* ``DATABASE`` (host, db, user and password)

If you are installing for **development**, you should put **"localhost"** as ``DOMAIN``.

Setup database and static files (images, css, js):

.. code-block:: bash

    exit  # go back being non-root
    # will prompt you to create a superuser, proceed!
    python manage.py syncdb && python manage.py migrate
    # static files (css, js, images)
    python manage.py collectstatic

If you are installing for **development**, you are done!

You just need to **run the django development server** in order to see the web application:

.. code-block:: bash

    # for development only!
    # listens only on 127.0.0.1
    python manage.py runserver
    # open browser at http://localhost:8000/admin/

    # alternatively, if you need to reach the dev server for other computers
    # on the same LAN, tell it to listen on all the interfaces:
    python manage.py runserver 0.0.0.0:8000

.. _production-instructions:

=======================
Production instructions
=======================

In production you will need more reliable instruments, we recommend the following
software stack:

* **Nginx**: main web server
* **uWSGI**: application server (serves requests to django)
* **Supervisor**: daemon process manager (used to manage uwsgi, celery and celery-beat)
* **Redis**: in memory key-value store (used as a message broker and cache storage)
* **Postfixs**: SMTP server

.. note::
    If you are installing for development you can skip the rest of this chapter.

-----
Nginx
-----

**Nginx** is the recommended webserver for nodeshot.

Alternatively you could also use any other webserver like apache2 or lighthttpd
but it won't be covered in this doc.

You can install from the system packages with the following command::

    sudo -s  # become root again
    apt-get install nginx-full nginx-common openssl zlib-bin

Create a temporary self signed SSL certificate (or install your own one if you already have it)::

    mkdir /etc/nginx/ssl
    cd /etc/nginx/ssl
    openssl req -new -x509 -nodes -out server.crt -keyout server.key

Copy ``uwsgi_params`` file::

    cp /etc/nginx/uwsgi_params /etc/nginx/sites-available/

Create public folder::

    mkdir /var/www/nodeshot/public_html

Create site configuration (replace ``nodeshot.yourdomain.com`` with your domain)::

    vim /etc/nginx/sites-available/nodeshot.yourdomain.com

Paste this configuration and tweak it according to your needs::

    server {
        listen   443; ## listen for ipv4; this line is default and implied
        #listen   [::]:443 default ipv6only=on; ## listen for ipv6

        root /var/www/nodeshot/public_html;
        index index.html index.htm;

        # error log
        error_log /var/www/nodeshot/log/nginx.error.log error;

        # Make site accessible from hostanme
        # change this according to your domain/hostanme
        server_name nodeshot.yourdomain.com;

        # set client body size #
        client_max_body_size 5M;

        ssl on;
        ssl_certificate ssl/server.crt;
        ssl_certificate_key ssl/server.key;

        ssl_session_timeout 5m;

        ssl_protocols SSLv3 TLSv1;
        ssl_ciphers ALL:!ADH:!EXPORT56:RC4+RSA:+HIGH:+MEDIUM:+LOW:+SSLv3:+EXP;
        ssl_prefer_server_ciphers on;

        location / {
            uwsgi_pass 127.0.0.1:3031;
            include uwsgi_params;
            uwsgi_param HTTP_X_FORWARDED_PROTO https;
        }

        location /static/ {
            alias /var/www/nodeshot/myproject/static/;
        }

        location /media/ {
            alias /var/www/nodeshot/myproject/media/;
        }

        #error_page 404 /404.html;

        # redirect server error pages to the static page /50x.html
        #
        #error_page 500 502 503 504 /50x.html;
        #location = /50x.html {
        #    root /usr/share/nginx/www;
        #}

        # deny access to .htaccess files, if Apache's document root
        # concurs with nginx's one
        #
        #location ~ /\.ht {
        #    deny all;
        #}
    }

    server {
        listen   80; ## listen for ipv4; this line is default and implied
        #listen   [::]:80 default ipv6only=on; ## listen for ipv6

        # Make site accessible from hostanme on port 80
        # change this according to your domain/hostanme
        server_name nodeshot.yourdomain.com;

        # redirect all requests to https
        return 301 https://$host$request_uri;
    }

Create a symbolic link to sites-enabled directory::

    ln -s /etc/nginx/sites-available/nodeshot.yourdomain.com /etc/nginx/sites-enabled/nodeshot.yourdomain.com

Test config, ensure it does not fail::

    service nginx configtest

-----
uWSGI
-----

**uWSGI** is a performant and scalable application server written in C.

We will use it to serve requests to the nodeshot django apps.

Install the latest version via pip::

    # deactivate python virtual environment
    deactivate
    # install uwsgi globally
    pip install --use-mirrors uwsgi

Create a new ini configuration file::

    vim /var/www/nodeshot/uwsgi.ini

Paste this config (replace `<myproject>`` with the project name chosen at the beginning)::

    [uwsgi]
    chdir=/var/www/nodeshot
    module=<myproject>.wsgi:application
    master=True
    pidfile=/var/www/nodeshot/uwsgi.pid
    socket=127.0.0.1:3031
    processes=2
    harakiri=20
    max-requests=5000
    vacuum=True
    home=/usr/local/lib/virtualenvs/nodeshot
    enable-threads=True
    env=HTTPS=on
    buffer-size=8192

-----
Redis
-----

Install **Redis**, we will use it as a message broker for *Celery* and as a *Cache Storage*::

    apt-get install redis-server

Install celery bindings in your virtual environment::

    workon nodeshot  # activates virtualenv again
    pip install --use-mirrors -U celery[redis]

Change the ``DEBUG`` setting to ``False``, leaving it to ``True``
**might lead to poor performance or security issues**::

    vim /var/www/nodeshot/myproject/settings.py
    # set DEBUG to False
    DEBUG = False
    # save and exit

You might encounter an issue in the Redis log that says:
"Can't save in background: fork: Cannot allocate memory", in that case run this command::

    echo 1 > /proc/sys/vm/overcommit_memory

Restart redis and ensure is running::

    service redis-server restart
    service redis-server status

----------
Supervisor
----------

We will use `Supervisor`_ as a process manager. Install it via your package
system (or alternatively via pip)::

    apt-get install supervisor

.. _Supervisor: http://supervisord.org/

Create new config file::

    vim /etc/supervisor/conf.d/uwsgi.conf

Save this in ``/etc/supervisor/conf.d/uwsgi.conf``::

    [program:uwsgi]
    user=www-data
    directory=/var/www/nodeshot
    command=uwsgi --ini uwsgi.ini
    autostart=true
    autorestart=true
    stopsignal=INT
    redirect_stderr=true
    stdout_logfile=/var/www/nodeshot/log/uwsgi.log
    stdout_logfile_maxbytes=30MB
    stdout_logfile_backups=5

Repeat in a similar way for celery::

    vim /etc/supervisor/conf.d/celery.conf

And paste (replace ``<myproject>`` with the project name chosen at the beginning)::

    [program:celery]
    user=www-data
    directory=/var/www/nodeshot
    command=/usr/local/lib/virtualenvs/nodeshot/bin/celery -A <myproject> worker -l info
    autostart=true
    autorestart=true
    redirect_stderr=true
    stdout_logfile=/var/www/nodeshot/log/celery.log
    stdout_logfile_maxbytes=30MB
    stdout_logfile_backups=10
    startsecs=10
    stopwaitsecs=600
    numprocs=1

Now repeat in a similar way for celery-beat::

    vim /etc/supervisor/conf.d/celery-beat.conf

And paste (replace ``<myproject>`` with the project name chosen at the beginning)::

    [program:celery-beat]
    user=www-data
    directory=/var/www/nodeshot
    command=/usr/local/lib/virtualenvs/nodeshot/bin/celery -A <myproject> beat -s ./celerybeat-schedule -l info
    autostart=true
    autorestart=true
    redirect_stderr=true
    stdout_logfile=/var/www/nodeshot/log/celery-beat.log
    stdout_logfile_maxbytes=30MB
    stdout_logfile_backups=10
    startsects=10
    numprocs=1

Then run::

    rm /var/www/nodeshot/log/*.log  # reset logs
    supervisorctl update

You can check the status with::

    supervisorctl status

And you can also use other commands like start, stop and restart.

-------
Postfix
-------

Postfix is needed to send emails.
By default postfix is configured to accept local connections only.
It is better to leave this default config unchanged to avoid spam, unless you know what you are doing.

To have a working SMTP server in the least possible steps follow this procedure:

**1. install postfix**::

    apt-get install postfix

**2. open configuration in editor**::

    vim /etc/postfix/main.cf

**3. disable TLS**::

    smtpd_use_tls=no

**4. set** ``myhostname``::

    myhostname = nodeshot.yourdomain.com

**5. add your hostname to** ``destination``::

    mydestination = localhost.localdomain, localhost, nodeshot.yourdomain.com

**6. save changes and restart postfix**::

    service postfix restart

---------------------
Restart all processes
---------------------

Restart all the processes to reload the new configurations::

    service nginx restart && supervisorctl restart all

You should be done!

Test your installation and if everything works as expected.

=======
Support
=======

If you have any issue and you need support reach us at our `Mailing List`_.

.. _Mailing List: http://ml.ninux.org/mailman/listinfo/nodeshot

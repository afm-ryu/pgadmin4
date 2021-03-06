##########################################################################
#
# pgAdmin 4 - PostgreSQL Tools
#
# Copyright (C) 2013 - 2018, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""
Implementation of ServerManager
"""
import os
import datetime
from flask import current_app, session
from flask_security import current_user
from flask_babelex import gettext

from pgadmin.utils.crypto import decrypt
from .connection import Connection
from pgadmin.model import Server
from pgadmin.utils.exception import ConnectionLost


class ServerManager(object):
    """
    class ServerManager

    This class contains the information about the given server.
    And, acts as connection manager for that particular session.
    """

    def __init__(self, server):
        self.connections = dict()

        self.update(server)

    def update(self, server):
        assert (server is not None)
        assert (isinstance(server, Server))

        self.ver = None
        self.sversion = None
        self.server_type = None
        self.server_cls = None
        self.password = None

        self.sid = server.id
        self.host = server.host
        self.hostaddr = server.hostaddr
        self.port = server.port
        self.db = server.maintenance_db
        self.did = None
        self.user = server.username
        self.password = server.password
        self.role = server.role
        self.ssl_mode = server.ssl_mode
        self.pinged = datetime.datetime.now()
        self.db_info = dict()
        self.server_types = None
        self.db_res = server.db_res
        self.passfile = server.passfile
        self.sslcert = server.sslcert
        self.sslkey = server.sslkey
        self.sslrootcert = server.sslrootcert
        self.sslcrl = server.sslcrl
        self.sslcompression = True if server.sslcompression else False
        self.service = server.service

        for con in self.connections:
            self.connections[con]._release()

        self.update_session()

        self.connections = dict()

    def as_dict(self):
        """
        Returns a dictionary object representing the server manager.
        """
        if self.ver is None or len(self.connections) == 0:
            return None

        res = dict()
        res['sid'] = self.sid
        res['ver'] = self.ver
        res['sversion'] = self.sversion
        if hasattr(self, 'password') and self.password:
            # If running under PY2
            if hasattr(self.password, 'decode'):
                res['password'] = self.password.decode('utf-8')
            else:
                res['password'] = str(self.password)
        else:
            res['password'] = self.password

        connections = res['connections'] = dict()

        for conn_id in self.connections:
            conn = self.connections[conn_id].as_dict()

            if conn is not None:
                connections[conn_id] = conn

        return res

    def ServerVersion(self):
        return self.ver

    @property
    def version(self):
        return self.sversion

    def MajorVersion(self):
        if self.sversion is not None:
            return int(self.sversion / 10000)
        raise Exception("Information is not available.")

    def MinorVersion(self):
        if self.sversion:
            return int(int(self.sversion / 100) % 100)
        raise Exception("Information is not available.")

    def PatchVersion(self):
        if self.sversion:
            return int(int(self.sversion / 100) / 100)
        raise Exception("Information is not available.")

    def connection(
            self, database=None, conn_id=None, auto_reconnect=True, did=None,
            async=None, use_binary_placeholder=False, array_to_string=False
    ):
        if database is not None:
            if hasattr(str, 'decode') and \
                    not isinstance(database, unicode):
                database = database.decode('utf-8')
            if did is not None:
                if did in self.db_info:
                    self.db_info[did]['datname'] = database
        else:
            if did is None:
                database = self.db
            elif did in self.db_info:
                database = self.db_info[did]['datname']
            else:
                maintenance_db_id = u'DB:{0}'.format(self.db)
                if maintenance_db_id in self.connections:
                    conn = self.connections[maintenance_db_id]
                    if conn.connected():
                        status, res = conn.execute_dict(u"""
SELECT
    db.oid as did, db.datname, db.datallowconn,
    pg_encoding_to_char(db.encoding) AS serverencoding,
    has_database_privilege(db.oid, 'CREATE') as cancreate, datlastsysoid
FROM
    pg_database db
WHERE db.oid = {0}""".format(did))

                        if status and len(res['rows']) > 0:
                            for row in res['rows']:
                                self.db_info[did] = row
                                database = self.db_info[did]['datname']

                        if did not in self.db_info:
                            raise Exception(gettext(
                                "Could not find the specified database."
                            ))

        if database is None:
            raise ConnectionLost(self.sid, None, None)

        my_id = (u'CONN:{0}'.format(conn_id)) if conn_id is not None else \
            (u'DB:{0}'.format(database))

        self.pinged = datetime.datetime.now()

        if my_id in self.connections:
            return self.connections[my_id]
        else:
            if async is None:
                async = 1 if conn_id is not None else 0
            else:
                async = 1 if async is True else 0
            self.connections[my_id] = Connection(
                self, my_id, database, auto_reconnect, async,
                use_binary_placeholder=use_binary_placeholder,
                array_to_string=array_to_string
            )

            return self.connections[my_id]

    def _restore(self, data):
        """
        Helps restoring to reconnect the auto-connect connections smoothly on
        reload/restart of the app server..
        """
        # restore server version from flask session if flask server was
        # restarted. As we need server version to resolve sql template paths.
        from pgadmin.browser.server_groups.servers.types import ServerType

        self.ver = data.get('ver', None)
        self.sversion = data.get('sversion', None)

        if self.ver and not self.server_type:
            for st in ServerType.types():
                if st.instanceOf(self.ver):
                    self.server_type = st.stype
                    self.server_cls = st
                    break

        # Hmm.. we will not honour this request, when I already have
        # connections
        if len(self.connections) != 0:
            return

        # We need to know about the existing server variant supports during
        # first connection for identifications.
        self.pinged = datetime.datetime.now()
        try:
            if 'password' in data and data['password']:
                data['password'] = data['password'].encode('utf-8')
        except Exception as e:
            current_app.logger.exception(e)

        connections = data['connections']
        for conn_id in connections:
            conn_info = connections[conn_id]
            conn = self.connections[conn_info['conn_id']] = Connection(
                self, conn_info['conn_id'], conn_info['database'],
                conn_info['auto_reconnect'], conn_info['async'],
                use_binary_placeholder=conn_info['use_binary_placeholder'],
                array_to_string=conn_info['array_to_string']
            )

            # only try to reconnect if connection was connected previously and
            # auto_reconnect is true.
            if conn_info['wasConnected'] and conn_info['auto_reconnect']:
                try:
                    conn.connect(
                        password=data['password'],
                        server_types=ServerType.types()
                    )
                    # This will also update wasConnected flag in connection so
                    # no need to update the flag manually.
                except Exception as e:
                    current_app.logger.exception(e)
                    self.connections.pop(conn_info['conn_id'])

    def release(self, database=None, conn_id=None, did=None):
        if did is not None:
            if did in self.db_info and 'datname' in self.db_info[did]:
                database = self.db_info[did]['datname']
                if hasattr(str, 'decode') and \
                        not isinstance(database, unicode):
                    database = database.decode('utf-8')
                if database is None:
                    return False
            else:
                return False

        my_id = (u'CONN:{0}'.format(conn_id)) if conn_id is not None else \
            (u'DB:{0}'.format(database)) if database is not None else None

        if my_id is not None:
            if my_id in self.connections:
                self.connections[my_id]._release()
                del self.connections[my_id]
                if did is not None:
                    del self.db_info[did]

                if len(self.connections) == 0:
                    self.ver = None
                    self.sversion = None
                    self.server_type = None
                    self.server_cls = None
                    self.password = None

                self.update_session()

                return True
            else:
                return False

        for con in self.connections:
            self.connections[con]._release()

        self.connections = dict()
        self.ver = None
        self.sversion = None
        self.server_type = None
        self.server_cls = None
        self.password = None

        self.update_session()

        return True

    def _update_password(self, passwd):
        self.password = passwd
        for conn_id in self.connections:
            conn = self.connections[conn_id]
            if conn.conn is not None or conn.wasConnected is True:
                conn.password = passwd

    def update_session(self):
        managers = session['__pgsql_server_managers'] \
            if '__pgsql_server_managers' in session else dict()
        updated_mgr = self.as_dict()

        if not updated_mgr:
            if self.sid in managers:
                managers.pop(self.sid)
        else:
            managers[self.sid] = updated_mgr
        session['__pgsql_server_managers'] = managers
        session.force_write = True

    def utility(self, operation):
        """
        utility(operation)

        Returns: name of the utility which used for the operation
        """
        if self.server_cls is not None:
            return self.server_cls.utility(operation, self.sversion)

        return None

    def export_password_env(self, env):
        if self.password:
            password = decrypt(
                self.password, current_user.password
            ).decode()
            os.environ[str(env)] = password

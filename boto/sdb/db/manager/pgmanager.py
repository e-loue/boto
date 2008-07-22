# Copyright (c) 2006,2007,2008 Mitch Garnaat http://garnaat.org/
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish, dis-
# tribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the fol-
# lowing conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABIL-
# ITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT
# SHALL THE AUTHOR BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, 
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
from boto.sdb.db.key import Key
import psycopg2
import uuid, sys, os
from boto.exception import *

class PGConverter:
    
    def __init__(self, manager):
        self.manager = manager
        self.type_map = {Key : (self.encode_reference, self.decode_reference)}

    def encode(self, type, value):
        if type in self.type_map:
            encode = self.type_map[type][0]
            return encode(value)
        return value

    def decode(self, type, value):
        if type in self.type_map:
            decode = self.type_map[type][1]
            return decode(value)
        return value

    def encode_prop(self, prop, value):
        if isinstance(value, list):
            s = "{"
            value = ['"%s"' % self.encode(getattr(prop, 'item_type'), v) for v in value]
            s += ','.join(value)
            s += "}"
            return s
        return self.encode(prop.data_type, value)

    def decode_prop(self, prop, value):
        if isinstance(value, list):
            if hasattr(prop, 'item_type'):
                new_value = []
                for v in value:
                    new_value.append(self.decode(getattr(prop, 'item_type'), v))
                return new_value
            else:
                return value
        elif prop.data_type == Key:
            return self.decode_reference(prop, value)
        else:
            return self.decode(prop.data_type, value)

    def encode_reference(self, value):
        if isinstance(value, str) or isinstance(value, unicode):
            return value
        if value == None:
            return ''
        else:
            return value.id

    def decode_reference(self, prop, value):
        if not value:
            return None
        try:
            return prop.reference_class._manager.get_object(None, value)
        except:
            raise ValueError, 'Unable to convert %s to Object' % value

class PGManager(object):

    def __init__(self, cls, db_name, db_user, db_passwd,
                 db_host, db_port, db_table):
        self.cls = cls
        self.db_name = db_name
        self.db_user = db_user
        self.db_passwd = db_passwd
        self.db_host = db_host
        self.db_port = db_port
        self.db_table = db_table
        self.converter = PGConverter(self)
        self._connect()

    def _build_connect_string(self):
        cs = 'dbname=%s user=%s password=%s host=%s port=%d'
        return cs % (self.db_name, self.db_user, self.db_passwd,
                     self.db_host, self.db_port)

    def _connect(self):
        self.connection = psycopg2.connect(self._build_connect_string())
        self.cursor = self.connection.cursor()

    def _object_lister(self, cursor):
        try:
            for row in cursor:
                yield self._object_from_row(row, cursor.description)
        except StopIteration:
            cursor.close()
            raise StopIteration
                
    def _dict_from_row(self, row, description):
        d = {}
        for i in range(0, len(row)):
            d[description[i][0]] = row[i]
        return d

    def _object_from_row(self, row, description):
        d = self._dict_from_row(row, description)
        obj = self.cls(d['id'])
        obj._auto_update = False
        for prop in obj.properties(hidden=False):
            if prop.data_type != Key:
                v = self.decode_value(prop, d[prop.name])
                v = prop.make_value_from_datastore(v)
                if not prop.empty(v):
                    setattr(obj, prop.name, v)
                else:
                    setattr(obj, prop.name, prop.default_value())
        obj._auto_update = True
        return obj

    def _build_insert_qs(self, obj):
        fields = []
        values = []
        for property in obj.properties(hidden=False):
            value = property.get_value_for_datastore(obj)
            if value:
                value = self.encode_value(property, value)
                values.append("'%s'" % value)
                fields.append('"%s"' % property.name)
        qs = 'INSERT INTO "%s" (id,' % self.db_table
        qs += ','.join(fields)
        qs += ") VALUES ('%s'," % obj.id
        qs += ','.join(values)
        qs += ');'
        return qs

    def _build_update_qs(self, obj):
        fields = []
        for property in obj.properties(hidden=False):
            value = property.get_value_for_datastore(obj)
            if value:
                value = self.encode_value(property, value)
                fields.append(""""%s"='%s'""" % (property.name, value))
        qs = 'UPDATE "%s" SET ' % self.db_table
        qs += ','.join(fields)
        qs += """ WHERE "id" = '%s';""" % obj.id
        return qs

    def _get_ddl(self):
        m = sys.modules[self.cls.__module__]
        path = m.__file__
        path = os.path.split(path)[0]
        path = os.path.join(path, 'models')
        path = os.path.join(path, self.cls.__name__ + '.ddl')
        fp = open(path)
        ddl = fp.read()
        fp.close()
        return ddl

    def delete_table(self):
        self.cursor.execute('DROP TABLE "%s";' % self.db_table)
        self.cursor.commit()

    def create_table(self):
        self.cursor.execute(self._get_ddl())
        self.cursor.execute()

    def encode_value(self, prop, value):
        return self.converter.encode_prop(prop, value)

    def decode_value(self, prop, value):
        return self.converter.decode_prop(prop, value)

    def query_sql(self, query, vars=None):
        self.cursor.execute(query, vars)
        return self.cursor.fetchall()

    def lookup(self, cls, name, value):
        parts = []
        qs = 'SELECT * FROM "%s" WHERE ' % self.db_table
        found = False
        for property in cls.properties(hidden=False):
            if property.name == name:
                found = True
                value = self.encode_value(property, value)
                qs += "%s='%s'" % (name, value)
        if not found:
            raise SDBPersistenceError('%s is not a valid field' % key)
        qs += ';'
        print qs
        self.cursor.execute(qs)
        if self.cursor.rowcount == 1:
            row = self.cursor.fetchone()
            return self._object_from_row(row, self.cursor.description)
        elif self.cursor.rowcount == 0:
            raise KeyError, 'Object not found'
        else:
            raise LookupError, 'Multiple Objects Found'

    def query(self, cls, filters=None):
        parts = []
        qs = 'SELECT * FROM "%s"' % self.db_table
        if filters:
            qs += ' WHERE '
            properties = cls.properties()
            for filter, value in filters:
                name, op = filter.strip().split()
                found = False
                for property in properties:
                    if property.name == name:
                        found = True
                        value = self.encode_value(property, value)
                        parts.append(""""%s"%s'%s'""" % (name, op, value))
                if not found:
                    raise SDBPersistenceError('%s is not a valid field' % key)
            qs += ','.join(parts)
        qs += ';'
        print qs
        cursor = self.connection.cursor()
        cursor.execute(qs)
        return self._object_lister(cursor)

    def get_property(self, prop, obj, name):
        qs = """SELECT "%s" FROM "%s" WHERE id='%s';""" % (name, self.db_table, obj.id)
        self.cursor.execute(qs, None)
        if self.cursor.rowcount == 1:
            rs = self.cursor.fetchone()
            for prop in obj.properties(hidden=False):
                if prop.name == name:
                    v = self.decode_value(prop, rs[0])
                    return v
        else:
            raise SDBPersistenceError('problem getting %s' % (prop.name))

    def set_property(self, prop, obj, name, value):
        pass
        value = self.encode_value(prop, value)
        qs = 'UPDATE "%s" SET ' % self.db_table
        qs += "%s='%s'" % (name, self.encode_value(prop, value))
        qs += " WHERE id='%s'" % obj.id
        qs += ';'
        print qs
        self.cursor.execute(qs)
        self.connection.commit()

    def get_object(self, cls, id):
        qs = """SELECT * FROM "%s" WHERE id='%s';""" % (self.db_table, id)
        self.cursor.execute(qs, None)
        if self.cursor.rowcount == 1:
            row = self.cursor.fetchone()
            return self._object_from_row(row, self.cursor.description)
        else:
            raise SDBPersistenceError('%s object with id=%s does not exist' % (cls.__name__, id))
        
    def save_object(self, obj):
        obj._auto_update = False
        if not obj.id:
            obj.id = str(uuid.uuid4())
            qs = self._build_insert_qs(obj)
        else:
            qs = self._build_update_qs(obj)
        print qs
        self.cursor.execute(qs)
        self.connection.commit()
        obj._auto_update = True

    def delete_object(self, obj):
        qs = """DELETE FROM "%s" WHERE id='%s';""" % (self.db_table, obj.id)
        print qs
        self.cursor.execute(qs)
        self.connection.commit()

            
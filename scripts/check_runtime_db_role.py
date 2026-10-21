"""Real PostgreSQL role checks, only the fixed disposable CI database."""
import os
import secrets
from uuid import uuid4
import psycopg
from psycopg import sql
from provision_runtime_database import provision


def main():
    url=os.environ['DATABASE_URL']
    info=psycopg.conninfo.conninfo_to_dict(url)
    if info.get('host') != '127.0.0.1' or info.get('port') != '5432' or info.get('dbname') != 'routing_test':
        raise ValueError('Only fixed local disposable CI database is allowed')
    role='ichiyon_runtime_fixture_'+uuid4().hex[:16]
    table='security_role_'+uuid4().hex[:16]
    password=secrets.token_urlsafe(32)
    with psycopg.connect(url,autocommit=True) as admin:
        try:
            admin.execute(sql.SQL('CREATE TABLE {} (id serial PRIMARY KEY, value text)').format(sql.Identifier(table)))
            with admin.transaction():provision(admin,password,role)
            with psycopg.connect(url,user=role,password=password,autocommit=True) as runtime:
                flags=runtime.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
                assert flags==(False,)*5
                runtime.execute(sql.SQL('INSERT INTO {} (value) VALUES (%s)').format(sql.Identifier(table)),('fixture',))
                assert runtime.execute(sql.SQL('SELECT value FROM {}').format(sql.Identifier(table))).fetchone()==('fixture',)
                runtime.execute(sql.SQL('UPDATE {} SET value=%s').format(sql.Identifier(table)),('updated',))
                runtime.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(table)))
                for query in ('CREATE TABLE public.denied_runtime_ddl(id int)', 'DELETE FROM schema_migrations WHERE false', 'SELECT pg_read_file(%s)'):
                    try:
                        runtime.execute(query,('PG_VERSION',) if '%s' in query else None)
                    except psycopg.errors.InsufficientPrivilege:pass
                    else:raise AssertionError('Forbidden runtime operation succeeded')
                assert not runtime.execute('SELECT pg_has_role(current_user,%s,%s)',(admin.info.user,'MEMBER')).fetchone()[0]
            print('PASS runtime CRUD/sequences; DDL, migration writes, server file reads and privilege inheritance denied')
        finally:
            admin.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(sql.Identifier(table)))
            if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


if __name__=='__main__':main()

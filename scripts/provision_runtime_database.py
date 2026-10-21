"""Provision a separate application role; never change existing credentials.

Operator-only: DATABASE_URL must be the existing migration/admin connection.
Writes a NEW mode-0600 environment fragment; never overwrites any existing file.
The existing deployment helper retains DATABASE_URL for migrations/backups.
"""
import argparse
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql


def provision(connection, password, role='ichiyon_runtime'):
    if not re.fullmatch(r'ichiyon_runtime(?:_fixture_[0-9a-f]{16})?', role):
        raise ValueError('Invalid runtime role')
    if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
        raise ValueError('Role already exists; credentials will not be replaced')
    logging = connection.execute("SELECT current_setting('log_statement'), current_setting('log_min_duration_statement'), current_setting('log_min_duration_sample'), current_setting('log_transaction_sample_rate')").fetchone()
    if logging[0] != 'none' or logging[1] != '-1' or logging[2] != '-1' or float(logging[3]) != 0:
        raise ValueError('Statement logging requires a credential-safe provisioning path')
    owner = connection.execute('SELECT current_user').fetchone()[0]
    connection.execute("SET LOCAL password_encryption = 'scram-sha-256'")
    connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role), sql.Literal(password)))
    database = connection.execute('SELECT current_database()').fetchone()[0]
    connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database), sql.Identifier(role)))
    connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
    connection.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
    connection.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(role)))
    connection.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}').format(sql.Identifier(owner), sql.Identifier(role)))
    connection.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}').format(sql.Identifier(owner), sql.Identifier(role)))
    if connection.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]:
        connection.execute(sql.SQL('REVOKE INSERT, UPDATE, DELETE ON public.schema_migrations FROM {}').format(sql.Identifier(role)))


def runtime_url(admin_url, password):
    parsed = urlsplit(admin_url)
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname or not parsed.username:
        raise ValueError('DATABASE_URL must be a PostgreSQL URL')
    host = parsed.netloc.rsplit('@', 1)[-1]
    return urlunsplit(parsed._replace(netloc='ichiyon_runtime:' + password + '@' + host))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-env-file', type=Path, required=True)
    args = parser.parse_args()
    fd = None
    try:
        url = os.environ['DATABASE_URL']
        password = secrets.token_urlsafe(48)
        value = runtime_url(url, password)
        fd = os.open(args.output_env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with psycopg.connect(url) as connection:
            provision(connection, password)
            # Write/fsync before committing. On any error retain the protected
            # fragment for reconciliation; never log passwords or connection URLs.
            os.write(fd, ('RUNTIME_DATABASE_URL=' + value + '\n').encode())
            os.fsync(fd)
        print('Runtime role created; protected environment fragment written.')
        return 0
    except Exception as exc:
        print('Runtime role provisioning failed: ' + type(exc).__name__)
        return 1
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())

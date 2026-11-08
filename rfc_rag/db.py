import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://rfc:rfc@localhost/rfc"), row_factory=dict_row)

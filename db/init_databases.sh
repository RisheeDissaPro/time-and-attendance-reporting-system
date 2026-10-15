#!/bin/bash
# Runs once, on the first start of the postgres container (empty data volume).
# Creates the Metabase application database and a read-only reporting role.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v reader_password="${METABASE_READER_PASSWORD}" <<-'EOSQL'
    CREATE DATABASE metabase;
    CREATE ROLE metabase_reader LOGIN PASSWORD :'reader_password';
EOSQL

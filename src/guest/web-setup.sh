#!/bin/bash
# Read the SQL password from Key Vault with this VM's managed identity, then
# query sqldb-app. The password is not in this file. Keep the names below in
# step with src/lib/config.py.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

VAULT_URL="https://kv-paas-zeon01.vault.azure.net/"
SECRET_NAME="SqlAdminPassword"
SQL_FQDN="sql-paas-zeon01.database.windows.net"
SQL_DATABASE="sqldb-app"
SQL_ADMIN="sqladmin"
QUERY_MARKER="sql-connected"

apt-get update -y
apt-get install -y curl ca-certificates gnupg python3

rm -f /usr/share/keyrings/microsoft-prod.gpg
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/prod.list -o /etc/apt/sources.list.d/mssql-release.list
apt-get update -y
ACCEPT_EULA=Y apt-get install -y mssql-tools18

cat > /tmp/read-field.py <<'PY'
import json
import sys

data = json.load(sys.stdin)
value = data.get(sys.argv[1], "")
if not value:
    sys.exit(1)
sys.stdout.write(value)
PY

token_json=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if token_json=$(curl -fsS --max-time 10 -H Metadata:true \
        "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net"); then
        break
    fi
    sleep 10
done
if [ -z "$token_json" ]; then
    echo "The VM identity endpoint did not return a token." >&2
    exit 1
fi
token=$(printf '%s' "$token_json" | python3 /tmp/read-field.py access_token)
unset token_json

secret_json=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if secret_json=$(curl -fsS --max-time 30 \
        -H "Authorization: Bearer ${token}" \
        "${VAULT_URL}secrets/${SECRET_NAME}?api-version=7.4"); then
        break
    fi
    sleep 15
done
unset token
if [ -z "$secret_json" ]; then
    echo "Could not read ${SECRET_NAME}. The Key Vault role may still be propagating." >&2
    exit 1
fi
SQLCMDPASSWORD=$(printf '%s' "$secret_json" | python3 /tmp/read-field.py value)
unset secret_json
export SQLCMDPASSWORD
rm -f /tmp/read-field.py

/opt/mssql-tools18/bin/sqlcmd \
    -S "$SQL_FQDN" \
    -d "$SQL_DATABASE" \
    -U "$SQL_ADMIN" \
    -b -h -1 -W \
    -Q "SELECT 1"
unset SQLCMDPASSWORD
echo "$QUERY_MARKER"

"""Run the success checks and print a pass/fail table.

Run from the repository root, after deploy:

    python -m src.verify

After teardown:

    python -m src.verify --after-teardown
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.mgmt.compute.models import RunCommandInput

from src.lib import config
from src.lib.clients import clients
from src.lib.log import info, step
from src.lib.public_ip import operator_ipv4
from src.lib.retry import call_with_retry, wait

WRITE_TEST_SECRET = "WriteTest"


def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return inner
    return str(value)


def query_script() -> list[str]:
    """Ask the VM to read the secret and query SQL. The secret is not echoed."""
    return f"""
set -euo pipefail
token=$(curl -fsS --max-time 20 -H Metadata:true \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net" \
  | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin).get("access_token") or "")')
secret=$(curl -fsS --max-time 30 -H "Authorization: Bearer ${{token}}" \
  "{config.VAULT_URL}secrets/{config.SECRET_NAME}?api-version=7.4" \
  | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin).get("value") or "")')
unset token
if [ -z "$secret" ]; then
  echo RESULT=no-secret
  exit 0
fi
export SQLCMDPASSWORD="$secret"
unset secret
if /opt/mssql-tools18/bin/sqlcmd -S "{config.SQL_FQDN}" -d "{config.SQL_DATABASE}" -U "{config.SQL_ADMIN}" -b -h -1 -W -Q "SELECT 1" >/tmp/sql-out.txt 2>/tmp/sql-err.txt; then
  echo RESULT={config.QUERY_MARKER}
else
  echo RESULT=query-failed
fi
unset SQLCMDPASSWORD
rm -f /tmp/sql-out.txt /tmp/sql-err.txt
""".strip().splitlines()


def write_script() -> list[str]:
    """Ask the VM to write a secret. Only the HTTP status code comes back."""
    return f"""
set -euo pipefail
token=$(curl -fsS --max-time 20 -H Metadata:true \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net" \
  | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin).get("access_token") or "")')
code=$(curl -sS -o /dev/null -w '%{{http_code}}' --max-time 30 -X PUT \
  -H "Authorization: Bearer ${{token}}" -H "Content-Type: application/json" \
  -d '{{"value":"blocked-on-purpose"}}' \
  "{config.VAULT_URL}secrets/{WRITE_TEST_SECRET}?api-version=7.4" || true)
unset token
echo RESULT=${{code}}
""".strip().splitlines()


def command_result(compute, script: list[str], label: str) -> str:
    """Return the RESULT line from the VM. The rest of the output is discarded."""

    def start():
        poller = compute.virtual_machines.begin_run_command(
            config.RESOURCE_GROUP,
            config.VM_WEB,
            RunCommandInput(command_id="RunShellScript", script=script),
        )
        return wait(poller, f"{label} on {config.VM_WEB}", timeout=300)

    result = call_with_retry(start, f"{label} on {config.VM_WEB}")
    parts = []
    for status in result.value or []:
        message = getattr(status, "message", None)
        if message:
            parts.append(message)
    for line in "\n".join(parts).splitlines():
        if "RESULT=" in line:
            return line.strip().split("RESULT=", 1)[1].strip()
    return "unknown"


def firewall_has(rules, address: str) -> bool:
    for rule in rules:
        start = as_text(getattr(rule, "start_ip_address", None))
        end = as_text(getattr(rule, "end_ip_address", None))
        if start == address and end == address:
            return True
    return False


def firewall_allows_all_azure(rules) -> bool:
    for rule in rules:
        start = as_text(getattr(rule, "start_ip_address", None))
        end = as_text(getattr(rule, "end_ip_address", None))
        if start == "0.0.0.0" and end == "0.0.0.0":
            return True
    return False


def dtu_readable(monitor, resource_id: str) -> bool:
    """True when Azure returns at least one DTU percentage point, including zero."""
    for _ in range(6):
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)
        timespan = f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        response = monitor.metrics.list(
            resource_id,
            timespan=timespan,
            interval="PT1M",
            metricnames="dtu_consumption_percent",
            aggregation="Maximum",
        )
        for metric in response.value or []:
            for series in metric.timeseries or []:
                for point in series.data or []:
                    if point.maximum is not None:
                        return True
        time.sleep(30)
    return False


def secret_enabled(azure) -> bool:
    try:
        props = azure.secrets(config.VAULT_URL).list_properties_of_secrets()
    except HttpResponseError:
        return False
    return any(item.name == config.SECRET_NAME and item.enabled for item in props)


def vm_can_read_secrets(authorization, scope: str, principal_id: str) -> bool:
    for item in authorization.role_assignments.list_for_scope(scope):
        definition = as_text(getattr(item, "role_definition_id", ""))
        if getattr(item, "principal_id", None) == principal_id and definition.endswith(config.ROLE_SECRETS_USER_ID):
            return True
    return False


def print_table(rows: list[tuple[str, str, str]]) -> bool:
    step("Results")
    print(f"{'#':<4} {'Check':<62} {'Result'}")
    print("-" * 88)
    failed = False
    for number, label, result in rows:
        print(f"{number:<4} {label:<62} {result}")
        if result.startswith("FAIL"):
            failed = True
    print("-" * 88)
    return not failed


def vault_still_soft_deleted(keyvault) -> bool:
    for item in keyvault.vaults.list_deleted():
        if item.name == config.KEY_VAULT:
            return True
    return False


def after_teardown() -> None:
    azure = clients()
    try:
        azure.resource.resource_groups.get(config.RESOURCE_GROUP)
        group_gone = False
    except ResourceNotFoundError:
        group_gone = True
    purged = group_gone and not vault_still_soft_deleted(azure.keyvault)
    if group_gone and not purged:
        detail = "FAIL  the resource group is gone and the vault name is still soft-deleted"
    elif purged:
        detail = "PASS"
    else:
        detail = f"FAIL  {config.RESOURCE_GROUP} still exists"
    ok = print_table([("7", "Teardown removed the group and purged the vault", detail)])
    if not ok:
        raise SystemExit(1)


def verify() -> None:
    azure = clients()
    try:
        azure.resource.resource_groups.get(config.RESOURCE_GROUP)
    except ResourceNotFoundError:
        raise SystemExit(
            f"{config.RESOURCE_GROUP} does not exist. Deploy first, or use --after-teardown."
        )

    operator_ip = operator_ipv4()
    database = azure.sql.databases.get(config.RESOURCE_GROUP, config.SQL_SERVER, config.SQL_DATABASE)
    sku = database.sku
    sku_name = as_text(sku.name) if sku else ""
    sku_tier = as_text(sku.tier) if sku else ""
    backup = as_text(getattr(database, "current_backup_storage_redundancy", None)) or as_text(
        getattr(database, "requested_backup_storage_redundancy", None)
    )
    basic = sku_name == config.SQL_SKU_NAME and sku_tier == config.SQL_SKU_TIER
    basic = basic and as_text(database.status) == "Online" and backup == config.SQL_BACKUP_STORAGE_REDUNDANCY
    if sku and sku.capacity not in (None, config.SQL_SKU_CAPACITY):
        basic = False

    vault = azure.keyvault.vaults.get(config.RESOURCE_GROUP, config.KEY_VAULT)
    rbac_on = bool(vault.properties.enable_rbac_authorization)
    purge_off = vault.properties.enable_purge_protection is not True
    vm = azure.compute.virtual_machines.get(config.RESOURCE_GROUP, config.VM_WEB)
    principal_id = vm.identity.principal_id if vm.identity else None
    role_ok = bool(principal_id) and vm_can_read_secrets(azure.authorization, vault.id, principal_id)
    vault_ok = rbac_on and purge_off and secret_enabled(azure) and role_ok

    pip = azure.network.public_ip_addresses.get(config.RESOURCE_GROUP, config.PIP_WEB)
    rules = list(azure.sql.firewall_rules.list_by_server(config.RESOURCE_GROUP, config.SQL_SERVER))
    firewall_ok = (
        firewall_has(rules, operator_ip)
        and firewall_has(rules, pip.ip_address or "")
        and not firewall_allows_all_azure(rules)
    )

    query = command_result(azure.compute, query_script(), "SQL check")
    write_code = command_result(azure.compute, write_script(), "Write test")
    info("DTU percentage can take a few minutes to appear.")
    metric_ok = dtu_readable(azure.monitor, database.id)

    rows = [
        ("1", "VM reads the secret and SQL answers", "PASS" if query == config.QUERY_MARKER else f"FAIL  {query}"),
        ("2", "Database is Basic, online, local backups", "PASS" if basic else "FAIL  tier, status, or backup redundancy"),
        ("3", "RBAC vault, purge protection off, VM can read", "PASS" if vault_ok else "FAIL  role, secret, or purge protection"),
        ("4", "SQL firewall allows this machine and the web VM", "PASS" if firewall_ok else "FAIL  expected addresses missing"),
        ("5", "DTU percentage metric is readable", "PASS" if metric_ok else "FAIL  no datapoint yet"),
        ("6", "VM is blocked from writing a secret (403)", "PASS" if write_code == "403" else f"FAIL  HTTP {write_code}"),
        ("7", "Teardown removed the group and purged the vault", "NOT RUN  python -m src.teardown, then --after-teardown"),
    ]
    ok = print_table(rows)
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the managed SQL and Key Vault proof of concept.")
    parser.add_argument(
        "--after-teardown",
        action="store_true",
        help="Check that the resource group is gone and the vault name was purged.",
    )
    args = parser.parse_args()
    if args.after_teardown:
        after_teardown()
        return
    verify()


if __name__ == "__main__":
    main()

"""Create the managed SQL and Key Vault proof of concept.

Run from the repository root:

    python -m src.deploy --plan
    python -m src.deploy
"""

import argparse
import base64
import json
import secrets
import string
import uuid
from pathlib import Path

from azure.core.exceptions import HttpResponseError
from azure.mgmt.authorization.v2022_04_01.models import RoleAssignmentCreateParameters
from azure.mgmt.compute.models import (
    HardwareProfile,
    ImageReference,
    LinuxConfiguration,
    ManagedDiskParameters,
    NetworkInterfaceReference,
    NetworkProfile,
    OSDisk,
    OSProfile,
    RunCommandInput,
    SshConfiguration,
    SshPublicKey,
    StorageProfile,
    VirtualMachine,
    VirtualMachineIdentity,
)
from azure.mgmt.keyvault.models import NetworkRuleSet
from azure.mgmt.keyvault.models import Sku as VaultSku
from azure.mgmt.keyvault.models import VaultCreateOrUpdateParameters, VaultProperties
from azure.mgmt.network.models import (
    AddressSpace,
    NetworkInterface,
    NetworkInterfaceIPConfiguration,
    NetworkSecurityGroup,
    PublicIPAddress,
    PublicIPAddressSku,
    SecurityRule,
    Subnet,
    VirtualNetwork,
)
from azure.mgmt.resource.resources.models import ResourceGroup
from azure.mgmt.sql.models import Database, FirewallRule, Server, Sku

from src.lib import config
from src.lib.clients import clients, subscription
from src.lib.log import info, step
from src.lib.public_ip import operator_ipv4
from src.lib.retry import call_with_retry, wait
from src.lib.sshkey import load_or_create_public_key

GUEST_SCRIPT = Path(__file__).resolve().parent / "guest" / "web-setup.sh"


def new_database_password() -> str:
    """Build a SQL password in memory.

    Azure requires three of uppercase, lowercase, a digit, and a symbol.
    The characters stay inside letters, digits, hyphen, and underscore so
    the value needs no extra quoting. It is not printed.
    """
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("-_"),
    ]
    pool = string.ascii_letters + string.digits + "-_"
    chars = required + [secrets.choice(pool) for _ in range(28)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def signed_in_principal(credential) -> tuple[str, str, str]:
    """Return the object id, tenant id, and principal type for the current sign-in.

    The access token is decoded locally so the Key Vault and the role assignment
    know who is calling. The token is not printed.
    """
    token = credential.get_token("https://management.azure.com/.default").token
    payload = token.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload + padding))
    principal_type = "ServicePrincipal" if claims.get("idtyp") == "app" else "User"
    return str(claims["oid"]), str(claims["tid"]), principal_type


def rule(name: str, priority: int, dest_port: str, source: str) -> SecurityRule:
    return SecurityRule(
        name=name,
        priority=priority,
        access="Allow",
        direction="Inbound",
        protocol="Tcp",
        source_port_range="*",
        destination_port_range=dest_port,
        source_address_prefix=source,
        destination_address_prefix="*",
        description=name.replace("-", " "),
    )


def ensure_role(authorization, scope: str, principal_id: str, principal_type: str, role_name: str, role_id: str) -> None:
    """Assign one built-in role on one resource. A repeat deploy updates the same assignment."""
    subscription_id = scope.split("/")[2]
    assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}|{principal_id}|{role_id}"))
    definition = f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_id}"
    try:
        authorization.role_assignments.create(
            scope,
            assignment,
            RoleAssignmentCreateParameters(
                role_definition_id=definition,
                principal_id=principal_id,
                principal_type=principal_type,
            ),
        )
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) != 409:
            raise
    info(f"Assigned {role_name}.")


def run_guest(compute, password: str) -> str:
    """Run the guest script. Any copy of the password in the output is removed before printing."""
    script = GUEST_SCRIPT.read_text(encoding="utf-8").splitlines()

    def start():
        poller = compute.virtual_machines.begin_run_command(
            config.RESOURCE_GROUP,
            config.VM_WEB,
            RunCommandInput(command_id="RunShellScript", script=script),
        )
        return wait(poller, f"guest setup on {config.VM_WEB}", timeout=1800)

    result = call_with_retry(start, f"Guest setup on {config.VM_WEB}")
    parts = []
    for status in result.value or []:
        message = getattr(status, "message", None)
        if message:
            parts.append(message)
    output = "\n".join(parts).replace(password, "[redacted]")
    tail = "\n".join(output.splitlines()[-15:])
    if tail:
        info(tail)
    return output


def print_plan() -> None:
    step("Plan only. Nothing is created in Azure.")
    info(f"Resource group: {config.RESOURCE_GROUP} ({config.LOCATION})")
    info(f"Key Vault: {config.KEY_VAULT}")
    info(f"Secret: {config.SECRET_NAME}")
    info(f"SQL server: {config.SQL_FQDN} ({config.SQL_LOCATION})")
    info(f"SQL database: {config.SQL_DATABASE} ({config.SQL_SKU_NAME}, {config.SQL_SKU_CAPACITY} DTU)")
    info(f"Network: {config.VNET_NAME} {config.VNET_PREFIX}, {config.WEB_SUBNET} {config.WEB_PREFIX}")
    info(f"VM: {config.VM_WEB} at {config.WEB_PRIVATE_IP}, size {config.VM_SIZE}")
    info("The SQL firewall allows this machine and the web VM. Those addresses are read at deploy time.")
    info("The database password is generated in memory and is not printed.")


def deploy(operator_ip: str) -> None:
    public_key = load_or_create_public_key()
    password = new_database_password()
    azure = clients()
    object_id, tenant_id, principal_type = signed_in_principal(azure.credential)

    step(f"Resource group {config.RESOURCE_GROUP}")
    azure.resource.resource_groups.create_or_update(
        config.RESOURCE_GROUP,
        ResourceGroup(location=config.LOCATION, tags=config.TAGS),
    )

    step(f"Key Vault {config.KEY_VAULT}")
    vault = wait(
        azure.keyvault.vaults.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.KEY_VAULT,
            VaultCreateOrUpdateParameters(
                location=config.LOCATION,
                tags=config.TAGS,
                properties=VaultProperties(
                    tenant_id=tenant_id,
                    sku=VaultSku(family="A", name=config.KEY_VAULT_SKU),
                    access_policies=[],
                    enable_rbac_authorization=True,
                    enable_soft_delete=True,
                    soft_delete_retention_in_days=config.SECRET_RETENTION_DAYS,
                    public_network_access="Enabled",
                    network_acls=NetworkRuleSet(default_action="Allow", bypass="None"),
                ),
            ),
        ),
        config.KEY_VAULT,
    )

    step(f"Role {config.ROLE_VAULT_ADMIN}")
    ensure_role(
        azure.authorization,
        vault.id,
        object_id,
        principal_type,
        config.ROLE_VAULT_ADMIN,
        config.ROLE_VAULT_ADMIN_ID,
    )

    step(f"Secret {config.SECRET_NAME}")

    def store_secret():
        azure.secrets(config.VAULT_URL).set_secret(config.SECRET_NAME, password)

    call_with_retry(store_secret, "Store the SQL password in Key Vault")
    info("Secret stored. The value was not printed.")

    step(f"SQL server {config.SQL_SERVER}")
    wait(
        azure.sql.servers.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.SQL_SERVER,
            Server(
                location=config.SQL_LOCATION,
                tags=config.TAGS,
                administrator_login=config.SQL_ADMIN,
                administrator_login_password=password,
                version="12.0",
                minimal_tls_version=config.SQL_TLS_VERSION,
                public_network_access=config.SQL_PUBLIC_NETWORK_ACCESS,
            ),
        ),
        config.SQL_SERVER,
    )
    azure.sql.firewall_rules.create_or_update(
        config.RESOURCE_GROUP,
        config.SQL_SERVER,
        config.FIREWALL_OPERATOR,
        FirewallRule(start_ip_address=operator_ip, end_ip_address=operator_ip),
    )
    info(f"SQL firewall {config.FIREWALL_OPERATOR} allows this machine.")

    step(f"SQL database {config.SQL_DATABASE}")
    wait(
        azure.sql.databases.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.SQL_SERVER,
            config.SQL_DATABASE,
            Database(
                location=config.SQL_LOCATION,
                tags=config.TAGS,
                sku=Sku(
                    name=config.SQL_SKU_NAME,
                    tier=config.SQL_SKU_TIER,
                    capacity=config.SQL_SKU_CAPACITY,
                ),
                create_mode="Default",
                collation=config.SQL_COLLATION,
                max_size_bytes=config.SQL_MAX_SIZE_BYTES,
                requested_backup_storage_redundancy=config.SQL_BACKUP_STORAGE_REDUNDANCY,
            ),
        ),
        config.SQL_DATABASE,
    )

    step("Web network")
    pip = wait(
        azure.network.public_ip_addresses.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.PIP_WEB,
            PublicIPAddress(
                location=config.LOCATION,
                tags=config.TAGS,
                sku=PublicIPAddressSku(name="Standard"),
                public_ip_allocation_method="Static",
                public_ip_address_version="IPv4",
            ),
        ),
        config.PIP_WEB,
    )
    if not pip.ip_address:
        pip = azure.network.public_ip_addresses.get(config.RESOURCE_GROUP, config.PIP_WEB)
    if not pip.ip_address:
        raise SystemExit("The web public IP address was not allocated.")
    azure.sql.firewall_rules.create_or_update(
        config.RESOURCE_GROUP,
        config.SQL_SERVER,
        config.FIREWALL_WEB,
        FirewallRule(start_ip_address=pip.ip_address, end_ip_address=pip.ip_address),
    )
    info(f"SQL firewall {config.FIREWALL_WEB} allows the web VM.")

    nsg = wait(
        azure.network.network_security_groups.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.NSG_WEB,
            NetworkSecurityGroup(
                location=config.LOCATION,
                tags=config.TAGS,
                security_rules=[
                    rule("Allow-Ssh-Operator", 100, str(config.SSH_PORT), f"{operator_ip}/32"),
                ],
            ),
        ),
        config.NSG_WEB,
    )
    wait(
        azure.network.virtual_networks.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.VNET_NAME,
            VirtualNetwork(
                location=config.LOCATION,
                tags=config.TAGS,
                address_space=AddressSpace(address_prefixes=[config.VNET_PREFIX]),
            ),
        ),
        config.VNET_NAME,
    )
    subnet = wait(
        azure.network.subnets.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.VNET_NAME,
            config.WEB_SUBNET,
            Subnet(
                address_prefix=config.WEB_PREFIX,
                network_security_group=NetworkSecurityGroup(id=nsg.id),
                default_outbound_access=False,
            ),
        ),
        config.WEB_SUBNET,
    )
    nic = wait(
        azure.network.network_interfaces.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.NIC_WEB,
            NetworkInterface(
                location=config.LOCATION,
                tags=config.TAGS,
                ip_configurations=[
                    NetworkInterfaceIPConfiguration(
                        name="ipconfig1",
                        subnet=Subnet(id=subnet.id),
                        private_ip_allocation_method="Static",
                        private_ip_address=config.WEB_PRIVATE_IP,
                        public_ip_address=PublicIPAddress(id=pip.id),
                    )
                ],
            ),
        ),
        config.NIC_WEB,
    )

    step(f"Virtual machine {config.VM_WEB}")
    vm = wait(
        azure.compute.virtual_machines.begin_create_or_update(
            config.RESOURCE_GROUP,
            config.VM_WEB,
            VirtualMachine(
                location=config.LOCATION,
                tags=config.TAGS,
                identity=VirtualMachineIdentity(type=config.IDENTITY_TYPE),
                hardware_profile=HardwareProfile(vm_size=config.VM_SIZE),
                storage_profile=StorageProfile(
                    image_reference=ImageReference(**config.IMAGE),
                    os_disk=OSDisk(
                        create_option="FromImage",
                        managed_disk=ManagedDiskParameters(storage_account_type=config.OS_DISK_SKU),
                        delete_option=config.DELETE_OPTION,
                        disk_size_gb=config.OS_DISK_GB,
                    ),
                ),
                os_profile=OSProfile(
                    computer_name=config.VM_WEB,
                    admin_username=config.ADMIN_USER,
                    linux_configuration=LinuxConfiguration(
                        disable_password_authentication=True,
                        ssh=SshConfiguration(
                            public_keys=[
                                SshPublicKey(
                                    path=f"/home/{config.ADMIN_USER}/.ssh/authorized_keys",
                                    key_data=public_key,
                                )
                            ]
                        ),
                    ),
                ),
                network_profile=NetworkProfile(
                    network_interfaces=[
                        NetworkInterfaceReference(id=nic.id, primary=True, delete_option=config.DELETE_OPTION)
                    ]
                ),
            ),
        ),
        config.VM_WEB,
    )
    principal_id = vm.identity.principal_id if vm.identity else None
    if not principal_id:
        vm = azure.compute.virtual_machines.get(config.RESOURCE_GROUP, config.VM_WEB)
        principal_id = vm.identity.principal_id
    if not principal_id:
        raise SystemExit("The web VM has no managed identity principal id.")

    step(f"Role {config.ROLE_SECRETS_USER}")
    ensure_role(
        azure.authorization,
        vault.id,
        principal_id,
        "ServicePrincipal",
        config.ROLE_SECRETS_USER,
        config.ROLE_SECRETS_USER_ID,
    )

    step("Guest setup")
    output = run_guest(azure.compute, password)
    if config.QUERY_MARKER not in output:
        raise SystemExit("The web VM did not confirm the SQL query. The password was not printed.")

    address = pip.ip_address or "(public IP not ready yet)"
    step("Deploy finished")
    info(f"Database: {config.SQL_FQDN} / {config.SQL_DATABASE}")
    info(f"Key Vault: {config.VAULT_URL}")
    info(f"SSH: ssh -i keys/id_ed25519 {config.ADMIN_USER}@{address}")
    info("Next: python -m src.verify")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the managed SQL and Key Vault proof of concept.")
    parser.add_argument("--plan", action="store_true", help="Print the resources and exit. Does not call Azure.")
    args = parser.parse_args()
    if args.plan:
        print_plan()
        return
    # subscription() is read so a missing sign-in fails before any create.
    subscription()
    deploy(operator_ipv4())


if __name__ == "__main__":
    main()

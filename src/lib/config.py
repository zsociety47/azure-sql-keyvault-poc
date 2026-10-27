"""Names, addresses, and tags for the managed SQL and Key Vault proof of concept.

Every Azure resource this project creates is listed here so deploy, verify,
and teardown stay in agreement. If you are not zeon, change RESOURCE_GROUP,
SQL_SERVER, KEY_VAULT, and TAGS["owner"] before you deploy, and keep
src/guest/web-setup.sh in step with the Key Vault and SQL names. The
database password is not in this file. Deploy generates it in memory,
stores it in Key Vault, and does not print it.
"""

LOCATION = "eastus"
RESOURCE_GROUP = "rg-paas-poc-zeon01"

# Azure SQL capacity is restricted for this subscription in East US, East US 2,
# North Central US, South Central US, and West US 2. Central US is the nearest
# region that accepts a new server.
SQL_LOCATION = "centralus"
SQL_SERVER = "sql-paas-zeon01"
SQL_DATABASE = "sqldb-app"
SQL_ADMIN = "sqladmin"
SQL_PORT = 1433
SQL_SKU_NAME = "Basic"
SQL_SKU_TIER = "Basic"
SQL_SKU_CAPACITY = 5
SQL_MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024
SQL_BACKUP_STORAGE_REDUNDANCY = "Local"
SQL_TLS_VERSION = "1.2"
SQL_PUBLIC_NETWORK_ACCESS = "Enabled"
SQL_COLLATION = "SQL_Latin1_General_CP1_CI_AS"

FIREWALL_OPERATOR = "AllowOperator"
FIREWALL_WEB = "AllowWebVm"

KEY_VAULT = "kv-paas-zeon01"
KEY_VAULT_SKU = "standard"
SECRET_NAME = "SqlAdminPassword"
SECRET_RETENTION_DAYS = 90
VAULT_URL = f"https://{KEY_VAULT}.vault.azure.net/"
SQL_FQDN = f"{SQL_SERVER}.database.windows.net"

# Built-in role IDs. Microsoft publishes the same values in every commercial tenant.
# https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide
ROLE_VAULT_ADMIN = "Key Vault Administrator"
ROLE_VAULT_ADMIN_ID = "00482a5a-887f-4fb3-b363-3b7fe8e74483"
ROLE_SECRETS_USER = "Key Vault Secrets User"
ROLE_SECRETS_USER_ID = "4633458b-17de-408a-b874-0445c86b69e6"

VNET_NAME = "vnet-paas-poc"
VNET_PREFIX = "10.1.0.0/16"
WEB_SUBNET = "snet-web"
WEB_PREFIX = "10.1.1.0/24"
WEB_PRIVATE_IP = "10.1.1.4"

VM_WEB = "vm-web-01"
NIC_WEB = "nic-web-01"
PIP_WEB = "pip-web-01"
NSG_WEB = "nsg-web"
ADMIN_USER = "azureuser"
VM_SIZE = "Standard_F1als_v7"
SSH_PORT = 22
IDENTITY_TYPE = "SystemAssigned"

IMAGE = {
    "publisher": "Canonical",
    "offer": "ubuntu-24_04-lts",
    "sku": "server",
    "version": "latest",
}

OS_DISK_GB = 30
OS_DISK_SKU = "StandardSSD_LRS"
DELETE_OPTION = "Delete"

QUERY_MARKER = "sql-connected"

TAGS = {
    "project": "azure-sql-keyvault-poc",
    "environment": "poc",
    "managed-by": "python-sdk",
    "owner": "zeon",
}

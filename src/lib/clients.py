"""Azure clients signed in with DefaultAzureCredential.

DefaultAzureCredential uses the existing `az login` session. No password is
stored in this repository.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.keyvault import KeyVaultManagementClient
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource.resources import ResourceManagementClient
from azure.mgmt.sql import SqlManagementClient

from src.lib.log import info


def subscription() -> tuple[str, str]:
    """Return (subscription id, display name) for the signed-in account.

    AZURE_SUBSCRIPTION_ID wins when it is set. Otherwise the default entry in
    the local Azure CLI profile is used. Only the id and name are read.
    """
    chosen = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if chosen:
        return chosen, "AZURE_SUBSCRIPTION_ID"

    profile = Path.home() / ".azure" / "azureProfile.json"
    if not profile.exists():
        raise SystemExit(
            "No Azure subscription selected. Run `az login`, or set AZURE_SUBSCRIPTION_ID."
        )
    data = json.loads(profile.read_text(encoding="utf-8-sig"))
    subscriptions = data.get("subscriptions") or []
    defaults = [item for item in subscriptions if item.get("isDefault") and item.get("id")]
    if len(defaults) != 1:
        raise SystemExit(
            "Could not find one default Azure subscription. Set AZURE_SUBSCRIPTION_ID and run again."
        )
    return str(defaults[0]["id"]), str(defaults[0].get("name") or "default")


def credential() -> DefaultAzureCredential:
    """Sign in from the existing Azure command-line interface (CLI) session.

    Interactive browser login is turned off here so this process does not open
    a window. Run `az login` in a browser before deploy.
    """
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@dataclass(frozen=True)
class AzureClients:
    """One signed-in handle for each Azure service this project calls."""

    resource: ResourceManagementClient
    network: NetworkManagementClient
    compute: ComputeManagementClient
    sql: SqlManagementClient
    keyvault: KeyVaultManagementClient
    authorization: AuthorizationManagementClient
    monitor: MonitorManagementClient
    credential: DefaultAzureCredential

    def secrets(self, vault_url: str) -> SecretClient:
        """Open the client that reads and writes secret values in one vault."""
        return SecretClient(vault_url=vault_url, credential=self.credential)


def clients() -> AzureClients:
    """Build the clients for the signed-in subscription."""
    cred = credential()
    subscription_id, subscription_name = subscription()
    info(f"Subscription: {subscription_name}")
    return AzureClients(
        resource=ResourceManagementClient(cred, subscription_id),
        network=NetworkManagementClient(cred, subscription_id),
        compute=ComputeManagementClient(cred, subscription_id),
        sql=SqlManagementClient(cred, subscription_id),
        keyvault=KeyVaultManagementClient(cred, subscription_id),
        authorization=AuthorizationManagementClient(
            cred,
            subscription_id,
            api_version="2022-04-01",
        ),
        monitor=MonitorManagementClient(cred, subscription_id),
        credential=cred,
    )

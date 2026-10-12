"""Delete the proof of concept after you type the resource group name.

Run from the repository root:

    python -m src.teardown
"""

import time

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from src.lib import config
from src.lib.clients import clients
from src.lib.log import info, step
from src.lib.retry import wait


def find_deleted(keyvault):
    """Return the soft-deleted vault, or None when the name is free."""
    for item in keyvault.vaults.list_deleted():
        if item.name == config.KEY_VAULT:
            return item
    return None


def purge_vault(keyvault) -> None:
    """Permanently delete the soft-deleted vault so the name can be used again."""
    for attempt in range(1, 7):
        deleted = find_deleted(keyvault)
        if deleted is None:
            info(f"{config.KEY_VAULT} is not soft-deleted. The name is free.")
            return
        if deleted.properties and deleted.properties.purge_protection_enabled:
            raise SystemExit(
                f"{config.KEY_VAULT} has purge protection on. "
                "The name stays reserved until the retention period ends."
            )
        location = config.LOCATION
        if deleted.properties and deleted.properties.location:
            location = deleted.properties.location
        try:
            wait(
                keyvault.vaults.begin_purge_deleted(config.KEY_VAULT, location),
                f"purge {config.KEY_VAULT}",
                timeout=600,
            )
            break
        except (ResourceNotFoundError, HttpResponseError) as exc:
            status = getattr(exc, "status_code", 404)
            if status not in (404, 409, 429, 500, 502, 503):
                raise SystemExit(
                    f"Could not purge {config.KEY_VAULT} (HTTP {status}). "
                    "The name stays reserved until the vault is purged."
                ) from None
            info(f"Purge is not ready (HTTP {status}). Retry {attempt} of 6.")
            time.sleep(20)
    else:
        raise SystemExit(f"Could not purge {config.KEY_VAULT}. The name is still reserved.")

    if find_deleted(keyvault) is not None:
        raise SystemExit(f"{config.KEY_VAULT} is still soft-deleted.")
    info(f"{config.KEY_VAULT} is purged. The name is free.")


def delete_group(resource) -> None:
    try:
        resource.resource_groups.get(config.RESOURCE_GROUP)
    except ResourceNotFoundError:
        info(f"{config.RESOURCE_GROUP} is already gone.")
        return

    step(f"Deleting {config.RESOURCE_GROUP}")
    info("This often takes several minutes.")
    wait(
        resource.resource_groups.begin_delete(config.RESOURCE_GROUP),
        config.RESOURCE_GROUP,
        timeout=3600,
    )
    try:
        resource.resource_groups.get(config.RESOURCE_GROUP)
    except ResourceNotFoundError:
        info(f"{config.RESOURCE_GROUP} is gone.")
        return
    raise SystemExit(f"{config.RESOURCE_GROUP} still exists. Check the portal before assuming it is gone.")


def main() -> None:
    print(f"This deletes {config.RESOURCE_GROUP} and every resource inside it.")
    print("The SQL server, database, virtual machine, disk, network, and Key Vault go with it.")
    print(f"It then purges the soft-deleted vault {config.KEY_VAULT} so the name can be used again.")
    typed = input(f"Type {config.RESOURCE_GROUP} to continue: ").strip()
    if typed != config.RESOURCE_GROUP:
        raise SystemExit("The name did not match. Nothing was deleted.")

    azure = clients()
    delete_group(azure.resource)
    step(f"Purging {config.KEY_VAULT}")
    purge_vault(azure.keyvault)
    step("Teardown finished.")
    info("Next: python -m src.verify --after-teardown")


if __name__ == "__main__":
    main()

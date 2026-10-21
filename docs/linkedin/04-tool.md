# Post 4: Tool (Key Vault RBAC)

**Publish:** Thursday, October 29, 2026, 9:00 AM Eastern  
**Visual:** `vm-web-01` assigned Key Vault Secrets User on `kv-paas-zeon01`. Use `docs/images/step-10-vm-role.png` and save the JPEG as `04-tool.jpg`. Optional crop: `docs/images/step-11-write-blocked.png` if you want the 403 instead of the role list.

## Post

```text
Creating a vault does not let anyone read a secret.

The operator is Key Vault Administrator. The VM is Key Vault Secrets User.

A write from that VM returned 403. That is a pass.

Read worked. Write did not. Least privilege is the gap between those two.

When you grant Secrets User, do you test the write, or only the read?

https://github.com/zsociety47/azure-sql-keyvault-poc

#Azure #KeyVault #RBAC #CloudComputing #DevOps
```

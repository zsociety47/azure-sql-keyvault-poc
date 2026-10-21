# Post 3: Architecture diagram

**Publish:** Wednesday, October 28, 2026, 9:00 AM Eastern  
**Visual:** export of the README architecture diagram, following the [diagram standard](https://github.com/zsociety47/cloud-projects/blob/main/standards/DIAGRAM-STANDARD.md). Highlight the `write secret · 403 denied` arrow from `vm-web-01` to `kv-paas-zeon01`. Save as `03-architecture.jpg`.

## Post

```text
Solid line: the VM reads the secret over HTTPS.
Solid line: the VM opens SQL on 1433.
The other solid line is the one that should fail.

Key Vault answers 403 when the VM tries to write.

SQL is in Central US. The VM stays in East US.

Which identity would you grant first: the operator, or the VM?

https://github.com/zsociety47/azure-sql-keyvault-poc

#Azure #KeyVault #AzureSQL #CloudComputing #DevOps
```

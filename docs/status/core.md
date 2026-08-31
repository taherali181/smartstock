# Core lane status

## Requests

- Mount `smartstock_api.api.routes.reports.router` in the shared router registry after the edge lane's current `main.py` work lands. The core route module is `apps/api/smartstock_api/api/routes/reports.py`. Core will then regenerate the OpenAPI artifacts from the merged router set.

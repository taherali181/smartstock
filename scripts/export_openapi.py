import json
from pathlib import Path

from smartstock_api.main import create_app


destination = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "openapi.json"
destination.write_text(json.dumps(create_app().openapi(), indent=2) + "\n")

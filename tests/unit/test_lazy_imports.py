from __future__ import annotations

import importlib
import sys


def test_cli_and_api_imports_do_not_eagerly_load_server_module() -> None:
    sys.modules.pop("zingclaw.api.server", None)
    sys.modules.pop("zingclaw.api", None)
    sys.modules.pop("zingclaw.cli.main", None)

    api_module = importlib.import_module("zingclaw.api")
    cli_module = importlib.import_module("zingclaw.cli.main")

    assert callable(api_module.create_app)
    assert hasattr(cli_module, "gateway")
    assert "zingclaw.api.server" not in sys.modules

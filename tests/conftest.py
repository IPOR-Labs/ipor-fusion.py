import pytest
from dotenv import load_dotenv

load_dotenv()


def pytest_collection_modifyitems(items):
    for item in items:
        path = str(item.fspath)
        if "test_cli_" in path:
            item.add_marker(pytest.mark.cli)
        elif "test_mcp_" in path:
            item.add_marker(pytest.mark.mcp)
        else:
            item.add_marker(pytest.mark.sdk)

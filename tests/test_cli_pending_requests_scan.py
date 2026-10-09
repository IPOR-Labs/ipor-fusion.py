"""`vault info` when the WithdrawRequestUpdated scan cannot complete: the
pending requests are reported as unknown (null), never as an empty queue."""

from unittest.mock import MagicMock, patch

import requests
from click.testing import CliRunner

from ipor_fusion.cli.vault_cmd import (
    _build_withdraw_manager_json,
    _print_pending_requests,
)
from ipor_fusion.cli.vault_fetcher import (
    _fetch_withdraw_manager,
    _VaultData,
    _WithdrawManagerData,
)
from ipor_fusion.core.withdraw_manager import AccountRequest
from ipor_fusion.errors import LogScanError
from ipor_fusion.mcp.models import WithdrawManagerDetails

WM = "0x6666666666666666666666666666666666666666"
USER = "0x7777777777777777777777777777777777777777"


def _wm_data(pending: list[AccountRequest] | None) -> _WithdrawManagerData:
    return _WithdrawManagerData(
        withdraw_window=3600,
        request_fee=0,
        withdraw_fee=0,
        shares_to_release=0,
        last_release_funds_timestamp=0,
        pending_requests=pending,
    )


def _vault_data(pending: list[AccountRequest] | None) -> MagicMock:
    data = MagicMock(spec=_VaultData)
    data.withdraw_manager_data = _wm_data(pending)
    data.share_decimals = 18
    data.asset_decimals = 6
    data.asset_price_usd = None
    data.block_timestamp = 1_000
    return data


def _fetch(side_effect: BaseException | list[AccountRequest]):
    vault = MagicMock()
    vault.withdraw_manager_address.return_value = WM
    wm = MagicMock()
    if isinstance(side_effect, BaseException):
        wm.get_pending_requests.side_effect = side_effect
    else:
        wm.get_pending_requests.return_value = side_effect
    with (
        patch("ipor_fusion.cli.vault_fetcher.WithdrawManager", return_value=wm),
        patch(
            "ipor_fusion.cli.vault_fetcher._read_batch",
            return_value=([], [3600, 0, 0, 0, 0]),
        ),
    ):
        return _fetch_withdraw_manager(MagicMock(), vault)


def test_incomplete_scan_is_unknown_not_empty():
    error = LogScanError("timed out", address=WM, from_block=1, to_block=2)

    _, data = _fetch(error)

    assert data is not None
    assert data.pending_requests is None


def test_transport_failure_is_unknown():
    _, data = _fetch(requests.exceptions.HTTPError("413"))

    assert data is not None
    assert data.pending_requests is None


def test_completed_scan_keeps_requests():
    request = AccountRequest(
        account=USER,  # type: ignore[arg-type]
        shares=5,  # type: ignore[arg-type]
        end_withdraw_window_timestamp=2_000,
        can_withdraw=False,
    )

    _, data = _fetch([request])

    assert data is not None
    assert data.pending_requests == [request]


def test_json_reports_null_and_validates():
    result = _build_withdraw_manager_json(_vault_data(None), MagicMock())

    assert result is not None
    assert result["pending_requests"] is None
    assert result["total_pending_shares"] is None
    WithdrawManagerDetails.model_validate(result)


def test_json_keeps_an_empty_queue_distinct():
    result = _build_withdraw_manager_json(_vault_data([]), MagicMock())

    assert result is not None
    assert result["pending_requests"] == []
    assert result["total_pending_shares"]["raw"] == 0


def test_text_output_says_unavailable():
    runner = CliRunner()
    with runner.isolation() as (out, _err, _in):
        _print_pending_requests(_vault_data(None), MagicMock())

    assert "unavailable" in out.getvalue().decode()

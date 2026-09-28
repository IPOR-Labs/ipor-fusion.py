"""Full simulated lifecycle for every pinned crosschain deployment."""

from __future__ import annotations

import pytest
from _crosschain import LIFECYCLES, Deployment, Spoke
from _crosschain_lifecycle import prepare_deployed_run, run_lifecycle


@pytest.mark.parametrize(("dep", "spoke", "transport_kind"), LIFECYCLES)
def test_simulate_crosschain_lifecycle(
    request, dep: Deployment, spoke: Spoke, transport_kind
):
    spoke.chain.require_available()
    web3_hub = request.getfixturevalue(dep.hub.web3_fixture)
    web3_spoke = request.getfixturevalue(spoke.chain.web3_fixture)
    run = prepare_deployed_run(web3_hub, web3_spoke, dep, spoke, transport_kind)
    run_lifecycle(run)

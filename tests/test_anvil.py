def test_anvil_reset(web3, anvil):
    # given
    block_number = 254080000

    # when
    anvil.reset_fork(block_number)

    # then
    assert web3.eth.get_block("latest").number == block_number

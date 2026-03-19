__all__: list[str] = []

try:
    from ipor_fusion.testing.anvil import AnvilTestContainerStarter
    from ipor_fusion.testing.cheating import ForkedWeb3Context

    __all__ += ["AnvilTestContainerStarter", "ForkedWeb3Context"]
except ImportError as _exc:
    _missing = _exc.name or "unknown"

    _msg = (
        "ipor_fusion.testing requires the 'testing' extra. "
        "Install it with: pip install 'ipor_fusion[testing]'"
    )

    def _raise() -> None:
        raise ImportError(_msg) from None

    class AnvilTestContainerStarter:  # type: ignore[no-redef]
        def __init__(self, *_args, **_kwargs):
            _raise()

    class ForkedWeb3Context:  # type: ignore[no-redef]
        def __init__(self, *_args, **_kwargs):
            _raise()

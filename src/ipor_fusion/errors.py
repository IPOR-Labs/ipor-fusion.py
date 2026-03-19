class IporFusionError(Exception):
    pass


class UnsupportedFuseError(IporFusionError):
    def __init__(self, fuse_name: str):
        self.fuse_name = fuse_name
        super().__init__(f"Fuse not supported: {fuse_name}")


class UnsupportedAssetError(IporFusionError):
    def __init__(self, asset: str):
        self.asset = asset
        super().__init__(f"Unsupported asset: {asset}")


class UnsupportedMarketError(IporFusionError):
    def __init__(self, market: str):
        self.market = market
        super().__init__(f"Unsupported market: {market}")


class TransactionError(IporFusionError):
    def __init__(self, message: str, tx_hash: str | None = None):
        self.tx_hash = tx_hash
        super().__init__(message)


class ConfigurationError(IporFusionError):
    pass

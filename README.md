See https://github.com/ChardXBT/CS2-Marketplace-Trading-System for README.

Multi-account feature-gating, state ownership, validation, and failure semantics are documented in [PORTFOLIO_OPERATIONS.md](PORTFOLIO_OPERATIONS.md). Missing/blank `AUTO_LISTER_ACCOUNTS` remains Main-only.

Operator guides (git-crypt encrypted): `DEV_LOG.md` (runbook), `TRADE_REVERSALS.md` (automatic reversal detection and recovery), `AUCTION_FALLBACK.md` (moving idle buy-now listings to auction).

Config Example:
        {
            "name": "",
            "asset_id": "",
            "reserve_price": ,
            "description": "",
            "cost": ,
            "marketplace": "",
            "decrease": 0,
            "lowest_sell": null
        }

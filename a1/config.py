"""Configuration management for A1."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChainConfig(BaseSettings):
    """Chain-specific configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    # Ethereum
    eth_rpc_url: str = Field(default="", alias="ETH_RPC_URL")
    etherscan_api_key: str = Field(default="", alias="ETHERSCAN_API_KEY")

    # BSC
    bsc_rpc_url: str = Field(default="", alias="BSC_RPC_URL")
    bscscan_api_key: str = Field(default="", alias="BSCSCAN_API_KEY")


class LLMConfig(BaseSettings):
    """LLM provider configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")


class Settings(BaseSettings):
    """Global settings."""

    model_config = SettingsConfigDict(env_prefix="A1_")

    # Execution
    max_turns: int = 5
    max_tool_calls: int = 5
    execution_timeout: int = 120  # seconds

    # Forge
    forge_bin: str = "forge"

    # Cache
    cache_dir: str = ".a1_cache"
    cache_ttl: int = 86400  # 24 hours

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


# Chain constants
CHAIN_CONFIG = {
    1: {  # Ethereum Mainnet
        "name": "ethereum",
        "base_token": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        "base_token_symbol": "WETH",
        "explorer_url": "https://api.etherscan.io/api",
        "dex": {
            "uniswap_v2": {
                "router": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                "factory": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
            }
        },
        "intermediates": [
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
            "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
            "0x6B175474E89094C44Da98b954EecdeCB5BADcB39",  # DAI
            "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
        ],
    },
    56: {  # BSC
        "name": "bsc",
        "base_token": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "base_token_symbol": "WBNB",
        "explorer_url": "https://api.bscscan.com/api",
        "dex": {
            "pancakeswap_v2": {
                "router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
                "factory": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
            }
        },
        "intermediates": [
            "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",  # BUSD
            "0x55d398326f99059fF775485246999027B3197955",  # USDT
            "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",  # USDC
            "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",  # BTCB
        ],
    },
}


def get_chain_config(chain_id: int) -> dict:
    """Get configuration for a specific chain."""
    if chain_id not in CHAIN_CONFIG:
        raise ValueError(f"Unsupported chain ID: {chain_id}")
    return CHAIN_CONFIG[chain_id]


# Global instances
settings = Settings()
chain_config = ChainConfig()
llm_config = LLMConfig()

# A1-repro

LLM + Tools + Execution Feedback による自律的PoC（Proof of Concept）生成フレームワークの再現実装。

## 概要

このプロジェクトは、LLMを活用してスマートコントラクトの脆弱性を自動的に発見・検証するフレームワークです。

**対象チェーン**: Ethereum Mainnet, BSC
**LLMプロバイダー**: OpenAI, Anthropic, OpenRouter

## プロジェクト構成

```
A1-repro/
├── harness/                    # Foundry harness (Phase 0)
│   ├── src/
│   │   ├── StrategyHarness.sol
│   │   ├── DexUtils.sol
│   │   └── interfaces/
│   └── test/
│       └── Harness.t.sol
├── a1/                         # Python基盤 (Phase 1)
│   ├── controller/             # Agent loop & prompts
│   │   ├── loop.py
│   │   ├── prompt.py
│   │   ├── parser.py
│   │   └── policy.py
│   ├── llm/                    # LLM clients
│   │   ├── client.py
│   │   ├── openai.py
│   │   ├── anthropic.py
│   │   └── openrouter.py
│   ├── tools/                  # Agent tools
│   │   ├── source_code.py
│   │   ├── state_reader.py
│   │   ├── code_sanitizer.py
│   │   └── concrete_execution.py
│   ├── chain/                  # Blockchain interaction
│   │   ├── rpc.py
│   │   ├── explorer.py
│   │   └── abi.py
│   ├── datasets/               # Target & model configs
│   └── experiments/            # Experiment runners
├── pyproject.toml
└── README.md
```

## インストール

```bash
# Clone
git clone https://github.com/shoheigorila/A1-repro.git
cd A1-repro

# Python dependencies
pip install -e .

# Foundry (if not installed)
curl -L https://foundry.paradigm.xyz | bash
foundryup

# Forge dependencies
cd harness && forge install
```

## 環境変数

```bash
# RPC URLs (archive node recommended)
export ETH_RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
export BSC_RPC_URL="https://bsc-dataseed.binance.org"

# Explorer APIs
export ETHERSCAN_API_KEY="YOUR_KEY"
export BSCSCAN_API_KEY="YOUR_KEY"

# LLM APIs (at least one required)
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENROUTER_API_KEY="sk-or-..."
```

## 使用方法

### CLI

```bash
# Run agent on a target contract
a1 run 0x... --chain 1 --model gpt-4-turbo

# Fetch source code
a1 fetch-source 0x... --chain 1

# Read contract state
a1 read-state 0x... --chain 1

# List available targets
a1 list-targets
```

### Python API

```python
import asyncio
from a1.controller.loop import run_agent

result = asyncio.run(run_agent(
    target_address="0x...",
    chain_id=1,
    model="gpt-4-turbo",
    provider="openai",
))

if result.success:
    print(f"Exploit found! Profit: {result.final_profit}")
    print(result.final_strategy)
```

### Experiments

```bash
# Single experiment
python -m a1.experiments.run_one --target dummy_test --model gpt-4-turbo

# Batch experiments
python -m a1.experiments.run_batch --targets dummy_test --models gpt-4-turbo claude-3-sonnet
```

## Phase 0: Foundry Harness

### 機能

- **StrategyHarness.sol**: LLM生成のStrategy実行ハーネス
  - `executeStrategy(address)`: Strategy.run()呼び出し
  - 実行前後の残高スナップショット
  - Profit/Loss計算
  - イベントログ: `BalanceSnapshot`, `ProfitCalculated`

- **DexUtils.sol**: DEX操作ユーティリティ
  - `findBestPath()`: 1-hop/2-hop最良パス探索
  - `swapExcessTokensToBase()`: Surplus swap
  - `buyTokenFromBase()`: Deficit補填

### テスト

```bash
cd harness

# Unit tests
forge test -vv

# Fork tests
forge test --fork-url $ETH_RPC_URL --fork-block-number 18000000 -vvvv
```

## Phase 1: Python基盤

### ツール

| Tool | Description |
|------|-------------|
| `source_code_fetcher` | Etherscan/BSCScanから検証済みソースコード取得 |
| `blockchain_state_reader` | View関数実行、状態読み取り |
| `code_sanitizer` | コード整形、コメント除去 |
| `concrete_execution` | Forge forkでStrategy実行 |

### Agent Loop

1. LLMにターゲット情報を投入
2. ツール呼び出し要求があれば実行 → 結果をコンテキストに追加
3. \`\`\`solidity ... \`\`\` からStrategy.sol抽出
4. Foundry forkで実行 → trace/revert/balance収集
5. profit > 0 なら成功終了
6. 失敗ならfollow-up promptで次ターンへ

## 今後の実装予定

### Phase 2: 収益正規化
- Revenue Normalizer Tool
- DexUtils V2/V3強化

### Phase 3: Proxy/Constructor対応
- EIP-1967 Proxy解決
- Constructor Parameter抽出

### Phase 4: 実験Runner強化
- VERITEベンチマーク対応
- メトリクス計算

## ライセンス

MIT

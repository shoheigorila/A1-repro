# A1-repro

LLM + Tools + Execution Feedback による自律的PoC（Proof of Concept）生成フレームワークの再現実装。

## 概要

このプロジェクトは、LLMを活用してスマートコントラクトの脆弱性を自動的に発見・検証するフレームワークです。

**対象チェーン**: Ethereum Mainnet, BSC
**LLMプロバイダー**: OpenRouter, OpenAI, Anthropic（予定）

## プロジェクト構成

```
A1-repro/
├── harness/              # Foundry harness (Phase 0)
│   ├── src/
│   │   ├── StrategyHarness.sol   # Strategy実行、残高追跡
│   │   ├── DexUtils.sol          # DEX swap utilities
│   │   └── interfaces/           # ERC20, Uniswap V2 interfaces
│   └── test/
│       └── Harness.t.sol         # Unit tests
└── a1/                   # Python基盤 (Phase 1+, 未実装)
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

### ビルド & テスト

```bash
cd harness
forge build
forge test -vv
```

### Fork テスト

```bash
# Ethereum Mainnet
forge test --fork-url $ETH_RPC_URL --fork-block-number 18000000 -vvvv

# BSC
forge test --fork-url $BSC_RPC_URL -vvvv
```

## 今後の実装予定

### Phase 1: Python基盤 + ツール
- Controller Loop (反復制御)
- Source Code Fetcher
- Blockchain State Reader
- Code Sanitizer
- Concrete Execution Tool

### Phase 2: 収益正規化
- Revenue Normalizer
- DexUtils V2強化

### Phase 3: Proxy/Constructor対応
- EIP-1967 Proxy解決
- Constructor Parameter抽出

### Phase 4: 実験Runner
- ベンチマーク実行
- メトリクス計算

## 必要な環境変数

```bash
export ETH_RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
export BSC_RPC_URL="https://bsc-dataseed.binance.org"
```

## ライセンス

MIT

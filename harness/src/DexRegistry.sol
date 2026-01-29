// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IERC20.sol";
import "./interfaces/IUniswapV2Router.sol";
import "./interfaces/IUniswapV2Factory.sol";
import "./interfaces/IUniswapV2Pair.sol";

/// @title DexRegistry - Multi-DEX aggregator for swap operations
/// @notice Supports multiple DEXes per chain for best-rate routing
/// @dev Phase 2: Enhanced DEX support with multi-path routing
contract DexRegistry {
    // ============ Constants ============

    uint256 public constant SLIPPAGE_BPS = 500; // 5% slippage tolerance
    uint256 public constant BPS_MAX = 10000;
    uint256 public constant MAX_DEXES = 10;

    // ============ Structs ============

    struct DexConfig {
        string name;
        address router;
        address factory;
        uint256 feeBps; // e.g., 30 for 0.3%
        bool active;
    }

    struct SwapQuote {
        uint256 dexIndex;
        address[] path;
        uint256 amountOut;
        uint256 effectivePrice; // scaled by 1e18
    }

    struct SwapResult {
        bool success;
        uint256 amountIn;
        uint256 amountOut;
        uint256 dexIndex;
        address[] path;
    }

    // ============ State ============

    address public owner;
    address public baseToken; // WETH/WBNB

    DexConfig[] public dexes;
    address[] public intermediateTokens;

    // ============ Events ============

    event DexAdded(uint256 indexed index, string name, address router, address factory);
    event DexUpdated(uint256 indexed index, bool active);
    event SwapExecuted(
        uint256 indexed dexIndex,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut
    );
    event SwapFailed(address indexed tokenIn, address indexed tokenOut, string reason);
    event QuoteFound(uint256 indexed dexIndex, uint256 amountOut, uint256 pathLength);

    // ============ Modifiers ============

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    // ============ Constructor ============

    constructor(address _baseToken) {
        owner = msg.sender;
        baseToken = _baseToken;
    }

    // ============ DEX Management ============

    /// @notice Add a new DEX to the registry
    function addDex(
        string memory name,
        address router,
        address factory,
        uint256 feeBps
    ) external onlyOwner returns (uint256 index) {
        require(dexes.length < MAX_DEXES, "Max DEXes reached");

        index = dexes.length;
        dexes.push(DexConfig({
            name: name,
            router: router,
            factory: factory,
            feeBps: feeBps,
            active: true
        }));

        emit DexAdded(index, name, router, factory);
    }

    /// @notice Enable/disable a DEX
    function setDexActive(uint256 index, bool active) external onlyOwner {
        require(index < dexes.length, "Invalid index");
        dexes[index].active = active;
        emit DexUpdated(index, active);
    }

    /// @notice Add intermediate tokens for multi-hop routing
    function addIntermediateToken(address token) external onlyOwner {
        intermediateTokens.push(token);
    }

    /// @notice Set intermediate tokens in batch
    function setIntermediateTokens(address[] calldata tokens) external onlyOwner {
        delete intermediateTokens;
        for (uint256 i = 0; i < tokens.length; i++) {
            intermediateTokens.push(tokens[i]);
        }
    }

    // ============ Quote Functions ============

    /// @notice Get best quote across all DEXes
    function getBestQuote(
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) public view returns (SwapQuote memory bestQuote) {
        if (tokenIn == tokenOut || amountIn == 0) {
            address[] memory emptyPath = new address[](0);
            return SwapQuote({
                dexIndex: 0,
                path: emptyPath,
                amountOut: tokenIn == tokenOut ? amountIn : 0,
                effectivePrice: tokenIn == tokenOut ? 1e18 : 0
            });
        }

        for (uint256 i = 0; i < dexes.length; i++) {
            if (!dexes[i].active) continue;

            // Try direct path
            SwapQuote memory quote = _getQuoteFromDex(i, tokenIn, tokenOut, amountIn);
            if (quote.amountOut > bestQuote.amountOut) {
                bestQuote = quote;
            }

            // Try 2-hop paths through intermediates
            for (uint256 j = 0; j < intermediateTokens.length; j++) {
                address intermediate = intermediateTokens[j];
                if (intermediate == tokenIn || intermediate == tokenOut) continue;

                quote = _getQuoteWithIntermediate(i, tokenIn, tokenOut, amountIn, intermediate);
                if (quote.amountOut > bestQuote.amountOut) {
                    bestQuote = quote;
                }
            }
        }
    }

    /// @notice Get quote from a specific DEX (direct path)
    function _getQuoteFromDex(
        uint256 dexIndex,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) internal view returns (SwapQuote memory quote) {
        DexConfig storage dex = dexes[dexIndex];

        // Check if pair exists
        address pair = IUniswapV2Factory(dex.factory).getPair(tokenIn, tokenOut);
        if (pair == address(0)) {
            return quote;
        }

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;

        try IUniswapV2Router02(dex.router).getAmountsOut(amountIn, path) returns (uint256[] memory amounts) {
            quote = SwapQuote({
                dexIndex: dexIndex,
                path: path,
                amountOut: amounts[1],
                effectivePrice: (amounts[1] * 1e18) / amountIn
            });
        } catch {
            // Quote failed, return empty
        }
    }

    /// @notice Get quote with intermediate token
    function _getQuoteWithIntermediate(
        uint256 dexIndex,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        address intermediate
    ) internal view returns (SwapQuote memory quote) {
        DexConfig storage dex = dexes[dexIndex];

        // Check if both pairs exist
        address pair1 = IUniswapV2Factory(dex.factory).getPair(tokenIn, intermediate);
        address pair2 = IUniswapV2Factory(dex.factory).getPair(intermediate, tokenOut);

        if (pair1 == address(0) || pair2 == address(0)) {
            return quote;
        }

        address[] memory path = new address[](3);
        path[0] = tokenIn;
        path[1] = intermediate;
        path[2] = tokenOut;

        try IUniswapV2Router02(dex.router).getAmountsOut(amountIn, path) returns (uint256[] memory amounts) {
            quote = SwapQuote({
                dexIndex: dexIndex,
                path: path,
                amountOut: amounts[2],
                effectivePrice: (amounts[2] * 1e18) / amountIn
            });
        } catch {
            // Quote failed
        }
    }

    // ============ Swap Functions ============

    /// @notice Execute swap using best available route
    function swapExact(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) external returns (SwapResult memory result) {
        SwapQuote memory quote = getBestQuote(tokenIn, tokenOut, amountIn);

        if (quote.amountOut == 0 || quote.path.length == 0) {
            emit SwapFailed(tokenIn, tokenOut, "No valid route");
            return result;
        }

        uint256 minOut = minAmountOut > 0 ? minAmountOut : (quote.amountOut * (BPS_MAX - SLIPPAGE_BPS)) / BPS_MAX;

        return _executeSwap(quote.dexIndex, tokenIn, amountIn, minOut, quote.path);
    }

    /// @notice Swap surplus tokens to base token
    function swapToBase(
        address token,
        uint256 amount
    ) external returns (uint256 amountOut) {
        if (token == baseToken) return amount;
        if (amount == 0) return 0;

        SwapResult memory result = this.swapExact(token, baseToken, amount, 0);
        return result.amountOut;
    }

    /// @notice Buy exact amount of token using base token
    function buyExact(
        address token,
        uint256 amountNeeded
    ) external returns (uint256 amountSpent) {
        if (token == baseToken) return amountNeeded;
        if (amountNeeded == 0) return 0;

        // Find best route for buying exact output
        SwapQuote memory quote = _getBestQuoteExactOut(baseToken, token, amountNeeded);

        if (quote.amountOut == 0) {
            emit SwapFailed(baseToken, token, "No valid route for exact output");
            return 0;
        }

        // Use getAmountsIn to determine input needed
        DexConfig storage dex = dexes[quote.dexIndex];

        try IUniswapV2Router02(dex.router).getAmountsIn(amountNeeded, quote.path) returns (uint256[] memory amounts) {
            uint256 maxIn = (amounts[0] * (BPS_MAX + SLIPPAGE_BPS)) / BPS_MAX;

            // Check balance
            uint256 balance = IERC20(baseToken).balanceOf(address(this));
            if (balance < maxIn) {
                maxIn = balance;
            }

            IERC20(baseToken).approve(dex.router, maxIn);

            uint256[] memory swapAmounts = IUniswapV2Router02(dex.router).swapTokensForExactTokens(
                amountNeeded,
                maxIn,
                quote.path,
                address(this),
                block.timestamp + 300
            );

            amountSpent = swapAmounts[0];
            emit SwapExecuted(quote.dexIndex, baseToken, token, amountSpent, amountNeeded);
        } catch Error(string memory reason) {
            emit SwapFailed(baseToken, token, reason);
        } catch {
            emit SwapFailed(baseToken, token, "Unknown error");
        }
    }

    /// @notice Get best quote for exact output (reverse routing)
    function _getBestQuoteExactOut(
        address tokenIn,
        address tokenOut,
        uint256 amountOut
    ) internal view returns (SwapQuote memory bestQuote) {
        uint256 bestAmountIn = type(uint256).max;

        for (uint256 i = 0; i < dexes.length; i++) {
            if (!dexes[i].active) continue;

            DexConfig storage dex = dexes[i];

            // Try direct path
            address pair = IUniswapV2Factory(dex.factory).getPair(tokenIn, tokenOut);
            if (pair != address(0)) {
                address[] memory path = new address[](2);
                path[0] = tokenIn;
                path[1] = tokenOut;

                try IUniswapV2Router02(dex.router).getAmountsIn(amountOut, path) returns (uint256[] memory amounts) {
                    if (amounts[0] < bestAmountIn) {
                        bestAmountIn = amounts[0];
                        bestQuote = SwapQuote({
                            dexIndex: i,
                            path: path,
                            amountOut: amountOut,
                            effectivePrice: (amountOut * 1e18) / amounts[0]
                        });
                    }
                } catch {}
            }

            // Try 2-hop paths
            for (uint256 j = 0; j < intermediateTokens.length; j++) {
                address intermediate = intermediateTokens[j];
                if (intermediate == tokenIn || intermediate == tokenOut) continue;

                address pair1 = IUniswapV2Factory(dex.factory).getPair(tokenIn, intermediate);
                address pair2 = IUniswapV2Factory(dex.factory).getPair(intermediate, tokenOut);

                if (pair1 != address(0) && pair2 != address(0)) {
                    address[] memory path = new address[](3);
                    path[0] = tokenIn;
                    path[1] = intermediate;
                    path[2] = tokenOut;

                    try IUniswapV2Router02(dex.router).getAmountsIn(amountOut, path) returns (uint256[] memory amounts) {
                        if (amounts[0] < bestAmountIn) {
                            bestAmountIn = amounts[0];
                            bestQuote = SwapQuote({
                                dexIndex: i,
                                path: path,
                                amountOut: amountOut,
                                effectivePrice: (amountOut * 1e18) / amounts[0]
                            });
                        }
                    } catch {}
                }
            }
        }
    }

    /// @notice Execute a swap on a specific DEX
    function _executeSwap(
        uint256 dexIndex,
        address tokenIn,
        uint256 amountIn,
        uint256 minAmountOut,
        address[] memory path
    ) internal returns (SwapResult memory result) {
        DexConfig storage dex = dexes[dexIndex];

        IERC20(tokenIn).approve(dex.router, amountIn);

        try IUniswapV2Router02(dex.router).swapExactTokensForTokens(
            amountIn,
            minAmountOut,
            path,
            address(this),
            block.timestamp + 300
        ) returns (uint256[] memory amounts) {
            result = SwapResult({
                success: true,
                amountIn: amountIn,
                amountOut: amounts[amounts.length - 1],
                dexIndex: dexIndex,
                path: path
            });

            emit SwapExecuted(dexIndex, path[0], path[path.length - 1], amountIn, result.amountOut);
        } catch Error(string memory reason) {
            emit SwapFailed(path[0], path[path.length - 1], reason);
        } catch {
            emit SwapFailed(path[0], path[path.length - 1], "Unknown error");
        }
    }

    // ============ View Functions ============

    /// @notice Get number of registered DEXes
    function getDexCount() external view returns (uint256) {
        return dexes.length;
    }

    /// @notice Get all intermediate tokens
    function getIntermediateTokens() external view returns (address[] memory) {
        return intermediateTokens;
    }

    /// @notice Check if a pair exists on any active DEX
    function hasPair(address tokenA, address tokenB) external view returns (bool) {
        for (uint256 i = 0; i < dexes.length; i++) {
            if (!dexes[i].active) continue;

            address pair = IUniswapV2Factory(dexes[i].factory).getPair(tokenA, tokenB);
            if (pair != address(0)) return true;
        }
        return false;
    }

    // ============ Admin Functions ============

    /// @notice Update base token
    function setBaseToken(address _baseToken) external onlyOwner {
        baseToken = _baseToken;
    }

    /// @notice Transfer ownership
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Invalid owner");
        owner = newOwner;
    }

    /// @notice Receive ETH
    receive() external payable {}
}

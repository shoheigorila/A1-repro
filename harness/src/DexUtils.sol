// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IERC20.sol";
import "./interfaces/IUniswapV2Router.sol";
import "./interfaces/IUniswapV2Factory.sol";
import "./interfaces/IUniswapV2Pair.sol";

/// @title DexUtils - DEX utility library for surplus/deficit normalization
/// @notice Provides swap functionality to normalize profits to base token
/// @dev Phase 1: Uniswap V2 compatible DEXes only
contract DexUtils {
    // ============ Constants ============

    uint256 public constant SLIPPAGE_BPS = 500; // 5% slippage tolerance
    uint256 public constant BPS_MAX = 10000;

    // ============ Structs ============

    struct DexConfig {
        address router;
        address factory;
        address weth;
    }

    struct SwapPath {
        address[] path;
        uint256 expectedOut;
        bool valid;
    }

    // ============ State ============

    DexConfig public dexConfig;
    address[] public intermediateTokens;

    // ============ Events ============

    event PathFound(address indexed tokenIn, address indexed tokenOut, uint256 hops, uint256 expectedOut);
    event SwapExecuted(address indexed tokenIn, address indexed tokenOut, uint256 amountIn, uint256 amountOut);
    event SwapFailed(address indexed tokenIn, address indexed tokenOut, string reason);

    // ============ Constructor ============

    constructor(
        address _router,
        address _factory,
        address _weth,
        address[] memory _intermediates
    ) {
        dexConfig = DexConfig({
            router: _router,
            factory: _factory,
            weth: _weth
        });

        for (uint256 i = 0; i < _intermediates.length; i++) {
            intermediateTokens.push(_intermediates[i]);
        }
    }

    // ============ Path Finding ============

    /// @notice Find the best swap path from tokenIn to baseToken
    /// @param tokenIn Source token
    /// @param baseToken Target token (usually WETH/WBNB)
    /// @param amountIn Amount of tokenIn to swap
    /// @return bestPath The optimal path and expected output
    function findBestPath(
        address tokenIn,
        address baseToken,
        uint256 amountIn
    ) public view returns (SwapPath memory bestPath) {
        if (tokenIn == baseToken || amountIn == 0) {
            return SwapPath({path: new address[](0), expectedOut: amountIn, valid: tokenIn == baseToken});
        }

        IUniswapV2Router02 router = IUniswapV2Router02(dexConfig.router);
        IUniswapV2Factory factory = IUniswapV2Factory(dexConfig.factory);

        // Try direct path first
        address directPair = factory.getPair(tokenIn, baseToken);
        if (directPair != address(0)) {
            address[] memory directPath = new address[](2);
            directPath[0] = tokenIn;
            directPath[1] = baseToken;

            try router.getAmountsOut(amountIn, directPath) returns (uint256[] memory amounts) {
                bestPath = SwapPath({path: directPath, expectedOut: amounts[1], valid: true});
            } catch {}
        }

        // Try 2-hop paths through intermediate tokens
        for (uint256 i = 0; i < intermediateTokens.length; i++) {
            address intermediate = intermediateTokens[i];
            if (intermediate == tokenIn || intermediate == baseToken) continue;

            address pair1 = factory.getPair(tokenIn, intermediate);
            address pair2 = factory.getPair(intermediate, baseToken);

            if (pair1 != address(0) && pair2 != address(0)) {
                address[] memory hopPath = new address[](3);
                hopPath[0] = tokenIn;
                hopPath[1] = intermediate;
                hopPath[2] = baseToken;

                try router.getAmountsOut(amountIn, hopPath) returns (uint256[] memory amounts) {
                    if (amounts[2] > bestPath.expectedOut) {
                        bestPath = SwapPath({path: hopPath, expectedOut: amounts[2], valid: true});
                    }
                } catch {}
            }
        }

        return bestPath;
    }

    // ============ Swap Functions ============

    /// @notice Swap excess tokens back to base token (for surplus normalization)
    /// @param token Token with surplus
    /// @param amount Amount to swap
    /// @param baseToken Target base token
    /// @return amountOut Amount of base token received
    function swapExcessTokensToBase(
        address token,
        uint256 amount,
        address baseToken
    ) external returns (uint256 amountOut) {
        if (token == baseToken) return amount;
        if (amount == 0) return 0;

        SwapPath memory path = findBestPath(token, baseToken, amount);
        if (!path.valid || path.path.length == 0) {
            emit SwapFailed(token, baseToken, "No valid path found");
            return 0;
        }

        IERC20(token).approve(dexConfig.router, amount);

        uint256 minOut = (path.expectedOut * (BPS_MAX - SLIPPAGE_BPS)) / BPS_MAX;

        try IUniswapV2Router02(dexConfig.router).swapExactTokensForTokens(
            amount,
            minOut,
            path.path,
            address(this),
            block.timestamp + 300
        ) returns (uint256[] memory amounts) {
            amountOut = amounts[amounts.length - 1];
            emit SwapExecuted(token, baseToken, amount, amountOut);
        } catch Error(string memory reason) {
            emit SwapFailed(token, baseToken, reason);
            return 0;
        } catch {
            emit SwapFailed(token, baseToken, "Unknown swap error");
            return 0;
        }
    }

    /// @notice Buy token from base token (for deficit compensation)
    /// @param token Token to buy
    /// @param targetAmount Desired amount of token
    /// @param baseToken Source base token
    /// @return amountIn Amount of base token spent
    /// @return amountOut Actual amount of token received
    function buyTokenFromBase(
        address token,
        uint256 targetAmount,
        address baseToken
    ) external returns (uint256 amountIn, uint256 amountOut) {
        if (token == baseToken) return (targetAmount, targetAmount);
        if (targetAmount == 0) return (0, 0);

        IUniswapV2Router02 router = IUniswapV2Router02(dexConfig.router);

        // Find reverse path (base -> token)
        SwapPath memory path = findBestPath(baseToken, token, 1 ether); // Use 1 ether as reference
        if (!path.valid || path.path.length == 0) {
            emit SwapFailed(baseToken, token, "No valid path found");
            return (0, 0);
        }

        // Reverse the path for buying
        address[] memory reversePath = new address[](path.path.length);
        for (uint256 i = 0; i < path.path.length; i++) {
            reversePath[i] = path.path[path.path.length - 1 - i];
        }

        // Estimate input needed
        try router.getAmountsIn(targetAmount, reversePath) returns (uint256[] memory amounts) {
            amountIn = amounts[0];
            uint256 maxIn = (amountIn * (BPS_MAX + SLIPPAGE_BPS)) / BPS_MAX;

            uint256 baseBalance = IERC20(baseToken).balanceOf(address(this));
            if (baseBalance < maxIn) {
                maxIn = baseBalance;
            }

            IERC20(baseToken).approve(dexConfig.router, maxIn);

            try router.swapTokensForExactTokens(
                targetAmount,
                maxIn,
                reversePath,
                address(this),
                block.timestamp + 300
            ) returns (uint256[] memory swapAmounts) {
                amountIn = swapAmounts[0];
                amountOut = swapAmounts[swapAmounts.length - 1];
                emit SwapExecuted(baseToken, token, amountIn, amountOut);
            } catch Error(string memory reason) {
                emit SwapFailed(baseToken, token, reason);
            } catch {
                emit SwapFailed(baseToken, token, "Unknown swap error");
            }
        } catch {
            emit SwapFailed(baseToken, token, "Cannot estimate input");
        }
    }

    // ============ View Functions ============

    /// @notice Check if a token pair has liquidity
    function hasPair(address tokenA, address tokenB) external view returns (bool) {
        return IUniswapV2Factory(dexConfig.factory).getPair(tokenA, tokenB) != address(0);
    }

    /// @notice Get expected output for a swap
    function getExpectedOutput(
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) external view returns (uint256) {
        SwapPath memory path = findBestPath(tokenIn, tokenOut, amountIn);
        return path.expectedOut;
    }

    /// @notice Get all intermediate tokens
    function getIntermediateTokens() external view returns (address[] memory) {
        return intermediateTokens;
    }

    // ============ Admin Functions ============

    /// @notice Add intermediate token for path finding
    function addIntermediateToken(address token) external {
        intermediateTokens.push(token);
    }

    /// @notice Update DEX configuration
    function updateDexConfig(address _router, address _factory, address _weth) external {
        dexConfig = DexConfig({
            router: _router,
            factory: _factory,
            weth: _weth
        });
    }
}

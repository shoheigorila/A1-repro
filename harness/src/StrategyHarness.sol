// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IERC20.sol";
import "./DexUtils.sol";

/// @title IStrategy - Interface for LLM-generated exploit strategies
interface IStrategy {
    function run() external;
}

/// @title StrategyHarness - Execution harness for exploit strategies
/// @notice Executes strategies, tracks balances, and normalizes profits
/// @dev Designed for fork-based testing with profit measurement
contract StrategyHarness {
    // ============ Structs ============

    struct TokenBalance {
        address token;
        uint256 before;
        uint256 after_;
        int256 delta;
    }

    struct ExecutionResult {
        bool success;
        string revertReason;
        uint256 gasUsed;
        int256 profit;
        bool allBalancesNonNegative;
        TokenBalance[] balances;
    }

    // ============ State ============

    address public owner;
    address public baseToken;
    DexUtils public dexUtils;

    // Tracked tokens for balance snapshots
    address[] public trackedTokens;
    mapping(address => bool) public isTracked;

    // Execution state
    mapping(address => uint256) private _balancesBefore;
    bool private _executing;

    // ============ Events ============

    event BalanceSnapshot(address indexed token, uint256 before, uint256 after_, int256 delta);
    event ProfitCalculated(int256 rawProfit, int256 normalizedProfit);
    event StrategyExecuted(address indexed strategy, bool success, uint256 gasUsed);
    event DebugValue(string label, uint256 value);
    event DebugAddress(string label, address value);
    event DebugInt(string label, int256 value);

    // ============ Modifiers ============

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    modifier notExecuting() {
        require(!_executing, "Already executing");
        _;
    }

    // ============ Constructor ============

    constructor(address _baseToken, address _dexUtils) {
        owner = msg.sender;
        baseToken = _baseToken;
        dexUtils = DexUtils(_dexUtils);

        // Auto-track base token
        _addTrackedToken(_baseToken);
    }

    // ============ Token Tracking ============

    /// @notice Add token to tracked list
    function addTrackedToken(address token) external onlyOwner {
        _addTrackedToken(token);
    }

    /// @notice Add multiple tokens to tracked list
    function addTrackedTokens(address[] calldata tokens) external onlyOwner {
        for (uint256 i = 0; i < tokens.length; i++) {
            _addTrackedToken(tokens[i]);
        }
    }

    function _addTrackedToken(address token) internal {
        if (!isTracked[token]) {
            trackedTokens.push(token);
            isTracked[token] = true;
        }
    }

    /// @notice Get all tracked tokens
    function getTrackedTokens() external view returns (address[] memory) {
        return trackedTokens;
    }

    // ============ Balance Snapshots ============

    /// @notice Take snapshot of all tracked token balances
    function _snapshotBalancesBefore() internal {
        for (uint256 i = 0; i < trackedTokens.length; i++) {
            address token = trackedTokens[i];
            _balancesBefore[token] = _getBalance(token);
        }
    }

    /// @notice Collect balance changes after execution
    function _collectBalanceDeltas() internal view returns (TokenBalance[] memory) {
        TokenBalance[] memory balances = new TokenBalance[](trackedTokens.length);

        for (uint256 i = 0; i < trackedTokens.length; i++) {
            address token = trackedTokens[i];
            uint256 before = _balancesBefore[token];
            uint256 after_ = _getBalance(token);
            int256 delta = int256(after_) - int256(before);

            balances[i] = TokenBalance({
                token: token,
                before: before,
                after_: after_,
                delta: delta
            });
        }

        return balances;
    }

    /// @notice Get balance of token (handles ETH specially)
    function _getBalance(address token) internal view returns (uint256) {
        if (token == address(0)) {
            return address(this).balance;
        }
        return IERC20(token).balanceOf(address(this));
    }

    // ============ Strategy Execution ============

    /// @notice Execute a strategy and measure results
    /// @param strategy Address of the strategy contract
    /// @return result Execution result with balances and profit
    function executeStrategy(address strategy) external notExecuting returns (ExecutionResult memory result) {
        _executing = true;

        // Pre-execution snapshot
        _snapshotBalancesBefore();
        uint256 gasStart = gasleft();

        // Execute strategy
        try IStrategy(strategy).run() {
            result.success = true;
        } catch Error(string memory reason) {
            result.success = false;
            result.revertReason = reason;
        } catch (bytes memory lowLevelData) {
            result.success = false;
            result.revertReason = _parseRevertReason(lowLevelData);
        }

        // Post-execution measurements
        result.gasUsed = gasStart - gasleft();

        // Collect balance deltas
        result.balances = _collectBalanceDeltas();

        // Emit balance snapshots
        for (uint256 i = 0; i < result.balances.length; i++) {
            TokenBalance memory b = result.balances[i];
            emit BalanceSnapshot(b.token, b.before, b.after_, b.delta);
        }

        // Calculate profit in base token
        (result.profit, result.allBalancesNonNegative) = _calculateProfit(result.balances);

        emit StrategyExecuted(strategy, result.success, result.gasUsed);
        emit ProfitCalculated(result.profit, result.profit);

        _executing = false;
    }

    /// @notice Execute strategy with custom tokens to track
    function executeStrategyWithTokens(
        address strategy,
        address[] calldata tokens
    ) external notExecuting returns (ExecutionResult memory result) {
        // Add tokens to tracked list
        for (uint256 i = 0; i < tokens.length; i++) {
            _addTrackedToken(tokens[i]);
        }

        // Delegate to main execution
        _executing = true;
        _snapshotBalancesBefore();
        uint256 gasStart = gasleft();

        try IStrategy(strategy).run() {
            result.success = true;
        } catch Error(string memory reason) {
            result.success = false;
            result.revertReason = reason;
        } catch (bytes memory lowLevelData) {
            result.success = false;
            result.revertReason = _parseRevertReason(lowLevelData);
        }

        result.gasUsed = gasStart - gasleft();
        result.balances = _collectBalanceDeltas();

        for (uint256 i = 0; i < result.balances.length; i++) {
            TokenBalance memory b = result.balances[i];
            emit BalanceSnapshot(b.token, b.before, b.after_, b.delta);
        }

        (result.profit, result.allBalancesNonNegative) = _calculateProfit(result.balances);

        emit StrategyExecuted(strategy, result.success, result.gasUsed);
        emit ProfitCalculated(result.profit, result.profit);

        _executing = false;
    }

    // ============ Profit Calculation ============

    /// @notice Calculate profit in base token terms
    function _calculateProfit(TokenBalance[] memory balances) internal view returns (int256 profit, bool allNonNegative) {
        allNonNegative = true;
        profit = 0;

        for (uint256 i = 0; i < balances.length; i++) {
            TokenBalance memory b = balances[i];

            if (b.delta < 0) {
                allNonNegative = false;
            }

            if (b.token == baseToken) {
                profit += b.delta;
            } else if (b.delta > 0) {
                // Convert surplus to base token value
                uint256 valueInBase = dexUtils.getExpectedOutput(b.token, baseToken, uint256(b.delta));
                profit += int256(valueInBase);
            } else if (b.delta < 0) {
                // Calculate cost to cover deficit
                uint256 costInBase = dexUtils.getExpectedOutput(baseToken, b.token, uint256(-b.delta));
                if (costInBase > 0) {
                    profit -= int256(costInBase);
                }
            }
        }
    }

    // ============ Revenue Normalization ============

    /// @notice Normalize all token balances to base token
    /// @dev Swaps surplus tokens to base, attempts to cover deficits
    function normalizeToBase() external returns (int256 finalProfit) {
        TokenBalance[] memory balances = _collectBalanceDeltas();

        // First pass: swap all surplus tokens to base
        for (uint256 i = 0; i < balances.length; i++) {
            TokenBalance memory b = balances[i];
            if (b.token != baseToken && b.delta > 0) {
                uint256 currentBalance = IERC20(b.token).balanceOf(address(this));
                if (currentBalance > 0) {
                    IERC20(b.token).transfer(address(dexUtils), currentBalance);
                    dexUtils.swapExcessTokensToBase(b.token, currentBalance, baseToken);
                }
            }
        }

        // Second pass: attempt to cover deficits
        for (uint256 i = 0; i < balances.length; i++) {
            TokenBalance memory b = balances[i];
            if (b.token != baseToken && b.delta < 0) {
                uint256 deficit = uint256(-b.delta);
                uint256 baseBalance = IERC20(baseToken).balanceOf(address(this));

                if (baseBalance > 0) {
                    IERC20(baseToken).transfer(address(dexUtils), baseBalance);
                    dexUtils.buyTokenFromBase(b.token, deficit, baseToken);
                }
            }
        }

        // Calculate final profit
        uint256 finalBaseBalance = IERC20(baseToken).balanceOf(address(this));
        uint256 initialBaseBalance = _balancesBefore[baseToken];
        finalProfit = int256(finalBaseBalance) - int256(initialBaseBalance);
    }

    // ============ Utility Functions ============

    /// @notice Parse revert reason from low-level data
    function _parseRevertReason(bytes memory data) internal pure returns (string memory) {
        if (data.length < 4) {
            return "Unknown error";
        }

        // Check for Error(string) selector
        if (data[0] == 0x08 && data[1] == 0xc3 && data[2] == 0x79 && data[3] == 0xa0) {
            assembly {
                data := add(data, 4)
            }
            return abi.decode(data, (string));
        }

        // Check for Panic(uint256) selector
        if (data[0] == 0x4e && data[1] == 0x48 && data[2] == 0x7b && data[3] == 0x71) {
            return "Panic error";
        }

        return "Unknown error";
    }

    /// @notice Debug: emit current balance of token
    function debugBalance(address token) external {
        uint256 bal = _getBalance(token);
        emit DebugValue("balance", bal);
        emit DebugAddress("token", token);
    }

    // ============ Admin Functions ============

    /// @notice Update base token
    function setBaseToken(address _baseToken) external onlyOwner {
        baseToken = _baseToken;
        _addTrackedToken(_baseToken);
    }

    /// @notice Update DexUtils contract
    function setDexUtils(address _dexUtils) external onlyOwner {
        dexUtils = DexUtils(_dexUtils);
    }

    /// @notice Withdraw tokens (for testing)
    function withdrawToken(address token, uint256 amount) external onlyOwner {
        IERC20(token).transfer(owner, amount);
    }

    /// @notice Withdraw ETH
    function withdrawETH() external onlyOwner {
        payable(owner).transfer(address(this).balance);
    }

    /// @notice Receive ETH
    receive() external payable {}
}

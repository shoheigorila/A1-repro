// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/StrategyHarness.sol";
import "../src/DexUtils.sol";
import "../src/interfaces/IERC20.sol";

/// @title MockERC20 - Simple mock for testing
contract MockERC20 is IERC20 {
    string public name = "Mock Token";
    string public symbol = "MOCK";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// @title MockDexUtils - Minimal mock for DexUtils
contract MockDexUtils {
    function getExpectedOutput(address, address, uint256 amount) external pure returns (uint256) {
        return amount; // 1:1 for simplicity
    }

    function swapExcessTokensToBase(address, uint256, address) external pure returns (uint256) {
        return 0;
    }

    function buyTokenFromBase(address, uint256, address) external pure returns (uint256, uint256) {
        return (0, 0);
    }
}

/// @title DummyStrategy - Simple test strategy
contract DummyStrategy is IStrategy {
    address public target;

    constructor(address _target) {
        target = _target;
    }

    function run() external override {}
}

/// @title RevertingStrategy - Strategy that always reverts
contract RevertingStrategy is IStrategy {
    function run() external pure override {
        revert("Intentional revert for testing");
    }
}

/// @title ProfitStrategy - Strategy that generates profit
contract ProfitStrategy is IStrategy {
    MockERC20 public token;
    address public harness;

    constructor(MockERC20 _token, address _harness) {
        token = _token;
        harness = _harness;
    }

    function run() external override {
        // Mint tokens to harness to simulate profit
        token.mint(harness, 1 ether);
    }
}

contract HarnessTest is Test {
    MockERC20 baseToken;
    MockERC20 otherToken;
    MockDexUtils mockDex;
    StrategyHarness harness;

    function setUp() public {
        // Deploy mocks
        baseToken = new MockERC20();
        otherToken = new MockERC20();
        mockDex = new MockDexUtils();

        // Deploy harness with mock dex
        harness = new StrategyHarness(address(baseToken), address(mockDex));

        // Fund harness
        vm.deal(address(harness), 10 ether);
        baseToken.mint(address(harness), 100 ether);
    }

    function test_DummyStrategy() public {
        DummyStrategy strategy = new DummyStrategy(address(harness));
        StrategyHarness.ExecutionResult memory result = harness.executeStrategy(address(strategy));

        assertTrue(result.success, "Strategy should succeed");
        assertGt(result.gasUsed, 0, "Gas should be used");
    }

    function test_RevertingStrategy() public {
        RevertingStrategy strategy = new RevertingStrategy();
        StrategyHarness.ExecutionResult memory result = harness.executeStrategy(address(strategy));

        assertFalse(result.success, "Strategy should fail");
        assertEq(result.revertReason, "Intentional revert for testing");
    }

    function test_BalanceTracking() public {
        harness.addTrackedToken(address(otherToken));

        address[] memory tokens = harness.getTrackedTokens();
        assertEq(tokens.length, 2, "Should have 2 tracked tokens");
        assertEq(tokens[0], address(baseToken));
        assertEq(tokens[1], address(otherToken));
    }

    function test_ExecuteWithTokens() public {
        DummyStrategy strategy = new DummyStrategy(address(harness));

        address[] memory tokens = new address[](1);
        tokens[0] = address(otherToken);

        StrategyHarness.ExecutionResult memory result = harness.executeStrategyWithTokens(
            address(strategy),
            tokens
        );

        assertTrue(result.success);
        assertEq(harness.getTrackedTokens().length, 2);
    }

    function test_ProfitCalculation() public {
        ProfitStrategy strategy = new ProfitStrategy(baseToken, address(harness));

        uint256 balanceBefore = baseToken.balanceOf(address(harness));
        StrategyHarness.ExecutionResult memory result = harness.executeStrategy(address(strategy));
        uint256 balanceAfter = baseToken.balanceOf(address(harness));

        assertTrue(result.success, "Strategy should succeed");
        assertEq(balanceAfter - balanceBefore, 1 ether, "Should have 1 ether profit");
        assertEq(result.profit, 1 ether, "Profit should be 1 ether");
    }

    function test_BalanceSnapshot() public {
        // Initial balance
        uint256 initialBalance = baseToken.balanceOf(address(harness));

        // Execute strategy that adds profit
        ProfitStrategy strategy = new ProfitStrategy(baseToken, address(harness));
        StrategyHarness.ExecutionResult memory result = harness.executeStrategy(address(strategy));

        // Check balance delta in result
        assertEq(result.balances.length, 1, "Should track 1 token");
        assertEq(result.balances[0].token, address(baseToken));
        assertEq(result.balances[0].before, initialBalance);
        assertEq(result.balances[0].after_, initialBalance + 1 ether);
        assertEq(result.balances[0].delta, 1 ether);
    }
}

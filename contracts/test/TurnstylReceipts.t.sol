// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {TurnstylReceipts} from "../src/TurnstylReceipts.sol";

/// @dev The smallest ERC20 that exercises TurnstylReceipts: balances, allowances,
///      and a switch to make transferFrom return false without reverting (the
///      case a naive integration silently ignores).
contract MockERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    bool public failTransfers;

    function setFailTransfers(bool value) external {
        failTransfers = value;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        if (failTransfers) {
            return false;
        }
        require(balanceOf[from] >= amount, "mock: insufficient balance");
        require(allowance[from][msg.sender] >= amount, "mock: insufficient allowance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract TurnstylReceiptsTest is Test {
    event Paid(bytes32 indexed memo, address indexed payer, uint256 amount);
    event Committed(bytes32 indexed memo, bytes32 outputHash);

    MockERC20 internal usdc;
    TurnstylReceipts internal receipts;

    address internal agent = address(0xA6E17);
    address internal buyer = address(0xB0FE7);
    address internal stranger = address(0x57A6E);

    bytes32 internal memo = keccak256(bytes("job123:2"));
    uint256 internal amount = 500_000; // 0.50 USDC, 6 decimals

    function setUp() public {
        usdc = new MockERC20();
        receipts = new TurnstylReceipts(address(usdc), agent);
        usdc.mint(buyer, 10_000_000);
        vm.prank(buyer);
        usdc.approve(address(receipts), type(uint256).max);
    }

    function test_ConstructorSetsImmutables() public view {
        assertEq(receipts.usdc(), address(usdc));
        assertEq(receipts.agent(), agent);
    }

    function test_PayMovesTokensToAgentAndEmits() public {
        uint256 buyerBefore = usdc.balanceOf(buyer);

        vm.expectEmit(true, true, false, true, address(receipts));
        emit Paid(memo, buyer, amount);

        vm.prank(buyer);
        receipts.pay(memo, amount);

        assertEq(usdc.balanceOf(agent), amount, "agent did not receive the payment");
        assertEq(usdc.balanceOf(buyer), buyerBefore - amount, "buyer was not debited");
        assertEq(usdc.balanceOf(address(receipts)), 0, "contract must never hold custody");
    }

    function test_PayRevertsOnZeroAmount() public {
        vm.prank(buyer);
        vm.expectRevert("amount must be > 0");
        receipts.pay(memo, 0);
    }

    function test_PayRevertsWhenTransferFromReturnsFalse() public {
        usdc.setFailTransfers(true);
        vm.prank(buyer);
        vm.expectRevert("USDC transferFrom failed");
        receipts.pay(memo, amount);
        assertEq(usdc.balanceOf(agent), 0, "no tokens should have moved");
    }

    function test_PayRevertsWithoutAllowance() public {
        // Funded but unapproved: isolates the allowance check from the balance one.
        usdc.mint(stranger, 10_000_000);
        vm.prank(stranger);
        vm.expectRevert("mock: insufficient allowance");
        receipts.pay(memo, amount);
    }

    function test_CommitRevertsForNonAgent() public {
        vm.prank(stranger);
        vm.expectRevert("only agent");
        receipts.commit(memo, keccak256("output"));

        vm.prank(buyer);
        vm.expectRevert("only agent");
        receipts.commit(memo, keccak256("output"));
    }

    function test_CommitEmitsForAgent() public {
        bytes32 outputHash = keccak256("the step output");

        vm.expectEmit(true, false, false, true, address(receipts));
        emit Committed(memo, outputHash);

        vm.prank(agent);
        receipts.commit(memo, outputHash);
    }
}

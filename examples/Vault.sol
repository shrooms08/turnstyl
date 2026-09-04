// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Vault
/// @notice A minimal ETH vault. Anyone may deposit and withdraw their own
///         balance. Deliberately vulnerable: used as the sample contract for
///         the turnstyl metered audit agent.
contract Vault {
    mapping(address => uint256) private balances;

    uint256 public totalDeposits;

    /// @notice Deposit ETH into the caller's vault balance.
    function deposit() external payable {
        require(msg.value > 0, "zero deposit");
        balances[msg.sender] += msg.value;
        totalDeposits += msg.value;
    }

    /// @notice Withdraw ETH from the caller's vault balance.
    /// @dev BUG (reentrancy): the external call to msg.sender happens BEFORE
    ///      balances[msg.sender] is reduced, so a contract caller can re-enter
    ///      withdraw() from its receive() hook while its recorded balance is
    ///      still the full pre-withdrawal amount and drain the vault. The
    ///      return value of the low-level call is also ignored.
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "insufficient balance");

        (bool ok, ) = msg.sender.call{value: amount}("");

        balances[msg.sender] -= amount;
        totalDeposits -= amount;
    }

    /// @notice Read the vault balance of an account.
    function getBalance(address account) external view returns (uint256) {
        return balances[account];
    }
}

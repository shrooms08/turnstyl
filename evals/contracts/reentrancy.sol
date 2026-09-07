// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title EtherBank
/// @notice A minimal ETH bank with per-account balances.
contract EtherBank {
    mapping(address => uint256) private balances;

    function deposit() external payable {
        require(msg.value > 0, "zero deposit");
        balances[msg.sender] += msg.value;
    }

    /// @notice Withdraw the caller's whole balance.
    function withdraw() external {
        uint256 amount = balances[msg.sender];
        require(amount > 0, "nothing to withdraw");

        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");

        balances[msg.sender] = 0;
    }

    function balanceOf(address account) external view returns (uint256) {
        return balances[account];
    }
}

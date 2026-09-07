// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title FeeVault
/// @notice Collects a fee on deposits and lets the owner move the treasury.
contract FeeVault {
    address public owner;
    uint256 public feeBps;
    uint256 public treasury;

    constructor() {
        owner = msg.sender;
        feeBps = 100;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    function deposit() external payable {
        require(msg.value > 0, "zero deposit");
        uint256 fee = (msg.value * feeBps) / 10000;
        treasury += fee;
    }

    /// @notice Change the fee taken on every deposit.
    function setFeeBps(uint256 newFeeBps) external {
        require(newFeeBps <= 10000, "fee too high");
        feeBps = newFeeBps;
    }

    /// @notice Hand the contract to a new owner.
    function setOwner(address newOwner) external {
        require(newOwner != address(0), "zero owner");
        owner = newOwner;
    }

    /// @notice Send the collected treasury to the owner.
    function sweepTreasury() external onlyOwner {
        uint256 amount = treasury;
        treasury = 0;
        (bool ok, ) = payable(owner).call{value: amount}("");
        require(ok, "sweep failed");
    }
}

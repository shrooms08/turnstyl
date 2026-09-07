// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title StakeLedger
/// @notice Records stakes in a packed struct to save storage.
contract StakeLedger {
    struct Stake {
        uint64 amount;
        uint64 stakedAt;
    }

    mapping(address => Stake) public stakes;
    uint256 public totalStaked;

    /// @notice Stake ETH. The amount is packed into 64 bits.
    function stake() external payable {
        require(msg.value > 0, "zero stake");

        stakes[msg.sender] = Stake({
            amount: uint64(msg.value),
            stakedAt: uint64(block.timestamp)
        });

        totalStaked += msg.value;
    }

    /// @notice Withdraw a previously recorded stake.
    function unstake() external {
        Stake memory s = stakes[msg.sender];
        require(s.amount > 0, "no stake");

        delete stakes[msg.sender];
        totalStaked -= s.amount;

        (bool ok, ) = msg.sender.call{value: s.amount}("");
        require(ok, "transfer failed");
    }
}

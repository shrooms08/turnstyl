// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title PayoutSplitter
/// @notice Splits an incoming payment between two fixed recipients.
contract PayoutSplitter {
    address payable public immutable left;
    address payable public immutable right;

    uint256 public paidOut;

    constructor(address payable leftRecipient, address payable rightRecipient) {
        left = leftRecipient;
        right = rightRecipient;
    }

    receive() external payable {}

    /// @notice Split the contract balance in half and send it on.
    function release() external {
        uint256 half = address(this).balance / 2;

        left.call{value: half}("");
        right.call{value: half}("");

        paidOut += half * 2;
    }
}

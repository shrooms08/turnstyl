// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Registry
/// @notice A small owner-controlled registry of addresses to labels.
/// @dev No value is held or moved by this contract.
contract Registry {
    address public owner;

    mapping(address => string) private labels;

    event LabelSet(address indexed subject, string label);
    event OwnerTransferred(address indexed from, address indexed to);

    error NotOwner();
    error ZeroAddress();
    error EmptyLabel();

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice Set the label for an address.
    function setLabel(address subject, string calldata label) external onlyOwner {
        if (subject == address(0)) revert ZeroAddress();
        if (bytes(label).length == 0) revert EmptyLabel();
        labels[subject] = label;
        emit LabelSet(subject, label);
    }

    /// @notice Read the label for an address.
    function labelOf(address subject) external view returns (string memory) {
        return labels[subject];
    }

    /// @notice Transfer ownership of the registry.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        address previous = owner;
        owner = newOwner;
        emit OwnerTransferred(previous, newOwner);
    }
}

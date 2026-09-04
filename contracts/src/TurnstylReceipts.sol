// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title TurnstylReceipts
/// @notice On-chain receipts for a turnstyl metered agent: buyers pay per step,
///         the agent commits the hash of what it delivered for that step.
/// @dev No custody. `pay` moves USDC straight from the buyer to the agent in the
///      same call; this contract never holds a balance. There is no owner, no
///      pause and no upgrade path — nothing here can be turned off or rewritten.
contract TurnstylReceipts {
    /// @notice The ERC20 buyers pay in (USDC on Base Sepolia, 6 decimals).
    address public immutable usdc;

    /// @notice The agent that receives every payment and may commit hashes.
    address public immutable agent;

    /// @notice A buyer paid for one metered step.
    /// @param memo keccak256("<job_id>:<step>") — ties the payment to the invoice.
    event Paid(bytes32 indexed memo, address indexed payer, uint256 amount);

    /// @notice The agent published the hash of the output it delivered for a step.
    event Committed(bytes32 indexed memo, bytes32 outputHash);

    constructor(address _usdc, address _agent) {
        usdc = _usdc;
        agent = _agent;
    }

    /// @notice Pay for one metered step. Requires a prior ERC20 approval.
    /// @param memo keccak256 of "<job_id>:<step>", as issued on the invoice.
    /// @param amount Amount in the token's base units (USDC: 6 decimals).
    function pay(bytes32 memo, uint256 amount) external {
        require(amount > 0, "amount must be > 0");
        require(
            IERC20(usdc).transferFrom(msg.sender, agent, amount),
            "USDC transferFrom failed"
        );
        emit Paid(memo, msg.sender, amount);
    }

    /// @notice Commit the hash of the output delivered for a step.
    /// @param outputHash sha256 of the step output, as recorded in agent memory.
    function commit(bytes32 memo, bytes32 outputHash) external {
        require(msg.sender == agent, "only agent");
        emit Committed(memo, outputHash);
    }
}

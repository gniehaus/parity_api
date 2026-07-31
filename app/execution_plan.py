from typing import Literal


StrategyType = Literal[
    "covered_call",
    "married_put",
    "collar",
    "buffer",
]

UnderlyingSource = Literal[
    "existing",
    "new",
]


def _step(
    sequence: int,
    key: str,
    order_role: str,
    order_scope: str,
    description: str,
    requires_previous_fill: bool,
) -> dict:
    return {
        "sequence": sequence,
        "key": key,
        "order_role": order_role,
        "order_scope": order_scope,
        "description": description,
        "requires_previous_fill": requires_previous_fill,
    }


def build_execution_plan(
    strategy_type: StrategyType,
    underlying_source: UnderlyingSource,
) -> list[dict]:
    """
    Return the execution sequence for one 100-share outcome lot.

    This function does not create orders or write to the database.
    """

    if strategy_type not in {
        "covered_call",
        "married_put",
        "collar",
        "buffer",
    }:
        raise ValueError(f"Unsupported strategy type: {strategy_type}")

    if underlying_source not in {"existing", "new"}:
        raise ValueError(
            f"Unsupported underlying source: {underlying_source}"
        )

    if underlying_source == "existing":
        plans = {
            "covered_call": [
                _step(
                    1,
                    "SELL_COVERED_CALL",
                    "SELL_COVERED_CALL",
                    "OPTIONS",
                    "Sell one covered call against 100 shares already owned.",
                    False,
                ),
            ],
            "married_put": [
                _step(
                    1,
                    "BUY_PROTECTIVE_PUT",
                    "BUY_PROTECTIVE_PUT",
                    "OPTIONS",
                    "Buy one protective put against 100 shares already owned.",
                    False,
                ),
            ],
            "collar": [
                _step(
                    1,
                    "COLLAR_OPTIONS_PACKAGE",
                    "COLLAR_OPTIONS_PACKAGE",
                    "OPTIONS_PACKAGE",
                    "Submit one protective put and one covered call together.",
                    False,
                ),
            ],
            "buffer": [
                _step(
                    1,
                    "BUFFER_OPTIONS_PACKAGE",
                    "BUFFER_OPTIONS_PACKAGE",
                    "OPTIONS_PACKAGE",
                    (
                        "Submit the higher-strike put, lower-strike put, "
                        "and covered call together."
                    ),
                    False,
                ),
            ],
        }

        return plans[strategy_type]

    plans = {
        "covered_call": [
            _step(
                1,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "Buy 100 shares.",
                False,
            ),
            _step(
                2,
                "SELL_COVERED_CALL",
                "SELL_COVERED_CALL",
                "OPTIONS",
                "After 100 shares fill, sell one covered call.",
                True,
            ),
        ],
        "married_put": [
            _step(
                1,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "Buy 100 shares.",
                False,
            ),
            _step(
                2,
                "BUY_PROTECTIVE_PUT",
                "BUY_PROTECTIVE_PUT",
                "OPTIONS",
                "After 100 shares fill, buy one protective put.",
                True,
            ),
        ],
        "collar": [
            _step(
                1,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "Buy 100 shares.",
                False,
            ),
            _step(
                2,
                "COLLAR_OPTIONS_PACKAGE",
                "COLLAR_OPTIONS_PACKAGE",
                "OPTIONS_PACKAGE",
                (
                    "After 100 shares fill, submit one protective "
                    "put and one covered call together."
                ),
                True,
            ),
        ],
        "buffer": [
            _step(
                1,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "Buy 100 shares.",
                False,
            ),
            _step(
                2,
                "BUFFER_OPTIONS_PACKAGE",
                "BUFFER_OPTIONS_PACKAGE",
                "OPTIONS_PACKAGE",
                (
                    "After 100 shares fill, submit the higher-strike "
                    "put, lower-strike put, and covered call together."
                ),
                True,
            ),
        ],
    }

    return plans[strategy_type]


    

ExitMode = Literal[
    "REMOVE_PROTECTION",
    "SELL_PROTECTED_POSITION",
]


def build_protected_position_exit_plan(
    strategy_type: StrategyType,
    exit_mode: ExitMode,
) -> list[dict]:
    """
    Return the safe execution sequence for one active protected lot.

    REMOVE_PROTECTION closes only the options overlay.
    SELL_PROTECTED_POSITION closes the overlay first, then sells the
    associated 100 shares only after the close package fills.
    """

    if strategy_type not in {
        "covered_call",
        "married_put",
        "collar",
        "buffer",
    }:
        raise ValueError(f"Unsupported strategy type: {strategy_type}")

    if exit_mode not in {
        "REMOVE_PROTECTION",
        "SELL_PROTECTED_POSITION",
    }:
        raise ValueError(f"Unsupported exit mode: {exit_mode}")

    close_scope = (
        "OPTIONS"
        if strategy_type in {
            "covered_call",
            "married_put",
        }
        else "OPTIONS_PACKAGE"
    )

    close_step = _step(
        1,
        "CLOSE_OPTIONS_OVERLAY",
        "CLOSE_OPTIONS_OVERLAY",
        close_scope,
        "Close the complete protection overlay first.",
        False,
    )

    if exit_mode == "REMOVE_PROTECTION":
        return [close_step]

    return [
        close_step,
        _step(
            2,
            "SELL_UNDERLYING",
            "SELL_UNDERLYING",
            "EQUITY",
            (
                "After the protection overlay is fully closed, "
                "sell the associated 100 shares."
            ),
            True,
        ),
    ]
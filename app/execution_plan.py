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
                "BUY_PROTECTIVE_PUT",
                "BUY_PROTECTIVE_PUT",
                "OPTIONS",
                "Buy one protective put first.",
                False,
            ),
            _step(
                2,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "After the put fills, buy 100 shares.",
                True,
            ),
        ],
        "collar": [
            _step(
                1,
                "BUY_PROTECTIVE_PUT",
                "BUY_PROTECTIVE_PUT",
                "OPTIONS",
                "Buy one protective put first.",
                False,
            ),
            _step(
                2,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "After the put fills, buy 100 shares.",
                True,
            ),
            _step(
                3,
                "SELL_COVERED_CALL",
                "SELL_COVERED_CALL",
                "OPTIONS",
                "After 100 shares fill, sell one covered call.",
                True,
            ),
        ],
        "buffer": [
            _step(
                1,
                "BUY_PUT_SPREAD_PACKAGE",
                "BUY_PUT_SPREAD_PACKAGE",
                "OPTIONS_PACKAGE",
                "Buy the defined-risk put spread first.",
                False,
            ),
            _step(
                2,
                "BUY_UNDERLYING",
                "BUY_UNDERLYING",
                "EQUITY",
                "After the put spread fills, buy 100 shares.",
                True,
            ),
            _step(
                3,
                "SELL_COVERED_CALL",
                "SELL_COVERED_CALL",
                "OPTIONS",
                "After 100 shares fill, sell one covered call.",
                True,
            ),
        ],
    }

    return plans[strategy_type]
def build_zero_cost_dividend_floor_collar(
    expiry_chain,
    max_loss_pct=0.005,
    assumed_dividend_yield=0.01,
):
    """
    Product: Defined Floor

    Updated logic:
    1. Determine the put strike needed to satisfy the user's max loss target
       after expected dividends.
    2. Buy the lowest put strike that satisfies the floor.
    3. Sell the call that creates the smallest possible option debit.
    4. Do NOT accept net credits.

    Net option cost:
        net_cost = put_cost - call_credit

    Required:
        net_cost >= 0

    Objective:
        choose the smallest net_cost above zero.
    """

    g = expiry_chain.copy()
    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = (
        notional * assumed_dividend_yield * (dte / 365.25)
    )
    expected_dividend_per_share = expected_dividend_dollars / MULT

    # Required terminal floor value after dividends and option debit.
    target_floor_value = notional * (1 - max_loss_pct)

    # Approximate required put strike assuming near-zero option cost.
    required_put_strike = (
        target_floor_value - expected_dividend_dollars
    ) / MULT

    valid_puts = g[
        (g["strike"] < spot)
        & (g["strike"] >= required_put_strike)
        & (g["putAskPrice"] > g["putBidPrice"])
        & (g["putMid"] > 0)
    ].copy()

    if valid_puts.empty:
        valid_puts = g[
            (g["strike"] < spot)
            & (g["putAskPrice"] > g["putBidPrice"])
            & (g["putMid"] > 0)
        ].copy()

        if valid_puts.empty:
            return None

        valid_puts["required_distance"] = (
            valid_puts["strike"] - required_put_strike
        ).abs()
        put = valid_puts.sort_values("required_distance").iloc[0]
    else:
        # Lowest strike that satisfies the floor target.
        put = valid_puts.sort_values("strike", ascending=True).iloc[0]

    put_strike = float(put["strike"])
    put_cost = float(put["putMid"]) * MULT

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callAskPrice"] > g["callBidPrice"])
        & (g["callMid"] > 0)
    ].copy()

    if valid_calls.empty:
        return None

    valid_calls["call_credit_dollars"] = valid_calls["callMid"] * MULT
    valid_calls["net_cost_dollars"] = put_cost - valid_calls["call_credit_dollars"]

    # Key change:
    # Do not accept net credits. Only allow zero or positive debits.
    debit_calls = valid_calls[
        valid_calls["net_cost_dollars"] >= 0
    ].copy()

    if debit_calls.empty:
        # No call creates a non-credit structure.
        # Return None instead of taking a credit.
        return None

    # Choose the smallest possible debit.
    call = debit_calls.sort_values(
        ["net_cost_dollars", "strike"],
        ascending=[True, False],
    ).iloc[0]

    call_strike = float(call["strike"])
    call_credit = float(call["callMid"]) * MULT

    net_cost = put_cost - call_credit
    net_cost_bps = net_cost / notional * 10000

    floor_value = (
        put_strike * MULT
        + expected_dividend_dollars
        - net_cost
    )

    cap_value = (
        call_strike * MULT
        + expected_dividend_dollars
        - net_cost
    )

    floor_return = floor_value / notional - 1
    cap_return = cap_value / notional - 1

    max_loss_dollars = notional - floor_value
    max_gain_dollars = cap_value - notional

    worst_net_cost = (
        float(put["putAskPrice"]) - float(call["callBidPrice"])
    ) * MULT

    bid_ask_drag_dollars = worst_net_cost - net_cost
    bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

    liq_score, total_volume, total_oi = liquidity_score(put, call)

    return {
        "product_name": "Defined Floor",
        "structure": "collar",
        "backend_structure": "long_underlying_plus_long_put_short_call",
        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_max_loss_pct": max_loss_pct,
        "required_put_strike": required_put_strike,

        "long_put_strike": put_strike,
        "call_strike": call_strike,

        "put_cost_dollars": put_cost,
        "call_credit_dollars": call_credit,
        "net_cost_dollars": net_cost,
        "net_cost_bps": net_cost_bps,

        # Updated label. This is no longer allowed to be a credit.
        "smallest_debit_ok": True,
        "zero_or_debit_only": True,

        "floor_value": floor_value,
        "cap_value": cap_value,
        "floor_return": floor_return,
        "cap_return": cap_return,
        "max_loss_dollars": max_loss_dollars,
        "max_gain_dollars": max_gain_dollars,

        "bid_ask_drag_bps": bid_ask_drag_bps,
        "total_volume": total_volume,
        "total_oi": total_oi,
        "liquidity_score": liq_score,

        "display": {
            "title": "Defined Floor",
            "subtitle": "Hard-loss target with capped upside",
            "estimated_max_loss_pct": round_pct(floor_return),
            "estimated_cap_pct": round_pct(cap_return),
            "estimated_option_cost_dollars": net_cost,
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to target a defined floor over the selected outcome period. "
                "Upside is capped in exchange for downside protection."
            ),
        },
    }


def build_zero_cost_target_cap_buffer(
    expiry_chain,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
):
    """
    Product: Buffered Growth

    Updated logic:
    1. Find the call strike closest to the user's target gain.
    2. Use the call premium to help fund the put spread.
    3. Do NOT accept net credits.
    4. Choose the smallest possible debit.
    5. Among near-equal debits, prefer the larger buffer.

    Net option cost:
        net_cost = put_spread_cost - call_credit

    Required:
        net_cost >= 0
    """

    g = expiry_chain.copy()
    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = (
        notional * assumed_dividend_yield * (dte / 365.25)
    )
    expected_dividend_per_share = expected_dividend_dollars / MULT

    target_cap_value = notional * (1 + target_gain_pct)

    required_call_strike = (
        target_cap_value - expected_dividend_dollars
    ) / MULT

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callAskPrice"] > g["callBidPrice"])
        & (g["callMid"] > 0)
    ].copy()

    if valid_calls.empty:
        return None

    valid_calls["target_distance"] = (
        valid_calls["strike"] - required_call_strike
    ).abs()

    # Pick the call closest to target gain.
    call = valid_calls.sort_values(
        ["target_distance", "strike"],
        ascending=[True, True],
    ).iloc[0]

    call_strike = float(call["strike"])
    call_credit = float(call["callMid"]) * MULT

    candidate_long_puts = g[
        (g["strike"] <= spot)
        & (g["putAskPrice"] > g["putBidPrice"])
        & (g["putMid"] > 0)
    ].copy()

    if candidate_long_puts.empty:
        return None

    candidate_long_puts["atm_distance"] = (
        candidate_long_puts["strike"] - spot
    ).abs()

    long_put = candidate_long_puts.sort_values("atm_distance").iloc[0]
    long_put_strike = float(long_put["strike"])
    long_put_cost = float(long_put["putMid"]) * MULT

    short_puts = g[
        (g["strike"] < long_put_strike)
        & (g["putAskPrice"] > g["putBidPrice"])
        & (g["putMid"] > 0)
    ].copy()

    if short_puts.empty:
        return None

    rows = []

    for _, short_put in short_puts.iterrows():
        short_put_strike = float(short_put["strike"])
        short_put_credit = float(short_put["putMid"]) * MULT

        put_spread_cost = long_put_cost - short_put_credit
        net_cost = put_spread_cost - call_credit
        net_cost_bps = net_cost / notional * 10000

        # Key change:
        # Do not accept net credits.
        if net_cost < 0:
            continue

        buffer_width_points = long_put_strike - short_put_strike
        buffer_pct = buffer_width_points / spot

        max_buffer_value = buffer_width_points * MULT

        protected_zone_value = (
            short_put_strike * MULT
            + max_buffer_value
            + expected_dividend_dollars
            - net_cost
        )

        cap_value = (
            call_strike * MULT
            + expected_dividend_dollars
            - net_cost
        )

        protected_zone_return = protected_zone_value / notional - 1
        cap_return = cap_value / notional - 1

        worst_net_cost = (
            float(long_put["putAskPrice"])
            - float(short_put["putBidPrice"])
            - float(call["callBidPrice"])
        ) * MULT

        bid_ask_drag_dollars = worst_net_cost - net_cost
        bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

        liq_score, total_volume, total_oi = liquidity_score(
            long_put, short_put, call
        )

        rows.append({
            "short_put": short_put,
            "short_put_strike": short_put_strike,
            "short_put_credit": short_put_credit,
            "put_spread_cost": put_spread_cost,
            "net_cost": net_cost,
            "net_cost_bps": net_cost_bps,
            "buffer_width_points": buffer_width_points,
            "buffer_pct": buffer_pct,
            "protected_zone_value": protected_zone_value,
            "protected_zone_return": protected_zone_return,
            "cap_value": cap_value,
            "cap_return": cap_return,
            "bid_ask_drag_bps": bid_ask_drag_bps,
            "total_volume": total_volume,
            "total_oi": total_oi,
            "liquidity_score": liq_score,
        })

    if not rows:
        # No buffer can be built without taking a credit.
        return None

    candidates = pd.DataFrame(rows)

    # Primary objective: smallest debit possible.
    # Secondary objective: larger buffer if debits are close.
    candidates["debit_rank"] = candidates["net_cost"]
    candidates["buffer_rank"] = -candidates["buffer_pct"]

    best = candidates.sort_values(
        ["debit_rank", "buffer_rank", "bid_ask_drag_bps", "total_oi"],
        ascending=[True, True, True, False],
    ).iloc[0]

    return {
        "product_name": "Buffered Growth",
        "structure": "buffer",
        "backend_structure": "long_underlying_plus_long_put_short_put_short_call",
        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_gain_pct": target_gain_pct,
        "required_call_strike": required_call_strike,

        "long_put_strike": long_put_strike,
        "short_put_strike": float(best["short_put_strike"]),
        "call_strike": call_strike,

        "long_put_cost_dollars": long_put_cost,
        "short_put_credit_dollars": float(best["short_put_credit"]),
        "put_spread_cost_dollars": float(best["put_spread_cost"]),
        "call_credit_dollars": call_credit,
        "net_cost_dollars": float(best["net_cost"]),
        "net_cost_bps": float(best["net_cost_bps"]),

        # Updated label. This is no longer allowed to be a credit.
        "smallest_debit_ok": True,
        "zero_or_debit_only": True,

        "buffer_width_points": float(best["buffer_width_points"]),
        "buffer_pct": float(best["buffer_pct"]),

        "protected_zone_value": float(best["protected_zone_value"]),
        "protected_zone_return": float(best["protected_zone_return"]),
        "cap_value": float(best["cap_value"]),
        "cap_return": float(best["cap_return"]),

        "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
        "total_volume": float(best["total_volume"]),
        "total_oi": float(best["total_oi"]),
        "liquidity_score": float(best["liquidity_score"]),

        "display": {
            "title": "Buffered Growth",
            "subtitle": "First-loss protection with more upside potential",
            "estimated_buffer_pct": round_pct(float(best["buffer_pct"])),
            "estimated_cap_pct": round_pct(float(best["cap_return"])),
            "estimated_option_cost_dollars": float(best["net_cost"]),
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to absorb a defined range of losses first. "
                "Losses may continue if the market falls beyond the buffer."
            ),
        },
    }
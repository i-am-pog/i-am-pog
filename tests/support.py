"""Fixed configuration for tests of pricing mechanics.

config/pricing.yaml holds business decisions that change -- the fee split, the
margin bands. Tests of *behaviour* (does rounding respect the floor? does a
thin undercut still sell?) must not move every time someone tunes those, so
they pin their own numbers here. Tests that assert the live policy itself read
the real config on purpose.
"""

from bw.pricing import PricingEngine

FIXED = {
    "currency": "CAD",
    "payment": {"rate": 0.029, "fixed": 0.30},
    "suppliers": {
        # The whole fee on every item: the strictest case, and the one the
        # edge-case tests are written against.
        "ace": {"order_fee": 15.00, "expected_units": 1,
                "fee_full_below": 60.00, "fee_free_above": 150.00},
        "twin_a": {"order_fee": 15.00, "expected_units": 1,
                   "fee_full_below": 60.00, "fee_free_above": 150.00},
        "twin_b": {"order_fee": 15.00, "expected_units": 1,
                   "fee_full_below": 60.00, "fee_free_above": 150.00},
        "onhand": {"order_fee": 0},
    },
    "margin_floor": {"bands": [
        {"max_price": 40.00, "min_margin": 0.45},
        {"max_price": 80.00, "min_margin": 0.38},
        {"max_price": 150.00, "min_margin": 0.32},
        {"max_price": None, "min_margin": 0.28},
    ]},
    "market": {"undercut": 0.05, "min_undercut": 1.00, "msrp_cap": 0.95,
               "hold_above_msrp": True},
    "rounding": {"endings": [0.99, 0.95], "max_markdown": 6.00},
    "order": {"free_shipping_threshold": 149.00},
}


def fixed_engine() -> PricingEngine:
    import copy
    return PricingEngine(config=copy.deepcopy(FIXED))

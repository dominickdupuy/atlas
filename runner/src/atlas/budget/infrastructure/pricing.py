"""StaticPricingTable: $/Mtok by model-id prefix, integer math throughout.

Prices drift; the table is data, and ATLAS_PRICE_INPUT_PER_MTOK /
ATLAS_PRICE_OUTPUT_PER_MTOK override it wholesale for the configured model
without a code change. Costs round UP — the ledger must never flatter the
spend toward the ceiling.
"""

from __future__ import annotations

from atlas.budget.domain.ledger import UsdMicros, usd
from atlas.connectors.domain.tools import TokenUsage

# (input $/Mtok, output $/Mtok) by model-id prefix; first match wins.
_DEFAULT_PRICES: list[tuple[str, tuple[UsdMicros, UsdMicros]]] = [
    ("claude-opus", (usd("15.00"), usd("75.00"))),
    ("claude-sonnet", (usd("3.00"), usd("15.00"))),
    ("claude-haiku", (usd("0.80"), usd("4.00"))),
]
# Unknown model: assume the most expensive tier rather than undercounting.
_FALLBACK = (usd("15.00"), usd("75.00"))

_TOKENS_PER_MTOK = 1_000_000


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


class StaticPricingTable:
    def __init__(
        self,
        input_per_mtok: UsdMicros | None = None,
        output_per_mtok: UsdMicros | None = None,
    ) -> None:
        self._override = (
            (input_per_mtok, output_per_mtok)
            if input_per_mtok is not None and output_per_mtok is not None
            else None
        )

    def cost(self, model: str, usage: TokenUsage) -> UsdMicros:
        if self._override is not None:
            input_price, output_price = self._override
        else:
            input_price, output_price = next(
                (prices for prefix, prices in _DEFAULT_PRICES if model.startswith(prefix)),
                _FALLBACK,
            )
        input_cost = _ceil_div(usage.input_tokens * int(input_price), _TOKENS_PER_MTOK)
        output_cost = _ceil_div(usage.output_tokens * int(output_price), _TOKENS_PER_MTOK)
        return UsdMicros(input_cost + output_cost)

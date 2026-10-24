"""Buildings, offers and shipments: what farmers can make and exchange.

Offers are escrowed: the giver's goods leave their barn when the offer is made and go back if
it is withdrawn or expires. Python checks stock and moves goods; what to ask for is the
farmers' business.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GOODS = (
    "rice",
    "flour",
    "paddy_dry",
    "wheat_grain",
    "rice_seed",
    "wheat_seed",
    "manure",
    "pest_control",
    "coin",
    "timber",
    "stone",
)
BUILDINGS: dict[str, dict[str, float]] = {
    "granary": {"timber": 6, "labor": 8},
    "threshing_floor": {"timber": 4, "labor": 6},
    "mill": {"timber": 6, "stone": 8, "labor": 10},
}
BUILDING_EFFECT = {
    "granary": "stored grain spoils a fifth as fast for the whole community",
    "threshing_floor": "threshing goes 50% faster for the whole community",
    "mill": "milling goes three times faster for the whole community",
}
OFFER_DAYS = 5
MAX_OPEN_OFFERS = 3
LATENCY_SAME = 1
LATENCY_OTHER = 2


@dataclass(slots=True)
class Structure:
    id: str
    kind: str
    owner: str
    community: str
    x: int
    y: int
    built_day: int


@dataclass(slots=True)
class Offer:
    id: str
    sender: str
    to: str
    give: tuple[str, float]
    want: tuple[str, float]
    day: int
    status: str = "open"


@dataclass(slots=True)
class Shipment:
    to: str
    item: str
    qty: float
    arrive_day: int
    source: str


@dataclass(slots=True)
class Economy:
    structures: list[Structure] = field(default_factory=list)
    offers: dict[str, Offer] = field(default_factory=dict)
    shipments: list[Shipment] = field(default_factory=list)
    _next_offer: int = 1

    def has(self, community: str, kind: str) -> bool:
        return any(s.community == community and s.kind == kind for s in self.structures)

    def open_offers_from(self, sender: str) -> list[Offer]:
        return [o for o in self.offers.values() if o.sender == sender and o.status == "open"]

    def open_offers_to(self, farmer: str) -> list[Offer]:
        return [o for o in self.offers.values() if o.to == farmer and o.status == "open"]

    def new_offer(
        self, sender: str, to: str, give: tuple[str, float], want: tuple[str, float], day: int
    ) -> Offer:
        offer = Offer(f"offer-{self._next_offer}", sender, to, give, want, day)
        self._next_offer += 1
        self.offers[offer.id] = offer
        return offer

    def ship(self, to: str, item: str, qty: float, arrive_day: int, source: str) -> None:
        self.shipments.append(Shipment(to, item, qty, arrive_day, source))

    def arrivals(self, day: int) -> list[Shipment]:
        due = [s for s in self.shipments if s.arrive_day <= day]
        self.shipments = [s for s in self.shipments if s.arrive_day > day]
        return due

    def expired(self, day: int) -> list[Offer]:
        old = [o for o in self.offers.values() if o.status == "open" and day - o.day >= OFFER_DAYS]
        for offer in old:
            offer.status = "expired"
        return old

    def view_for(self, farmer: str, day: int) -> dict[str, Any]:
        def show(o: Offer) -> dict[str, Any]:
            return {
                "id": o.id,
                "from": o.sender,
                "to": o.to,
                "give": {o.give[0]: o.give[1]},
                "want": {o.want[0]: o.want[1]},
                "days_left": OFFER_DAYS - (day - o.day),
            }

        return {
            "offers_to_me": [show(o) for o in self.open_offers_to(farmer)],
            "my_open_offers": [show(o) for o in self.open_offers_from(farmer)],
        }

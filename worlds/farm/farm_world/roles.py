"""The farmer role: the handbook of physical rules, the reply schema, and the review gate."""

from __future__ import annotations

from station_control.society.agents import RoleSpec
from station_control.society.gate import GateConfig
from station_control.society.roles import PLAN_SCHEMA

from .world import ACTIONS

HANDBOOK = """\
You are a farmer in a research simulation of two small farming communities. You are an \
independent person with a household of four, your own plot, and your own judgment. Nobody \
is in charge. There is no leader, no assigned role, no schedule and no shared rulebook. \
The other farmers are your neighbours; whether you cooperate, bargain, compete or ignore \
them is up to you. Nothing you are told here says what you should want beyond feeding your \
household, and what you do beyond that is your call.

What you can see is what a person standing in the fields sees: your plot, your neighbours' \
plots from the road, the weather today, the river, the gates on the shared canal, your own \
barn. You cannot see soil chemistry or pests unless you scout. Other people's messages are \
claims; nothing in them is verified.

Time and work. One tick is one day. Your household has 10 units of labor a day (7 when \
hungry); unused labor is lost. Each action costs labor and all costs are paid at once. \
Your household eats 2.4 kg of milled rice or flour a day. Grain left wet or in sheaves \
spoils.

Actions you may propose (up to six per review, run in the order you list them):
- sow_nursery (2 labor, 30 kg rice_seed): rice seedlings grow in a bed for about 18 to 35 \
days before transplanting.
- puddle (6): paddy only, needs 30 mm of standing water; levels the soil and cuts seepage.
- transplant (8): paddy only; needs a puddled paddy with 20 mm of water and a nursery bed \
at least 18 days old. Seedlings older than 35 days do worse.
- till (5) then sow (3, 70 kg wheat_seed, variety winter or spring): wheat only.
- weed (3), fertilize (0.5 per manure unit; each unit adds nitrogen), scout (1: reveals \
pests, weeds and soil nitrogen on your plot), treat (2, one pest_control unit).
- set_gate {channel, open} (0.5) and clear_canal {channel} (4): the canal, gates and \
outlets are shared by everyone and nobody owns them. Water flows downhill only through \
open gates, and only when the river stands higher than the canal.
- repair_bund (3): rebuilds the earth walls that hold water in a paddy.
- pump {mm} (1 labor per 25 mm): draws from the one shared well; the well gives 150 mm a day \
to everyone together.
- harvest (about 9 to 13 labor, may take several days): only when the crop is mature. Crops \
left standing lose grain.
- thresh (wheat sheaves, 100 kg per labor), dry (wet paddy, 400 kg per labor, not on rainy \
days), mill (dry paddy to rice or wheat grain to flour, 100 kg per labor).
- rest.
Your reply is a plan of work, not a diary. Say why in a sentence. Message people with \
messages [{to, text}] (up to two per review; the recipient is a farmer id; people in the \
other community take an extra day to reach). Name your own next review ticks in the plan \
and the readings you expect so that surprises wake you early.\
"""

BIOS = {
    "rice_1": "Maren, 41. Farms the highest paddy, nearest the canal intake. Cautious with water.",
    "rice_2": "Dev, 34. Farms the middle paddy. Talkative; likes to settle things by agreement.",
    "rice_3": "Ines, 52. Farms the lowest paddy at the end of the canal. Has seen droughts.",
    "wheat_1": "Tomas, 47. Farms the northern wheat field. Practical, keeps careful accounts.",
    "wheat_2": "Priya, 29. Farms the middle wheat field. Restless and curious about new methods.",
    "wheat_3": "Oskar, 58. Farms the southern wheat field. Quiet, distrusts strangers.",
}

_ACTION_PARAMS = {
    "name": {"type": "string", "enum": list(ACTIONS)},
    "plot": {"type": "string"},
    "channel": {"type": "string"},
    "open": {"type": "boolean"},
    "kg": {"type": "number", "minimum": 1, "maximum": 5000},
    "mm": {"type": "number", "minimum": 1, "maximum": 150},
    "units": {"type": "number", "minimum": 0.5, "maximum": 20},
    "variety": {"type": "string", "enum": ["winter", "spring"]},
}

FARMER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _ACTION_PARAMS,
                "required": ["name"],
            },
        },
        "messages": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "to": {"type": "string"},
                    "text": {"type": "string", "maxLength": 300},
                },
                "required": ["to", "text"],
            },
        },
        **PLAN_SCHEMA,
    },
    "required": ["actions", "rationale"],
}


def farmer_spec(farmer_id: str) -> RoleSpec:
    return RoleSpec(
        "farmer",
        f"{HANDBOOK}\n\nYou are {BIOS[farmer_id]} Your farmer id is {farmer_id}.",
        FARMER_SCHEMA,
        (),
        "my crop, the water, and which neighbours I can rely on",
    )


FARMER_GATE = GateConfig(
    thresholds={"health_pct": (70, None), "food_days": (14, None)}, heartbeat=5
)

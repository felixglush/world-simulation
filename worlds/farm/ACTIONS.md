# What a farmer can do

A farmer proposes up to six actions each time they think, plus up to two messages. The world checks every one: the right parameter names, types and ranges, ownership, stock, labor and the physical prerequisites. Anything that fails is rejected with a reason the farmer reads next review. Each farmer has 10 labor a day (7 when hungry) and unused labor is lost. Costs below are labor unless stated.

## Rice (paddy farmers only)

| Action | Cost | Needs | Effect |
| --- | --- | --- | --- |
| `sow_nursery` {kg} | 2 | 20 to 500 kg rice seed, no bed already sown | Starts a nursery bed. Seedlings are ready from 18 days; older than 35 days they do worse. A thin seed rate thins the crop. |
| `puddle` {plot} | 6 | Empty paddy with 30 mm of standing water | Levels the soil and cuts seepage from 6 to 2 mm a day. |
| `transplant` {plot} | 8 | Puddled paddy, 20 mm of water, a nursery at least 18 days old | Plants the crop. |

## Wheat (field farmers only)

| Action | Cost | Needs | Effect |
| --- | --- | --- | --- |
| `till` {plot} | 5 | Empty wheat field | Prepares the seedbed. |
| `sow` {plot, variety, kg} | 3 | Tilled field, 50 to 500 kg wheat seed (70 is a full rate) | Plants winter wheat (needs 40 cold days to head) or lower-yielding spring wheat. |

## Looking after the crop (own plot)

| Action | Cost | Effect |
| --- | --- | --- |
| `weed` {plot} | 3 | Cuts weeds to a low level. |
| `fertilize` {plot, units} | 0.5 per unit | Spends manure; each unit adds 25 kg of nitrogen. |
| `scout` {plot} | 1 | Reveals pests, weeds and soil nitrogen as words (none, some, heavy). Pests are otherwise hidden. |
| `treat` {plot} | 2 + 1 pest_control | Cuts pest and disease pressure to a fifth. |
| `repair_bund` {plot} | 3 | Rebuilds the walls that hold water in a paddy after an overtop. |
| `pump` {plot, mm} | 1 per 25 mm | Draws from the community well onto the plot: 100 mm a day in Riverbend, 150 in Highfield, shared by all its farmers. |

## Water network (shared, nobody owns it)

| Action | Cost | Effect |
| --- | --- | --- |
| `set_gate` {channel, open} | 0.5 | Opens or closes the intake, a carry channel, a paddy turnout or an outlet. Water flows downhill only through open gates and only when the river stands above the canal. |
| `clear_canal` {channel} | 4 | Removes silt that cuts a channel's capacity. |
| `dig` {src, dst, labor} | 1 to 10 (default 4) | Digs a new channel between two nodes (`river`, `drain`, a plot id or a canal node). It carries 10 mm a day per labor spent, starts with its gate shut, silts and is shared like every other channel (anyone can open, close or clear it). Water still only runs downhill, so a ditch dug uphill carries nothing. Everyone can see who dug it. |

## Harvest and storing

| Action | Cost | Effect |
| --- | --- | --- |
| `harvest` {plot} | 9 (13 if the paddy holds more than 30 mm) | Cuts a mature crop over as many days as the labor takes. Wet rice also loses 8% of grain. Crops left standing lose grain. |
| `thresh` {kg} | 1 per 100 kg (150 with a threshing floor) | Wheat sheaves to wheat grain, 5% lost. |
| `dry` {kg} | 1 per 400 kg | Wet paddy to dry paddy, 10% lost. Not on a rainy day. |
| `mill` {item, kg} | 1 per 100 kg (300 with a mill) | Dry paddy to rice (65%) or wheat grain to flour (75%). |
| `save_seed` {item, kg} | 1 per 200 kg | Keeps dry paddy or wheat grain as seed. Seed you do not save is gone after sowing. |

## Building (helps the whole community, one of each per community)

| Action | Cost | Effect |
| --- | --- | --- |
| `build` {kind: granary} | 8 + 6 timber | Stored rice and dry paddy spoil a fifth as fast. |
| `build` {kind: threshing_floor} | 6 + 4 timber | Threshing is 50% faster. |
| `build` {kind: mill} | 10 + 6 timber + 8 stone | Milling is three times faster. |

Any other name is also allowed: `build` {kind: "drying rack", timber, stone, labor}. The farmer chooses what to put in (up to 20 timber, 20 stone, 20 labor; labor defaults to 4). It is placed on the map and visible to everyone, and has no mechanical effect, so what it means is for the farmers to agree. Open kinds are not limited to one per community.

The building goes on the nearest free ground to the builder's home. Riverbend starts with 20 timber and 2 stone per farmer, Highfield with 6 timber and 12 stone, so the valley cannot build a mill without stone from the uplands.

## Trade and gifts

| Action | Cost | Effect |
| --- | --- | --- |
| `give` {to, item, qty} | 0 | Sends goods to another farmer. They arrive after a day in your community, two in the other. |
| `offer` {to, give_item, give_qty, want_item, want_qty} | 0 | Proposes a swap to one farmer. Your goods are held in escrow. Up to 3 open offers; an offer lapses after 5 days and the goods come back. |
| `accept` {offer_id} | 0 | Takes an offer made to you if you hold what it asks. Both sides ship, with the same delay. |
| `withdraw` {offer_id} | 0 | Cancels your open offer and returns the goods. |

Goods: rice, flour, paddy_dry, wheat_grain, rice_seed, wheat_seed, manure, pest_control, coin, timber, stone.

## Other

- `rest`: do nothing.
- Messages: up to two per review to any farmer; they take a day to arrive, two across communities. A message is only a claim and changes nothing in the world.

## Happens without being asked

Households eat 2.4 kg of rice or flour a day (hungry households have 7 labor), and grain spoils: wet paddy 2% a day, sheaves 0.5%, dry stock 0.05%. Weather, evapotranspiration, seepage, weeds, pests and crop growth run every day.

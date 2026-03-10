<picture>
  <source media="(prefers-color-scheme: dark)" srcset="custom_components/kilowahti/brands/dark_logo.png">
  <img alt="Kilowahti" src="custom_components/kilowahti/brands/logo.png" width="400">
</picture>

**Kilowahti** (*kilowatti* + *vahti* — "the kilowatt sentinel") is a Home Assistant custom integration for Nordic/Baltic electricity spot price tracking.

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Documentation](https://img.shields.io/badge/docs-kilowahti.fi-blue)](https://docs.kilowahti.fi/)

## Features

- Day-ahead spot price data from [spot-hinta.fi](https://spot-hinta.fi) (no API key required)
- 15-minute or 1-hour price resolution
- VAT applied automatically; user-entered prices (transfer, fixed periods) are always gross
- Fixed-price contract period management
- Transfer price tier groups (switch between multiple contracts without re-entering data)
- Optimization scores (daily and monthly) per configurable meter group
- Price rank, rank quartile, control factors, and threshold binary sensors
- Service calls: `get_active_prices`, `get_prices`, `cheapest_hours`, `average_price`, `add_fixed_period`, `remove_fixed_period`, `list_fixed_periods`
- ENTSO-E and Kilowahti proxy support planned for V2

## Supported Regions

`FI`, `EE`, `LT`, `LV`, `DK1`, `DK2`, `NO1`–`NO5`, `SE1`–`SE4`

> **Currency notice:** Kilowahti currently supports only the official electricity market currency EUR. Conversions to DKK, NOK and SEK will become possible later.

## Installation

1. Install via HACS:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Kilowahti&repository=ha-kilowahti&category=Integration)

   Or add `https://github.com/Kilowahti/ha-kilowahti` manually via HACS → Custom Repositories
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration → Kilowahti**
4. Complete the setup wizard

## Entities

### Sensors

| Entity | Description |
|---|---|
| `sensor.kilowahti_{name}_spot_price` | Current slot's spot price |
| `sensor.kilowahti_{name}_effective_price` | Spot or fixed-period price |
| `sensor.kilowahti_{name}_transfer_price` | Active transfer tier price |
| `sensor.kilowahti_{name}_total_price` | Energy + transfer price |
| `sensor.kilowahti_{name}_price_rank` | Rank 1–96 (15 min) or 1–24 (1 hour); 1 = cheapest |
| `sensor.kilowahti_{name}_price_quartile` | Price quartile 1–4; 1 = cheapest quarter |
| `sensor.kilowahti_{name}_today_avg/min/max` | Today's spot stats |
| `sensor.kilowahti_{name}_tomorrow_avg/min/max` | Tomorrow's stats (0 when unavailable) |
| `sensor.kilowahti_{name}_next_hours_avg` | Average over next N hours |
| `sensor.kilowahti_{name}_control_factor_price` | Normalized 0–1 price factor |
| `sensor.kilowahti_{name}_control_factor_price_bipolar` | ±1 bipolar factor |
| `sensor.kilowahti_{name}_score_{profile}_daily` | Daily optimization score 0–100 |
| `sensor.kilowahti_{name}_score_{profile}_monthly` | Monthly optimization score 0–100 |

### Binary Sensors

| Entity | Description |
|---|---|
| `binary_sensor.kilowahti_{name}_price_acceptable` | Price ≤ configured threshold |
| `binary_sensor.kilowahti_{name}_rank_acceptable` | Rank ≤ configured threshold |
| `binary_sensor.kilowahti_{name}_price_or_rank_acceptable` | Either condition met |
| `binary_sensor.kilowahti_{name}_fixed_period_active` | Currently in a fixed-price period |
| `binary_sensor.kilowahti_{name}_tomorrow_available` | Tomorrow's prices fetched |

## Services

```yaml
# Get active prices (today + tomorrow by default, or a custom range)
service: kilowahti.get_active_prices
data: {}

# Get price slots for a time range
service: kilowahti.get_prices
data:
  start: "2026-03-07T00:00:00"
  end: "2026-03-07T23:59:59"

# Find cheapest consecutive window
service: kilowahti.cheapest_hours
data:
  start: "2026-03-07T18:00:00"
  end: "2026-03-08T08:00:00"
  hours: 3

# Add a fixed-price period
service: kilowahti.add_fixed_period
data:
  label: "Q1 2026 fixation"
  start_date: "2026-01-01"
  end_date: "2026-03-31"
  price: 8.5
```

All price services accept an optional `formatted` boolean (default `true`). Set to `false` to get raw c/kWh values at full precision.

## Optimization Score Algorithm

Scores are computed from consumption in each price-rank quartile:

```
Q = slots_per_day // 4  (24 for 15-min, 6 for 1-hour)
raw = (Q1×3 + Q2×2 + Q3×1) / (Total×3) × 100
score = clamp((raw − 30.0) / 53.3 × 100, 0, 100)
```

## Migration from spot_price.yaml

1. Disable `packages/spot_price.yaml`
2. Install Kilowahti via HACS → configure
3. Swap entity IDs in automations: `sensor.shf_*` → `sensor.kilowahti_{name}_*`
4. Remove `input_number.shf_*` and `input_datetime.shf_*` helpers

## Acknowledgments

Kilowahti would not exist without the prior work of **Teemu Mikkonen** and his [spotprices2ha](https://github.com/T3m3z/spotprices2ha) project. That copy-paste solution was the direct inspiration for this integration — it proved the concept, shaped the sensor design, and provided automation patterns that many Finnish HA users have relied on. Thank you, Teemu.

A special thanks also to the Finnish hobbyist behind [spot-hinta.fi](https://spot-hinta.fi) for building and maintaining an excellent, free, and easy-to-use electricity price API that makes projects like this possible.

## Credits

Kilowahti was created by Jessi Björk, building on the foundation of spotprices2ha to bring richer functionality to her home automation.

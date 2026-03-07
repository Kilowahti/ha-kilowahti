# Kilowahti

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

**Kilowahti** (*kilowatti* + *vahti* — "the kilowatt sentinel") is a Home Assistant custom integration for Nordic/Baltic electricity spot price tracking.

## Features

- Real-time spot price data from [spot-hinta.fi](https://spot-hinta.fi) (no API key required)
- 15-minute or 1-hour price resolution
- VAT and electricity tax applied automatically
- Fixed-price contract period management
- Transfer price tier groups (switch between multiple contracts without re-entering data)
- Optimization scores (daily and monthly) per configurable meter group
- Price rank, control factors, and threshold binary sensors
- Service calls: `get_prices`, `cheapest_hours`, `average_price`, `add_fixed_period`, `remove_fixed_period`, `list_fixed_periods`
- ENTSO-E and Kilowahti proxy support planned for V2

## Supported Regions

`FI`, `EE`, `LT`, `LV`, `DK1`, `DK2`, `NO1`–`NO5`, `SE1`–`SE4`

## Installation

1. Install via HACS → Custom Repositories → add `https://github.com/geekuality/ha-kilowahti`
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration → Kilowahti**
4. Complete the setup wizard

## Entities

### Sensors

| Entity | Description |
|---|---|
| `sensor.{name}_spot_price` | Current slot's spot price |
| `sensor.{name}_effective_price` | Spot or fixed-period price |
| `sensor.{name}_transfer_price` | Active transfer tier price |
| `sensor.{name}_total_price` | All-in: energy + transfer + electricity tax |
| `sensor.{name}_price_rank` | Rank 1–96 (15 min) or 1–24 (1 hour); 1 = cheapest |
| `sensor.{name}_today_avg/min/max` | Today's spot stats |
| `sensor.{name}_tomorrow_avg/min/max` | Tomorrow's stats (0 when unavailable) |
| `sensor.{name}_next_hours_avg` | Average over next N hours |
| `sensor.{name}_control_factor` | Normalized 0–1 price factor |
| `sensor.{name}_control_factor_bipolar` | ±1 bipolar factor |
| `sensor.{name}_score_{profile}_today` | Daily optimization score 0–100 |
| `sensor.{name}_score_{profile}_month` | Monthly optimization score 0–100 |

### Binary Sensors

| Entity | Description |
|---|---|
| `binary_sensor.{name}_price_acceptable` | Price ≤ configured threshold |
| `binary_sensor.{name}_rank_acceptable` | Rank ≤ configured threshold |
| `binary_sensor.{name}_price_or_rank_acceptable` | Either condition met |
| `binary_sensor.{name}_fixed_period_active` | Currently in a fixed-price period |
| `binary_sensor.{name}_tomorrow_available` | Tomorrow's prices fetched |

## Services

```yaml
# Get price slots for a time range
service: kilowahti.get_prices
data:
  start: "2026-03-07T00:00:00"
  end: "2026-03-07T23:59:59"

# Find cheapest window
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
3. Swap entity IDs in automations: `sensor.shf_*` → `sensor.{name}_*`
4. Remove `input_number.shf_*` and `input_datetime.shf_*` helpers

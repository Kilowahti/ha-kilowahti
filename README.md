<picture>
  <source media="(prefers-color-scheme: dark)" srcset="custom_components/kilowahti/brand/dark_logo.png">
  <img alt="Kilowahti" src="custom_components/kilowahti/brand/logo.png" width="400">
</picture>

**Kilowahti** (*kilowatti* + *vahti* — "the kilowatt sentinel") is a Home Assistant integration for Nordic/Baltic electricity cost awareness and optimization — whether you are on a spot contract or a fixed rate.

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Documentation](https://img.shields.io/badge/docs-kilowahti.fi-blue)](https://docs.kilowahti.fi/)
[![Feedback](https://img.shields.io/badge/feedback-share%20yours-brightgreen)](https://tally.so/r/QK05XY)

## What Kilowahti can do for you

**On a fixed-rate contract?** You still get transfer tariff management, monthly fixed cost tracking, a true total cost-per-kWh sensor, optimization scores, and — if you have solar or a battery — the full generation and battery feature set. Kilowahti is not a spot-only tool.

**On a spot contract?** Electricity prices change every hour — or every 15 minutes if your contract/meter supports it — and can swing dramatically across the day. Kilowahti brings those prices into Home Assistant with live sensors, smart threshold binary sensors that flip on when it's cheap, price rankings, control factors, and service actions to find the cheapest windows — so your automations can act on prices instead of just a schedule.

**For every household:**
- Fixed-price contract support — define periods; all sensors and stats reflect them automatically
- Full cost picture: combine spot price with your actual transfer tariff for the true cost-per-kWh you pay
- Live spot price with rank, quartile, and control factors — ready to wire into any automation
- Price and rank threshold binary sensors — turn on when electricity is cheap, turn off when it isn't
- Today's price statistics, forward-window averages, and tomorrow's prices as soon as they publish
- Transfer tariff tier groups — model your exact contract and switch between tiers without re-entering data
- Monthly fixed cost tracking — spread your base fee across days for an accurate daily cost figure
- Optimization scores (daily & monthly) that show how well your household shifts consumption to cheap hours

**For solar panel and battery owners:**
- Live export price and today's export statistics — see what you'd earn by selling to the grid right now
- Self-consumption value sensor — know what each kWh of your own generation saves you in avoided import costs
- Charge opportunity factor showing how good right now is for grid charging (0 = worst, 1 = best)
- Charge and discharge recommendation binary sensors
- Optimal charge window for a full battery cycle, calculated from today's and tomorrow's prices
- Generation schedule service — feed in a solar forecast and get hour-by-hour recommendations: self-consume, export, or charge the battery (if battery is configured)

**For automations and dashboards:**
- Rich service actions: query price windows, find cheapest hours, find best export hours, get generation schedules — usable from scripts, automations, and dashboard cards

→ Full entity reference and service documentation at **[docs.kilowahti.fi](https://docs.kilowahti.fi/)**

## Supported regions

`FI`, `EE`, `LT`, `LV`, `DK1`, `DK2`, `NO1`–`NO5`, `SE1`–`SE4`

> **Currency notice:** Kilowahti currently displays prices in EUR for all regions. Conversion to local currencies (DKK, NOK, SEK) is planned for a future release.

## Installation

1. Install via HACS:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Kilowahti&repository=ha-kilowahti&category=Integration)

   Or add `https://github.com/Kilowahti/ha-kilowahti` manually via HACS → Custom Repositories
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration → Kilowahti**
4. Complete the setup wizard

See the [installation guide](https://docs.kilowahti.fi/installation/) and [configuration reference](https://docs.kilowahti.fi/configuration/) for full details.

## Feedback

Found a bug or have a feature idea? Open an [issue](https://github.com/Kilowahti/ha-kilowahti/issues) or start a [discussion](https://github.com/Kilowahti/ha-kilowahti/discussions) on GitHub.

If you don't have a GitHub account, you can share feedback via [this short form](https://tally.so/r/QK05XY) — no account needed.

## Acknowledgments

Kilowahti would not exist without the prior work of **Teemu Mikkonen** and his [spotprices2ha](https://github.com/T3m3z/spotprices2ha) project. That copy-paste solution was the direct inspiration for this integration — it proved the concept, shaped the sensor design, and provided automation patterns that many Finnish HA users have relied on. Thank you, Teemu.

A special thanks also to the Finnish hobbyist behind [spot-hinta.fi](https://spot-hinta.fi) for building and maintaining an excellent, free, and easy-to-use electricity price API that makes projects like this possible.

## Credits

Kilowahti was created by Jessi Björk, building on the foundation of spotprices2ha to bring richer functionality to her home automation.

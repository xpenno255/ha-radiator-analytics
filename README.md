# Radiator Analytics

A Home Assistant custom integration that monitors Ramses CC heating zones, computes performance statistics, estimates radiator circuit position, and provides balancing recommendations.

## Features

- **Automatic zone detection** — discovers all Ramses CC climate entities
- **Real-time session tracking** — monitors heating sessions via state change events
- **Historical backfill** — pulls existing data from HA recorder on first install
- **Per-zone analytics:**
  - Heating rate (overall and morning ramp)
  - Duty cycle percentage
  - Time to reach setpoint
  - Setpoint achievement rate
  - Flow impact under concurrent demand
  - Estimated circuit position
- **System-level insights:**
  - Balance score (0-100)
  - Circuit order estimation
  - Actionable recommendations

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/xpenno255/ha-radiator-analytics` as an **Integration**
4. Search for "Radiator Analytics" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration → Radiator Analytics**

### Manual

1. Copy the `custom_components/radiator_analytics` folder to your `custom_components` directory
2. Restart Home Assistant
3. Add via Settings → Devices & Services

## Configuration

The integration is configured via the UI:

1. **Zone Selection** — auto-detects Ramses CC zones, select which to monitor
2. **Settings:**
   - Analysis window: 3-14 days (default: 7)
   - Update interval: 5-60 minutes (default: 15)

## Sensors Created

### Per Zone
| Sensor | Description |
|--------|-------------|
| Heating Rate | Average °C/hr across all sessions |
| Morning Rate | Average °C/hr during 05:00-09:00 |
| Duty Cycle | % of time actively heating |
| Time to Setpoint | Average minutes to reach target |
| Setpoint Achievement | % of sessions reaching target |
| Circuit Position | Estimated position (1 = nearest boiler) |
| Flow Impact | Performance ratio under concurrent demand |

### System
| Sensor | Description |
|--------|-------------|
| Balance Score | 0-100 overall system balance |
| Circuit Order | Estimated zone order from boiler |
| Recommendations | Actionable balancing suggestions |

## How It Works

1. **Session tracking**: Listens for `hvac_action` transitions (idle → heating → idle) on monitored climate entities
2. **Data storage**: Completed sessions are persisted to `.storage/radiator_analytics`
3. **Analysis**: The coordinator runs statistics computation on the configured interval
4. **Circuit estimation**: Zones are ranked by morning ramp rate and flow impact under concurrent demand

## Requirements

- Home Assistant 2024.1+
- Ramses CC integration with climate entities
- Recorder integration (for historical backfill)

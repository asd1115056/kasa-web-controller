# Kasa Web Controller

A web-based controller for TP-Link Kasa smart devices.

## Features

- MAC-based device identification (stable across IP changes)
- Smart IP caching with automatic discovery on connection failure
- Power strip support with individual outlet control
- Offline device handling with cached topology
- Web UI with Bootstrap 5

## Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager

## Quick Start

```bash
# Install dependencies
uv sync

# Configure devices (see Configuration section)
cp config/devices.example.json config/devices.json
cp config/.env.example config/.env

# Run the server
uv run kasa-web
# Or with auto-reload for development
uv run uvicorn app.main:app --reload

# Open http://localhost:8000
```

## Configuration

### Device Whitelist (`config/devices.json`)

Copy `config/devices.example.json` to `config/devices.json` and add your devices.

```json
{
  "devices": [
    {
      "mac": "AA:BB:CC:DD:EE:FF",
      "name": "Living Room Strip"
    },
    {
      "mac": "11:22:33:44:55:66",
      "name": "Bedroom Plug"
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `mac` | Yes | Device MAC address (formats: `AA:BB:CC:DD:EE:FF`, `AA-BB-CC-DD-EE-FF`, `AABBCCDDEEFF`) |
| `name` | No | Display name (defaults to device ID if omitted) |

**Finding your device MAC address:**
```bash
uv run kasa discover
```

### Credentials (`config/.env`)

Copy `config/.env.example` to `config/.env` if your devices require cloud authentication.

```env
KASA_USERNAME=your@email.com
KASA_PASSWORD=your_password
```

Credentials are required for newer Kasa devices that use TP-Link cloud authentication.

## API Reference

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/devices` | List all devices with status |
| GET | `/api/devices/{id}` | Get single device status |
| PATCH | `/api/devices/{id}` | Control device (on/off) |
| POST | `/api/devices/{id}/refresh` | Refresh single device (targeted discover) |
| POST | `/api/devices/discover` | Force full device discovery |

### Device ID

Devices are identified by an 8-character ID derived from their MAC address (SHA-256 hash).
This provides a stable identifier that doesn't change when the device's IP address changes.

Example: MAC `AA:BB:CC:DD:EE:FF` → ID `a1b2c3d4`

### Device Status

| Status    | Description                                   |
|-----------|-----------------------------------------------|
| `online`  | Device is connected and responding            |
| `offline` | Connection failed, will retry on next request |

### Examples

#### GET /api/devices

```json
{
  "devices": [
    {
      "id": "a1b2c3d4",
      "name": "Living Room Strip",
      "status": "online",
      "alias": "TP-LINK_Power Strip_A1B2",
      "model": "KP303",
      "is_on": true,
      "is_strip": true,
      "children": [
        { "id": "0", "alias": "Outlet 1", "is_on": true },
        { "id": "1", "alias": "Outlet 2", "is_on": false }
      ]
    }
  ]
}
```

#### PATCH /api/devices/{id}

Request:
```json
{
  "action": "on",
  "child_id": "optional_outlet_id"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `action` | Yes | `"on"` or `"off"` |
| `child_id` | No | Outlet ID for power strips |

Response:
```json
{
  "success": true,
  "id": "a1b2c3d4",
  "is_on": true,
  "children": [
    { "id": "0", "alias": "Outlet 1", "is_on": true },
    { "id": "1", "alias": "Outlet 2", "is_on": false }
  ]
}
```

## Project Structure

```
kasa-web-controller/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI application and routes
│   └── device_manager.py # Device management logic
├── config/
│   ├── devices.json      # Device whitelist (create from example)
│   ├── devices.example.json
│   ├── .env              # Credentials (create from example)
│   └── .env.example
├── static/
│   ├── index.html        # Web UI
│   ├── app.js            # Frontend logic
│   └── style.css
└── pyproject.toml
```

# Kasa Web Controller

A web-based controller for TP-Link Kasa smart devices.

## Quick Start

```bash
# Install dependencies
uv sync

# Configure devices (see Configuration section)
cp config/devices.example.json config/devices.json
cp config/.env.example config/.env

# Run the server
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

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/devices` | List all devices with status |
| POST | `/api/device/{id}/toggle` | Control device (on/off/cycle) |
| POST | `/api/discover` | Force device discovery |
```

# SIM800C SMS Manager

Web UI and REST API for sending, receiving, and storing SMS through a SIM800C GSM module.

## Start

```powershell
cd C:\SIM800C
.\.venv\Scripts\Activate.ps1
python run.py
```

- UI: http://localhost:8000
- Documentation: http://localhost:8000/documentation
- API docs (OpenAPI): http://localhost:8000/docs

## Hardware vs mock

Edit `.env`:

```
SIM800C_PORT=auto
SIM800C_BAUDRATE=115200
SIM800C_MOCK=false
SIM800C_POLL_SECONDS=15
SIM800C_AUTO_RECONNECT=true
```

### Port auto-detection

`SIM800C_PORT=auto` (the default) finds the COM port the SIM800C is plugged into — no
manual port configuration needed:

1. **Signature scan** — serial ports are scored by USB identity (SIMCom/SIM800 strings and
   the USB-UART bridges used by SIM800C boards: CP210x, CH340/CH341, FT232, PL2303).
2. **AT probe** — if the signature is ambiguous, up to 6 candidate ports are opened and sent
   `AT`; the port answering `OK` is the modem.
3. Detection re-runs on every connect, so replugging the modem on another port is handled.

Set `SIM800C_PORT=COM3` (or `/dev/ttyUSB0`) to force a specific port instead. Detection can
also be triggered at runtime via `POST /api/modem/detect` or the **Auto-detect port** button
in the UI.

Set `SIM800C_MOCK=true` to run without a modem. Mock mode includes **Inject inbound SMS** on the UI.

## API

If `API_KEY` is set, send it as `X-API-Key`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/sms/send` | Send SMS `{ "phone", "message" }` |
| GET | `/api/sms` | List stored messages (filters: `direction`, `phone`, `status`, `limit`, `offset`) |
| GET | `/api/sms/{id}` | Fetch one message |
| DELETE | `/api/sms/{id}` | Delete a message |
| POST | `/api/sms/sync` | Import SIM inbox into SQLite |
| GET | `/api/modem/status` | Signal, operator, IMEI |
| POST | `/api/modem/connect` | Open serial port (auto-detects when port is `auto`) |
| POST | `/api/modem/disconnect` | Close serial port |
| GET | `/api/modem/ports` | List COM ports with detection score |
| POST | `/api/modem/detect` | Auto-detect the SIM800C port now |
| GET | `/api/stats` | Message counts |

Full integration guide with examples (curl / PowerShell / JavaScript): **http://localhost:8000/documentation**

## Smoke test

```powershell
python smoke_test.py
```

Starts the server in real mode (no modem attached) and in mock mode, then checks the UI,
the documentation page, port detection endpoints and the message flows.

Example:

```powershell
Invoke-RestMethod -Method POST -Uri http://localhost:8000/api/sms/send `
  -ContentType "application/json" `
  -Body '{"phone":"+15551234567","message":"Hello from SIM800C"}'
```

Messages are stored in `data/messages.db`. The inbox is auto-synced from the modem on the poll interval.

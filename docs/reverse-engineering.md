# Reverse-engineering notes

How the ring's protocol works, what's already decoded, and what's left. The open problems
at the end are the original reverse-engineering part of the class project.

## Prior work this builds on

| project | what it contributed |
|---|---|
| [open_oura](https://github.com/Th0rgal/open_oura) (Rust, MIT) | GATT layout, framing, auth handshake, key install on a reset ring, history drain, most event decoders (from Ghidra on the Android app's `libringeventparser.so`). `ring/` is a Python port of it. |
| [open_ring](https://github.com/LogosIsLife/open_ring) (Python, GPL-3) | Independent full protocol spec (`PROTOCOL.md`) validated on ~950k captured records. Its approach needs a rooted Android to copy the app's key, which is why we use open_oura's factory-reset path. Read it, but don't copy code (GPL). |
| [rayhanadev/oura](https://github.com/rayhanadev/oura) | A local-first sync tool on top of open_oura, verified on a Ring 4. It's the same architecture as ours: periodic sync job, raw events in a database. |
| [ringverse/protocol](https://github.com/ringverse/protocol/blob/main/oura/BLE.md) | Early Ring 4 BLE notes. |

## The protocol, layer by layer

**1. GATT.** One custom service `98ed0001-a541-11e4-b6a0-0002a5d5c51b`. The client writes
requests to `…0002` and the ring answers with notifications on `…0003`. Check this yourself
with `python -m ring.cli gatt`.

**2. Framing.** Every message is `tag (1 byte) | length (1 byte) | payload`. One notification
can contain several messages back to back. Tag `0x2f` is an "extended" command whose first
payload byte is a sub-command. Tags `0x41` and up are history events.

**3. Authentication.** The client asks for a nonce (`2f 01 2b`) and the ring answers
`2f 10 2c <15-byte nonce>`. The client replies with `2f 11 2d <AES-128-ECB(key, PKCS7(nonce))>`,
and the ring answers `2f 02 2e <result>` (0 = OK, 1 = wrong key, 2 = factory-reset). A
factory-reset ring accepts a new key with `24 10 <key>`. See `ring/auth.py`. Device info like
firmware and serial is readable without auth. Battery and history require it.

**4. History.** The ring logs *events* into flash: `u32 ring_timestamp (deciseconds) | body`.
The client asks for everything after a cursor (`10 09 <cursor u32> <max u8> ffffffff`, or the
extended `2f 0c 41 …` form). The ring streams events and ends each batch with a summary
(`11 …` or `2f … 42 …`) that says how many bytes remain. See `ring/client.py: drain_events`.

**5. Time.** Event timestamps come from the ring's own counter, not the calendar. Each sync
sets the clock, and the ring logs a `time_sync` event with the Unix time at that counter
value. `ring/timeline.py` uses those anchors to date every event.

**6. Event bodies.** Each tag has its own byte layout, listed in `ring/events.py: DECODERS`.
For example:
- `hrv_event` (0x5d): pairs of `(hr bpm, RMSSD ms)`, one per 5 minutes overnight
- `green_ibi_quality_event` (0x80): 2-byte beats, `ibi = (b1 & 7) | (b0 << 3)`, `quality = (b1 >> 3) & 3`
- `temp_event` / `sleep_temp_event`: `i16 / 100` °C
- `activity_information` (0x50): state byte, then MET values (`b < 128 ? b × 0.1 : 12.8 + (b − 128) × 0.2`)
- `bedtime_period` (0x76): two ring timestamps, the ring's own detected sleep window

## What the ring does not give you

The 0–100 Readiness, Sleep and Activity scores aren't on the ring. The Oura app computes
them on the phone from these events. Trying to re-derive simple versions (for example, a
sleep score from duration, HRV and resting HR) is a good extension for the project.

## Open problems (your contribution)

The dashboard's **"What the ring has sent"** table shows every event type with its
decoded %. Anything below 100% is stored raw and still needs work. Known gaps:

- `debug_data` (0x61): about 40 subtypes. Battery is subtype `0x24`: `[0x24, pct, mV u16]`.
- `sleep_summary_1..4` (0x49/0x4c/0x4f/0x58): partially understood.
- `real_step_event_feature_1/2` (0x7e/0x7f): step counts.
- `motion_period` (0x6b), `feature_session` (0x6c), `spo2_r_pi_event` (0x8b) to SpO2 %.

**A method that works:**
1. Change one thing on purpose. Walk exactly 500 steps, take the ring off for 10 minutes, or
   sit still for an hour.
2. Sync, then pull that time window: `GET /api/events?name=<type>&limit=200`, or SQL on
   `ring_events.body_hex`.
3. Line the bytes up in columns and look for counters, little-endian integers that track
   what you did, and fixed headers.
4. Write the decoder in `ring/events.py`, add a test with the captured bytes in
   `tests/test_protocol.py`, then run `python -m ring.cli redecode` to apply it to everything
   already stored.

`python -m ring.cli sniff` and `-v` on any command show the live frames as they arrive.

## Ground rules

- It's your own ring, used for an educational project. Don't publish your key, serial or MAC,
  or Oura's app code or models.
- Stick to the commands in `ring/`. Don't send random opcodes. Firmware update, factory reset
  and the raw-data sampler are deliberately left out because they can brick the ring or drain
  the battery.

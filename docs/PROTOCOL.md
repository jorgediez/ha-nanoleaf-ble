# Nanoleaf Essentials: Bluetooth LE control protocol

I reconstructed this protocol from the Nanoleaf Android app (`me.nanoleaf.nanoleaf`; the APK is not part of this repository) by decompiling it with jadx 1.5.1. Each section names the source class it came from, using the de-obfuscated names jadx recovered from Kotlin metadata.

> **Status.** The **legacy protocol (§3) and the LTPDU command layer (§4.1–4.3) are verified on real hardware**; see §7. The LTPDUv3 path (§2) is still static analysis only: no firmware ≥ 5.0.0 device has been tested. Every byte layout, constant and algorithm comes from the app's code. Where the app's behaviour is odd (for example, unpadded SRP values), the oddity is documented as it is, because the device has to match it. The reference implementation is the integration's protocol library, `custom_components/nanoleaf_ble/lib/`, with a command-line client in `scripts/nanoleaf_cli.py`.

---

## 0. Which devices and which protocol

The app treats a device as a Bluetooth device when it belongs to the **Essentials** family (`Accessory.isBluetoothAccessoryType` → `isEssentials`):

| Model code (advertised) | Product | Family |
|---|---|---|
| `NL45` | Essentials A19 bulb (HomeKit/Thread) | HomeKit Essentials |
| `NL55` | **Essentials Lightstrip** (HomeKit/Thread) | HomeKit Essentials |
| `NL62` | Secretlab lightstrip | HomeKit Essentials |
| `HFSR70` | Hafa smart mirror | HomeKit Essentials |
| `NL67` | Essentials A19 (Matter) | Matter Essentials |
| `NL68` | **Essentials Lightstrip (Matter)** | Matter Essentials |
| `NL53`, `NL54`, `NL65`, `NL70` | BR30, GU10, Downlight, Ceiling lamp (Matter) | Matter Essentials |

1D devices (string lights, strips, outdoor and Wi-Fi Essentials) and Sense+ switches also advertise on BLE. See §14 for every device group and what's covered.

The app speaks two BLE protocol generations. It picks one from the firmware version in the advertisement (`BluetoothScanResult.isLTPDUv3()`):

| Firmware | Protocol | Section |
|---|---|---|
| **≥ 5.0.0** | **LTPDUv3**: SRP-6a handshake, then an AES-CCM or AES-CTR encrypted session. Each message is a framed request/response. | §2 |
| **< 5.0.0** | **Legacy**: X25519 key exchange, then AES-CTR. Uses read/write characteristics and CoAP-wrapped commands. | §3 |

Both carry the same **LTPDU command language** (§4): endpoint strings such as `lb/0/oo` (on/off) with binary values.

What you need in both cases:
1. The device's **8-digit setup code**. This is the HomeKit-style code on the label or in the QR code (`XXX-XX-XXX`); drop the dashes. Pairing with it returns an **auth token**, which you store and use on every later connection.
2. **v3 only:** the **BLE advertised local name**, which is the SRP username, and the `client_id` the device assigns while you pair.

---

## 1. Discovery (advertisement)

Source: `lg/C6437C` (BluetoothScanner).

* Scan filter: **Manufacturer-specific data, company ID `0x080B` (2059)**. Data shorter than 10 bytes is ignored.
* Manufacturer data layout (after the 2-byte company ID):

| Offset | Len | Meaning |
|---|---|---|
| 0 | 4 | Model, ASCII (`NL55`, `NL68`, `NL1D`, `NLM…`) |
| 4 | 3 | Firmware version `major.minor.patch` |
| 7 | 3 | Hardware version (`0.3.6` is rewritten to `1.0.4` by the app) |
| 10 | 1 | Flags. **bit 1 = already paired** |
| 11 | 8 | Device ID / EUI (`accessoryPairingId`) |
| 19 | 2 | Pair ID |
| 21 | n | Sub-model string, for `NL1D` / `NLM*` / 1D models (for `NLM0` partner devices it starts at **offset 19**, replacing the pair ID; e.g. `NL62`) |

A packet of exactly 10 bytes is a different, shorter scan-result type.
* The **device name** used as the SRP username is `ScanRecord.getDeviceName()`, the advertised *Complete Local Name*.

---

## 2. LTPDUv3 over BLE (firmware ≥ 5.0.0)

Source: `light_talk/ltpdu_v3/bluetooth/BluetoothConnectionManager`, `LTPDUv3BluetoothConnection`, `BaseLTPDUv3Connection`, `contract/LTPDUDataWrapper`, `mg/C7409b`, `mg/C7408a`, `utilities/external/SRP6a/SRP6aClient`.

### 2.1 GATT

| Role | UUID | Properties used |
|---|---|---|
| LTPDU service | `A1000000-183A-7E69-41EE-B6872DF15CA3` | |
| → Write (app → device) | `A1C10000-8A2F-84C9-1562-F8AC7B913ED4` | Write **with response** |
| → Indicate (device → app) | `A1C20000-C78E-15FC-429B-57E13CA8D26F` | Indications (enable CCCD) |
| → OTA write | `A1C30000-E6A6-13B8-E278-7C09BF6FCE41` | Firmware chunks (optional) |
| Event service | `A2000000-1EAF-3078-5EC6-6AE8239F4DB7` | |
| → Event | `A2C10000-A414-BF52-80E5-3B6DA1479CF2` | Indications (the app enables these but ignores the data on BLE) |

On connect, the app requests connection priority HIGH and an **MTU of 512**, then enables indications on both characteristics. The app does not fragment writes, so each frame must fit in one ATT write (MTU − 3).

### 2.2 Frame format (`LTPDUDataWrapper`), big-endian

Each GATT write or indication carries exactly one frame.

```
Request  (12 + n): ver(1)=0x01 | type(1)=0x01 | msgId(2) | clientId(2) | uri(1) | method(1) | len(4) | data(n)
Response (12 + n): ver(1)      | type(1)=0x02 | msgId(2) | clientId(2) | respCode(2)           | len(4) | data(n)
Event    (14 + n): ver(1)      | type(1)=0x03 | msgId(2) | clientId(2) | eventId(4)            | len(4) | data(n)
Ack      (6)     : ver(1)      | type(1)=0xFE | msgId(2) | clientId(2)
(type 0x04 "PublicMessage" is deprecated)
```

| Field | Values |
|---|---|
| `msgId` | Random 16-bit value. The response echoes it, and that is how the app matches responses (it polls up to 50 × ~100 ms; 100 polls during auth). |
| `clientId` | **0xFFFF** until assigned. The device puts the assigned ID in the header of the step-1 response (§2.4). |
| `uri` | `0x01` Secure (authentication), `0x02` LTPDU (commands), `0x03` Events, `0x04` Identify, `0x05` Info |
| `method` | `0` GET, `1` POST, `2` PUT, `3` DELETE. The app always sends POST in the wrapper; the real verb goes inside the LTPDU command, §4. |
| `respCode` | `0` OK, `1` NoContent, `2` BadRequest, `3` Unauthorized, `4` PayloadTooLarge, `5` InternalServerError |

### 2.3 Unauthenticated: Identify (blink)

To make the light blink without pairing, send `Request(uri=Identify(4), method=POST, data=empty)`. This is a good first test.

### 2.4 Authentication: SRP-6a (3 steps)

**Authentication TLVs** (`TlvType2.LTPDUv3AuthenticationType`): **1-byte type, 2-byte big-endian length, value**. When the app serializes a TLV array, it **sorts the entries by type**.

| Type | Name | Notes |
|---|---|---|
| 0x01 | AuthenticationType | `0x01` = pairing code, `0x02` = auth token |
| 0x02 | Salt | from device |
| 0x03 | PublicKey | A (client) / B (device) |
| 0x04 | VerificationChallenge | M1 (client) / M2 (device) |
| 0x05 | AuthTokenRequest | empty |
| 0x06 | AuthTokenResponse | token bytes |
| 0x07 | SecureSessionType | `0x01` connection-dependent, **`0x02` connection-independent (the app always sends this)** |
| 0x08 | Disconnect | |
| 0x09 | Unpair | empty |
| 0x0A | GetEventEncryptionData | empty |
| 0x0B / 0x0C | EventEncryptionKey / EventEncryptionIV | |
| 0x0D | GenerateNewClientAuth | migration |
| 0x0E | ClientId | 2 bytes, big-endian |
| 0x11 | SecureSessionVersion | `03 00`, `03 01` or `03 02` |
| 0xF0 | Error | error code; abort |

**SRP parameters**

* Hash: **SHA-256** (both groups).
* Group: **512-bit** custom prime for *Matter Essentials* (NL67/NL68/BR30/GU10/Downlight/Ceiling), otherwise the **RFC 5054 2048-bit** prime. g = 2.
  * N₅₁₂ = `D66AAFE8E245F9AC245A199F62CE61AB8FA90A4D80C71CD2ADFD0B9DA163B29F2A34AFBDB3B1B5D0102559CE63D8B6E86B0AA59C14E79D4AA62D1748E4249DF3`
* **Username I** = BLE advertised device name.
* **Password P**:
  * when pairing: the 8 setup-code **digits as byte values 0–9**, not ASCII. For example, `12345678` becomes `01 02 03 04 05 06 07 08`.
  * when reconnecting: the raw auth-token bytes.
* `bytes(x)` = **minimal unsigned big-endian** encoding with no padding to the length of N. This also applies to g, which becomes the single byte `02`.

```
a  = random(bits(N));  A = g^a mod N
u  = H(bytes(A) || bytes(B))
x  = H(bytes(s) || H(I || ":" || P))
k  = H(bytes(N) || bytes(g))                    # g unpadded!
S  = (B − k·g^x)^(a + u·x) mod N
K  = H(bytes(S))
M1 = H( (H(bytes(N)) XOR H(bytes(g))) || H(I) || bytes(s) || bytes(A) || bytes(B) || K )
M2 = H(bytes(A) || M1 || K)                     # the app computes this but never checks it
SK = SHA-256(K)                                  # 32-byte session secret used for encryption
```

**Flow.** All three steps are `Request(uri=Secure(1), method=POST)`.

1. **Step 1** (plaintext): TLVs `{01: auth type, 03: A, 07: 02}`.
   The response header's `clientId` is your client ID; keep it. The response TLVs are `{02: salt, 03: B, 11: session version}`. The session version defaults to `03 00` if absent.
2. **Step 2** (plaintext): TLV `{04: M1}`. The response contains `{04: M2}`. Then derive the session from SK (§2.5) with message counter = 0.
3. **Step 3** (only when pairing with the setup code): **encrypted** TLV `{05: <empty>}`. The decrypted response contains `{06: auth token}`.
   Store `(client_id, auth_token)`. Later connections use AuthenticationType `02`, P = token, and the stored client ID in the frame header, and they skip step 3.

When the app does not already hold an auth token for a v3 device, it "migrates" one. It authenticates with an existing legacy token, using `clientId` = the token's first 2 bytes, and then sends `GenerateNewClientAuth (0x0D)`. The reply contains `0E: new client ID` and `06: new token`.

After authenticating, the app also sends an encrypted `{0A}` (GetEventEncryptionData) and receives `{0B: key, 0C: iv}`. A BLE controller does not need these.

### 2.5 Session encryption

`aesKey = SK[0:16]`, `ivBase = SK[16:24]`.

**Message counter.** A single 64-bit counter, starting at 0 after step 2, is shared by **both directions**. It increments on every encrypt (request) and every decrypt (response). Strictly alternate one request and one response; a lost message desynchronizes the counter.

| Session version | Cipher |
|---|---|
| **3.1** (`03 01`) | **AES-128-CCM**, **4-byte tag**, nonce = counter (low **5** bytes, BE) ‖ ivBase → 13-byte nonce, no AAD |
| **3.2** (`03 02`) | **AES-128-CCM**, **4-byte tag**, nonce = counter (low **4** bytes, BE) ‖ ivBase → 12-byte nonce, no AAD |
| **3.0** (`03 00`) | **AES-128-CTR**, initial counter block = `00×8 ‖ ivBase`. **One keystream is shared** by both directions (encrypting and decrypting each consume keystream). App → device plaintext gets a **CRC-16/XMODEM** appended (poly 0x1021, init 0, big-endian); the last 2 bytes of each decrypted response are a CRC and are dropped. |

The encrypted bytes go in the frame's `data` field. Step 1, step 2 and Identify are not encrypted.

### 2.6 Sending commands

`Request(uri=LTPDU(2), method=POST, clientId, data = encrypt(<LTPDU commands §4>))` → `Response(respCode, data = encrypt(<LTPDU responses §4.3>))`.

Disconnect: send an encrypted `{08}` on uri Secure. Unpair: send an encrypted `{09}` on uri Secure.

---

## 3. Legacy BLE protocol (firmware < 5.0.0)

Source: `homekitclient/networking/bluetooth/b.java` (BluetoothConnection), `mg/C7408a` (EncryptedSession), `command_centre/BLECharacteristicDataSource`, `pg/h` (LTPDURequest).

### 3.1 GATT characteristics

| UUID | Use |
|---|---|
| `6D2ADFDA-9AEA-11EA-BB37-0242AC130002` | **Encryption setup**: key exchange and auth token |
| `6D2ADDA0-9AEA-11EA-BB37-0242AC130002` | **Encrypted** request/response: write the request, then read the response |
| `A9E0A2DE-9AEA-11EA-BB37-0242AC130002` | **Unencrypted** requests (public Identify) |
| `135634E6-3CC1-05B8-0D49-4842AD5F93C9` / `0919921C-C4E7-8C94-504B-D04A146B8157` | Firmware control / data (OTA) |

On connect, the app requests connection priority HIGH and an MTU of **178** (HomeKit Essentials), **240** (other "v2" devices) or **512** (1D devices on newer firmware).

### 3.2 Handshake

1. Generate an X25519 key pair. **Write** your 32-byte public key to `…ADFDA`.
2. **Read** `…ADFDA`; this is the device's 32-byte public key. Compute `shared = X25519(priv, devicePub)`.
3. Derive the keys. The salts depend on the device family (`usesNewEncryption` = `isMatterDevice`, i.e. the **"v2" devices**: Matter Essentials + 1D + Sense+; see §14):
   * HomeKit Essentials (NL55, …): `keySalt = "AES-NL-OPENAPI-KEY"`, `ivSalt = "AES-NL-OPENAPI-IV"` (ASCII)
   * v2 devices: `keySalt = bca9e39738e2611c25d243c985d3c592ccbee330e077b89d7978f5fc785cb5e8`, `ivSalt = 41e2e9eb5e5fa56e800e346d8c2b600742eb49839626faecdc37fb94a3a3c202` (hex)

   `key = SHA1(keySalt ‖ shared)[0:16]`, `iv = SHA1(ivSalt ‖ shared)[0:16]` → **AES-128-CTR with ONE shared keystream** for everything that follows, in both directions.
4. **Write** to `…ADFDA` the encrypted TLV8 (1-byte type, 1-byte length):
   * pairing: `03 <len> <setup code as ASCII digits>`
   * reconnect: `02 <len> <auth token>`
5. **Read** `…ADFDA` and decrypt it to get the auth token. Store it. A single byte `0x86` means "no more pairings allowed". An empty value means the token is invalid.

### 3.3 Requests

Write `encrypt(payload)` to `…ADDA0`, then read `…ADDA0` and decrypt to get the response.

* **v2 devices (Matter Essentials, 1D, Sense+):** `payload` = LTPDU commands (§4) including the Method TLV. The response is LTPDU response TLVs.
* **HomeKit Essentials:** the payload is wrapped in a minimal CoAP message, and the commands carry **no Method TLV**:

```
42 | code | mid(2) | token(2) | B0|len(path) | "nlltpdu" | FF | <LTPDU commands>
code: 01 GET, 02 POST, 03 PUT, 04 DELETE        (path is "nlpublic" for public requests)
```

The response has the same 7-byte CoAP prefix (4-byte header, 2-byte token, `FF`). Strip it, then parse §4.3.

* **Public Identify, no auth:** write a request containing `lb/0/id = 01` to `A9E0…` (unencrypted), then read it back. For HomeKit Essentials, wrap it in CoAP with the `nlpublic` path. For Matter devices, send it unwrapped, with the Method TLV.

---

## 4. LTPDU command layer (common)

Source: `pg/i` (LTPDURequestCommand), `pg/h`, `qg/k` (LTPDUResponse), `light_talk/Endpoints`, `light_talk/EndpointLookup`, `ng/*` (formats).

### 4.1 Request command encoding

A request is a concatenation of commands. Each command is a set of TLVs: **2-byte type, 2-byte BE length**.

| Type | Content |
|---|---|
| `0x0001` | Endpoint, ASCII (e.g. `lb/0/pb`) |
| `0x0004` | Method, 1 byte: `0` GET, `1` POST, `2` PUT, `3` DELETE. Omitted for legacy HomeKit/CoAP. |
| `0x0002` | Request payload. Omitted for reads. |

Example: set brightness to 50 % on v3:
`0001 0007 "lb/0/pb" | 0004 0001 02 | 0002 0002 0032`

The app uses **PUT** to set a value and **GET** to read one; GET carries no payload.

### 4.2 Endpoints

`lb/*/…` endpoints are per fixture. Replace `*` with the fixture index; `0` is the whole lightstrip. The app substitutes `0` when no fixture is given.

| Endpoint | Meaning | Value encoding |
|---|---|---|
| `lb/0/oo` | On/Off | 1 byte: `00` / `01` |
| `lb/0/pb` | Brightness | uint16 BE, **0–100 %** |
| `lb/0/hu` | Hue | uint16 BE, **0–360°** |
| `lb/0/sa` | Saturation | uint16 BE, **0–100 %** |
| `lb/0/ct` | Colour temperature | uint16 BE, **Kelvin** (Essentials range is about 2127–6535 K). Mireds are used only for Elements, Skylight and Blocks. |
| `lb/0/cm` | Colour mode (read) | uint |
| `lb/0/id` | Identify / blink | bool |
| `lb/0/chusa` | Convert XY | bytes |
| `lb/0/plr` | Power-loss recovery | bytes |
| `lb/0/lsLength` | Lightstrip length | bytes |
| `lb/0/lpz` | Min/max LEDs per zone | bytes |
| `lb/0/effect/select_id` | Play stored effect by ID (NL-Core, fw ≥ 5) | uint |
| `lb/0/effect/select` | Play effect | bytes |
| `di` | Device information (fw, serial, model, …) | binary struct |
| `ci` | **Command Interface** (scenes; see 4.4) | TLV bytes |
| `effect`, `effect/display`, `effect/ids`, `effect/hash`, `effect/maxcnt`, `effect/motions` | Effects V2 (fw ≥ 5) | TLV bytes |
| `tz`, `tm`, `lglt` | Timezone, time, longitude/latitude | bytes |
| `ac`, `ac/en` | Auto CCT (circadian) config / enable | bytes / bool |
| `nlsch`, `nlsch/enabled`, `nlsch/ids`, `nlsch/maxcnt`, `nlsch/hash` | Schedules v2 | bytes |
| `th/tc`, `th/ds`, `th/tr`, `sp/mc` | Thread control / dataset / role / channel | |
| `mt/onp`, `mt/comm/bcm`, `mt/comm/ecm`, `sw/mt/oi` | Matter setup payload, open commissioning window, opt-in | |
| `openapi/enable`, `openapi/pair`, `openapi/token_gen` | Open API | |
| `fwu/*` | Firmware update (`url`, `ds`, `version`, `trigger`, `avail`, `check`, `prepare`, `chunk_sz`, `chunk`, `verify`, `reboot`) | |
| `ds/state`, `ds/restore`, `cloud_sync_token`, `cn`, `mdns_info`, `wifi/creds`, `wifi/status`, `ap`, `layout/*` | Cloud sync, notifications, and other functions | |

You can send several commands in one request. For example, the app sets colour by sending `hu` and `sa` together.

### 4.3 Response encoding

Responses use the same TLV framing. For each command the device returns `0x0001 endpoint` followed by **`0x0003` = `status(1) ‖ value`**.

Status codes: `0` Success, `1` Cannot read, `2` Cannot write, `3` Invalid value, `4` Invalid endpoint, `5` Malformed payload, `6` Invalid method.

Large values can arrive fragmented:
* `0x0006` header: `status(1)`, `count = byte[1] & 0x0F`, total length in the last 4 bytes (BE).
* That is followed by `count` × `0x0007` fragments, which you concatenate.

For `ci` (Command Interface) replies, the value starts with a **4-byte reply header**: `<cmd byte 1 | 0x80> <cmd byte 2> <len16>`, followed by the data. This was verified on hardware. The app code drops 3 bytes for non-panel devices, which doesn't match what the strip sends.

### 4.4 Multi-colour scenes on Essentials (`ci` endpoint)

This path covers all HomeKit Essentials, and Matter Essentials below firmware 5.0.0. In the app's terms, it is used when the device does not support "NL-Core".

Source: `NanoleafEffect.toSimpleSceneTLV`, `CommandCentreRepository`.

The lightstrip's multi-colour scenes are "Simple Scenes". The app sends them as a **POST** to `ci` (Command Interface); this was verified on hardware. The value is an outer TLV with **2-byte type and 2-byte length**:

| Outer type | Meaning | Inner value |
|---|---|---|
| `07 01` | Preview / display scene | Simple-scene TLVs (below) |
| `07 02` | Add (store) scene | Simple-scene TLVs |
| `07 03` | List scene IDs | none |
| `07 04` | Get scene | ID (a non-existent ID returns stale data; use List) |
| `07 05` | Delete scene | ID |
| `07 06` | Activate stored scene (also turns the light on) | 1 byte: scene ID |
| `07 07` | Current effect | none → playing stored-scene ID; `ff` while a preview (`07 01`) plays; `00` when off **or** showing a static colour (§7.3) |

Replies and limits (12 scenes in total, add-reply `00`/`01`) are in §7.2. Parameter units and semantics are in §11. Power-loss recovery uses the same channel with `09 xx` (§13).

The simple-scene TLVs use a **1-byte type and 1-byte length**:

* `01` **MetaData** = scene ID, `effectType`, then the motion parameters. Stored scenes keep their full ID (e.g. `fa` = 250); the app writes `id & 0x0F` for user scenes. Times are in **tenths of a second**.

| effectType | Name | Params |
|---|---|---|
| 1 | Fade | transitionTime (default 24), delayTime (0), loop (bool, default 1) |
| 2 | Random | transitionTime, delayTime |
| 3 | Highlight | transitionTime, delayTime, mainColourProbability (default 80) |
| 4 | Stream control | none |
| 5 | Flow | transitionTime, delayTime, direction (0 L, 1 R, 2 Up, 3 Down), loop |
| 6 | Stripes | transitionTime, direction, segment (default 50) |

* `02` **Palette** = `count(1)`, then for each colour 3 bytes BE holding `rep<<23 | hue(9 bits)<<14 | sat(7)<<7 | bri(7)`. If the `rep` bit is set, a count byte follows giving the **total** number of copies of that colour (verified, §11). The app sends at most 7 entries.

On firmware ≥ 5.0.0 ("NL-Core"), the app instead uses Effects V2: `effect/display` with an EffectsV2 TLV of `03` effectId (4 B), `05` name, `04` motionId (4 B), `07` motion data and `06` palette. To play a stored effect it uses `lb/0/effect/select_id`. I haven't documented every motion-option encoding; see `NanoleafEffect.toTLV`.

---

## 5. Minimal recipes

**v3 (fw ≥ 5): turn on at 40 % red**

1. Connect. Set the MTU to 512. Enable indications on `A1C2…`.
2. Run SRP steps 1–2 with the stored token (or steps 1–3 with the setup code the first time).
3. Encrypt these concatenated commands and send them as uri=2:
   * `lb/0/oo` PUT `01`
   * `lb/0/pb` PUT `0028`
   * `lb/0/hu` PUT `0000`
   * `lb/0/sa` PUT `0064`
4. Decrypt the response and check that every status is `00`.

**Legacy (fw < 5, NL55):**

1. Do the X25519 exchange on `…ADFDA` and derive the keys with the "AES-NL-OPENAPI" salts.
2. Write an encrypted `02 len token` to `…ADFDA`, then read it back.
3. Write the encrypted CoAP(`PUT`, `nlltpdu`) + commands without the Method TLV to `…ADDA0`, then read the response.

See `custom_components/nanoleaf_ble/lib/` (`legacy.py`, `ltpdu_v3.py`, `device.py`) for a complete implementation of both.

---

## 6. Caveats and open points

* **The official app keeps its token.** The app stores the auth token in its private SharedPreferences. To control the strip from elsewhere, pair your own client with the setup code; pairing a second controller does not affect the app (verified). The device may cap how many controllers it accepts (legacy reply `0x86`). Legacy firmware has no unpair (§12), so the only remedy is a factory reset.
* **The message counter must stay in sync.** v3 sessions use one counter (or one CTR keystream) for both directions, so every request needs exactly one response. After any error, re-authenticate from step 1.
* **"Connection-independent" sessions.** The app saves SK, the counter and the session version, and resumes later without SRP. A new client can simply run SRP on every connection.
* **Events.** The app doesn't process event indications on BLE. The event key and IV from `{0A}` are used for Wi-Fi/UDP events.
* **Value ranges.** Ranges for on/off, brightness, hue and saturation were verified on NL62 (§7). `ct` and `cm` return "invalid endpoint" there. For other models, confirm with a GET.

---

## 7. Hardware verification log (2026-10-15)

**Device:** Secretlab MAGRGB 40G8, address `DF:31:88:26:BE:3A`. Tested through an ESPHome Bluetooth proxy in active mode, using `bleak-esphome`.

**Advertisement:** `4e4c4d30 010631 020000 10 dc8e95fffe0b345f 4e4c3632 00000000`
* Model `NLM0`, a "partner" device. For `NLM0` the real model string starts at **offset 19**, not 21: here it is **`NL62`** (Secretlab lightstrip, HomeKit Essentials family).
* Firmware **1.6.49**, so the **legacy protocol** applies. Hardware 2.0.0.
* Flags `0x10`: the "paired" bit (bit 1) is 0.
* GATT name: "Nanoleaf Light Bulb".

**GATT layout.** Apple HomeKit-over-BLE services and Nanoleaf's own services sit side by side:
* HomeKit services `0x3E`, `0x55`, `0xA2`, `0x43`, `0x701`, `0x239` (Apple base UUID). Plain reads return error 131 and can make the strip drop the link.
* Nanoleaf service **`6D2AE1C4-9AEA-11EA-BB37-0242AC130002`**, containing `6D2ADFDA` (setup, read/write), `6D2ADDA0` (encrypted, read/write/indicate) and `A9E0A2DE` (unencrypted, read/write).
* OTA service `0E9CC677-A71F-8B83-B84D-568278790CB3`, containing `A5F5B581-…`, `0919921C-…` and `135634E6-…`.

**Results**

| Step | Result |
|---|---|
| Public Identify: CoAP `nlpublic` on `A9E0A2DE` | Strip blinks white. Read-back is `62 44 <mid> <tok> FF 0001 0007 "lb/0/id" 0003 0001 00` (CoAP ACK 2.04, status 0). Unwrapped LTPDU gets GATT error 132 (Busy). |
| X25519 key exchange | The device returns a fresh 32-byte public key on every connection. Reading `6D2ADFDA` again before the auth write gives error 135 (Illegal parameter), so the steps must run in order. |
| Pairing with the setup code (ASCII digits, TLV `03`) | Returns an **8-byte auth token**. |
| Login with the token (TLV `02`) | Works. All encrypted requests and responses decrypt correctly (`AES-NL-OPENAPI` salts, one shared CTR keystream). |
| `lb/0/oo`, `lb/0/pb`, `lb/0/hu`, `lb/0/sa`: GET and PUT | All status 0. Values read back as expected. |
| `lb/0/ct`, `lb/0/cm` | Status **4** (invalid endpoint). This model has **no colour temperature**. |

**Quirks**
* The strip sometimes answers **GATT error 133** to the first setup write or read after a quick reconnect. Waiting about 2 s and reconnecting fixes it; the client retries automatically.
* The proxy negotiated an MTU large enough for 32-byte writes without any explicit MTU request.
* **The basic state endpoints don't capture scenes.** Before testing, the strip was running an app scene ("Northern Lights"), yet `oo`/`pb`/`hu`/`sa` read off / 100 / 28 / 66. Writing on/colour replaced the scene, and writing those values back did not restore it. Afterwards the strip reported `oo=0` while visibly lit white. **Not reproduced** in the targeted retest (§7.3): scenes started from the phone app, by `07 06`, and resumed after off/on all read `oo=1`. The cause of the first observation is unknown; it may be an artefact of that first, partly buggy test session. **Snapshot the scene (`07 07`) and `lb/0/*` before writing, or reselect the scene in the app afterwards.**

### 7.1 Scenes (verified 2026-10-15, same strip)

All scene commands are **POST to `ci`**, which on this strip means CoAP POST with no Method TLV. Body = `07 <cmd> <len16> <data>`.

| Command | Body | Reply value (after the LTPDU status byte) |
|---|---|---|
| List scene IDs | `07 03 00 00` | `87 03 <len16> <id…>` |
| Get scene | `07 04 00 01 <id>` | `87 04 <len16> <simple-scene TLVs>` |
| Activate stored scene | `07 06 00 01 <id>` | status 0, empty. **Also switches the light on** (`oo` reads 1 afterwards). |
| Preview a custom scene | `07 01 <len16> 01 <len> <id> <type> <params…> 02 <len> <palette>` | status 0, empty |

* Replies have a **4-byte header**: command | 0x80, then a 2-byte length. The "drop 3 bytes" in the app code doesn't match what this strip sends.
* Stored scenes keep their full ID in metadata byte 0 (`fa` for Northern Lights). The app writes `id & 0x0F` for previews.
* While a scene runs, `lb/0/hu`/`sa` keep returning the last static colour. The basic endpoints don't report the active scene.
* Scenes present on this unit (IDs from the app's Secretlab table; 254 "Hip Hop" isn't installed): 250 Northern Lights, 251 Secretlab Signature, 252 Cloud9, 253 Team Liquid, 247 Dark Knight, 244 Cyberpunk 2077, 246 Assassin's Creed, 248 The Joker, 245 VALORANT, 249 Attack on Titan.
* Decoded examples:
  * Northern Lights = stripes `[speed 20, dir left, segment 20]` over 7 fully saturated hues (227, 182, 125, 62, 31, 2, 307).
  * Dark Knight = stripes `[35, left, 50]` over black and amber (41°/63 %).
  * Cloud9 uses palette repeat counts (3, 2).
* Parameter bytes by effect type:
  * fade = `[speed, delay, loop]`
  * flow = `[speed, delay, direction, loop]`
  * stripes = `[speed, direction, segment]`
  * Direction: 0 left, 1 right, 2 up, 3 down.

### 7.2 Storing, deleting and querying scenes (verified 2026-10-15, same strip)

The full Command Interface scene family (`07 xx`), all **POST to `ci`**. Request = `07 <cmd> <len16> <data>`; reply = `87 <cmd> <len16> <data>`.

| cmd | Name | Request data | Reply data | Verified behaviour |
|---|---|---|---|---|
| 01 | Display (preview) | simple-scene TLVs | none | Plays an unsaved scene. |
| 02 | Add | simple-scene TLVs; metadata byte 0 = **scene ID** | `00` stored / `01` refused | Stored scenes show up in the list and can be activated by ID. **The transport status is 0 even when refused; check the reply byte.** |
| 03 | List | none | IDs | |
| 04 | Get | ID | simple-scene TLVs | |
| 05 | Delete | ID (minimal big-endian bytes) | none | Removes the scene. Deleting a non-existent ID also returns success. |
| 06 | Activate | ID | none | Turns the light on and plays the scene. |
| 07 | Current effect | none | ID | `00` while the light is off. Otherwise the playing scene's ID (`01` for our test scene, `fa` for Northern Lights). |

* **Capacity: 12 scenes in total, built-ins included.** This matches `AccessoryUtilsKt.effectsMaxLimit()`, which returns 12 for Essentials. With the 10 factory scenes, adds 11 and 12 succeeded and adds 13 and 14 returned `01`.
* **Built-ins can be deleted with the same command.** Not tested, on purpose: there is no known way to restore them (perhaps a factory reset does). The integration never deletes 250–254 (it may delete the app-written 244–249, which it can write back); `scripts/nanoleaf_cli.py delscene` refuses 250–254 unless `--force` is given.
* **ID choice:** the app picks a random number in 1–253 and then stores `id & 0x0F` (confirmed in the bytecode). So app-saved scenes use IDs 0–15. A scene stored by us with ID 1 worked. The integration allocates 1–15: ID 0 would be indistinguishable from "nothing playing" in the current-effect reply (§7.3).
* **Downloads.** The app's Featured/Community scenes come from the Nanoleaf cloud (`https://my.nanoleaf.me/api`, e.g. `GET v1/effects/download/{id}`, Nanoleaf account required). The app converts them to the same palette + motion format and stores them with cmd 02, so no cloud access is needed to author scenes.

## 8. Nanoleaf cloud scene library (verified 2026-10-15)

Base URL `https://my.nanoleaf.me/api/` (`UrlProvider.getCloud2Url`). Search and download need **no authentication**; only `v1/effects/upload` takes an `Authorization` header.

* **Search:** `POST v1/effects/search`, JSON body `{"page":1, "items":20, "query":"…", "sort":"recent"|"top", "type":"color"|"music", "featured":true|false, "tag":"…", "colorType":"HSB"|"CCT"}`. Returns `{"items":n, "data":[{effect_name, effect_type, creator{display_name…}, plugin{name}, palette[…], key, uuid, featured, downloads, …}]}`.
* **Download:** `GET v1/effects/download/{key}`, where `key` = base64("creatorId/uuid") from the search result. Returns a 302 to a signed S3 URL holding the animation JSON: `animName`, `pluginUuid`, `pluginType`, `colorType`, `palette[{hue, saturation, brightness}]`, `pluginOptions[{name, value}]`, `loop`, `direction`, …
* Other endpoints on the same client (not explored): `v2/playlist/discover`, `v2/playlist/download/{id}`, `v2/plugins/{model}/{fw}`, `PUT v1/effects/save/{id}` (download counter).

**Converting cloud JSON to a strip scene.** This follows `NanoleafEffect.buildFromAnimationJson` and `toSimpleSceneTLV`. Verified **byte-for-byte** against the app-written "Pumpkin Hallow" (scene 9).

| pluginUuid | Strip effect | Params from `pluginOptions` (defaults) |
|---|---|---|
| `b3fd723a-aae8-4c99-bf2b-087159e0ef53` Fade | 1 | `transTime` (24), `delayTime` (0), `loop` (true) |
| `ba632d3e-9c2b-4413-a965-510c839b3f71` Random | 2 | `transTime`, `delayTime` |
| `70b7c636-6bf8-491f-89c1-f4103508d642` Highlight | 3 | `transTime`, `delayTime`, `mainColorProb` (80) |
| `027842e4-e1d6-4a4c-a731-be74a1ebd4cf` Flow | 5 | `transTime`, `delayTime`, `linDirection` (left/right/up/down → 0–3), `loop` |
| `Motion_STRIPES` | 6 | `transTime`, `linDirection`, `segment` (50) |
| **anything else** (Wheel, Burst, Radiate, Organic, …) | 2 Random | **defaults (24, 0); the plugin's own options are ignored.** Observed: the featured "Pumpkin Hallow" (Radiate, transTime 9) was stored as random [24, 0]. |

* **Palette:** the first 7 colours only. **Quirk:** a colour followed by ≥ 2 identical colours gets bit 23 set plus a count byte (the number of following duplicates), but the app's loop still emits those duplicates as their own entries. The count byte at the start is the number of entries (≤ 7).
* **Timing:** option ranges are `transTime` 1–600 and `delayTime` 0–600. The units are tenths of a second (verified on video, §11). The strip field is **one byte** and the app writes `value & 0xFF`, so values above 255 wrap.
* Scene names live only in the app and cloud. The strip stores just the numeric ID.

## 9. Connection behaviour (verified 2026-10-15)

* A persistent, authenticated legacy session handles commands in about **200 ms each**. A full connect, key exchange and login takes a few seconds.
* The strip kept an idle connection alive for at least **150 s** without keep-alives.
* After a link loss, reconnecting **immediately** tends to fail with GATT error 133. Waiting about 2 s first works, and a full reconnect plus login takes about 10 s through the ESPHome proxy.
* **Unpair (legacy):** the app's `DirectMessageTypes.Unpair` sends encrypted raw bytes `04 00` to `6D2ADDA0` (no CoAP, no LTPDU), then reads the reply. `Identify` uses the same path with `01`. The app code doesn't say whether this removes only the calling controller or every pairing. **Not yet tested.**

## 10. Where scene names and default scenes come from (app logic)

**Names are not on the device.** The app maps scene ID → name with hard-coded tables (`Constants`), chosen by product type (`SimpleScene` parser):

| Product type | Table |
|---|---|
| Secretlab lightstrip (`NL62`) | 244 Cyberpunk 2077, 245 VALORANT, 246 Assassin's Creed, 247 Dark Knight, 248 The Joker, 249 Attack on Titan, 250 Northern Lights, 251 Secretlab Signature, 252 Cloud9, 253 Team Liquid, 254 Hip Hop |
| Umbra lamps | 248 Modern, 249 Casual, 251 Northern Lights, 253 Date Night, 254 Starlight |
| Other Essentials (A19, Lightstrip NL55…) | 251 Northern Lights, 252 Blaze, 253 Date Night, 254 Starlight |

So every strip of a given type gets the same names. IDs outside the table (user scenes, 0–15) take their name from the app's local database or cloud sync, or show as "Effect N". (`NanoleafEffect.buildFromSimpleSceneTLV` has the table choice inverted, which looks like an app bug; the main parser above is correct.)

**Who writes which scenes (Secretlab only, `SecretLabsDefaultSimpleSceneWriter`).**
* **250–254** were already on the strip. They are presumably factory firmware scenes; the app never writes them.
* **244–249** are defined inside the app (colours and motion hard-coded) and **written to the strip by the app**. This happens on the main screen and after pairing, for `SECRETLABS_LIGHT_STRIPS`.
* Progress is tracked **per phone** in SharedPreferences `MIN_SLLS_SIMPLE_SCENE-<id>`, default 250. Each scene with an ID below the marker is written, then the marker is set to the lowest written ID. Once the marker reaches 244, nothing more happens. The marker is cleared when the app unpairs or re-pairs the device, and it is naturally absent on a new phone or a reinstall.
* Before writing, `makeRoomForNewScenes`:
  1. **always deletes 254 (Hip Hop)**, which is why it's missing from this strip;
  2. lists the scenes and counts the ones **not** in the Secretlab table (user scenes);
  3. if there are more than 6, **deletes the lowest-numbered scene IDs** until 6 remain.
* Result: 4 factory + 6 app defaults = 10 slots, leaving **2 for user scenes**. A re-pair, reinstall or new phone re-runs this, and can delete low-ID user scenes and rewrite 244–249.
* The strip's 247 "Dark Knight" is stripes, but the app's current built-in definition is flow. So the strip's copy was written by a different app version.
* **Get (07 04) on a non-existent ID doesn't return an error.** It returns stale data: the previous reply plus leftover buffer bytes (seen for IDs 10 and 254). Check existence with List (07 03) instead.
* The full set of default definitions (the app's built-ins 244–249, plus the strip copies of 244–253) was dumped from the app and the strip; the app's definitions of 244–249, byte for byte, are in `custom_components/nanoleaf_ble/lib/defaults.py`. The strip's copies of 244–249 differ slightly from the current app's definitions: colours by ±1–2, and 247 is stripes on the strip but flow in the app. So they were written by an older app version.

### 7.3 Scene state vs. basic endpoints (verified 2026-10-16, same strip)

Each step was confirmed by a person watching the strip; the strip was restored to its prior state afterwards.

| Situation | `07 07` | `oo` | `pb` | Seen on the strip |
|---|---|---|---|---|
| Off | `00` | 0 | unchanged | off |
| Static colour (`oo`=1 + `hu`/`sa`) | **`00`** | 1 | 100 | static colour |
| Preview playing (`07 01`) | **`ff`** | 1 | 100 | preview |
| Stored scene via `07 06` | scene ID (`fa`) | 1 | 100 | scene |
| `pb` = 15 while the scene plays | still `fa` | 1 | 15 | **the scene dims**, keeps animating |
| `oo`=0, then `oo`=1 after a scene | `00`, then **`fa`** | 0 → 1 | unchanged | **the scene resumes** |
| Scene started from the phone app (The Joker) | `f8` | **1** | 100 | scene |

Conclusions:
* `00` means "no scene playing" (off **or** static colour); `ff` means an unsaved preview; any other value is the stored scene ID.
* Brightness (`lb/0/pb`) is a **global dimmer** applied on top of the scene's palette brightness; it doesn't stop the scene.
* `lb/0/oo` is a plain power switch: off, then on returns to whatever was playing (scene or static colour).
* The earlier "`oo`=0 but visibly lit" observation (§7) did not recur.

## 11. Scene parameter semantics (verified on video, 2026-10-15)

I measured these by recording the strip on video and analysing the colour frame by frame (10 fps).

| Item | Result |
|---|---|
| **Speed (`transTime`) and delay (`delayTime`)** | **Tenths of a second.** A fade with speed 50 and delay 30 measured 4.9–5.5 s per transition and 2.5–3.1 s holds (nominal 5.0 s and 3.0 s). Speed 10 gave about a 1 s fade. |
| **Palette repeat flag** | The count byte is the **total number of copies** of that colour. A fade with speed 10 over "red ×3, blue" cycled every 4.0 s (3 s red, 1 s blue). If the count meant extra copies, the cycle would be 5 s. |
| App palette quirk (consequence of the above) | The app writes count = number of *following* duplicates, and still emits those duplicates. So N identical colours are played as more than N (three blacks → "black ×2, black, black" = 4 blacks). `encode_palette_app()` reproduces this exactly, for faithful clones. |
| **Highlight `mainColorProb`** | The probability (%) that each newly picked colour is the main colour (palette entry 1). Setting 90 → red 90.4 % of the time; setting 10 → red 2.8 % over a 30 s sample. On this strip highlight recolours the **whole strip at once**. |
| Stripes | Behind this strip's diffuser, adjacent stripes blend into gradients; repeat counts widen that colour's share. `segment` controls stripe size (not quantified). |
| Response time | The strip sometimes acknowledges a preview only after a long delay (once 32 s), but the effect starts immediately. |

## 12. Unpairing (verified 2026-10-15)

* **Legacy firmware (< 5.0.0): there is no device-side unpair.** `UnpairAccessoryUseCase` sends `DirectMessageTypes.Unpair` only when the connection is a `BaseLTPDUv3Connection`. For legacy BLE devices, "Remove device" in the app only forgets the device locally (and clears the Secretlab default-scene marker). The legacy `04 00` direct message exists in `BLECharacteristicDataSource`, but this unpair flow never sends it. Sent to the NL62 (fw 1.6.49), it is rejected with GATT error 132 "Busy". A legacy auth token therefore stays valid until the strip is factory-reset.
* **LTPDUv3 (≥ 5.0.0):** encrypted `{09}` (Unpair, empty) on uri Secure (§2.6). Not tested on hardware.
* `scripts/nanoleaf_cli.py unpair --yes` sends the v3 unpair where it exists. On legacy devices it only deletes the local token, and says so. The integration does the same when its entry is removed.

## 13. Device information and power-loss recovery (verified 2026-10-15)

### Device information: GET `di`
The app's parser (`ng.d` DeviceInformationFormat) accepts three forms:
* **37-byte fixed record** (this strip):

| Bytes | Content |
|---|---|
| 0–9 | hardware version, ASCII, null-padded (`0.3.6` is shown as `1.0.4`) |
| 10–17 | firmware version |
| 18–28 | serial number (11 characters) |
| 29–36 | EUI-64 (= the device ID in the advertisement) |

  This strip returned `2.0.0 / 1.6.49 / N23490K1500 / dc8e95fffe0b345f`.
* **TLV form** (other lengths): 1-byte type, 1-byte length. Types: `03` hardware, `04` serial, `06` EUI, `07` addressable, `08` country code, `09` setup key, `0A` discriminator, `0B` model, `0C` vendor ID, `0D` product ID, `1B` partner model (used when the model is `M1`/`M2`/`M3`), `1C` sub-model, `FE` firmware.
* **JSON form** (MQTT devices): `firmwareVersion`, `hardwareVersion`, `serialNo`.

The **model number** the app displays comes from the BLE advertisement (§1). For `NLM0` partner devices it's the sub-model at offset 19, here `NL62`.

### Power-loss recovery ("Retain On/Off")
Whether the strip restores its last state after a power cut or comes back off.
* **Where it applies:** Essentials (except Umbra Cono) and 1D devices with firmware ≥ `1.2.13` (Essentials) / `2.0.10` (1D), when the device doesn't use the newer "power-on settings" (`isRetainOnOffSupported`).
* **Read:** POST `ci` with `09 02 00 01 00`. The reply value is `89 02 <len16> <b>`: `01` = enabled (restore last state), `00` = disabled.
* **Write:** POST `ci` with `09 01 00 01 <01|00>`.
* **Verified:** read `01`, wrote `00` → read `00`, wrote `01` → read `01`.
* **Other devices:** Nanoleaf panel devices use a JSON request instead (`GetPLRConfigRequest` / `SetPLRConfigRequest`). Firmware ≥ 5 exposes the `lb/0/plr` endpoint with `PowerLossRecovery` TLVs. Neither path is traced or tested.

## 14. Coverage: what this document and the library do and don't cover

### Device groups (as the app defines them)
| Group | Models | BLE protocol | Doc | Client | Tested |
|---|---|---|---|---|---|
| HomeKit Essentials | NL45, NL55, **NL62 (Secretlab, this strip)**, HFSR70 | legacy (< 5.0.0): X25519 + CTR, CoAP-wrapped | full | full | **NL62 only** |
| Matter Essentials | NL67, NL68, NL53, NL54, NL65, NL70, SQUB01/03 | legacy, or LTPDUv3 at fw ≥ 5.0.0; "new" salts; 512-bit SRP | full | full | no |
| 1D devices | NL71/72/73/75/77 (+K/S variants), SQCM01, SQFX03/04, SQSL01 | legacy BLE as a "v2" device (new salts, no CoAP, MTU 240/512), or LTPDUv3 | framing and crypto; **1D commands not covered** | basic control only | no |
| Sense+ | NL43 Switch, NL44 Button, SQSL02 | as 1D | endpoints listed (`llc/*`, `sp/*`), formats not traced | no | no |
| Light panels (Aurora, Canvas, Shapes, Elements, Lines, Blocks, Skylight, 4D…) | NL22, NL29, NL42, NL52, NL59, NL81, NL64, NL69… | **not BLE-controlled** (Wi-Fi HTTP Open API / LTPDUv3 over HTTP/UDP). BLE is only used for provisioning (BlueStart). | no | no | no |

### Features
| Feature | Status |
|---|---|
| Discovery, pairing, login, on/off, brightness, hue/sat, CT, state, identify | covered; tested on NL62 (CT unsupported there) |
| Simple scenes (`ci` `07 xx`): list, get, add, delete, activate, preview, current; format semantics | covered; tested |
| Secretlab default-scene logic, scene-name tables | covered |
| Cloud scene search, download and conversion | covered; tested |
| Power-loss recovery (`ci` `09 xx`), device info (`di`) | covered; tested |
| Persistent session, reconnect | covered; tested |
| Unpair | v3 documented and implemented, untested; legacy has none (verified) |
| **LTPDUv3 (fw ≥ 5)** | handshake, crypto and framing documented and implemented; **never run on hardware**. Not implemented: session resume, event decryption, client migration (`0D`), OTA. |
| **Effects V2 / NL-Core scenes** (fw ≥ 5: `effect`, `effect/display`, `lb/*/effect/select_id`) | TLV types listed; full motion-option encoding not traced; not implemented |
| **1D `ci` commands** (`01 xx` Effects1D, Paint1D, layout, LEDs-per-zone) | type codes listed only |
| Schedules (`nlsch*`), time/timezone/location, Auto-CCT, Thread/Matter commissioning, Wi-Fi status, Open API token, Mijia, cloud-sync endpoints | endpoint names listed only; value formats not traced |
| Fragmented responses (`0x0006`/`0x0007`) | parser implemented, untested |
| Firmware update over BLE (legacy firmware characteristics; v3 OTA char + `fwu/*`) | characteristics/endpoints listed only, **deliberately not implemented** |
| **BlueStart** (BLE Wi-Fi provisioning, service UUIDs `ff936xxx-10fd-11ed-861d-0242ac120002`, TLV types in §2/TlvType2) | not covered |
| HomeKit-over-BLE (HAP services the strip also exposes) | not covered; the app does not use HAP-BLE for control |
| Non-BLE transports (Thread/CoAP, HTTP, UDP, MQTT) | not covered |

## 15. Notes for a Home Assistant integration

Everything here is based on the verified behaviour of NL62 (legacy firmware). Items marked *open* are not yet known.

**Discovery and setup**
* `manifest.json` Bluetooth matcher: `{"manufacturer_id": 2059, "connectable": true}`. Parse the advertisement (§1) for model, firmware and EUI. Use the EUI (`device_id`) as the unique ID; the MAC also works for BLE.
* Config flow: ask for the 8-digit setup code, then pair (§3.2), then store the **auth token** (and for v3 also `client_id`) in the config entry. `NanoleafDevice.pair()` in the library is the reference. Pairing doesn't disturb the phone app.
* Use HA's Bluetooth stack: `bleak_retry_connector.establish_connection` with the `BLEDevice` from HA. The library's handshake code is transport-agnostic. Dependencies: `cryptography`; `bleak` comes with HA.
* Device registry: model from the advertisement (sub-model for `NLM0`), plus hardware, firmware and serial from `di` (§13).

**Connection and state**
* **No push updates:** subscribing to `6D2ADDA0` / `135634E6` yields no indications, even when the strip's state changes. **Poll** instead: `GET lb/0/oo,pb,hu,sa` plus `07 07` (current scene). A 30–60 s interval with a persistent connection costs about 200 ms per poll.
* Keep **one** connection with a lock: the legacy CTR keystream is shared by both directions, so requests must never overlap.
* Reconnect: wait about 2 s before reconnecting (otherwise GATT 133); the full reconnect plus login takes about 10 s. Acknowledgements can occasionally take up to about 30 s, so use generous timeouts.
* **Coexistence with the phone app (verified):** with our authenticated connection open, the user turned the strip on from the Nanoleaf app, and our next poll saw the change (`07 07`: `00` → `fa`). An integration can therefore keep a persistent connection without locking out the app. *Open:* whether the phone reached the strip over a second BLE connection or another transport (e.g. Thread); it doesn't matter for the integration.

**Entity mapping (NL62)**
| HA entity | Implementation |
|---|---|
| `light`, ON/OFF | `lb/0/oo` |
| Brightness | `lb/0/pb` 0–100 % (HA 0–255 → scale). Dims scenes too, without stopping them (§7.3). |
| HS colour | `lb/0/hu` 0–360 + `lb/0/sa` 0–100, sent together |
| Colour temperature | **not supported on NL62** (`ct` → status 4). On other Essentials use Kelvin, about 2127–6535 K. |
| `effect_list` / `effect` | List `07 03`; names from §10 tables (built-ins) plus the integration's own name store (user scenes; names are **not** stored on the device). Activate with `07 06`, which also turns the light on. Current effect from `07 07`: `00` → no effect (static colour or off), `ff` → an unsaved preview (report e.g. "Preview" or none), else the scene ID. `07 07` reads `00` while off, but switching on resumes the previous scene (§7.3). |
| Switch "Restore after power loss" | `09 02` read / `09 01` write (§13) |
| Button "Identify" | public identify (§3.3); no auth needed |
| Diagnostics | `di`, firmware/model, RSSI |
| Optional services | save/delete scene (`07 02`/`07 05`, mind the 12-scene limit and avoid IDs 244–254), clone from the cloud (§8) |

**Scene state notes**
* *Settled (§7.3):* `07 07` is `00` for a static colour, `ff` for a preview; on after off resumes the scene; brightness dims scenes. Cheap insurance against the unreproduced §7 quirk: treat the light as on if `oo`=1 **or** `07 07` ≠ `00`.
* While a scene plays, `hu`/`sa` keep the last static colour. Report the effect, and not that colour, as the state.
* Firmware ≥ 5.0.0 switches the protocol to LTPDUv3 (§2): implemented but untested, and its scene format (Effects V2) isn't implemented.

**Reproducing the analysis**
Decompile `base.apk` with jadx 1.5.1 (`jadx -d out --no-res base.apk`). Key classes: `LTPDUv3BluetoothConnection`, `BaseLTPDUv3Connection`, `homekitclient/networking/bluetooth/b` (legacy), `CommandCentreRepository`, `NanoleafEffect`, `SecretLabsDefaultSimpleSceneWriter`, `Constants`, `TlvType2`. `tests/lib/test_protocol_basics.py` checks the crypto and encoders against the app's algorithms without hardware, and `tests/common.py` has fake strips speaking both protocols.

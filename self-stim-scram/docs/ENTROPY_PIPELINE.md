# Min-entropy pipeline (M5Stack FIRE, static IMU)

## Stages (matches firmware)

| Step | Implementation |
|------|----------------|
| **1. Self-stimulation** | **MPU6886 gyro self-test** (I2C): X/Y/Z on-chip proof-mass excitation on a deterministic schedule. No speaker, WiFi, or Bluetooth. Applied only *after* the static gate locks (self-test would otherwise break “still” detection). |
| **2. Sensor read** | Gyro + accel @ ~200 Hz (`M5.Imu.update()`). |
| **3. Residuals** | `r = ω − EMA(ω)` with small `α` so EMA tracks bias + slow deterministic bleed-through; residual ≈ noise + fast dynamics. |
| **4. Decorrelation** | **XOR** across `qx,qy,qz` (integer-scaled residuals). **Temporal:** `sym ← spatial ⊕ spatial_prev`. XOR is O(1) on ESP32; PCA would need covariance + eigendecomposition per window—overkill here. |
| **5. Quantization** | Combined word folded to **4-bit** symbols for histogram / H∞. Raw 16-bit words also appended to the hash pool. |
| **6. Sliding H∞** | Each **256-symbol** window: histogram over 16 bins, **H∞ = −log₂(p_max)** (conservative min-entropy estimate per NIST-style reasoning). |
| **7. Accumulate** | Decorrelated bytes stream into a **raw pool** (≥512 B before finalize). |
| **8. Whitening** | **SHA-256**( raw_pool ‖ domain string ‖ metadata ) → 256-bit key. |

## Quasi-static gate (variance-based)

Each loop: **normal** gyro read updates a 64-sample ring buffer (never self-test).  
**Rolling sum of per-axis gyro variances** is computed from that buffer.

- **Harvest ON** when variance drops below `kGyroVarEnter` (and optionally `kGateUseAccel` + ‖a‖≈g).  
- **Harvest OFF** when variance rises above `kGyroVarExit` (hysteresis so it doesn’t flicker).  
- While ON: MPU6886 self-test + entropy pipeline runs; when OFF, only gate updates.

So “static” means **small angular motion variance**, not perfect stillness.

## Tuning (`main.cpp`)

- `kStaticGyroVarMax`, `kAccelStillTol` — sensitivity of stillness.
- `kSymbolBits`, `kWindowSymbols`, `kMinWindows`, `kShaInputMinBytes` — statistics vs. collection time.
- `kEmaAlpha` — residual whitening vs. tracking.
- `kScale` — maps gyro residual to integer XOR domain.

## Security note

This is an **experimental** TRNG-style pipeline. For production keys, use a vetted HRNG, NIST SP 800-90B testing, and independent review. SHA-256 here acts as a **whitening** extractor on biased physical bits, not a proof of full entropy.

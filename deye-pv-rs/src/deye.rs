//! Decoding the Deye SUN-M160G4 register block into the published measurements.
//!
//! Register map from the deye-solarman-logger skill: one FC3 read of 125
//! registers from 0x0000 covers everything, the values are read-only, and the
//! scales are fixed (0.1 W, 0.1 V, 0.01 Hz, 0.1 kWh, 0.1 V / 0.1 A per DC input).
//!
//! The energy scale was *assumed* 0.01 kWh until 2026-09-22 and was wrong by a factor of
//! ten. Settled by the skill's own method - the counter must advance at the rate the power
//! implies: over 21.09.2026 05:00-17:00 UTC the register went 27913 -> 27955 (42 counts)
//! while the logged AC power integrated to 4.18 kWh, so one count is ~0.0995 kWh. The
//! vendor app agrees from the other side (2.79 MWh after 739 days of operation, ~3.8 kWh
//! per day, which matches this plant's measured daily yield).
//!
//! The rounding and the JSON shape are those of the Python poller, because the
//! differential test compares the published payloads byte for byte.

use crate::json::J;

/// Registers read in one go: the whole exposed map is 0x0000..=0x007C.
pub const MAP_SIZE: u16 = 125;

/// The four DC input pairs (voltage, current).
pub const DC_OFFSETS: [usize; 4] = [0x006D, 0x006F, 0x0071, 0x0073];

const R_AC_POWER: usize = 0x0056;
const R_AC_VOLTAGE: usize = 0x005B;
const R_FREQUENCY: usize = 0x005D;
const R_ENERGY: usize = 0x003F;
const R_SERIAL: usize = 0x0003;
const SERIAL_REGISTERS: usize = 5;

#[derive(Debug, Clone, PartialEq)]
pub struct Reading {
    pub ac_power_w: f64,
    pub ac_voltage_v: f64,
    pub frequency_hz: f64,
    pub energy_kwh: f64,
    /// (volts, amps) per DC input, unrounded like the Python version.
    pub dc_inputs: Vec<(f64, f64)>,
    pub dc_power_w: f64,
    pub logger_serial: String,
}

/// The key order matters: it is the order the Python dict is built in, and so the
/// order the JSON appears on the wire.
impl Reading {
    pub fn to_json(&self) -> J {
        // dc_inputs already hold the divided values (as Python's dict does), so
        // they are emitted as they are - dividing again here was a real bug
        let dc: Vec<J> = self
            .dc_inputs
            .iter()
            .map(|(v, a)| {
                J::Obj(vec![
                    ("v".to_string(), J::Repr(*v)),
                    ("a".to_string(), J::Repr(*a)),
                ])
            })
            .collect();
        J::Obj(vec![
            ("ac_power_w".to_string(), J::Dec(self.ac_power_w / 10.0, 1)),
            (
                "ac_voltage_v".to_string(),
                J::Dec(self.ac_voltage_v / 10.0, 1),
            ),
            (
                "frequency_hz".to_string(),
                J::Dec(self.frequency_hz / 100.0, 2),
            ),
            // 0.1 kWh per register count - see the module doc for how that was settled.
            ("energy_kwh".to_string(), J::Dec(self.energy_kwh / 10.0, 2)),
            ("dc_inputs".to_string(), J::Arr(dc)),
            ("dc_power_w".to_string(), J::Dec(self.dc_power_w, 1)),
            (
                "logger_serial".to_string(),
                J::Str(self.logger_serial.clone()),
            ),
        ])
    }
}

/// Turn one register block into a reading.
pub fn decode(regs: &[u16]) -> Result<Reading, String> {
    let needed = DC_OFFSETS[DC_OFFSETS.len() - 1] + 2;
    if regs.len() < needed {
        return Err(format!("short register block: {} < {}", regs.len(), needed));
    }

    let mut dc_inputs = Vec::with_capacity(4);
    for offset in DC_OFFSETS {
        dc_inputs.push((regs[offset] as f64 / 10.0, regs[offset + 1] as f64 / 10.0));
    }
    // Python sums the *divided* values, so do the same
    let dc_power_w = dc_inputs.iter().map(|(v, a)| v * a).sum::<f64>();

    let serial: String = (0..SERIAL_REGISTERS)
        .flat_map(|i| {
            let word = regs[R_SERIAL + i];
            [(word >> 8) & 0xFF, word & 0xFF]
        })
        .map(|byte| char::from(byte as u8))
        .collect();

    Ok(Reading {
        ac_power_w: regs[R_AC_POWER] as f64,
        ac_voltage_v: regs[R_AC_VOLTAGE] as f64,
        frequency_hz: regs[R_FREQUENCY] as f64,
        energy_kwh: regs[R_ENERGY] as f64,
        dc_inputs,
        dc_power_w,
        logger_serial: serial,
    })
}

/// The one-line summary the service logs each poll (Python's print format).
pub fn summary_line(timestamp: &str, r: &Reading) -> String {
    format!(
        "{}  {:6.1} W AC  {:6.1} W DC  {:5.1} V  {:5.2} Hz  {:8.2} kWh",
        timestamp,
        r.ac_power_w / 10.0,
        r.dc_power_w,
        r.ac_voltage_v / 10.0,
        r.frequency_hz / 100.0,
        r.energy_kwh / 10.0
    )
}

/// What to publish as the lifetime energy, given this reading and the last plausible one.
///
/// A lifetime counter never decreases, but this one forgets: for a few minutes after the
/// logger wakes up the register reports **0**, and the value then jumps back to the real one.
/// Publishing such a reading is not harmless - Home Assistant treats a drop on a
/// `total_increasing` sensor as a counter reset and ADDS the new value, so one morning would
/// book the register's whole value (~2795 kWh at the corrected scale) as freshly generated
/// energy in its statistics. So a lower reading is replaced by the last plausible one, and
/// the caller logs how often that happened.
///
/// Note for later: the register is 16-bit, so it wraps at 6553.5 kWh (about 2.7 years away
/// at this plant's rate) - at the wrap this guard would freeze the figure, so the 32-bit pair
/// (0x3F + a high word) has to be checked before then. It is an open item, not a defect yet.
pub fn energy_to_publish(raw: u16, last: Option<u16>) -> (u16, bool) {
    match last {
        Some(prev) if raw < prev => (prev, true),
        _ => (raw, false),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::fmt_repr;

    fn block(power: u16, voltage: u16, freq: u16, energy: u16, dc: [(u16, u16); 4]) -> Vec<u16> {
        let mut regs = vec![0u16; MAP_SIZE as usize];
        regs[R_AC_POWER] = power;
        regs[R_AC_VOLTAGE] = voltage;
        regs[R_FREQUENCY] = freq;
        regs[R_ENERGY] = energy;
        for (i, (v, a)) in dc.iter().enumerate() {
            regs[DC_OFFSETS[i]] = *v;
            regs[DC_OFFSETS[i] + 1] = *a;
        }
        // ASCII "2404190ABE" packed two characters per register
        for (i, pair) in "2404190ABE".as_bytes().chunks(2).enumerate() {
            regs[R_SERIAL + i] = ((pair[0] as u16) << 8) | pair[1] as u16;
        }
        regs
    }

    #[test]
    fn decodes_the_published_fields() {
        let r = decode(&block(
            2535,
            2363,
            4998,
            27656,
            [(2360, 12), (2358, 5), (0, 0), (0, 0)],
        ))
        .unwrap();
        assert_eq!(r.ac_power_w, 2535.0);
        assert_eq!(r.ac_voltage_v, 2363.0);
        assert_eq!(r.frequency_hz, 4998.0);
        assert_eq!(r.energy_kwh, 27656.0);
        assert_eq!(r.dc_inputs[0], (236.0, 1.2));
        assert_eq!(r.logger_serial, "2404190ABE");
        let text = r.to_json().to_json();
        assert!(text.contains(r#""ac_power_w": 253.5"#), "{}", text);
        assert!(text.contains(r#""ac_voltage_v": 236.3"#), "{}", text);
        assert!(text.contains(r#""frequency_hz": 49.98"#), "{}", text);
        assert!(text.contains(r#""energy_kwh": 2765.6"#), "{}", text);
        // the DC pair must appear divided exactly once
        assert!(text.contains(r#"{"v": 236.0, "a": 1.2}"#), "{}", text);
        assert!(
            text.contains(r#""logger_serial": "2404190ABE""#),
            "{}",
            text
        );
    }

    /// The scale that was wrong by ten until 2026-09-22: one count is 0.1 kWh, pinned
    /// against the power integrated over the same window (see the module doc). The live
    /// register stood at 27955 counts, which is 2795.5 kWh - the vendor app's 2.79 MWh.
    #[test]
    fn the_energy_scale_is_tenths_of_a_kwh() {
        let r = decode(&block(0, 2363, 4998, 27955, [(0, 0); 4])).unwrap();
        assert_eq!(r.energy_kwh, 27955.0);
        let text = r.to_json().to_json();
        assert!(text.contains(r#""energy_kwh": 2795.5"#), "{}", text);
        assert!(
            summary_line("x", &r).contains("2795.50 kWh"),
            "{}",
            summary_line("x", &r)
        );
    }

    /// The register's post-wake 0.00 must never reach Home Assistant (see the function doc).
    #[test]
    fn a_counter_that_goes_backwards_is_replaced() {
        // the first reading is taken as it comes
        assert_eq!(energy_to_publish(27955, None), (27955, false));
        // growth is published
        assert_eq!(energy_to_publish(27962, Some(27955)), (27962, false));
        // the wake-up zero (and any other drop) is replaced by the last plausible value
        assert_eq!(energy_to_publish(0, Some(27962)), (27962, true));
        assert_eq!(energy_to_publish(27900, Some(27962)), (27962, true));
        // an unchanged reading is fine, and stays the reference
        assert_eq!(energy_to_publish(27962, Some(27962)), (27962, false));
    }

    #[test]
    fn dc_power_is_summed_from_divided_values() {
        let r = decode(&block(0, 0, 0, 0, [(2360, 12), (2358, 5), (0, 0), (0, 0)])).unwrap();
        // 236.0*1.2 + 235.8*0.5 = 283.2 + 117.9 = 401.1
        assert!((r.dc_power_w - 401.1).abs() < 0.05, "got {}", r.dc_power_w);
        assert!(r.to_json().to_json().contains(r#""dc_power_w": 401.1"#));
    }

    #[test]
    fn an_idle_inverter_still_produces_valid_json() {
        let r = decode(&block(0, 0, 0, 0, [(0, 0); 4])).unwrap();
        let text = r.to_json().to_json();
        assert!(text.contains(r#""ac_power_w": 0.0"#), "{}", text);
        assert!(
            !text.contains(r#""ac_power_w": 0,"#),
            "must stay a float: {}",
            text
        );
    }

    #[test]
    fn a_short_block_is_rejected() {
        assert!(decode(&vec![0u16; 10]).is_err());
    }

    #[test]
    fn repr_matches_python_for_dc_values() {
        assert_eq!(fmt_repr(236.0 / 10.0), "23.6");
        assert_eq!(fmt_repr(2360.0 / 10.0), "236.0");
    }
}

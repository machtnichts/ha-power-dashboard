//! The side-by-side SolarEdge vs garage-PV log.
//!
//! One row per poll, appended to `logs/se_vs_garage.csv`, from the same HA
//! template the Python version uses. This is the data behind the SDM/Deye
//! comparison, so the column set, the rounding, the CRLF line endings and the
//! header-on-create behaviour are all reproduced exactly.
//!
//! Logging must never break publishing: every failure here is reported and
//! swallowed, exactly like the Python version.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};

use crate::deye::Reading;
use crate::ha::HaClient;
use crate::json::fmt_dec;
use crate::logging;

/// Same columns, same order.
pub const FIELDS: [&str; 10] = [
    "ts",
    "se_pv_w",
    "evcc_home_w",
    "home_total_w",
    "charger_w",
    "grid_w",
    "battery_w",
    "garage_pv_w",
    "garage_pv_dc_w",
    "excess_over_4600",
];

/// One template call per sample, so a 30 s cadence stays cheap.
pub const TEMPLATE: &str = concat!(
    r#"{{ {"se_pv": states("sensor.evcc_pv_power")|float(0),"#,
    r#" "home": states("sensor.evcc_home_power")|float(0),"#,
    r#" "grid": states("sensor.evcc_grid_power")|float(0),"#,
    r#" "battery": states("sensor.evcc_battery_power")|float(0),"#,
    r#" "charger_kw": states("sensor.evcc_go_e_charger_charge_power")|float(0)} | tojson }}"#
);

/// Relative to the working directory, matching the Python layout.
pub fn default_path() -> PathBuf {
    PathBuf::from("logs").join("se_vs_garage.csv")
}

pub fn append(path: &Path, garage: &Reading, ha: &HaClient) -> Option<String> {
    match try_append(path, garage, ha) {
        Ok(row) => Some(row),
        Err(problem) => {
            // the Python version prints this and carries on
            logging::line(&format!("  (compare log skipped: {})", problem));
            None
        }
    }
}

fn try_append(path: &Path, garage: &Reading, ha: &HaClient) -> Result<String, String> {
    let sampled = ha.render_template(TEMPLATE)?;

    let se_pv = sampled.get("se_pv").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let home = sampled.get("home").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let grid = sampled.get("grid").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let battery = sampled
        .get("battery")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let charger_kw = sampled
        .get("charger_kw")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    // this sensor is in kW, everything else here is in W
    let charger_w = charger_kw * 1000.0;

    let row = [
        logging::stamp(),
        fmt_dec(se_pv, 1),
        fmt_dec(home, 1),
        // evcc's homePower EXCLUDES the wallbox (verified: total = home + charger)
        fmt_dec(home + charger_w, 1),
        fmt_dec(charger_w, 1),
        fmt_dec(grid, 1),
        fmt_dec(battery, 1),
        fmt_dec(garage.ac_power_w / 10.0, 1),
        fmt_dec(garage.dc_power_w, 1),
        fmt_dec(se_pv - 4600.0, 1),
    ];
    let line = row.join(",");

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let fresh = !path.exists();
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    if fresh {
        // csv.DictWriter writes the header only for a new file, and terminates
        // every line with CRLF - including the header
        write!(file, "{}\r\n", FIELDS.join(",")).map_err(|e| e.to_string())?;
    }
    // Python's csv module terminates rows with \r\n by default
    write!(file, "{}\r\n", line).map_err(|e| e.to_string())?;
    Ok(line)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::deye::Reading;

    fn reading() -> Reading {
        Reading {
            ac_power_w: 2478.0,
            ac_voltage_v: 2363.0,
            frequency_hz: 4998.0,
            energy_kwh: 27656.0,
            dc_inputs: vec![(236.0, 1.2), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
            dc_power_w: 283.2,
            logger_serial: "2404190ABE".into(),
        }
    }

    #[test]
    fn the_template_is_the_python_one() {
        assert!(TEMPLATE.starts_with("{{ {\"se_pv\": states(\"sensor.evcc_pv_power\")|float(0)"));
        assert!(TEMPLATE.ends_with("| tojson }}"));
        assert!(TEMPLATE.contains("sensor.evcc_go_e_charger_charge_power"));
    }

    #[test]
    fn the_row_shape_matches_the_python_writer() {
        // build the row the way try_append does, without touching the network
        let garage = reading();
        let row = [
            "2026-09-13 11:00:00".to_string(),
            fmt_dec(1600.5, 1),
            fmt_dec(2958.9, 1),
            fmt_dec(2958.9 + 1380.0, 1),
            fmt_dec(1380.0, 1),
            fmt_dec(-0.9, 1),
            fmt_dec(909.0, 1),
            fmt_dec(garage.ac_power_w / 10.0, 1),
            fmt_dec(garage.dc_power_w, 1),
            fmt_dec(1600.5 - 4600.0, 1),
        ];
        assert_eq!(
            row.join(","),
            "2026-09-13 11:00:00,1600.5,2958.9,4338.9,1380.0,-0.9,909.0,247.8,283.2,-2999.5"
        );
        assert_eq!(FIELDS.join(","), "ts,se_pv_w,evcc_home_w,home_total_w,charger_w,grid_w,battery_w,garage_pv_w,garage_pv_dc_w,excess_over_4600");
    }

    #[test]
    fn whole_floats_stay_floats_in_the_csv() {
        // Python writes str(0.0) -> "0.0", not "0"
        assert_eq!(fmt_dec(0.0, 1), "0.0");
        assert_eq!(fmt_dec(-0.0, 1), "-0.0");
    }
}

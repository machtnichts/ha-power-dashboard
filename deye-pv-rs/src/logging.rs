//! Timestamps for the console lines and the comparison CSV.
//!
//! The Python poller uses `time.strftime`, which is local time. Rust's standard
//! library only offers UTC, and the CSV is a long-running file where a timezone
//! change would corrupt the data - so this asks libc for local time directly.
//! One `localtime_r` call is a much smaller dependency than a date crate, and the
//! unit test below compares the result against `date(1)` rather than trusting the
//! struct layout.

use std::os::raw::{c_char, c_int, c_long};

#[repr(C)]
struct Tm {
    tm_sec: c_int,
    tm_min: c_int,
    tm_hour: c_int,
    tm_mday: c_int,
    tm_mon: c_int,
    tm_year: c_int,
    tm_wday: c_int,
    tm_yday: c_int,
    tm_isdst: c_int,
    tm_gmtoff: c_long,
    tm_zone: *const c_char,
}

extern "C" {
    fn localtime_r(timep: *const i64, result: *mut Tm) -> *mut Tm;
}

/// (year, month, day, hour, minute, second) in local time.
pub fn local_parts() -> (i32, i32, i32, i32, i32, i32) {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let mut tm = Tm {
        tm_sec: 0,
        tm_min: 0,
        tm_hour: 0,
        tm_mday: 1,
        tm_mon: 0,
        tm_year: 70,
        tm_wday: 0,
        tm_yday: 0,
        tm_isdst: 0,
        tm_gmtoff: 0,
        tm_zone: std::ptr::null(),
    };
    let ok = unsafe { !localtime_r(&now, &mut tm).is_null() };
    if !ok {
        // fall back to UTC rather than emitting a wrong-looking timestamp
        let secs = now.max(0) as u64;
        let days = secs / 86_400;
        let rem = secs % 86_400;
        let (y, m, d) = civil_from_days(days as i64);
        return (
            y,
            m,
            d,
            (rem / 3600) as i32,
            ((rem % 3600) / 60) as i32,
            (rem % 60) as i32,
        );
    }
    (
        tm.tm_year + 1900,
        tm.tm_mon + 1,
        tm.tm_mday,
        tm.tm_hour,
        tm.tm_min,
        tm.tm_sec,
    )
}

/// "HH:MM:SS" - the prefix on every console line.
pub fn hms() -> String {
    let (_, _, _, h, m, s) = local_parts();
    format!("{:02}:{:02}:{:02}", h, m, s)
}

/// "YYYY-MM-DD HH:MM:SS" - the first CSV column.
pub fn stamp() -> String {
    let (y, mo, d, h, mi, s) = local_parts();
    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02}", y, mo, d, h, mi, s)
}

/// Howard Hinnant's days-to-civil algorithm, used only if localtime_r fails.
fn civil_from_days(z: i64) -> (i32, i32, i32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as i32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as i32;
    ((if m <= 2 { y + 1 } else { y }) as i32, m, d)
}

/// One console line, flushed so a journal tail shows it immediately.
pub fn line(text: &str) {
    use std::io::Write;
    let mut out = std::io::stdout();
    let _ = writeln!(out, "{}", text);
    let _ = out.flush();
}

/// The startup/status lines, which the Python version prints without a timestamp.
pub fn plain(text: &str) {
    line(text);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn local_time_matches_the_systems_own_clock() {
        // the C struct layout is the risky part, so compare with date(1)
        let expected = String::from_utf8(
            Command::new("date")
                .arg("+%Y-%m-%d %H:%M:%S")
                .output()
                .unwrap()
                .stdout,
        )
        .unwrap()
        .trim()
        .to_string();
        let got = stamp();
        // allow the second to tick over between the two calls
        if got != expected {
            let expected_hms = String::from_utf8(
                Command::new("date")
                    .arg("+%H:%M:%S")
                    .output()
                    .unwrap()
                    .stdout,
            )
            .unwrap()
            .trim()
            .to_string();
            assert_eq!(
                &got[11..],
                &expected_hms,
                "local time from localtime_r does not match date(1): '{}' vs '{}'",
                got,
                expected
            );
        }
    }

    #[test]
    fn the_utc_fallback_is_correct() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_000), (2022, 1, 8));
    }
}

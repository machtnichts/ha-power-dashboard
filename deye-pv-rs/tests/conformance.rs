//! End-to-end tests for the poller: a stub SolarMAN logger and a fake Home
//! Assistant, so the whole path is exercised - frame building and parsing, the
//! published JSON, the retained availability messages, the discovery payloads and
//! the comparison CSV.
//!
//! The real inverter is never contacted. It accepts one session at a time and is
//! currently owned by the Python poller, so a test that connected to it would
//! break the live dashboard.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const POLLER: &str = env!("CARGO_BIN_EXE_deyepv");
const STUB: &str = env!("CARGO_BIN_EXE_stubdeye");

/// Registers that decode to values seen on the real inverter, including the ASCII
/// serial the logger reports.
const REGISTERS: &[(&str, u16)] = &[
    ("0x3", 12852),  // "24"
    ("0x4", 12340),  // "04"
    ("0x5", 12601),  // "19"
    ("0x6", 12353),  // "0A"
    ("0x7", 16965),  // "BE"  -> logger_serial "2404190ABE"
    ("0x56", 2535),  // AC power 253.5 W
    ("0x5B", 2363),  // AC voltage 236.3 V
    ("0x5D", 4998),  // frequency 49.98 Hz
    ("0x3F", 27656), // energy 2765.6 kWh (0.1 kWh per count)
    ("0x6D", 2360),  // DC1 236.0 V
    ("0x6E", 12),    // DC1 1.2 A
    ("0x6F", 2358),  // DC2 235.8 V
    ("0x70", 5),     // DC2 0.5 A
    // the M160 has two DC inputs; the stub would otherwise default these to
    // reg[i] = i and add phantom DC power
    ("0x71", 0),
    ("0x72", 0),
    ("0x73", 0),
    ("0x74", 0),
];

const EXPECTED_STATE: &str = concat!(
    r#"{"ac_power_w": 253.5, "ac_voltage_v": 236.3, "frequency_hz": 49.98, "#,
    r#""energy_kwh": 2765.6, "dc_inputs": [{"v": 236.0, "a": 1.2}, {"v": 235.8, "a": 0.5}, "#,
    r#"{"v": 0.0, "a": 0.0}, {"v": 0.0, "a": 0.0}], "dc_power_w": 401.1, "#,
    r#""logger_serial": "2404190ABE"}"#
);

const HA_SAMPLE: &str = concat!(
    r#"{"se_pv": 1600.5, "home": 2958.9, "grid": -0.9, "battery": 909.0, "charger_kw": 1.38}"#
);

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn wait_for_port(port: u16, what: &str) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    panic!("{} never came up on port {}", what, port);
}

/// What the poller asked Home Assistant to publish.
#[derive(Debug, Clone, PartialEq)]
struct Published {
    topic: String,
    payload: String,
    retain: bool,
}

/// A minimal Home Assistant: it accepts the two endpoints the poller uses and
/// records every publish, so the test asserts on the bytes on the wire.
struct FakeHa {
    port: u16,
    published: Arc<Mutex<Vec<Published>>>,
    template_requests: Arc<Mutex<usize>>,
    template_body: String,
    reject_template: Arc<AtomicBool>,
    reject_topic: Arc<Mutex<Option<String>>>,
}

impl FakeHa {
    fn start(template_body: &str) -> FakeHa {
        let port = free_port();
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        let published: Arc<Mutex<Vec<Published>>> = Arc::new(Mutex::new(Vec::new()));
        let template_requests = Arc::new(Mutex::new(0usize));
        let reject_template = Arc::new(AtomicBool::new(false));
        let reject_topic: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));

        let template = template_body.to_string();
        let (p, tr, rt, rj) = (
            published.clone(),
            template_requests.clone(),
            reject_template.clone(),
            reject_topic.clone(),
        );
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(stream) = stream else { continue };
                let (p, tr, rt, rj) = (p.clone(), tr.clone(), rt.clone(), rj.clone());
                let template = template.clone();
                std::thread::spawn(move || serve_ha(stream, p, tr, rt, rj, template));
            }
        });

        FakeHa {
            port,
            published,
            template_requests,
            template_body: template_body.to_string(),
            reject_template,
            reject_topic,
        }
    }

    fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    fn published(&self) -> Vec<Published> {
        self.published.lock().unwrap().clone()
    }

    fn topics(&self) -> Vec<String> {
        self.published().into_iter().map(|p| p.topic).collect()
    }

    fn find(&self, topic: &str) -> Option<Published> {
        self.published().into_iter().find(|p| p.topic == topic)
    }

    fn template_requests(&self) -> usize {
        *self.template_requests.lock().unwrap()
    }

    /// Reject `/api/template` from now on, through the *live* flag the server
    /// thread is watching.
    fn reject_templates(&self) {
        self.reject_template.store(true, Ordering::Relaxed);
    }

    /// Make the publish to this topic fail with a 500.
    fn reject_publish_to(&self, topic: &str) {
        *self.reject_topic.lock().unwrap() = Some(topic.to_string());
    }
}

fn serve_ha(
    mut stream: TcpStream,
    published: Arc<Mutex<Vec<Published>>>,
    template_requests: Arc<Mutex<usize>>,
    reject_template: Arc<AtomicBool>,
    reject_topic: Arc<Mutex<Option<String>>>,
    template_body: String,
) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let mut reader = BufReader::new(stream.try_clone().unwrap());

    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() {
        return;
    }
    let path = request_line
        .split_whitespace()
        .nth(1)
        .unwrap_or("/")
        .to_string();

    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" || line == "\n" {
            break;
        }
        let lower = line.to_ascii_lowercase();
        if let Some(value) = lower.strip_prefix("content-length:") {
            content_length = value.trim().parse().unwrap_or(0);
        }
    }
    let mut raw = vec![0u8; content_length];
    if reader.read_exact(&mut raw).is_err() {
        return;
    }
    let body = String::from_utf8_lossy(&raw).to_string();

    let is_template = path.contains("/api/template");
    let is_publish = path.contains("/api/services/mqtt/publish");

    if is_template {
        *template_requests.lock().unwrap() += 1;
        if reject_template.load(Ordering::Relaxed) {
            respond(
                &mut stream,
                500,
                r#"{"error":"template disabled for this test"}"#,
            );
            return;
        }
        respond(&mut stream, 200, &template_body);
        return;
    }

    if is_publish {
        let topic = json_string(&body, "topic").unwrap_or_default();
        let rejected = reject_topic
            .lock()
            .unwrap()
            .as_deref()
            .is_some_and(|t| t == topic);
        if rejected {
            // a failed publish must count as a failed poll, so it is not recorded
            respond(
                &mut stream,
                500,
                r#"{"error":"publish rejected for this test"}"#,
            );
            return;
        }
        published.lock().unwrap().push(Published {
            topic,
            payload: json_string(&body, "payload").unwrap_or_default(),
            retain: body.contains(r#""retain": true"#),
        });
        respond(&mut stream, 200, "{}");
        return;
    }

    respond(&mut stream, 404, r#"{"error":"not found"}"#);
}

fn respond(stream: &mut TcpStream, status: u16, body: &str) {
    let reason = if status == 200 { "OK" } else { "Error" };
    let head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        status,
        reason,
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body.as_bytes());
    let _ = stream.flush();
}

/// Extract a top-level string field from a JSON body (enough for assertions).
fn json_string(body: &str, key: &str) -> Option<String> {
    let needle = format!("\"{}\": \"", key);
    let start = body.find(&needle)? + needle.len();
    let mut out = String::new();
    let mut chars = body[start..].chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => break,
            '\\' => {
                let esc = chars.next()?;
                out.push(match esc {
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    '"' => '"',
                    '\\' => '\\',
                    'u' => {
                        let hex: String = chars.by_ref().take(4).collect();
                        char::from_u32(u32::from_str_radix(&hex, 16).ok()?)?
                    }
                    other => other,
                });
            }
            c => out.push(c),
        }
    }
    Some(out)
}

/// The stub logger, killed when the test ends.
struct StubLogger {
    child: Child,
    port: u16,
}

impl Drop for StubLogger {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn start_logger(extra: &[&str]) -> StubLogger {
    let port = free_port();
    let mut args: Vec<String> = vec!["--port".into(), port.to_string()];
    let mut overrides: BTreeMap<String, u16> = BTreeMap::new();
    for (address, value) in REGISTERS {
        overrides.insert((*address).to_string(), *value);
    }
    for (address, value) in &overrides {
        args.push("--set".into());
        args.push(format!("{}={}", address, value));
    }
    for e in extra {
        args.push((*e).to_string());
    }
    let child = Command::new(STUB)
        .args(&args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn stub logger");
    wait_for_port(port, "stub logger");
    StubLogger { child, port }
}

/// Run the poller once against the stub logger and the fake HA.
fn run_poller(
    logger_port: u16,
    ha: &FakeHa,
    csv: &std::path::Path,
    extra: &[&str],
) -> (i32, String, String) {
    let mut args: Vec<String> = vec![
        "--host".into(),
        "127.0.0.1".into(),
        "--port".into(),
        logger_port.to_string(),
        "--timeout".into(),
        "5".into(),
        "--log-csv".into(),
        csv.to_string_lossy().to_string(),
    ];
    for e in extra {
        args.push((*e).to_string());
    }
    let output = Command::new(POLLER)
        .args(&args)
        .env("HASS_URL", ha.url())
        .env("HASS_TOKEN", "test-token-never-used")
        .output()
        .expect("run poller");
    (
        output.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    )
}

fn temp_csv(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("deyepv-test-{}-{}", name, free_port()));
    std::fs::create_dir_all(&dir).unwrap();
    dir.join("se_vs_garage.csv")
}

#[test]
fn one_poll_publishes_the_state_and_marks_availability_online() {
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("once");

    let (code, stdout, stderr) = run_poller(stub.port, &ha, &csv, &["--once"]);
    assert_eq!(code, 0, "stdout={} stderr={}", stdout, stderr);

    let state = ha
        .find("powerdash/garage_pv/state")
        .expect("state published");
    assert!(state.retain, "state must be retained");
    assert_eq!(
        state.payload, EXPECTED_STATE,
        "the published payload must be exactly the Python shape"
    );

    let online = ha
        .find("powerdash/garage_pv/status")
        .expect("availability published");
    assert_eq!(online.payload, "online");
    assert!(online.retain, "availability must be retained");
    assert_eq!(
        ha.topics(),
        vec!["powerdash/garage_pv/state", "powerdash/garage_pv/status"],
        "state first, then availability"
    );

    assert!(
        stdout.contains("253.5 W AC") && stdout.contains("49.98 Hz"),
        "the console line mirrors the Python format, got: {}",
        stdout
    );
}

#[test]
fn whole_floats_keep_their_decimal_point() {
    // Python writes 0.0 where Rust's default would write 0
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("floats");
    run_poller(stub.port, &ha, &csv, &["--once"]);

    let payload = ha.find("powerdash/garage_pv/state").unwrap().payload;
    for expected in [
        r#""v": 236.0"#,
        r#""a": 1.2"#,
        r#""v": 0.0"#,
        r#""a": 0.0"#,
        r#""ac_power_w": 253.5"#,
        r#""frequency_hz": 49.98"#,
    ] {
        assert!(
            payload.contains(expected),
            "missing {} in {}",
            expected,
            payload
        );
    }
    assert!(
        !payload.contains(r#""v": 236,"#) && !payload.contains(r#""a": 0,"#),
        "a whole float must not collapse to an integer: {}",
        payload
    );
}

#[test]
fn the_comparison_csv_gets_one_row_with_the_expected_columns() {
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("csv");

    let (code, stdout, stderr) = run_poller(stub.port, &ha, &csv, &["--once"]);
    assert_eq!(code, 0, "stdout={} stderr={}", stdout, stderr);
    assert_eq!(ha.template_requests(), 1, "one template call per sample");

    let text = std::fs::read_to_string(&csv).expect("csv written");
    assert!(text.contains("\r\n"), "Python's csv writer uses CRLF");
    let mut lines = text.split("\r\n").filter(|l| !l.is_empty());
    assert_eq!(
        lines.next().unwrap(),
        "ts,se_pv_w,evcc_home_w,home_total_w,charger_w,grid_w,battery_w,garage_pv_w,garage_pv_dc_w,excess_over_4600"
    );
    let row = lines.next().expect("one data row");
    let fields: Vec<&str> = row.split(',').collect();
    assert_eq!(fields.len(), 10, "row was {}", row);
    assert_eq!(
        &fields[1..],
        &["1600.5", "2958.9", "4338.9", "1380.0", "-0.9", "909.0", "253.5", "401.1", "-2999.5"],
        "home_total_w adds the wallbox, excess is measured against 4600"
    );
    assert!(lines.next().is_none(), "exactly one row");
}

#[test]
fn two_polls_publish_byte_identical_payloads() {
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("twice");

    run_poller(stub.port, &ha, &csv, &["--once"]);
    let first = ha.published();
    run_poller(stub.port, &ha, &csv, &["--once"]);
    let all = ha.published();

    assert_eq!(
        all.len(),
        first.len() * 2,
        "one state + one availability each"
    );
    assert_eq!(
        &all[..first.len()],
        &first[..],
        "payloads must be identical"
    );
    // and the second run appends a second CSV row rather than rewriting
    let text = std::fs::read_to_string(&csv).unwrap();
    assert_eq!(text.matches("\r\n").count(), 3, "header + two rows");
}

#[test]
fn discovery_publishes_the_five_entity_configs_then_availability() {
    let stub = start_logger(&[]);
    let ha = FakeHa::start("{}");
    let csv = temp_csv("discovery");

    let (code, stdout, stderr) = run_poller(stub.port, &ha, &csv, &["--discovery"]);
    assert_eq!(code, 0, "stderr={}", stderr);

    assert_eq!(
        ha.topics(),
        vec![
            "homeassistant/sensor/powerdash/garage_pv_power/config",
            "homeassistant/sensor/powerdash/garage_pv_energy/config",
            "homeassistant/sensor/powerdash/garage_pv_voltage/config",
            "homeassistant/sensor/powerdash/garage_pv_frequency/config",
            "homeassistant/sensor/powerdash/garage_pv_dc_power/config",
            "powerdash/garage_pv/status",
        ]
    );
    assert!(
        ha.published().iter().all(|p| p.retain),
        "discovery is retained"
    );
    assert_eq!(
        ha.published()[0].payload,
        concat!(
            r#"{"name": "Garage PV Leistung", "unique_id": "powerdash_garage_pv_power", "#,
            r#""object_id": "garage_pv_leistung", "state_topic": "powerdash/garage_pv/state", "#,
            r#""device": {"identifiers": ["deye_sun_m160g4_garage"], "#,
            r#""name": "Garage PV (Deye SUN-M160G4)", "manufacturer": "Deye", "#,
            r#""model": "SUN-M160G4-EU-Q0"}, "availability_topic": "powerdash/garage_pv/status", "#,
            r#""payload_available": "online", "payload_not_available": "offline", "#,
            r#""device_class": "power", "unit_of_measurement": "W", "state_class": "measurement", "#,
            r#""value_template": "{{ value_json.ac_power_w }}"}"#
        )
    );
    assert!(
        stdout.contains("sensor.garage_pv_leistung"),
        "the log names the entity it created: {}",
        stdout
    );
}

#[test]
fn dry_run_touches_neither_the_device_nor_home_assistant() {
    // deliberately no stub logger: a dry run must not need one
    let ha = FakeHa::start("{}");
    let csv = temp_csv("dryrun");
    let dead_port = free_port();

    let (code, stdout, stderr) = run_poller(dead_port, &ha, &csv, &["--dry-run"]);
    assert_eq!(code, 0, "stderr={}", stderr);
    assert!(stdout.contains("[dry-run]"), "stdout: {}", stdout);
    assert!(ha.published().is_empty(), "nothing may be published");
    assert_eq!(ha.template_requests(), 0);
    assert!(!csv.exists(), "no CSV for a dry run");
}

#[test]
fn a_keepalive_frame_costs_no_poll() {
    // This logger really does send a spurious 14-byte keep-alive (control code
    // 0x4710) every few minutes. The reference library treats it as fatal, which
    // costs the whole poll and shows up as an 'offline' blip in Home Assistant.
    // The reply follows the keep-alive here, and the poll must survive it.
    let stub = start_logger(&["--junk-once"]);
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("keepalive");

    let (code, stdout, stderr) = run_poller(stub.port, &ha, &csv, &["--once"]);
    assert_eq!(code, 0, "stdout={} stderr={}", stdout, stderr);

    let state = ha
        .find("powerdash/garage_pv/state")
        .expect("the poll must still have succeeded");
    assert_eq!(
        state.payload, EXPECTED_STATE,
        "and the reading must be the real one, not a fragment"
    );

    // exactly one availability message, and it says online - no blip
    let status: Vec<Published> = ha
        .published()
        .into_iter()
        .filter(|p| p.topic == "powerdash/garage_pv/status")
        .collect();
    assert_eq!(
        status.len(),
        1,
        "expected one availability publish, got {:?}",
        status
    );
    assert_eq!(status[0].payload, "online", "must not have gone offline");

    assert!(
        stdout.contains("keep-alive"),
        "the log should say what it skipped: {}",
        stdout
    );
    assert!(csv.exists(), "the poll counted, so the CSV row was written");
}

#[test]
fn the_poll_interval_is_respected() {
    // Regression: the loop once counted 250 ms slices against the interval in
    // *seconds*, so --interval 2 polled every 0.5 s - four times too fast, and it
    // only showed up in the journal timestamps. Two or three reads in 3.2 s is the
    // right shape; eight or more means the unit mix-up is back.
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("interval");

    let mut child = Command::new(POLLER)
        .args([
            "--host",
            "127.0.0.1",
            "--port",
            &stub.port.to_string(),
            "--timeout",
            "5",
            "--interval",
            "2",
            "--log-csv",
            csv.to_string_lossy().as_ref(),
        ])
        .env("HASS_URL", ha.url())
        .env("HASS_TOKEN", "test-token-never-used")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn the poller in loop mode");

    std::thread::sleep(Duration::from_millis(3200));
    let _ = child.kill();
    let _ = child.wait();

    let reads = ha
        .topics()
        .iter()
        .filter(|t| *t == "powerdash/garage_pv/state")
        .count();
    assert!(
        (2..=3).contains(&reads),
        "expected 2-3 polls in 3.2 s at --interval 2, got {} (a faster rate means the interval is not in seconds)",
        reads
    );
}

#[test]
fn dry_run_needs_no_credentials_and_no_device() {
    // the Python version's --dry-run also works without a token, and a dry run is
    // exactly what you reach for on a machine that has none
    let empty_home = std::env::temp_dir().join(format!("deyepv-nohome-{}", free_port()));
    std::fs::create_dir_all(&empty_home).unwrap();

    let output = Command::new(POLLER)
        .args(["--dry-run"])
        .env_clear()
        .env("HOME", &empty_home)
        .output()
        .expect("run poller");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    assert_eq!(
        output.status.code(),
        Some(0),
        "stdout={} stderr={}",
        stdout,
        stderr
    );
    assert_eq!(
        stdout.matches("[dry-run]").count(),
        6,
        "five entity configs plus availability, got: {}",
        stdout
    );
    assert!(stdout.contains("garage_pv_leistung"), "stdout: {}", stdout);
    let _ = std::fs::remove_dir_all(&empty_home);
}

#[test]
fn a_dead_logger_marks_availability_offline_and_publishes_no_state() {
    let ha = FakeHa::start(HA_SAMPLE);
    let csv = temp_csv("dead");
    let dead_port = free_port();

    let (code, stdout, _) = run_poller(dead_port, &ha, &csv, &["--once"]);
    assert_eq!(
        code, 0,
        "--once reports the failure but still exits cleanly"
    );

    let offline = ha
        .find("powerdash/garage_pv/status")
        .expect("availability published");
    assert_eq!(offline.payload, "offline");
    assert!(offline.retain);
    assert!(
        ha.find("powerdash/garage_pv/state").is_none(),
        "a failed read must not publish state"
    );
    assert!(stdout.contains("ERROR"), "stdout: {}", stdout);
    assert!(!csv.exists(), "nothing to log");
}

#[test]
fn a_rejected_publish_is_a_failed_poll_and_takes_availability_offline() {
    // the recovery path the Python version needed a fix for: a rejected publish
    // must be treated exactly like a failed read
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    ha.reject_publish_to("powerdash/garage_pv/state");
    let csv = temp_csv("rejected");

    let (code, stdout, _) = run_poller(stub.port, &ha, &csv, &["--once"]);
    assert_eq!(code, 0);

    assert!(
        ha.find("powerdash/garage_pv/state").is_none(),
        "the rejected publish must not count as delivered"
    );
    let availability = ha
        .find("powerdash/garage_pv/status")
        .expect("availability published");
    assert_eq!(availability.payload, "offline");
    assert!(stdout.contains("ERROR publish"), "stdout: {}", stdout);
}

#[test]
fn test_mode_publishes_only_to_the_selftest_topic() {
    let stub = start_logger(&[]);
    let ha = FakeHa::start("{}");
    let csv = temp_csv("selftest");

    let (code, stdout, stderr) = run_poller(stub.port, &ha, &csv, &["--test"]);
    assert_eq!(code, 0, "stderr={}", stderr);

    let published = ha.published();
    assert_eq!(published.len(), 1, "{:?}", published);
    assert_eq!(published[0].topic, "powerdash/selftest");
    assert!(!published[0].retain, "--test publishes unretained");
    assert!(stdout.contains("HTTP 200"), "stdout: {}", stdout);
    assert!(!csv.exists(), "no CSV row for --test");
}

#[test]
fn a_failing_template_call_is_skipped_without_breaking_the_poll() {
    let stub = start_logger(&[]);
    let ha = FakeHa::start(HA_SAMPLE);
    ha.reject_templates();
    let csv = temp_csv("notemplate");

    let (code, stdout, _) = run_poller(stub.port, &ha, &csv, &["--once"]);
    assert_eq!(code, 0);
    assert!(
        ha.find("powerdash/garage_pv/state").is_some(),
        "the state publish must still happen"
    );
    assert!(
        stdout.contains("compare log skipped"),
        "expected the skip notice, got: {}",
        stdout
    );
    assert!(!csv.exists(), "nothing may be written");
}

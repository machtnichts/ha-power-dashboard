//! deyepv - read the garage PV (Deye SUN-M160G4 behind its SolarMAN logger) and
//! publish it into Home Assistant.
//!
//! Behaviour-compatible with `python -m powerdash.deye_pv` next to it: same
//! topics, same discovery payloads, same CSV row, same 30 s cadence, same
//! persistent-connection rule (this logger accepts one session at a time, so
//! reconnecting on every poll is what breaks it).
//!
//! Usage:
//!   deyepv                     loop, publishing every 30 s
//!   deyepv --once              one reading, then exit
//!   deyepv --test              read and publish to powerdash/selftest only
//!   deyepv --discovery         (re)create the HA entities, retained
//!   deyepv --dry-run           print the discovery payloads, publish nothing
//!   deyepv --recreate          delete the entities, wait, recreate, push once
//!
//! Test-only options (the Python version hardcodes these): --host, --port,
//! --serial, --log-csv, --no-compare-log, --interval.

mod compare_log;
mod deye;
mod discovery;
mod env;
mod ha;
mod json;
mod logging;
mod v5;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use deye::Reading;
use ha::HaClient;
use v5::{V5Client, V5Error};

const HOST_DEFAULT: &str = "192.168.178.33";
const PORT_DEFAULT: u16 = 8899;
/// The serial the logger reports in its own replies - not the label on the box.
const SERIAL_DEFAULT: u32 = 3842831288;
const SLAVE_DEFAULT: u8 = 1;
/// 30 s, not 15: this logger accepts one session at a time.
const INTERVAL_DEFAULT: u64 = 30;
const TIMEOUT_DEFAULT: u64 = 10;
const SELFTEST_TOPIC: &str = "powerdash/selftest";
/// When the logger is away, retry no sooner than this - and let the garage meter wake us
/// earlier if energy starts flowing there. A PV logger is powered by the inverter and is
/// simply gone between dusk and sunrise; retrying it every 33 s produced ~2600 lines a night.
const BACKOFF_MAX_DEFAULT: u64 = 900;
/// The wake-up signal: this counter grows in *either* direction, so it moves whenever
/// anything flows in the garage branch - the inverter exporting, or the car taking it.
const SDM_ENTITY_DEFAULT: &str = "sensor.sdm630_total_kwh";
/// How often the meter is consulted while the logger is being backed off (one cheap HA read).
const SDM_CHECK_S: u64 = 60;

const SIGINT: i32 = 2;
const SIGTERM: i32 = 15;

extern "C" {
    fn signal(signum: i32, handler: usize) -> usize;
}

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_sig: i32) {
    STOP.store(true, Ordering::SeqCst);
}

fn install_signal_handlers() {
    unsafe {
        signal(SIGINT, on_signal as usize);
        signal(SIGTERM, on_signal as usize);
    }
}

#[derive(Debug, Clone)]
struct Options {
    host: String,
    port: u16,
    serial: u32,
    slave_id: u8,
    interval: u64,
    timeout: u64,
    once: bool,
    test: bool,
    discovery: bool,
    dry_run: bool,
    recreate: bool,
    compare_log: PathBuf,
      no_compare_log: bool,
      backoff_max: u64,
      sdm_entity: String,
      no_sdm_wake: bool,
  }

fn parse_args(argv: &[String]) -> Result<Option<Options>, String> {
    let mut o = Options {
        host: HOST_DEFAULT.to_string(),
        port: PORT_DEFAULT,
        serial: SERIAL_DEFAULT,
        slave_id: SLAVE_DEFAULT,
        interval: INTERVAL_DEFAULT,
        timeout: TIMEOUT_DEFAULT,
        once: false,
        test: false,
        discovery: false,
        dry_run: false,
        recreate: false,
        compare_log: compare_log::default_path(),
        no_compare_log: false,
        backoff_max: BACKOFF_MAX_DEFAULT,
        sdm_entity: SDM_ENTITY_DEFAULT.to_string(),
        no_sdm_wake: false,
        };
    let mut i = 0;
    while i < argv.len() {
        match argv[i].as_str() {
            "--host" => {
                i += 1;
                o.host = argv.get(i).ok_or("--host needs a value")?.clone();
            }
            "--port" => {
                i += 1;
                o.port = argv
                    .get(i)
                    .ok_or("--port needs a number")?
                    .parse()
                    .map_err(|_| "--port must be a number")?;
            }
            "--serial" => {
                i += 1;
                o.serial = argv
                    .get(i)
                    .ok_or("--serial needs a number")?
                    .parse()
                    .map_err(|_| "--serial must be a number")?;
            }
            "--slave-id" => {
                i += 1;
                o.slave_id = argv
                    .get(i)
                    .ok_or("--slave-id needs a number")?
                    .parse()
                    .map_err(|_| "--slave-id must be a number")?;
            }
            "--interval" => {
                i += 1;
                o.interval = argv
                    .get(i)
                    .ok_or("--interval needs a number")?
                    .parse()
                    .map_err(|_| "--interval must be a number")?;
            }
            "--timeout" => {
                i += 1;
                o.timeout = argv
                    .get(i)
                    .ok_or("--timeout needs a number")?
                    .parse()
                    .map_err(|_| "--timeout must be a number")?;
            }
            "--log-csv" => {
                i += 1;
                o.compare_log = PathBuf::from(argv.get(i).ok_or("--log-csv needs a path")?);
            }
            "--no-compare-log" => o.no_compare_log = true,
            "--once" => o.once = true,
            "--test" => o.test = true,
            "--discovery" => o.discovery = true,
            "--dry-run" => o.dry_run = true,
            "--recreate" => o.recreate = true,
            "--backoff-max" => {
                i += 1;
                o.backoff_max = argv
                    .get(i)
                    .ok_or("--backoff-max needs a number")?
                    .parse()
                    .map_err(|_| "--backoff-max must be a number")?;
            }
            "--sdm-entity" => {
                i += 1;
                o.sdm_entity = argv
                    .get(i)
                    .ok_or("--sdm-entity needs an entity id")?
                    .clone();
            }
            // The wake-up probe can be switched off where there is no garage meter.
            "--no-sdm-wake" => o.no_sdm_wake = true,
            "-h" | "--help" => {
                println!(
                    "deyepv - publish the garage PV into Home Assistant\n\n\
                     USAGE:\n    deyepv [--once] [--test] [--discovery] [--dry-run] [--recreate]\n\
                     \n\
                     TEST OPTIONS:\n    --host H --port P --serial N --slave-id N \
                     --interval S --timeout S\n    --log-csv PATH --no-compare-log\n\n\
                     RETRY BEHAVIOUR:\n    --backoff-max S      ceiling while the logger is \
                     away (default 900)\n    --sdm-entity ID      counter that wakes the \
                     poller (default sensor.sdm630_total_kwh)\n    --no-sdm-wake        never \
                     wake early, just back off\n\n\
                     Credentials come from HASS_URL / HASS_TOKEN or ~/.hermes/.env."
                );
                return Ok(None);
            }
            other => return Err(format!("unknown argument '{}'", other)),
        }
        i += 1;
    }
    Ok(Some(o))
}

/// The wait before the next attempt. While the logger answers, the configured interval;
/// while it does not, doubling from the interval up to the ceiling.
fn next_wait_s(interval: u64, failures: u32, backoff_max: u64) -> u64 {
      if failures == 0 {
          return interval;
      }
      let shift = (failures - 1).min(16);
      interval
          .saturating_mul(1u64 << shift)
          .min(backoff_max.max(interval))
  }

  /// Is a failure worth a line? The first of a streak, then every eighth step: a whole night
  /// of an absent logger has to fit into a handful of lines, while the start of the trouble,
  /// the steps of the schedule and every recovery stay readable.
  fn should_log_failure(failures: u32) -> bool {
      failures <= 1 || failures % 8 == 0
  }

  /// The retained availability message only has to go out when the state *changes* - it used
  /// to be published on every failing attempt, which was 2600 messages to Home Assistant a
  /// night for the same "offline".
  fn should_publish_offline(failures: u32) -> bool {
      failures == 1
  }

  /// May the garage meter be consulted right now? Only while the logger is actually being
/// backed off, only once per backoff period (so a logger that stays dead in daylight cannot
/// be hammered by the probe itself), and never on the plain interval.
fn should_probe_sdm(failing: bool, snoozed: bool, wait_s: u64, interval_s: u64, due: bool) -> bool {
    failing && !snoozed && wait_s > interval_s && due
}

/// Did the watched counter move? Only a change means energy actually flowed in the garage
  /// branch, so an unchanged counter is exactly the "nothing can be produced anyway" state
  /// in which the poller may stay quiet.
  fn counter_moved(seen: &Option<String>, now: &str) -> bool {
      matches!(seen, Some(prev) if prev != now)
  }

  struct Poller {
      options: Options,
      client: V5Client,
      ha: HaClient,
      /// Availability is retained, so it only has to be (re)published on recovery.
      last_ok: Option<bool>,
      /// Consecutive failed attempts - drives the backoff schedule.
      failures: u32,
      /// The garage meter's counter as last seen; `None` until the first look.
      sdm_seen: Option<String>,
  }

impl Poller {
    fn new(options: Options) -> Result<Poller, String> {
        let timeout = Duration::from_secs(options.timeout);
        let client = V5Client::new(
            &options.host,
            options.port,
            options.serial,
            options.slave_id,
            timeout,
        );
        let ha = HaClient::from_env(Duration::from_secs(20))?;
        Ok(Poller {
            options,
            client,
            ha,
            last_ok: None,
            failures: 0,
            sdm_seen: None,
        })
    }

    fn next_wait_s(&self) -> u64 {
        next_wait_s(self.options.interval, self.failures, self.options.backoff_max)
    }

    /// One cheap read of the garage meter through Home Assistant; true when the counter moved
    /// since the last look. Never an error path: if HA cannot answer, there is no reason to
    /// wake up early and the poller simply keeps quiet.
    fn sdm_moved(&mut self) -> bool {
        if self.options.no_sdm_wake {
            return false;
        }
        let state = match self.ha.get_state(&self.options.sdm_entity) {
            Ok(v) => match v.get("state") {
                Some(crate::json::J::Str(s)) => s.clone(),
                _ => return false,
            },
            Err(_) => return false,
        };
        let moved = counter_moved(&self.sdm_seen, &state);
        self.sdm_seen = Some(state);
        moved
    }

    fn read(&mut self) -> Result<Reading, V5Error> {
        let regs = self.client.read_holding_registers(0x0000, deye::MAP_SIZE)?;
        deye::decode(&regs).map_err(V5Error::Protocol)
    }

    /// One read, published. Returns true on success.
    fn push_once(&mut self) -> bool {
        let attempt = self.read();
        let reading = match attempt {
            Ok(r) => r,
            Err(e) => {
                // a failed read leaves a half-read socket behind, drop it
                self.client.disconnect();
                self.failures += 1;
                if should_log_failure(self.failures) {
                    logging::line(&format!(
                        "{}  ERROR {}: {} (attempt {}, next try in {} s)",
                        logging::hms(),
                        match e {
                            V5Error::Modbus(_) => "ModbusException",
                            V5Error::Protocol(_) => "ProtocolError",
                            V5Error::Io(_) => "ConnectionError",
                        },
                        e,
                        self.failures,
                        self.next_wait_s()
                    ));
                }
                if should_publish_offline(self.failures) {
                    let _ = self
                        .ha
                        .publish(discovery::AVAILABILITY_TOPIC, "offline", true);
                }
                self.last_ok = Some(false);
                return false;
            }
        };

        let payload = reading.to_json().to_json();
        if let Err(e) = self.ha.publish(discovery::STATE_TOPIC, &payload, true) {
            self.failures += 1;
            if should_log_failure(self.failures) {
                logging::line(&format!(
                    "{}  ERROR publish: {} (attempt {})",
                    logging::hms(),
                    e,
                    self.failures
                ));
            }
            if should_publish_offline(self.failures) {
                let _ = self
                    .ha
                    .publish(discovery::AVAILABILITY_TOPIC, "offline", true);
            }
            self.last_ok = Some(false);
            return false;
        }

        // A good poll ends the streak: the next wait is the plain interval again, and the
        // recovery line says how many attempts the outage cost - one line per episode instead
        // of one per attempt.
        let failed_attempts = self.failures;
        self.failures = 0;
        if self.last_ok != Some(true) {
            let _ = self
                .ha
                .publish(discovery::AVAILABILITY_TOPIC, "online", true);
            if failed_attempts > 0 {
                logging::line(&format!(
                    "availability -> online (retained) after {} failed attempt(s)",
                    failed_attempts
                ));
            } else {
                logging::line("availability -> online (retained)");
            }
            self.last_ok = Some(true);
        }

        logging::line(&deye::summary_line(&logging::hms(), &reading));

        if !self.options.no_compare_log {
            compare_log::append(&self.options.compare_log, &reading, &self.ha);
        }
        true
    }

    /// --test and --dry-run never publish discovery; this is the plain check.
    fn selftest(&mut self) -> Result<(), String> {
        let reading = self.read().map_err(|e| e.to_string())?;
        let code = self
            .ha
            .publish(SELFTEST_TOPIC, &reading.to_json().to_json(), false)?;
        logging::plain(&format!("publish to {} -> HTTP {}", SELFTEST_TOPIC, code));
        Ok(())
    }

    fn publish_discovery(&mut self, cleanup: bool) {
        for entity in discovery::ENTITIES.iter() {
            let topic = discovery::config_topic(entity);
            if cleanup {
                // an EMPTY retained payload is how MQTT discovery deletes an entity
                match self.ha.publish(&topic, "", true) {
                    Ok(code) => logging::line(&format!("  cleanup   {:<58} -> {}", topic, code)),
                    Err(e) => logging::line(&format!("  cleanup   {:<58} -> {}", topic, e)),
                }
                continue;
            }
            let payload = discovery::config_payload(entity);
            match self.ha.publish(&topic, &payload, true) {
                Ok(code) => logging::line(&format!(
                    "  discovery {:<58} -> {} (entity_id {}.{})",
                    topic, code, entity.component, entity.object_id
                )),
                Err(e) => logging::line(&format!("  discovery {:<58} -> {}", topic, e)),
            }
        }
        match self
            .ha
            .publish(discovery::AVAILABILITY_TOPIC, "online", true)
        {
            Ok(code) => logging::line(&format!("  availability -> {}", code)),
            Err(e) => logging::line(&format!("  availability -> {}", e)),
        }
    }
}

/// The discovery payloads, printed. Needs no Home Assistant and no credentials,
/// which is what makes `--dry-run` usable on a machine that has no token.
fn print_discovery() {
    for entity in discovery::ENTITIES.iter() {
        logging::line(&format!(
            "  [dry-run] {} -> {}\n            {}",
            discovery::config_topic(entity),
            entity.object_id,
            discovery::config_payload(entity)
        ));
    }
    logging::line(&format!(
        "  [dry-run] {} -> online (retained)",
        discovery::AVAILABILITY_TOPIC
    ));
}

fn run() -> Result<(), String> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let options = match parse_args(&argv)? {
        Some(o) => o,
        None => return Ok(()),
    };

    if options.dry_run {
        logging::plain("discovery configs (dry-run, nothing published):");
        print_discovery();
        return Ok(());
    }

    let mut poller = Poller::new(options.clone())?;

    if options.test {
        return poller.selftest();
    }

    if options.discovery {
        logging::plain("discovery configs (retained):");
        poller.publish_discovery(false);
        return Ok(());
    }

    if options.recreate {
        // entity_id is fixed at creation, so changing object_id needs the old
        // entities deleted first: empty retained config, wait, then re-create
        logging::plain("step 1: deleting the existing entities (empty retained configs):");
        poller.publish_discovery(true);
        logging::plain("waiting 5 s for HA to remove them...");
        std::thread::sleep(Duration::from_secs(5));
        logging::plain("step 2: re-creating with explicit object_ids:");
        poller.publish_discovery(false);
        logging::plain("step 3: first state push");
    }

    install_signal_handlers();

    // One early wake-up attempt per backoff period: at sunrise the meter starts moving and
    // the poller tries again at once, while a logger that stays dead in daylight cannot be
    // hammered by its own wake-up probe.
    let mut sdm_snoozed = false;

    loop {
        let ok = poller.push_once();
        if options.once {
            return Ok(());
        }
        // wait out the interval, in slices so a stop signal is noticed promptly.
        // This compares timestamps: an earlier version counted 250 ms slices
        // against the interval in seconds, which polled four times too fast.
        let wait = poller.next_wait_s();
        let deadline = Instant::now() + Duration::from_secs(wait);
        let mut next_sdm_check = Instant::now() + Duration::from_secs(SDM_CHECK_S);
        let mut woke = false;
        while Instant::now() < deadline && !STOP.load(Ordering::SeqCst) {
            let remaining = deadline.saturating_duration_since(Instant::now());
            std::thread::sleep(remaining.min(Duration::from_millis(250)));
            if STOP.load(Ordering::SeqCst) {
                break;
            }
            // The logger is away. The garage meter is then the only useful signal: as soon as
            // energy flows there - the inverter exporting, or the car charging - try again
            // instead of waiting out the rest of the backoff.
            if should_probe_sdm(!ok, sdm_snoozed, wait, options.interval,
                                Instant::now() >= next_sdm_check)
            {
                next_sdm_check = Instant::now() + Duration::from_secs(SDM_CHECK_S);
                if poller.sdm_moved() {
                    logging::line(
                        "activity in the garage branch - polling the logger again now",
                    );
                    woke = true;
                    break;
                }
            }
        }
        if STOP.load(Ordering::SeqCst) {
            logging::line("stopping on signal");
            return Ok(());
        }
        sdm_snoozed = woke;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_backoff_doubles_from_the_interval_and_stops_at_the_ceiling() {
        let seq: Vec<u64> = (0..9).map(|f| next_wait_s(30, f, 900)).collect();
        assert_eq!(seq, vec![30, 30, 60, 120, 240, 480, 900, 900, 900]);
    }

    #[test]
    fn a_ceiling_below_the_interval_cannot_shorten_the_interval() {
        assert_eq!(next_wait_s(30, 3, 10), 30);
    }

    #[test]
    fn a_night_of_an_absent_logger_costs_a_handful_of_lines() {
        // 8 h at 30 s is 960 attempts; at the ceiling it is ~32, and only a few are logged.
        let logged = (1..=32).filter(|f| should_log_failure(*f)).count();
        assert!(logged <= 6, "{} lines for 32 attempts", logged);
        assert!(should_log_failure(1));
        assert!(!should_log_failure(2) && !should_log_failure(7));
        assert!(should_log_failure(8));
    }

    #[test]
    fn availability_is_published_once_per_outage() {
        assert!(should_publish_offline(1));
        assert!(!should_publish_offline(2));
        assert!(!should_publish_offline(7));
    }

    #[test]
    fn only_a_moved_counter_counts_as_activity_in_the_garage() {
        assert!(!counter_moved(&None, "11826.81")); // no baseline yet
        assert!(!counter_moved(&Some("11826.81".into()), "11826.81"));
        assert!(counter_moved(&Some("11826.81".into()), "11830.42"));
        assert!(counter_moved(&Some("11826.81".into()), "11826.80")); // export side
    }

    #[test]
    fn the_meter_probe_runs_only_while_the_logger_is_backed_off() {
        // healthy poll -> never probe the meter
        assert!(!should_probe_sdm(false, false, 30, 30, true));
        // failing, but still on the plain interval -> nothing to shorten
        assert!(!should_probe_sdm(true, false, 30, 30, true));
        // backing off and due -> probe
        assert!(should_probe_sdm(true, false, 120, 30, true));
        // not due yet -> wait
        assert!(!should_probe_sdm(true, false, 120, 30, false));
        // already woke once in this period -> stay quiet, no hammering
        assert!(!should_probe_sdm(true, true, 900, 30, true));
    }

    #[test]
    fn the_defaults_are_the_ones_the_plant_needs() {
        assert_eq!(BACKOFF_MAX_DEFAULT, 900);
        assert_eq!(SDM_ENTITY_DEFAULT, "sensor.sdm630_total_kwh");
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("deyepv: {}", e);
            ExitCode::from(2)
        }
    }
}

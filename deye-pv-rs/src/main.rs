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
            "-h" | "--help" => {
                println!(
                    "deyepv - publish the garage PV into Home Assistant\n\n\
                     USAGE:\n    deyepv [--once] [--test] [--discovery] [--dry-run] [--recreate]\n\
                     \n\
                     TEST OPTIONS:\n    --host H --port P --serial N --slave-id N \
                     --interval S --timeout S\n    --log-csv PATH --no-compare-log\n\n\
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

struct Poller {
    options: Options,
    client: V5Client,
    ha: HaClient,
    /// Availability is retained, so it only has to be (re)published on recovery.
    last_ok: Option<bool>,
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
        })
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
                logging::line(&format!(
                    "{}  ERROR {}: {}",
                    logging::hms(),
                    match e {
                        V5Error::Modbus(_) => "ModbusException",
                        V5Error::Protocol(_) => "ProtocolError",
                        V5Error::Io(_) => "ConnectionError",
                    },
                    e
                ));
                let _ = self
                    .ha
                    .publish(discovery::AVAILABILITY_TOPIC, "offline", true);
                self.last_ok = Some(false);
                return false;
            }
        };

        let payload = reading.to_json().to_json();
        if let Err(e) = self.ha.publish(discovery::STATE_TOPIC, &payload, true) {
            logging::line(&format!("{}  ERROR publish: {}", logging::hms(), e));
            let _ = self
                .ha
                .publish(discovery::AVAILABILITY_TOPIC, "offline", true);
            self.last_ok = Some(false);
            return false;
        }

        if self.last_ok != Some(true) {
            let _ = self
                .ha
                .publish(discovery::AVAILABILITY_TOPIC, "online", true);
            logging::line("availability -> online (retained)");
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

    loop {
        poller.push_once();
        if options.once {
            return Ok(());
        }
        // wait out the interval, in slices so a stop signal is noticed promptly.
        // This compares timestamps: an earlier version counted 250 ms slices
        // against the interval in seconds, which polled four times too fast.
        let deadline = Instant::now() + Duration::from_secs(options.interval);
        while Instant::now() < deadline && !STOP.load(Ordering::SeqCst) {
            let remaining = deadline.saturating_duration_since(Instant::now());
            std::thread::sleep(remaining.min(Duration::from_millis(250)));
        }
        if STOP.load(Ordering::SeqCst) {
            logging::line("stopping on signal");
            return Ok(());
        }
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

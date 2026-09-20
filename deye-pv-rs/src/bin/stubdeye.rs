//! A SolarMAN V5 stub logger, so the poller can be tested end to end without
//! touching the real inverter.
//!
//! It answers FC3 with a deterministic register block: `reg[i] = i`, with the
//! interesting registers set from the command line. Anything it sends back goes
//! through the same framing the real logger uses, so the poller's decoder is
//! genuinely exercised - requests with a bad checksum or a wrong sequence are
//! rejected here too.
//!
//! Usage:
//!   stubdeye [--port N] [--serial N] [--set ADDR=VAL]... [--corrupt-checksum]
//!            [--wrong-sequence] [--garbage] [--drop-once] [--quiet]

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

const V5_START: u8 = 0xA5;
const V5_END: u8 = 0x15;
const CONTROL_REQUEST: u16 = 0x4510;
const CONTROL_RESPONSE: u16 = 0x1510;
/// Requests carry the RTU at 26, responses at 25 - measured, see src/v5.rs.
const REQUEST_RTU_OFFSET: usize = 26;
/// The response prefix is one byte shorter than the request's.
const RESPONSE_RTU_OFFSET: usize = 25;
const MAP_SIZE: u16 = 125;

fn crc16(data: &[u8]) -> u16 {
    let mut crc: u16 = 0xFFFF;
    for byte in data {
        crc ^= *byte as u16;
        for _ in 0..8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    crc
}

struct Device {
    serial: u32,
    overrides: Mutex<BTreeMap<u16, u16>>,
    requests: AtomicU64,
    connections: AtomicU64,
    corrupt_checksum: bool,
    wrong_sequence: bool,
    garbage: bool,
    drop_once: AtomicU64,
    /// 0 = still to send one spurious keep-alive, 1 = done
    junk_once: AtomicU64,
    quiet: bool,
}

impl Device {
    fn register(&self, address: u16) -> u16 {
        if let Some(v) = self.overrides.lock().unwrap().get(&address) {
            return *v;
        }
        address
    }
}

fn wrap(device: &Device, sequence: u8, rtu: &[u8]) -> Vec<u8> {
    let mut f = vec![V5_START];
    // the device answers with the shorter prefix: 14 + len(rtu), not 15 + len
    f.extend_from_slice(&((14 + rtu.len()) as u16).to_le_bytes());
    f.extend_from_slice(&CONTROL_RESPONSE.to_le_bytes());
    f.push(sequence);
    f.push(0);
    f.extend_from_slice(&device.serial.to_le_bytes());
    f.push(0x02);
    f.resize(RESPONSE_RTU_OFFSET, 0); // sensor type and the three clocks
    assert_eq!(f.len(), RESPONSE_RTU_OFFSET);
    f.extend_from_slice(rtu);
    let sum: u32 = f[1..].iter().map(|b| *b as u32).sum();
    let mut checksum = (sum & 0xFF) as u8;
    if device.corrupt_checksum {
        checksum ^= 0xFF;
    }
    f.push(checksum);
    f.push(V5_END);
    f
}

fn serve(mut stream: TcpStream, device: Arc<Device>) {
    device.connections.fetch_add(1, Ordering::Relaxed);
    if !device.quiet {
        println!(
            "CONNECTION total={}",
            device.connections.load(Ordering::Relaxed)
        );
    }
    let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(30)));

    let mut traffic_logged = false;
    loop {
        let mut head = [0u8; 3];
        if stream.read_exact(&mut head).is_err() {
            return;
        }
        if head[0] != V5_START {
            if !device.quiet {
                println!("REJECT bad start byte 0x{:02x}", head[0]);
            }
            return;
        }
        let payload_len = u16::from_le_bytes([head[1], head[2]]) as usize;
        if !(15..=600).contains(&payload_len) {
            return;
        }
        let mut rest = vec![0u8; 13 + payload_len - 3];
        if stream.read_exact(&mut rest).is_err() {
            return;
        }
        let mut frame = head.to_vec();
        frame.extend_from_slice(&rest);

        let sum: u32 = frame[1..frame.len() - 2].iter().map(|b| *b as u32).sum();
        if frame[frame.len() - 2] != (sum & 0xFF) as u8 {
            if !device.quiet {
                println!("REJECT bad request checksum");
            }
            return;
        }
        let sequence = frame[5];
        // a real logger only answers 0x4510 requests; anything else is a bug here
        if u16::from_le_bytes([frame[3], frame[4]]) != CONTROL_REQUEST {
            if !device.quiet {
                println!("REJECT wrong control code");
            }
            return;
        }
        let rtu = &frame[REQUEST_RTU_OFFSET..frame.len() - 2];
        if rtu.len() < 5 {
            return;
        }
        let n = device.requests.fetch_add(1, Ordering::Relaxed) + 1;
        if !traffic_logged {
            traffic_logged = true;
            if !device.quiet {
                println!("TRAFFIC (first request on this connection)");
            }
        }
        if !device.quiet {
            println!(
                "REQUEST n={} fc={} slave={} rtu={}",
                n,
                if rtu.len() > 1 { rtu[1] } else { 0 },
                rtu[0],
                rtu.iter()
                    .map(|b| format!("{:02x}", b))
                    .collect::<Vec<_>>()
                    .join("")
            );
        }

        if device.drop_once.load(Ordering::Relaxed) == 0 {
            device.drop_once.store(1, Ordering::Relaxed);
            if !device.quiet {
                println!("DROPPING the connection (drop-once)");
            }
            return;
        }

        if device.junk_once.load(Ordering::Relaxed) == 0 {
            device.junk_once.store(1, Ordering::Relaxed);
            // the spurious keep-alive this logger really sends: 14 bytes, control
            // code 0x4710. It arrives before (or instead of) the real reply.
            let mut junk = vec![V5_START, 0x01, 0x00, 0x10, 0x47, sequence, 0x00, 0x00];
            junk.extend_from_slice(&device.serial.to_le_bytes());
            junk.push(0x00); // checksum placeholder
            junk.push(V5_END);
            let sum: u32 = junk[1..junk.len() - 2].iter().map(|b| *b as u32).sum();
            let last = junk.len() - 2;
            junk[last] = (sum & 0xFF) as u8;
            if !device.quiet {
                println!(
                    "SENDING keep-alive ({} bytes): {}",
                    junk.len(),
                    junk.iter()
                        .map(|b| format!("{:02x}", b))
                        .collect::<Vec<_>>()
                        .join("")
                );
            }
            if stream.write_all(&junk).is_err() {
                return;
            }
            let _ = stream.flush();
        }

        if device.garbage {
            let _ = stream.write_all(&[0xFF; 32]);
            let _ = stream.flush();
            return;
        }

        // only FC3 is implemented, like the real map
        let address = u16::from_be_bytes([rtu[2], rtu[3]]);
        let count = u16::from_be_bytes([rtu[4], rtu[5]]);
        let response = if rtu[1] != 0x03 {
            vec![rtu[0], rtu[1] | 0x80, 0x01]
        } else if count == 0
            || count > MAP_SIZE
            || address as usize + count as usize > MAP_SIZE as usize + 1
        {
            vec![rtu[0], 0x83, 0x02]
        } else {
            let mut r = vec![rtu[0], 0x03, (count * 2) as u8];
            for i in 0..count {
                r.extend_from_slice(&device.register(address + i).to_be_bytes());
            }
            r
        };
        let crc = crc16(&response);
        let mut rtu_response = response.clone();
        rtu_response.extend_from_slice(&crc.to_le_bytes());

        let seq = if device.wrong_sequence {
            sequence.wrapping_add(7)
        } else {
            sequence
        };
        let out = wrap(&device, seq, &rtu_response);
        if stream.write_all(&out).is_err() {
            return;
        }
        let _ = stream.flush();
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut port: u16 = 18899;
    let mut serial: u32 = 3842831288;
    let mut overrides: BTreeMap<u16, u16> = BTreeMap::new();
    let mut corrupt_checksum = false;
    let mut wrong_sequence = false;
    let mut garbage = false;
    let mut drop_once = false;
    let mut junk_once = false;
    let mut quiet = false;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--port" => {
                i += 1;
                port = args.get(i).and_then(|s| s.parse().ok()).unwrap_or(port);
            }
            "--serial" => {
                i += 1;
                serial = args.get(i).and_then(|s| s.parse().ok()).unwrap_or(serial);
            }
            "--set" => {
                i += 1;
                if let Some(spec) = args.get(i) {
                    if let Some((a, v)) = spec.split_once('=') {
                        if let (Ok(a), Ok(v)) = (
                            u16::from_str_radix(a.trim_start_matches("0x"), 16),
                            v.parse::<u16>(),
                        ) {
                            overrides.insert(a, v);
                        }
                    }
                }
            }
            "--corrupt-checksum" => corrupt_checksum = true,
            "--wrong-sequence" => wrong_sequence = true,
            "--garbage" => garbage = true,
            "--drop-once" => drop_once = true,
            "--junk-once" => junk_once = true,
            "--quiet" => quiet = true,
            "-h" | "--help" => {
                println!("stubdeye [--port N] [--serial N] [--set 0x56=2535]... ");
                return;
            }
            _ => {}
        }
        i += 1;
    }

    let device = Arc::new(Device {
        serial,
        overrides: Mutex::new(overrides),
        requests: AtomicU64::new(0),
        connections: AtomicU64::new(0),
        corrupt_checksum,
        wrong_sequence,
        garbage,
        drop_once: AtomicU64::new(if drop_once { 0 } else { 1 }),
        junk_once: AtomicU64::new(if junk_once { 0 } else { 1 }),
        quiet,
    });

    let listener = TcpListener::bind(("127.0.0.1", port)).expect("bind stub port");
    if !quiet {
        println!(
            "stub logger listening on 127.0.0.1:{} (serial {})",
            port, serial
        );
    }
    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                let d = device.clone();
                std::thread::spawn(move || serve(s, d));
            }
            Err(e) => {
                eprintln!("stub accept: {}", e);
                return;
            }
        }
    }
}

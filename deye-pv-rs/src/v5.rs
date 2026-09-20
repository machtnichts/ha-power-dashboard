//! SolarMAN V5 (SolarMAN/IGEN-Tech data logger) protocol, and the Modbus RTU
//! frame it carries.
//!
//! Reimplemented from the wire format that `pysolarmanv5` 3.0.6 produces, because
//! that library is exactly the dependency this port is meant to remove. The frame
//! layout is fixed and small, so there is nothing here that needs a crate:
//!
//! ```text
//!   0     A5                      start
//!   1-2   length, u16 LE          = 15 + len(modbus rtu frame)
//!   3-4   control code u16 LE     0x4510 request / 0x1510 response
//!   5     sequence, u8            echoed back by the device
//!   6     zero
//!   7-10  logger serial, u32 LE
//!   11    frame type 0x02
//!   12-13 sensor type
//!   14-17 delivery time
//!   18-21 power-on time
//!   22-25 offset time
//!   26..  modbus RTU frame (slave id, PDU, CRC16 LE)
//!   -2    checksum = sum(frame[1 .. len-2]) & 0xFF
//!   -1    15                      end
//! ```
//!
//! The payload is not encrypted on this logger, which the first real read
//! confirmed.

use std::io::{ErrorKind, Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant};

use crate::logging;

pub const V5_START: u8 = 0xA5;
pub const V5_END: u8 = 0x15;
pub const CONTROL_REQUEST: u16 = 0x4510;
pub const CONTROL_RESPONSE: u16 = 0x1510;
/// Bytes in a frame that are not covered by the length field.
const FRAME_OVERHEAD: usize = 13;
/// Offset of the Modbus RTU frame inside a *request*.
const REQUEST_RTU_OFFSET: usize = 26;
/// Offset inside a *response*, which is one byte shorter - measured, not assumed:
/// the reference library's decoder uses 25 and only a frame built that way parses
/// back into correct register values (tools/reference_response.py). A request
/// built with the RTU at 25 is not what the device answers.
const RESPONSE_RTU_OFFSET: usize = 25;

#[derive(Debug)]
pub enum V5Error {
    /// The device answered with a Modbus exception; the code is the last byte.
    Modbus(u8),
    /// Framing, checksum or a field that did not match - the connection is suspect.
    Protocol(String),
    Io(String),
}

impl std::fmt::Display for V5Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            V5Error::Modbus(code) => write!(f, "modbus exception 0x{:02x}", code),
            V5Error::Protocol(m) => write!(f, "protocol error: {}", m),
            V5Error::Io(m) => write!(f, "io error: {}", m),
        }
    }
}

/// Standard Modbus CRC16 (poly 0xA001 reflected, init 0xFFFF).
pub fn crc16_modbus(data: &[u8]) -> u16 {
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

/// The Python library strips a trailing 0000 when that makes the CRC valid
/// ("double CRC" quirk on some loggers). Kept for parity.
fn strip_double_crc(rtu: &[u8]) -> Vec<u8> {
    if rtu.len() > 4 && rtu.ends_with(&[0x00, 0x00]) {
        let stripped = &rtu[..rtu.len() - 2];
        let declared =
            u16::from_le_bytes([stripped[stripped.len() - 2], stripped[stripped.len() - 1]]);
        if crc16_modbus(&stripped[..stripped.len() - 2]) == declared {
            return stripped.to_vec();
        }
    }
    rtu.to_vec()
}

/// The spurious keep-alive the logger sends carries this control code.
const CONTROL_KEEPALIVE: u16 = 0x4710;

/// Does this frame announce itself as a keep-alive rather than a reply?
fn is_keepalive(frame: &[u8]) -> bool {
    frame.len() >= 5 && u16::from_le_bytes([frame[3], frame[4]]) == CONTROL_KEEPALIVE
}

/// A few bytes for a log line, so an unexpected frame can be identified later.
fn hex_preview(bytes: &[u8]) -> String {
    let shown = &bytes[..bytes.len().min(24)];
    let mut out: String = shown.iter().map(|b| format!("{:02x}", b)).collect();
    if bytes.len() > shown.len() {
        out.push_str("...");
    }
    out
}

/// Does this slice end in a CRC that matches its content?
fn rtu_crc_is_valid(rtu: &[u8]) -> bool {
    if rtu.len() < 4 {
        return false;
    }
    let declared = u16::from_le_bytes([rtu[rtu.len() - 2], rtu[rtu.len() - 1]]);
    crc16_modbus(&rtu[..rtu.len() - 2]) == declared
}

pub struct V5Client {
    host: String,
    port: u16,
    /// The serial the logger reports in its replies - not a Modbus address.
    serial: u32,
    slave_id: u8,
    timeout: Duration,
    stream: Option<TcpStream>,
    sequence: u8,
}

impl V5Client {
    pub fn new(host: &str, port: u16, serial: u32, slave_id: u8, timeout: Duration) -> V5Client {
        V5Client {
            host: host.to_string(),
            port,
            serial,
            slave_id,
            timeout,
            stream: None,
            sequence: random_sequence(),
        }
    }

    #[allow(dead_code)] // asserted by the unit tests, not needed at runtime
    pub fn is_connected(&self) -> bool {
        self.stream.is_some()
    }

    /// Drop the socket. The Python version does the same on any error, so the
    /// next poll starts from a clean connection instead of a half-read stream.
    pub fn disconnect(&mut self) {
        self.stream = None;
    }

    fn connect(&mut self) -> Result<(), V5Error> {
        let addr = format!("{}:{}", self.host, self.port);
        let stream = TcpStream::connect(&addr)
            .map_err(|e| V5Error::Io(format!("connect {}: {}", addr, e)))?;
        stream
            .set_read_timeout(Some(self.timeout))
            .map_err(|e| V5Error::Io(e.to_string()))?;
        stream
            .set_write_timeout(Some(self.timeout))
            .map_err(|e| V5Error::Io(e.to_string()))?;
        let _ = stream.set_nodelay(true);
        self.stream = Some(stream);
        Ok(())
    }

    fn encode(&self, sequence: u8, rtu: &[u8]) -> Vec<u8> {
        let mut f = Vec::with_capacity(REQUEST_RTU_OFFSET + rtu.len() + 3);
        f.push(V5_START);
        f.extend_from_slice(&((15 + rtu.len()) as u16).to_le_bytes());
        f.extend_from_slice(&CONTROL_REQUEST.to_le_bytes());
        f.push(sequence);
        f.push(0);
        f.extend_from_slice(&self.serial.to_le_bytes());
        f.push(0x02);
        f.extend_from_slice(&[0, 0]); // sensor type
        f.extend_from_slice(&[0, 0, 0, 0]); // delivery time
        f.extend_from_slice(&[0, 0, 0, 0]); // power on time
        f.extend_from_slice(&[0, 0, 0, 0]); // offset time
        f.extend_from_slice(rtu);
        let checksum: u32 = f[1..].iter().map(|b| *b as u32).sum();
        f.push((checksum & 0xFF) as u8);
        f.push(V5_END);
        f
    }

    fn decode(&self, sequence: u8, frame: &[u8]) -> Result<Vec<u8>, V5Error> {
        if frame.len() < RESPONSE_RTU_OFFSET + 3 {
            return Err(V5Error::Protocol(format!(
                "frame too short: {} bytes",
                frame.len()
            )));
        }
        let payload_len = u16::from_le_bytes([frame[1], frame[2]]) as usize;
        if frame.len() != FRAME_OVERHEAD + payload_len {
            return Err(V5Error::Protocol(format!(
                "length field says {} bytes, got {}",
                FRAME_OVERHEAD + payload_len,
                frame.len()
            )));
        }
        if frame[0] != V5_START || frame[frame.len() - 1] != V5_END {
            return Err(V5Error::Protocol("bad start/end byte".into()));
        }
        let declared_checksum = frame[frame.len() - 2];
        let sum: u32 = frame[1..frame.len() - 2].iter().map(|b| *b as u32).sum();
        if declared_checksum != (sum & 0xFF) as u8 {
            return Err(V5Error::Protocol(format!(
                "bad checksum: frame says 0x{:02x}, computed 0x{:02x}",
                declared_checksum,
                sum & 0xFF
            )));
        }
        if frame[5] != sequence {
            return Err(V5Error::Protocol(format!(
                "sequence mismatch: sent {}, got {}",
                sequence, frame[5]
            )));
        }
        if frame[7..11] != self.serial.to_le_bytes() {
            return Err(V5Error::Protocol("logger serial mismatch in reply".into()));
        }
        if u16::from_le_bytes([frame[3], frame[4]]) != CONTROL_RESPONSE {
            return Err(V5Error::Protocol("not a 0x1510 reply (keep-alive?)".into()));
        }
        if frame[11] != 0x02 {
            return Err(V5Error::Protocol("bad frame type".into()));
        }

        let rtu = self.extract_rtu(frame);
        if rtu.len() < 5 {
            if let Some(code) = rtu.first() {
                return Err(V5Error::Modbus(*code));
            }
            return Err(V5Error::Protocol("no usable Modbus RTU frame".into()));
        }
        Ok(rtu)
    }

    /// Pull the Modbus RTU frame out of a response.
    ///
    /// The response prefix is one byte shorter than the request's, so the measured
    /// offset (25) is tried first. The CRC then decides: a slice at the wrong
    /// offset will not validate, and if the 25-layout somehow does not, the
    /// request layout is tried before giving up on the measured one - so a logger
    /// variant with a longer prefix still works instead of silently decoding
    /// garbage.
    fn extract_rtu(&self, frame: &[u8]) -> Vec<u8> {
        let end = frame.len() - 2;
        let slice_at = |offset: usize| -> Option<Vec<u8>> {
            if end < offset + 5 {
                return None;
            }
            Some(strip_double_crc(&frame[offset..end]))
        };
        for offset in [RESPONSE_RTU_OFFSET, REQUEST_RTU_OFFSET] {
            if let Some(rtu) = slice_at(offset) {
                if rtu_crc_is_valid(&rtu) {
                    return rtu;
                }
            }
        }
        slice_at(RESPONSE_RTU_OFFSET).unwrap_or_default()
    }

    fn exchange(&mut self, rtu: &[u8]) -> Result<Vec<u8>, V5Error> {
        if self.stream.is_none() {
            self.connect()?;
        }
        let sequence = self.sequence;
        let frame = self.encode(sequence, rtu);

        {
            let stream = self.stream.as_mut().expect("connected above");
            stream
                .write_all(&frame)
                .and_then(|_| stream.flush())
                .map_err(|e| V5Error::Io(format!("write: {}", e)))?;
        }

        // The logger sometimes emits a spurious keep-alive (control code 0x4710)
        // before or instead of a reply. The reference library treats that as a
        // fatal error and reconnects, which costs the whole poll and shows up in
        // Home Assistant as an 'offline' blip - this logger does it every few
        // minutes. Here, a frame that is not our reply is skipped and the read is
        // retried until the timeout budget is gone.
        let deadline = Instant::now() + self.timeout;
        loop {
            let received = match self.read_frame() {
                Ok(frame) => frame,
                Err(e) => {
                    self.disconnect();
                    return Err(e);
                }
            };

            if is_keepalive(&received) {
                logging::line(&format!(
                    "  ignoring a keep-alive frame ({} bytes): {}",
                    received.len(),
                    hex_preview(&received)
                ));
            } else {
                match self.decode(sequence, &received) {
                    Ok(rtu) => {
                        self.sequence = self.sequence.wrapping_add(1) & 0xFF;
                        return Ok(rtu);
                    }
                    Err(V5Error::Modbus(code)) => {
                        // the device did answer, with an exception - that is a reply
                        self.disconnect();
                        return Err(V5Error::Modbus(code));
                    }
                    Err(e) => {
                        if Instant::now() >= deadline {
                            self.disconnect();
                            return Err(e);
                        }
                        logging::line(&format!(
                            "  skipping an unusable frame ({} bytes): {} ({})",
                            received.len(),
                            hex_preview(&received),
                            e
                        ));
                    }
                }
            }

            if Instant::now() >= deadline {
                self.disconnect();
                return Err(V5Error::Protocol(
                    "no usable reply before the timeout".into(),
                ));
            }
        }
    }

    /// One whole frame: the 3-byte prefix says how long the rest is.
    fn read_frame(&mut self) -> Result<Vec<u8>, V5Error> {
        let mut head = [0u8; 3];
        self.read_exact(&mut head)?;
        if head[0] != V5_START {
            return Err(V5Error::Protocol(format!(
                "first byte 0x{:02x}, not 0xA5",
                head[0]
            )));
        }
        let payload_len = u16::from_le_bytes([head[1], head[2]]) as usize;
        let mut rest = vec![0u8; FRAME_OVERHEAD + payload_len - 3];
        self.read_exact(&mut rest)?;
        let mut full = head.to_vec();
        full.extend_from_slice(&rest);
        Ok(full)
    }

    fn read_exact(&mut self, buf: &mut [u8]) -> Result<(), V5Error> {
        let stream = self.stream.as_mut().expect("connected");
        match stream.read_exact(buf) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == ErrorKind::TimedOut || e.kind() == ErrorKind::WouldBlock => {
                Err(V5Error::Io("read timed out".into()))
            }
            Err(e) => Err(V5Error::Io(format!("read: {}", e))),
        }
    }

    /// FC3 - the only request this poller makes.
    pub fn read_holding_registers(
        &mut self,
        address: u16,
        count: u16,
    ) -> Result<Vec<u16>, V5Error> {
        let mut pdu = vec![self.slave_id, 0x03];
        pdu.extend_from_slice(&address.to_be_bytes());
        pdu.extend_from_slice(&count.to_be_bytes());
        let crc = crc16_modbus(&pdu);
        pdu.extend_from_slice(&crc.to_le_bytes());

        let rtu = self.exchange(&pdu)?;
        if rtu[1] & 0x80 != 0 {
            return Err(V5Error::Modbus(*rtu.get(2).unwrap_or(&0)));
        }
        if rtu[1] != 0x03 {
            return Err(V5Error::Protocol(format!(
                "unexpected function code 0x{:02x}",
                rtu[1]
            )));
        }
        let byte_count = rtu[2] as usize;
        let data = &rtu[3..rtu.len() - 2];
        if data.len() != byte_count {
            return Err(V5Error::Protocol(format!(
                "byte count says {}, have {}",
                byte_count,
                data.len()
            )));
        }
        Ok(data
            .chunks(2)
            .map(|c| u16::from_be_bytes([c[0], c[1]]))
            .collect())
    }
}

/// A starting sequence number from the kernel, matching the library's random
/// start. Falls back to the clock if /dev/urandom is unreadable.
fn random_sequence() -> u8 {
    let mut buf = [0u8; 1];
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .is_ok()
    {
        return buf[0] | 1; // never 0
    }
    (std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(1)
        & 0xFF) as u8
        | 1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc16_matches_the_known_modbus_vector() {
        // classic check value: "123456789" -> 0x4B37
        assert_eq!(crc16_modbus(b"123456789"), 0x4B37);
    }

    #[test]
    fn a_read_request_frame_is_laid_out_correctly() {
        let c = V5Client::new("127.0.0.1", 8899, 3842831288, 1, Duration::from_secs(1));
        assert!(!c.is_connected(), "a fresh client holds no socket");
        let rtu = {
            let mut pdu = vec![1u8, 0x03];
            pdu.extend_from_slice(&0u16.to_be_bytes());
            pdu.extend_from_slice(&125u16.to_be_bytes());
            let crc = crc16_modbus(&pdu);
            pdu.extend_from_slice(&crc.to_le_bytes());
            pdu
        };
        let frame = c.encode(0x2A, &rtu);

        assert_eq!(frame[0], V5_START);
        assert_eq!(frame[1..3], (15 + rtu.len() as u16).to_le_bytes());
        assert_eq!(frame[3..5], CONTROL_REQUEST.to_le_bytes());
        assert_eq!(frame[5], 0x2A, "sequence");
        assert_eq!(
            frame[7..11],
            3842831288u32.to_le_bytes(),
            "logger serial little endian"
        );
        assert_eq!(frame[11], 0x02);
        assert_eq!(
            &frame[REQUEST_RTU_OFFSET..REQUEST_RTU_OFFSET + rtu.len()],
            &rtu[..]
        );
        assert_eq!(*frame.last().unwrap(), V5_END);
        let sum: u32 = frame[1..frame.len() - 2].iter().map(|b| *b as u32).sum();
        assert_eq!(frame[frame.len() - 2], (sum & 0xFF) as u8, "checksum");
        // frame length must match the declared length field
        assert_eq!(frame.len(), 13 + 15 + rtu.len());
    }

    #[test]
    fn decodes_a_response_and_rejects_a_wrong_sequence() {
        let serial = 3842831288u32;
        let c = V5Client::new("127.0.0.1", 8899, serial, 1, Duration::from_secs(1));
        let payload = [1u8, 0x03, 0x04, 0x00, 0x0A, 0x00, 0x14];
        let frame = response_frame_at(serial, 7, &payload, RESPONSE_RTU_OFFSET);

        let decoded = c.decode(7, &frame).expect("valid frame");
        assert_eq!(
            decoded,
            frame[RESPONSE_RTU_OFFSET..frame.len() - 2],
            "the RTU must come back exactly as sent"
        );
        assert!(
            rtu_crc_is_valid(&decoded),
            "and it must carry a valid Modbus CRC"
        );
        assert!(c.decode(8, &frame).is_err(), "sequence must be echoed");
    }

    #[test]
    fn the_request_and_response_offsets_really_do_differ() {
        // measured, not assumed: the device answers one byte shorter than it is
        // asked (tools/reference_frame.py, tools/reference_response.py)
        assert_eq!(REQUEST_RTU_OFFSET, 26);
        assert_eq!(RESPONSE_RTU_OFFSET, 25);
        let c = V5Client::new("127.0.0.1", 8899, 1, 1, Duration::from_secs(1));
        let payload = [1u8, 0x03, 0x04, 0x00, 0x2A];
        let at_26 = response_frame_at(1, 3, &payload, REQUEST_RTU_OFFSET);
        // the CRC check must still find the frame if a logger ever answered with
        // the longer prefix instead
        assert_eq!(
            c.decode(3, &at_26).expect("fallback layout decodes"),
            at_26[REQUEST_RTU_OFFSET..at_26.len() - 2]
        );
    }

    /// A response frame: RTU at `offset`, length field such that
    /// `13 + length == frame length` (which the decoder checks).
    fn response_frame_at(serial: u32, sequence: u8, rtu_payload: &[u8], offset: usize) -> Vec<u8> {
        let mut rtu = rtu_payload.to_vec();
        let crc = crc16_modbus(&rtu);
        rtu.extend_from_slice(&crc.to_le_bytes());

        let mut frame = vec![V5_START];
        frame.extend_from_slice(&0u16.to_le_bytes()); // length, filled in below
        frame.extend_from_slice(&CONTROL_RESPONSE.to_le_bytes());
        frame.push(sequence);
        frame.push(0);
        frame.extend_from_slice(&serial.to_le_bytes());
        frame.push(0x02); // frame type
        frame.resize(offset, 0); // filler up to the RTU
        frame.extend_from_slice(&rtu);
        frame.push(0x00); // checksum placeholder
        frame.push(V5_END);
        let payload_len = (frame.len() - 13) as u16;
        frame[1..3].copy_from_slice(&payload_len.to_le_bytes());
        let sum: u32 = frame[1..frame.len() - 2].iter().map(|b| *b as u32).sum();
        let checksum = (sum & 0xFF) as u8;
        let last = frame.len() - 2;
        frame[last] = checksum;
        frame
    }

    #[test]
    fn a_corrupted_checksum_is_caught() {
        let c = V5Client::new("127.0.0.1", 8899, 1, 1, Duration::from_secs(1));
        let mut frame = vec![V5_START];
        frame.extend_from_slice(&(15u16 + 8).to_le_bytes());
        frame.extend_from_slice(&CONTROL_RESPONSE.to_le_bytes());
        frame.push(1);
        frame.push(0);
        frame.extend_from_slice(&1u32.to_le_bytes());
        frame.push(0x02);
        frame.extend_from_slice(&[0; 14]);
        frame.extend_from_slice(&[1, 3, 2, 0, 5, 0, 0]); // rtu
        frame.push(0x00); // deliberately wrong checksum
        frame.push(V5_END);
        assert!(matches!(c.decode(1, &frame), Err(V5Error::Protocol(_))));
    }
}

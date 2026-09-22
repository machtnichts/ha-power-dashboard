//! Talk to Home Assistant over its REST API.
//!
//! Deliberately the same route the Python poller takes: state and discovery
//! messages are pushed through HA's own `mqtt.publish` service, so nothing here
//! needs an MQTT client or a broker password. HA stays the only MQTT client.
//!
//! HTTP/1.1 by hand over TCP. `urllib.request` does the same thing underneath.
//! Home Assistant is reached over plain http on the local network; https would
//! need a TLS stack, which is a crate, so it is refused with a clear message
//! rather than silently failing (see the README).

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use crate::env;
use crate::json::J;

pub struct HaClient {
    host: String,
    port: u16,
    /// Path prefix in case HA is served under one, e.g. http://host/ha
    base_path: String,
    token: String,
    timeout: Duration,
}

impl HaClient {
    pub fn new(url: &str, token: &str, timeout: Duration) -> Result<HaClient, String> {
        let rest = url.strip_prefix("http://").ok_or_else(|| {
            if url.starts_with("https://") {
                format!(
                    "'{}' is https, which this build cannot speak: it has no TLS stack \
                         (by design, so it needs no crates). Use the http address.",
                    url
                )
            } else {
                format!("'{}' must start with http://", url)
            }
        })?;

        let (authority, base_path) = match rest.find('/') {
            Some(i) => (&rest[..i], rest[i..].trim_end_matches('/').to_string()),
            None => (rest, String::new()),
        };

        let (host, port) = match authority.rsplit_once(':') {
            Some((h, p)) => (
                h.to_string(),
                p.parse::<u16>()
                    .map_err(|_| format!("bad port in '{}'", url))?,
            ),
            None => (authority.to_string(), 80),
        };
        if host.is_empty() {
            return Err(format!("no host in '{}'", url));
        }

        Ok(HaClient {
            host,
            port,
            base_path,
            token: token.to_string(),
            timeout,
        })
    }

    pub fn from_env(timeout: Duration) -> Result<HaClient, String> {
        let url = env::ha_url()?;
        let token = env::ha_token()?;
        HaClient::new(&url, &token, timeout)
    }

    /// HA's `mqtt.publish` service. Returns the HTTP status.
    pub fn publish(&self, topic: &str, payload: &str, retain: bool) -> Result<u16, String> {
        let body = J::Obj(vec![
            ("topic".to_string(), J::Str(topic.to_string())),
            ("payload".to_string(), J::Str(payload.to_string())),
            ("retain".to_string(), J::Bool(retain)),
            ("qos".to_string(), J::Int(0)),
        ])
        .to_json();
        let (status, _) = self.post("/api/services/mqtt/publish", &body)?;
        Ok(status)
    }

    /// HA's template renderer, used by the comparison log.
    pub fn render_template(&self, template: &str) -> Result<J, String> {
        let body = J::Obj(vec![("template".to_string(), J::Str(template.to_string()))]).to_json();
        let (_, text) = self.post("/api/template", &body)?;
        crate::json::parse(&text)
    }

    /// One entity's state, read-only. Used as the liveness probe for the garage meter
    /// (`--sdm-entity`), so it must never disturb the poll: every failure is just "no answer".
    pub fn get_state(&self, entity: &str) -> Result<J, String> {
        let (_, text) = self.get(&format!("/api/states/{}", entity))?;
        crate::json::parse(&text)
    }

    /// Deliberately its own transport rather than a shared one with `post`: the publish
    /// request is byte-compared against the Python implementation in the conformance tests,
    /// and a refactor there is not worth the risk for one extra caller.
    fn get(&self, path: &str) -> Result<(u16, String), String> {
        let addr = format!("{}:{}", self.host, self.port);
        let mut stream = TcpStream::connect(&addr)
            .map_err(|e| format!("cannot reach Home Assistant at {}: {}", addr, e))?;
        stream
            .set_read_timeout(Some(self.timeout))
            .map_err(|e| e.to_string())?;
        stream
            .set_write_timeout(Some(self.timeout))
            .map_err(|e| e.to_string())?;

        let request = format!(
            "GET {}{} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\n\
             Connection: close\r\n\r\n",
            self.base_path, path, self.host, self.token
        );
        stream
            .write_all(request.as_bytes())
            .and_then(|_| stream.flush())
            .map_err(|e| format!("sending to Home Assistant failed: {}", e))?;

        let mut raw = Vec::new();
        stream
            .read_to_end(&mut raw)
            .map_err(|e| format!("reading Home Assistant's reply failed: {}", e))?;
        let text = String::from_utf8_lossy(&raw).to_string();
        let status_line = text.lines().next().unwrap_or_default();
        let status: u16 = status_line
            .split_whitespace()
            .nth(1)
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| format!("unreadable reply from Home Assistant: '{}'", status_line))?;
        let body_text = match text.find("\r\n\r\n") {
            Some(i) => text[i + 4..].to_string(),
            None => String::new(),
        };
        if status >= 400 {
            return Err(format!(
                "Home Assistant answered {} for {}: {}",
                status,
                path,
                body_text.trim()
            ));
        }
        Ok((status, body_text))
    }

    fn post(&self, path: &str, body: &str) -> Result<(u16, String), String> {
        let addr = format!("{}:{}", self.host, self.port);
        let mut stream = TcpStream::connect(&addr)
            .map_err(|e| format!("cannot reach Home Assistant at {}: {}", addr, e))?;
        stream
            .set_read_timeout(Some(self.timeout))
            .map_err(|e| e.to_string())?;
        stream
            .set_write_timeout(Some(self.timeout))
            .map_err(|e| e.to_string())?;

        let request = format!(
            "POST {}{} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n\
             Connection: close\r\n\r\n{}",
            self.base_path,
            path,
            self.host,
            self.token,
            body.len(),
            body
        );
        stream
            .write_all(request.as_bytes())
            .and_then(|_| stream.flush())
            .map_err(|e| format!("sending to Home Assistant failed: {}", e))?;

        let mut raw = Vec::new();
        stream
            .read_to_end(&mut raw)
            .map_err(|e| format!("reading Home Assistant's reply failed: {}", e))?;
        let text = String::from_utf8_lossy(&raw).to_string();

        let status_line = text.lines().next().unwrap_or_default();
        let status: u16 = status_line
            .split_whitespace()
            .nth(1)
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| format!("unreadable reply from Home Assistant: '{}'", status_line))?;

        let body_text = match text.find("\r\n\r\n") {
            Some(i) => text[i + 4..].to_string(),
            None => String::new(),
        };

        // urllib raises on 4xx/5xx, and the poller's error path depends on that:
        // a rejected publish must count as a failed poll, not as success.
        if status >= 400 {
            let detail = body_text.trim();
            let detail = if detail.len() > 200 {
                &detail[..200]
            } else {
                detail
            };
            return Err(format!(
                "Home Assistant returned HTTP {}: {}",
                status, detail
            ));
        }
        Ok((status, body_text))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_plain_and_prefixed_urls() {
        let c = HaClient::new("http://192.168.178.126:8123", "t", Duration::from_secs(1)).unwrap();
        assert_eq!(c.host, "192.168.178.126");
        assert_eq!(c.port, 8123);
        assert_eq!(c.base_path, "");

        let c = HaClient::new("http://ha.local/ha", "t", Duration::from_secs(1)).unwrap();
        assert_eq!(c.port, 80);
        assert_eq!(c.base_path, "/ha");
    }

    #[test]
    fn https_is_refused_with_an_explanation() {
        // deliberately not unwrap_err(): that would need Debug on HaClient, and
        // Debug on a type holding a bearer token is a bad idea.
        let err = match HaClient::new("https://ha.example", "t", Duration::from_secs(1)) {
            Err(e) => e,
            Ok(_) => panic!("an https URL must be refused"),
        };
        assert!(err.contains("https"), "{}", err);
        assert!(err.contains("TLS"), "{}", err);
    }

    #[test]
    fn the_publish_body_matches_pythons_json() {
        let body = J::Obj(vec![
            (
                "topic".to_string(),
                J::Str("powerdash/garage_pv/state".into()),
            ),
            ("payload".to_string(), J::Str("{}".into())),
            ("retain".to_string(), J::Bool(true)),
            ("qos".to_string(), J::Int(0)),
        ])
        .to_json();
        assert_eq!(
            body,
            r#"{"topic": "powerdash/garage_pv/state", "payload": "{}", "retain": true, "qos": 0}"#
        );
    }
}

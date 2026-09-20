//! Credentials, loaded the same way the Python poller loads them.
//!
//! `HASS_URL` / `HASS_TOKEN` come from the process environment, or from
//! `~/.hermes/.env` when they are not set there - which is what the systemd unit
//! relies on, so the token is never copied into a second file.
//!
//! The token is never logged, never printed and never written anywhere. The only
//! thing this module will ever say about it is whether it was found.

use std::env;
use std::fs;
use std::path::PathBuf;

fn env_file() -> PathBuf {
    let home = env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".hermes").join(".env")
}

/// Populate HASS_* from the env file when the environment does not carry them.
pub fn load_env() {
    if env::var("HASS_URL").is_ok() && env::var("HASS_TOKEN").is_ok() {
        return;
    }
    let path = env_file();
    let Ok(text) = fs::read_to_string(&path) else {
        return;
    };
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || !line.contains('=') {
            continue;
        }
        let (key, value) = line.split_once('=').unwrap();
        let key = key.trim();
        let value = value.trim().trim_matches('"').trim_matches('\'');
        if key.starts_with("HASS_") && env::var(key).is_err() {
            env::set_var(key, value);
        }
    }
}

pub fn ha_url() -> Result<String, String> {
    load_env();
    env::var("HASS_URL")
        .map(|u| u.trim_end_matches('/').to_string())
        .map_err(|_| "HASS_URL is not set (environment or ~/.hermes/.env)".to_string())
}

pub fn ha_token() -> Result<String, String> {
    load_env();
    env::var("HASS_TOKEN")
        .map_err(|_| "HASS_TOKEN is not set (environment or ~/.hermes/.env)".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_credentials_are_an_error_not_a_panic() {
        // whichever way the environment happens to be set up, this must not panic
        let _ = ha_url();
        let _ = ha_token();
    }
}

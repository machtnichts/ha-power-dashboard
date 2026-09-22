//! JSON writing, formatted exactly like Python's `json.dumps` defaults.
//!
//! Home Assistant would accept any valid JSON, so this is about equivalence: the
//! Python poller publishes with `json.dumps(...)` (separators `", "` / `": "`,
//! `ensure_ascii=True`) and this must produce byte-identical payloads, which is
//! what the differential test checks.
//!
//! The float handling is the part worth reading. Python prints a float via
//! `repr()`, which is the shortest representation that round-trips - and always
//! keeps a decimal point, so `0.0` stays `"0.0"` while Rust's `Display` would say
//! `"0"`. `Dec` mirrors `round(x, n)` followed by that repr; `Repr` mirrors a raw
//! float going straight into `json.dumps`.

/// A JSON value that knows how Python would have printed it.
#[derive(Debug, Clone, PartialEq)]
pub enum J {
    Null,
    Bool(bool),
    Int(i64),
    /// A float rendered with `prec` decimals - the `round(x, n)` case.
    Dec(f64, u8),
    /// A float rendered like Python's repr - the "no rounding applied" case.
    Repr(f64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

impl J {
    pub fn to_json(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }

    fn write(&self, out: &mut String) {
        match self {
            J::Null => out.push_str("null"),
            J::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            J::Int(i) => out.push_str(&i.to_string()),
            J::Dec(v, prec) => out.push_str(&fmt_dec(*v, *prec)),
            J::Repr(v) => out.push_str(&fmt_repr(*v)),
            J::Str(s) => write_string(s, out),
            J::Arr(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    item.write(out);
                }
                out.push(']');
            }
            J::Obj(fields) => {
                out.push('{');
                for (i, (k, v)) in fields.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    write_string(k, out);
                    out.push_str(": ");
                    v.write(out);
                }
                out.push('}');
            }
        }
    }

    /// Object lookup, for pulling a field out of a parsed response.
    pub fn get(&self, key: &str) -> Option<&J> {
        match self {
            J::Obj(fields) => fields.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            J::Int(i) => Some(*i as f64),
            J::Dec(v, _) | J::Repr(v) => Some(*v),
            J::Str(s) => s.parse().ok(),
            _ => None,
        }
    }
}

/// `round(x, prec)` as Python's `json.dumps` prints it.
///
/// Python does not print the requested number of decimals: `round(2765.6, 2)` is the float
/// 2765.6 and its repr is `2765.6`, not `2765.60`. Fixed-decimal formatting matched that only
/// while every value happened to need all its decimals - the corrected energy scale (27955
/// counts -> 2795.5 kWh) was the first case where it did not, and the byte-comparison test
/// against the Python poller is what caught it. So: format with `prec` decimals, drop
/// trailing zeros, but always keep at least one decimal (`253.0`, never `253`).
pub fn fmt_dec(v: f64, prec: u8) -> String {
    let mut s = format!("{:.*}", prec as usize, v);
    if s.contains('.') {
        while s.ends_with('0') {
            s.pop();
        }
        if s.ends_with('.') {
            s.push('0');
        }
    }
    s
}

/// Python's `repr(float)`: shortest round-trip, with `.0` when it looks integral.
pub fn fmt_repr(v: f64) -> String {
    let s = format!("{}", v);
    if s.contains('.') || s.contains('e') || s.contains("inf") || s.contains("NaN") {
        s
    } else {
        s + ".0"
    }
}

/// Python's `json.dumps` string escaping, `ensure_ascii=True` (the default).
fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            // ensure_ascii escapes everything outside ASCII, including accents
            c if (c as u32) > 0x7e => {
                let cp = c as u32;
                if cp > 0xFFFF {
                    // surrogate pair, like Python emits
                    let v = cp - 0x10000;
                    out.push_str(&format!(
                        "\\u{:04x}\\u{:04x}",
                        0xD800 + (v >> 10),
                        0xDC00 + (v & 0x3FF)
                    ));
                } else {
                    out.push_str(&format!("\\u{:04x}", cp));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

// ------------------------------------------------------------------ parsing

/// A small parser, used for Home Assistant's `/api/template` response only.
pub fn parse(text: &str) -> Result<J, String> {
    let bytes: Vec<char> = text.chars().collect();
    let mut p = Parser {
        chars: bytes,
        pos: 0,
    };
    p.skip_ws();
    let value = p.value()?;
    p.skip_ws();
    if p.pos != p.chars.len() {
        return Err(format!("trailing data at {}", p.pos));
    }
    Ok(value)
}

struct Parser {
    chars: Vec<char>,
    pos: usize,
}

impl Parser {
    fn skip_ws(&mut self) {
        while self.pos < self.chars.len() && self.chars[self.pos].is_whitespace() {
            self.pos += 1;
        }
    }

    fn peek(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }

    fn value(&mut self) -> Result<J, String> {
        self.skip_ws();
        match self.peek() {
            Some('{') => self.object(),
            Some('[') => self.array(),
            Some('"') => Ok(J::Str(self.string()?)),
            Some('t') => self.literal("true", J::Bool(true)),
            Some('f') => self.literal("false", J::Bool(false)),
            Some('n') => self.literal("null", J::Null),
            Some(_) => self.number(),
            None => Err("unexpected end of input".into()),
        }
    }

    fn literal(&mut self, word: &str, value: J) -> Result<J, String> {
        for expected in word.chars() {
            if self.peek() != Some(expected) {
                return Err(format!("expected {}", word));
            }
            self.pos += 1;
        }
        Ok(value)
    }

    fn object(&mut self) -> Result<J, String> {
        self.pos += 1; // '{'
        let mut fields = Vec::new();
        self.skip_ws();
        if self.peek() == Some('}') {
            self.pos += 1;
            return Ok(J::Obj(fields));
        }
        loop {
            self.skip_ws();
            let key = self.string()?;
            self.skip_ws();
            if self.peek() != Some(':') {
                return Err("expected ':'".into());
            }
            self.pos += 1;
            let value = self.value()?;
            fields.push((key, value));
            self.skip_ws();
            match self.peek() {
                Some(',') => self.pos += 1,
                Some('}') => {
                    self.pos += 1;
                    return Ok(J::Obj(fields));
                }
                _ => return Err("expected ',' or '}'".into()),
            }
        }
    }

    fn array(&mut self) -> Result<J, String> {
        self.pos += 1; // '['
        let mut items = Vec::new();
        self.skip_ws();
        if self.peek() == Some(']') {
            self.pos += 1;
            return Ok(J::Arr(items));
        }
        loop {
            items.push(self.value()?);
            self.skip_ws();
            match self.peek() {
                Some(',') => self.pos += 1,
                Some(']') => {
                    self.pos += 1;
                    return Ok(J::Arr(items));
                }
                _ => return Err("expected ',' or ']'".into()),
            }
        }
    }

    fn string(&mut self) -> Result<String, String> {
        if self.peek() != Some('"') {
            return Err("expected a string".into());
        }
        self.pos += 1;
        let mut s = String::new();
        while let Some(c) = self.peek() {
            self.pos += 1;
            match c {
                '"' => return Ok(s),
                '\\' => {
                    let esc = self.peek().ok_or("truncated escape")?;
                    self.pos += 1;
                    match esc {
                        '"' => s.push('"'),
                        '\\' => s.push('\\'),
                        '/' => s.push('/'),
                        'n' => s.push('\n'),
                        'r' => s.push('\r'),
                        't' => s.push('\t'),
                        'b' => s.push('\u{08}'),
                        'f' => s.push('\u{0c}'),
                        'u' => {
                            let hex: String = self.chars[self.pos..self.pos + 4].iter().collect();
                            self.pos += 4;
                            let cp = u32::from_str_radix(&hex, 16)
                                .map_err(|_| "bad \\u escape".to_string())?;
                            if let Some(ch) = char::from_u32(cp) {
                                s.push(ch);
                            }
                        }
                        other => return Err(format!("bad escape \\{}", other)),
                    }
                }
                c => s.push(c),
            }
        }
        Err("unterminated string".into())
    }

    fn number(&mut self) -> Result<J, String> {
        let start = self.pos;
        while let Some(c) = self.peek() {
            if c.is_ascii_digit() || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E' {
                self.pos += 1;
            } else {
                break;
            }
        }
        let text: String = self.chars[start..self.pos].iter().collect();
        if let Ok(i) = text.parse::<i64>() {
            return Ok(J::Int(i));
        }
        text.parse::<f64>()
            .map(J::Repr)
            .map_err(|_| format!("bad number '{}'", text))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_keep_pythons_shape() {
        assert_eq!(J::Dec(253.5, 1).to_json(), "253.5");
        assert_eq!(J::Dec(0.0, 1).to_json(), "0.0");
        assert_eq!(J::Dec(276.56, 2).to_json(), "276.56");
        // Python drops trailing zeros, so a 2-decimal field may print fewer of them
        assert_eq!(J::Dec(2765.6, 2).to_json(), "2765.6");
        assert_eq!(J::Dec(50.0, 2).to_json(), "50.0");
        // a whole float must not collapse to an integer
        assert_eq!(J::Repr(0.0).to_json(), "0.0");
        assert_eq!(J::Repr(235.7).to_json(), "235.7");
        assert_eq!(J::Int(0).to_json(), "0");
    }

    #[test]
    fn objects_match_python_separators() {
        let j = J::Obj(vec![
            ("topic".into(), J::Str("a/b".into())),
            ("retain".into(), J::Bool(true)),
            ("qos".into(), J::Int(0)),
        ]);
        assert_eq!(j.to_json(), r#"{"topic": "a/b", "retain": true, "qos": 0}"#);
    }

    #[test]
    fn non_ascii_is_escaped_like_ensure_ascii() {
        assert_eq!(
            J::Str("Netzbezug ü".into()).to_json(),
            r#""Netzbezug \u00fc""#
        );
    }

    #[test]
    fn parses_a_template_response() {
        let v = parse(r#"{"se_pv": 1600.5, "charger_kw": 0, "nested": {"x": [1, 2]}}"#).unwrap();
        assert_eq!(v.get("se_pv").unwrap().as_f64(), Some(1600.5));
        assert_eq!(v.get("charger_kw").unwrap().as_f64(), Some(0.0));
        assert!(v.get("nested").unwrap().get("x").is_some());
    }
}

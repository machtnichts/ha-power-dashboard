//! The Home Assistant entities this poller creates, copied field for field from
//! the Python version.
//!
//! `unique_id` is what HA matches on, and `object_id` is what fixes the entity_id
//! at creation time - `sensor.garage_pv_leistung` rather than a name built from
//! the device. Renaming needs the old entity deleted first, which is what
//! `--recreate` does.

use crate::json::J;

pub const STATE_TOPIC: &str = "powerdash/garage_pv/state";
pub const AVAILABILITY_TOPIC: &str = "powerdash/garage_pv/status";
pub const DISCOVERY_PREFIX: &str = "homeassistant";

pub struct Entity {
    pub component: &'static str,
    pub topic_id: &'static str,
    pub object_id: &'static str,
    pub name: &'static str,
    pub extra: &'static [(&'static str, &'static str)],
}

/// name first, then the extra fields in this order - the order they end up in the
/// retained discovery payload, which is why the Python dicts are written the same
/// way.
pub const ENTITIES: [Entity; 5] = [
    Entity {
        component: "sensor",
        topic_id: "garage_pv_power",
        object_id: "garage_pv_leistung",
        name: "Garage PV Leistung",
        extra: &[
            ("device_class", "power"),
            ("unit_of_measurement", "W"),
            ("state_class", "measurement"),
            ("value_template", "{{ value_json.ac_power_w }}"),
        ],
    },
    Entity {
        component: "sensor",
        topic_id: "garage_pv_energy",
        object_id: "garage_pv_energie",
        name: "Garage PV Energie gesamt",
        extra: &[
            ("device_class", "energy"),
            ("unit_of_measurement", "kWh"),
            ("state_class", "total_increasing"),
            ("value_template", "{{ value_json.energy_kwh }}"),
        ],
    },
    Entity {
        component: "sensor",
        topic_id: "garage_pv_voltage",
        object_id: "garage_pv_spannung",
        name: "Garage PV Spannung",
        extra: &[
            ("device_class", "voltage"),
            ("unit_of_measurement", "V"),
            ("state_class", "measurement"),
            ("value_template", "{{ value_json.ac_voltage_v }}"),
        ],
    },
    Entity {
        component: "sensor",
        topic_id: "garage_pv_frequency",
        object_id: "garage_pv_frequenz",
        name: "Garage PV Frequenz",
        extra: &[
            ("device_class", "frequency"),
            ("unit_of_measurement", "Hz"),
            ("state_class", "measurement"),
            ("value_template", "{{ value_json.frequency_hz }}"),
        ],
    },
    Entity {
        component: "sensor",
        topic_id: "garage_pv_dc_power",
        object_id: "garage_pv_dc_leistung",
        name: "Garage PV DC-Leistung",
        extra: &[
            ("device_class", "power"),
            ("unit_of_measurement", "W"),
            ("state_class", "measurement"),
            ("value_template", "{{ value_json.dc_power_w }}"),
        ],
    },
];

pub fn device() -> J {
    J::Obj(vec![
        (
            "identifiers".to_string(),
            J::Arr(vec![J::Str("deye_sun_m160g4_garage".to_string())]),
        ),
        (
            "name".to_string(),
            J::Str("Garage PV (Deye SUN-M160G4)".to_string()),
        ),
        ("manufacturer".to_string(), J::Str("Deye".to_string())),
        ("model".to_string(), J::Str("SUN-M160G4-EU-Q0".to_string())),
    ])
}

pub fn config_topic(entity: &Entity) -> String {
    format!(
        "{}/{}/powerdash/{}/config",
        DISCOVERY_PREFIX, entity.component, entity.topic_id
    )
}

/// The retained discovery payload. Key order matches the Python version.
pub fn config_payload(entity: &Entity) -> String {
    let mut fields: Vec<(String, J)> = vec![
        ("name".to_string(), J::Str(entity.name.to_string())),
        (
            "unique_id".to_string(),
            J::Str(format!("powerdash_{}", entity.topic_id)),
        ),
        (
            "object_id".to_string(),
            J::Str(entity.object_id.to_string()),
        ),
        ("state_topic".to_string(), J::Str(STATE_TOPIC.to_string())),
        ("device".to_string(), device()),
        (
            "availability_topic".to_string(),
            J::Str(AVAILABILITY_TOPIC.to_string()),
        ),
        (
            "payload_available".to_string(),
            J::Str("online".to_string()),
        ),
        (
            "payload_not_available".to_string(),
            J::Str("offline".to_string()),
        ),
    ];
    for (key, value) in entity.extra {
        fields.push((key.to_string(), J::Str(value.to_string())));
    }
    J::Obj(fields).to_json()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn topics_and_ids_match_the_python_poller() {
        assert_eq!(
            config_topic(&ENTITIES[0]),
            "homeassistant/sensor/powerdash/garage_pv_power/config"
        );
        assert!(
            config_payload(&ENTITIES[0]).contains(r#""unique_id": "powerdash_garage_pv_power""#)
        );
        assert!(config_payload(&ENTITIES[0]).contains(r#""object_id": "garage_pv_leistung""#));
    }

    #[test]
    fn the_power_entity_config_is_field_for_field() {
        let expected = concat!(
            r#"{"name": "Garage PV Leistung", "unique_id": "powerdash_garage_pv_power", "#,
            r#""object_id": "garage_pv_leistung", "state_topic": "powerdash/garage_pv/state", "#,
            r#""device": {"identifiers": ["deye_sun_m160g4_garage"], "#,
            r#""name": "Garage PV (Deye SUN-M160G4)", "manufacturer": "Deye", "#,
            r#""model": "SUN-M160G4-EU-Q0"}, "availability_topic": "powerdash/garage_pv/status", "#,
            r#""payload_available": "online", "payload_not_available": "offline", "#,
            r#""device_class": "power", "unit_of_measurement": "W", "state_class": "measurement", "#,
            r#""value_template": "{{ value_json.ac_power_w }}"}"#
        );
        assert_eq!(config_payload(&ENTITIES[0]), expected);
    }

    #[test]
    fn every_entity_shares_one_device_and_state_topic() {
        for e in &ENTITIES {
            let payload = config_payload(e);
            assert!(payload.contains(r#""state_topic": "powerdash/garage_pv/state""#));
            assert!(payload.contains(r#""identifiers": ["deye_sun_m160g4_garage"]"#));
        }
    }
}

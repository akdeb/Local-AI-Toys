use serde::Serialize;
use std::process::Command;

#[derive(Debug, Clone, Serialize)]
pub struct SystemProfile {
    pub chip: String,
    pub model_identifier: Option<String>,
    pub total_memory_gb: Option<u64>,
    pub arch: String,
}

fn sysctl_value(key: &str) -> Option<String> {
    let output = Command::new("sysctl").args(["-n", key]).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

#[tauri::command]
pub async fn get_system_profile() -> Result<SystemProfile, String> {
    let arch = std::env::consts::ARCH.to_string();

    let model_identifier = sysctl_value("hw.model");
    let chip = sysctl_value("machdep.cpu.brand_string")
        .or_else(|| model_identifier.clone())
        .unwrap_or_else(|| "Unknown".to_string());

    let total_memory_gb = sysctl_value("hw.memsize")
        .and_then(|v| v.parse::<u64>().ok())
        .map(|bytes| bytes / (1024_u64 * 1024 * 1024));

    Ok(SystemProfile {
        chip,
        model_identifier,
        total_memory_gb,
        arch,
    })
}

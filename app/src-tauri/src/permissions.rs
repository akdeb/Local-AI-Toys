use std::process::Command;
use std::net::UdpSocket;

#[tauri::command]
pub async fn open_system_permission(kind: String) -> Result<String, String> {
    if !cfg!(target_os = "macos") {
        return Err("System permission shortcuts are only supported on macOS".to_string());
    }

    let urls: &[&str] = match kind.as_str() {
        "microphone" => &[
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone",
            "x-apple.systempreferences:com.apple.preference.security",
        ],
        "local-network" => &[
            "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork",
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_LocalNetwork",
            "x-apple.systempreferences:com.apple.preference.security",
        ],
        _ => return Err("Unknown permission type".to_string()),
    };

    for url in urls {
        let status = Command::new("open").arg(url).status();
        if matches!(status, Ok(s) if s.success()) {
            return Ok(format!("Opened settings for {kind}"));
        }
    }

    Err(format!("Failed to open settings for {kind}"))
}

#[tauri::command]
pub async fn trigger_local_network_prompt() -> Result<String, String> {
    if !cfg!(target_os = "macos") {
        return Err("Local network prompt trigger is only supported on macOS".to_string());
    }

    let sock = UdpSocket::bind("0.0.0.0:0")
        .map_err(|e| format!("Failed to bind UDP socket: {e}"))?;
    sock.set_broadcast(true)
        .map_err(|e| format!("Failed to enable UDP broadcast: {e}"))?;

    let payload = b"opentoys-local-network-permission-check";
    let _ = sock.send_to(payload, "255.255.255.255:1900");
    let _ = sock.send_to(payload, "224.0.0.251:5353");

    Ok("Triggered local network access check. If prompted, click Allow.".to_string())
}

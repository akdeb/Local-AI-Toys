mod backend;
mod models;
mod permissions;
mod paths;
mod python_setup;
mod setup;
mod system;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            backend::setup_backend(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            setup::check_setup_status,
            setup::ensure_python_runtime,
            setup::create_python_venv,
            setup::install_python_deps,
            models::check_models_status,
            models::scan_local_models,
            models::download_model,
            models::download_all_models,
            setup::mark_setup_complete,
            setup::is_first_launch,
            backend::start_backend,
            permissions::open_system_permission,
            permissions::trigger_local_network_prompt,
            system::get_system_profile
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::ExitRequested { .. } => {
            backend::stop_api_server(app_handle);
        }
        tauri::RunEvent::WindowEvent { event, .. } => {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                backend::stop_api_server(app_handle);
            }
        }
        _ => {}
    });
}

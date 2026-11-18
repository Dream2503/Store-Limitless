use std::process::Command;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            let backend = if cfg!(debug_assertions) {
                std::path::PathBuf::from("../../dist/storelimitless-backend/storelimitless-backend")
            } else {
                app.path()
                    .resource_dir()?
                    .join("backend")
                    .join(if cfg!(target_os = "windows") {
                        "storelimitless-backend.exe"
                    } else {
                        "storelimitless-backend"
                    })
            };

            Command::new(backend)
                .spawn()
                .expect("failed to start StoreLimitless backend");

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running StoreLimitless application");
}
//! Protótipo local: nenhum canal/credencial em runtime. O overlay de build habilita loopback.
use super::{BackendState, Connection};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Clone, Serialize)]
pub struct UpdateState {
    phase: String,
    current_version: String,
    auto_check: bool,
    configured: bool,
    last_check_at: Option<String>,
    available: Option<Value>,
    blocked_reason: Option<String>,
    progress: Option<Value>,
    backup_path_label: Option<String>,
    error: Option<Value>,
}
impl UpdateState {
    fn initial(current_version: String, auto_check: bool, configured: bool) -> Self {
        Self {
            phase: if configured { "idle" } else { "disabled" }.into(),
            current_version,
            auto_check,
            configured,
            last_check_at: None,
            available: None,
            blocked_reason: None,
            progress: None,
            backup_path_label: None,
            error: None,
        }
    }
    fn transition(&mut self, phase: &str) {
        self.phase = phase.into();
        self.error = None;
        self.blocked_reason = None;
        if phase != "downloading" {
            self.progress = None;
        }
    }
    fn fail(&mut self, code: &str) {
        self.transition("error");
        self.error = Some(
            json!({"code":code,"message":"Atualização não concluída; preserve seus dados e tente novamente."}),
        );
    }
}
struct Model {
    state: UpdateState,
    update: Option<Update>,
}
pub struct UpdaterState {
    model: Mutex<Model>,
    operation: tauri::async_runtime::Mutex<()>,
    directory: PathBuf,
    installing: AtomicBool,
}
impl UpdaterState {
    pub fn installing(&self) -> bool {
        self.installing.load(Ordering::Acquire)
    }
}
struct InstallationGuard<'a>(&'a AtomicBool);
impl Drop for InstallationGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
fn should_auto_check(configured: bool, auto_check: bool) -> bool {
    configured && auto_check
}
fn preference(directory: &std::path::Path) -> bool {
    std::fs::read(directory.join("preferences.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v["auto_check"].as_bool())
        .unwrap_or(true)
}
fn save_preference(directory: &std::path::Path, enabled: bool) -> std::io::Result<()> {
    std::fs::create_dir_all(directory)?;
    let temporary = directory.join(format!("preferences-{}.tmp", uuid::Uuid::new_v4()));
    let result = std::fs::write(&temporary, json!({"auto_check":enabled}).to_string())
        .and_then(|_| std::fs::rename(&temporary, directory.join("preferences.json")));
    if result.is_err() {
        let _ = std::fs::remove_file(temporary);
    }
    result
}
fn snapshot(app: &AppHandle) -> UpdateState {
    app.state::<UpdaterState>()
        .model
        .lock()
        .unwrap()
        .state
        .clone()
}
fn change(app: &AppHandle, apply: impl FnOnce(&mut Model)) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    let state = {
        let mut model = managed.model.lock().unwrap();
        apply(&mut model);
        model.state.clone()
    };
    let _ = app.emit("update://state", &state);
    state
}
fn phase(app: &AppHandle, name: &str) {
    change(app, |m| {
        m.state.transition(name);
    });
}
fn failure(app: &AppHandle, code: &str) -> UpdateState {
    change(app, |m| {
        m.state.fail(code);
    })
}
fn blocked(app: &AppHandle, reason: &str) -> UpdateState {
    change(app, |m| {
        m.state.phase = "blocked".into();
        m.state.blocked_reason = Some(reason.into());
        m.state.error = None;
    })
}
pub fn newer(candidate: &str, current: &str) -> bool {
    match (
        semver::Version::parse(candidate),
        semver::Version::parse(current),
    ) {
        (Ok(a), Ok(b)) => a.cmp_precedence(&b).is_gt(),
        _ => false,
    }
}
fn local_url(raw: &str) -> bool {
    reqwest::Url::parse(raw).is_ok_and(|u| {
        u.scheme() == "http"
            && u.host_str() == Some("127.0.0.1")
            && u.username().is_empty()
            && u.password().is_none()
    })
}
fn configured(config: Option<&Value>) -> bool {
    config.is_some_and(|c| {
        c["pubkey"].as_str().is_some_and(|k| !k.is_empty())
            && c["dangerousInsecureTransportProtocol"] == true
            && c["requireSignedVersion"] == true
            && c["dangerousAcceptInvalidCerts"] != true
            && c["dangerousAcceptInvalidHostnames"] != true
            && c["endpoints"].as_array().is_some_and(|a| {
                !a.is_empty() && a.iter().all(|v| v.as_str().is_some_and(local_url))
            })
    })
}
pub fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let enabled = configured(app.config().plugins.0.get("updater"));
    let directory = std::env::var_os("HORUS_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or(app.path().app_local_data_dir()?.join("workspace"))
        .join("updater");
    let auto_check = preference(&directory);
    if enabled {
        app.handle()
            .plugin(tauri_plugin_updater::Builder::new().build())?;
    }
    app.manage(UpdaterState {
        directory,
        installing: AtomicBool::new(false),
        operation: Default::default(),
        model: Mutex::new(Model {
            state: UpdateState::initial(
                app.package_info().version.to_string(),
                auto_check,
                enabled,
            ),
            update: None,
        }),
    });
    if should_auto_check(enabled, auto_check) {
        let handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            update_check(handle).await;
        });
    }
    Ok(())
}
#[tauri::command(rename_all = "snake_case")]
pub fn update_get_state(app: AppHandle) -> UpdateState {
    snapshot(&app)
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_set_auto_check(app: AppHandle, enabled: bool) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    if save_preference(&managed.directory, enabled).is_err() {
        return failure(&app, "install_failed");
    }
    change(&app, |m| m.state.auto_check = enabled)
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_check(app: AppHandle) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    let Ok(_guard) = managed.operation.try_lock() else {
        return snapshot(&app);
    };
    if !snapshot(&app).configured {
        return snapshot(&app);
    }
    phase(&app, "checking");
    change(&app, |m| {
        m.state.available = None;
        m.update = None;
        m.state.last_check_at = Some(timestamp());
    });
    // O comparador permite inspecionar também manifesto antigo; a guarda abaixo nunca o disponibiliza.
    let builder = app
        .updater_builder()
        .timeout(Duration::from_secs(20))
        .no_proxy()
        .configure_client(|c| c.redirect(reqwest::redirect::Policy::none()))
        .version_comparator(|_, _| true);
    let result = match builder.build() {
        Ok(updater) => updater.check().await,
        Err(e) => Err(e),
    };
    match result {
        Ok(Some(update)) => {
            if !newer(&update.version, &snapshot(&app).current_version) {
                return failure(&app, "downgrade_refused");
            }
            if !local_url(update.download_url.as_str()) {
                return failure(&app, "network");
            }
            change(&app, |m| {
                m.state.phase = "available".into();
                m.state.last_check_at = Some(timestamp());
                m.state.available = Some(
                    json!({"version":update.version,"notes":update.body.clone().unwrap_or_default(),
                    "pub_date":update.raw_json.get("pub_date").cloned().unwrap_or(Value::Null)}),
                );
                m.update = Some(update);
            })
        }
        Ok(None) => change(&app, |m| {
            m.state.phase = "up_to_date".into();
            m.state.available = None;
            m.update = None;
            m.state.last_check_at = Some(timestamp());
        }),
        Err(_) => failure(&app, "network"),
    }
}
fn timestamp() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap()
}
#[derive(Deserialize)]
struct Activity {
    busy: bool,
}
async fn backend(
    client: &reqwest::Client,
    connection: &Connection,
    method: reqwest::Method,
    route: &str,
) -> Result<Value, ()> {
    client
        .request(method, format!("{}{}", connection.base_url, route))
        .bearer_auth(&connection.token)
        .send()
        .await
        .map_err(|_| ())?
        .error_for_status()
        .map_err(|_| ())?
        .json()
        .await
        .map_err(|_| ())
}
async fn activity(client: &reqwest::Client, connection: &Connection) -> Result<bool, ()> {
    let value = backend(
        client,
        connection,
        reqwest::Method::GET,
        "/v1/update/activity",
    )
    .await?;
    serde_json::from_value::<Activity>(value)
        .map(|a| a.busy)
        .map_err(|_| ())
}
fn confirm(confirmed: &str, available: &str, current: &str) -> Result<(), &'static str> {
    if confirmed != available {
        return Err("install_failed");
    }
    if !newer(available, current) {
        return Err("downgrade_refused");
    }
    Ok(())
}
trait GuardBackend {
    fn activity(&self) -> impl std::future::Future<Output = Result<bool, &'static str>> + Send;
    fn reserve(&self) -> impl std::future::Future<Output = Result<(), &'static str>> + Send;
    fn backup(&self) -> impl std::future::Future<Output = Result<String, &'static str>> + Send;
}
async fn prepare(backend: &impl GuardBackend, reserved: &mut bool) -> Result<String, &'static str> {
    if backend.activity().await? {
        return Err("job_running");
    }
    backend.reserve().await?;
    *reserved = true;
    if backend.activity().await? {
        return Err("job_running");
    }
    backend.backup().await
}
struct LiveBackend<'a> {
    app: &'a AppHandle,
    client: &'a reqwest::Client,
    connection: &'a Connection,
}
impl GuardBackend for LiveBackend<'_> {
    async fn activity(&self) -> Result<bool, &'static str> {
        activity(self.client, self.connection)
            .await
            .map_err(|_| "backend_unavailable")
    }
    async fn reserve(&self) -> Result<(), &'static str> {
        match self
            .client
            .post(format!(
                "{}/v1/update/reservation",
                self.connection.base_url
            ))
            .bearer_auth(&self.connection.token)
            .send()
            .await
        {
            Ok(response) if response.status() == reqwest::StatusCode::CONFLICT => {
                Err("job_running")
            }
            Ok(response) if response.status().is_success() => {
                match response.json::<Value>().await {
                    Ok(value) if value["reserved"] == true => Ok(()),
                    _ => Err("backend_unavailable"),
                }
            }
            _ => Err("backend_unavailable"),
        }
    }
    async fn backup(&self) -> Result<String, &'static str> {
        phase(self.app, "backing_up");
        let value = backend(
            self.client,
            self.connection,
            reqwest::Method::POST,
            "/v1/update/backup",
        )
        .await
        .map_err(|_| "backup_failed")?;
        if value["ok"] != true {
            return Err("backup_failed");
        }
        value["label"]
            .as_str()
            .map(String::from)
            .ok_or("backup_failed")
    }
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_install(app: AppHandle, confirmed_version: String) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    let Ok(_guard) = managed.operation.try_lock() else {
        return snapshot(&app);
    };
    if !snapshot(&app).configured {
        return snapshot(&app);
    }
    let update = managed.model.lock().unwrap().update.clone();
    let Some(update) = update else {
        return failure(&app, "install_failed");
    };
    if let Err(code) = confirm(
        &confirmed_version,
        &update.version,
        &snapshot(&app).current_version,
    ) {
        return failure(&app, code);
    }
    managed.installing.store(true, Ordering::Release);
    let _installation = InstallationGuard(&managed.installing);
    let connection = app
        .state::<BackendState>()
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|b| b.connection.clone());
    let Some(connection) = connection else {
        return blocked(&app, "backend_unavailable");
    };
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(60))
        .build()
        .unwrap();
    let mut reserved = false;
    let preparation = prepare(
        &LiveBackend {
            app: &app,
            client: &client,
            connection: &connection,
        },
        &mut reserved,
    )
    .await;
    let result = match preparation {
        Ok(label) => {
            change(&app, |m| m.state.backup_path_label = Some(label));
            install_reserved(&app, &client, &connection, update).await
        }
        Err("job_running") => blocked(&app, "job_running"),
        Err("backend_unavailable") => blocked(&app, "backend_unavailable"),
        Err(code) => failure(&app, code),
    };
    // Se instalou no Windows o plugin encerrou o processo. Em toda falha, libera a reserva.
    if reserved {
        let _ = backend(
            &client,
            &connection,
            reqwest::Method::DELETE,
            "/v1/update/reservation",
        )
        .await;
    }
    result
}
trait PayloadBackend {
    fn download(&self) -> impl std::future::Future<Output = Result<Vec<u8>, &'static str>> + Send;
    fn cache(&self, bytes: &[u8]) -> Result<(), &'static str>;
    fn shutdown(&self) -> impl std::future::Future<Output = Result<(), &'static str>> + Send;
    fn install(&self, bytes: &[u8]) -> Result<(), &'static str>;
}
async fn finish_update(steps: &impl PayloadBackend) -> Result<(), &'static str> {
    let bytes = steps.download().await?; // O plugin verifica assinatura e versão antes de devolver bytes.
    steps.cache(&bytes)?;
    steps.shutdown().await?;
    steps.install(&bytes)
}
struct LivePayload<'a> {
    backend: LiveBackend<'a>,
    update: Update,
}
impl PayloadBackend for LivePayload<'_> {
    async fn download(&self) -> Result<Vec<u8>, &'static str> {
        let app = self.backend.app;
        phase(app, "downloading");
        let mut downloaded = 0u64;
        self.update
            .download(
                |chunk, total| {
                    downloaded += chunk as u64;
                    change(app, |m| {
                        m.state.progress = Some(json!({"downloaded":downloaded,"total":total}))
                    });
                },
                || {},
            )
            .await
            .map_err(|e| download_error(&e))
    }
    fn cache(&self, bytes: &[u8]) -> Result<(), &'static str> {
        let folder = self
            .backend
            .app
            .state::<UpdaterState>()
            .directory
            .join("installers")
            .join(&self.update.version);
        std::fs::create_dir_all(&folder)
            .and_then(|_| std::fs::write(folder.join("setup.exe"), bytes))
            .and_then(|_| std::fs::write(folder.join("setup.exe.sig"), &self.update.signature))
            .map_err(|_| "install_failed")
    }
    async fn shutdown(&self) -> Result<(), &'static str> {
        let app = self.backend.app;
        phase(app, "installing");
        let owned = app.state::<BackendState>().0.lock().unwrap().take();
        let Some(mut owned) = owned else {
            return Err("backend_unavailable");
        };
        backend(
            self.backend.client,
            self.backend.connection,
            reqwest::Method::POST,
            "/v1/shutdown",
        )
        .await
        .map_err(|_| "install_failed")?;
        let shutdown = tauri::async_runtime::spawn_blocking(move || {
            let until = std::time::Instant::now() + Duration::from_secs(15);
            while std::time::Instant::now() < until {
                if let Some(status) = owned.child.try_wait().map_err(|_| ())? {
                    return if status.success() { Ok(()) } else { Err(()) };
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(())
        })
        .await;
        if matches!(shutdown, Ok(Ok(()))) {
            Ok(())
        } else {
            Err("install_failed")
        }
    }
    fn install(&self, bytes: &[u8]) -> Result<(), &'static str> {
        self.update.install(bytes).map_err(|_| "install_failed")
    }
}
async fn install_reserved(
    app: &AppHandle,
    client: &reqwest::Client,
    connection: &Connection,
    update: Update,
) -> UpdateState {
    match finish_update(&LivePayload {
        backend: LiveBackend {
            app,
            client,
            connection,
        },
        update,
    })
    .await
    {
        Ok(()) => snapshot(app),
        Err("backend_unavailable") => blocked(app, "backend_unavailable"),
        Err(code) => failure(app, code),
    }
}
fn download_error(error: &tauri_plugin_updater::Error) -> &'static str {
    match error {
        tauri_plugin_updater::Error::Minisign(_)
        | tauri_plugin_updater::Error::Base64(_)
        | tauri_plugin_updater::Error::SignatureUtf8(_)
        | tauri_plugin_updater::Error::SignedVersionMismatch { .. }
        | tauri_plugin_updater::Error::MissingSignedVersion => "signature_invalid",
        _ => "network",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preference_survives_restart_and_auto_check_requires_both_flags() {
        let directory =
            std::env::temp_dir().join(format!("horus-updater-test-{}", uuid::Uuid::new_v4()));
        assert!(preference(&directory));
        save_preference(&directory, false).unwrap();
        assert!(!preference(&directory));
        save_preference(&directory, true).unwrap();
        assert!(preference(&directory));
        std::fs::remove_dir_all(directory).unwrap();
        assert!(should_auto_check(true, true));
        for (c, a) in [(false, true), (true, false), (false, false)] {
            assert!(!should_auto_check(c, a));
        }
    }
    #[test]
    fn install_guard_is_released_on_error_return() {
        let flag = AtomicBool::new(true);
        {
            let _guard = InstallationGuard(&flag);
            assert!(flag.load(Ordering::Acquire));
        }
        assert!(!flag.load(Ordering::Acquire));
    }
    #[test]
    fn state_defaults_and_transitions_match_frontend_contract() {
        let disabled = UpdateState::initial("1.0.0".into(), true, false);
        assert_eq!(disabled.phase, "disabled");
        assert!(!disabled.configured);
        let mut state = UpdateState::initial("1.0.0".into(), true, true);
        assert_eq!(state.phase, "idle");
        state.transition("downloading");
        state.progress = Some(json!({"downloaded":7,"total":10}));
        state.fail("signature_invalid");
        assert_eq!(state.phase, "error");
        assert!(state.progress.is_none());
        assert_eq!(state.error.as_ref().unwrap()["code"], "signature_invalid");
        state.transition("checking");
        assert!(state.error.is_none());
        let wire = serde_json::to_value(&state).unwrap();
        assert_eq!(wire.as_object().unwrap().len(), 10);
        assert_eq!(wire["current_version"], "1.0.0");
        assert!(wire["last_check_at"].is_null());
    }
    #[test]
    fn semver_guard() {
        assert!(newer("1.2.0", "1.1.9"));
        assert!(newer("1.0.0", "1.0.0-rc.1"));
        for v in ["1.0.0", "0.9.0", "1.0.0+build.2", "invalid"] {
            assert!(!newer(v, "1.0.0"));
        }
    }
    #[test]
    fn channel_is_only_explicit_loopback() {
        assert!(!configured(None));
        assert!(!configured(Some(&json!({}))));
        let mut c = json!({"pubkey":"test","endpoints":["http://127.0.0.1:19420/latest.json"],"dangerousInsecureTransportProtocol":true,"requireSignedVersion":true});
        assert!(configured(Some(&c)));
        for url in [
            "http://evil.test",
            "http://127.0.0.1.evil.test",
            "http://secret@127.0.0.1",
            "https://127.0.0.1",
        ] {
            c["endpoints"] = json!([url]);
            assert!(!configured(Some(&c)));
        }
    }
    #[test]
    fn time_is_rfc3339() {
        let value = timestamp();
        assert!(value.contains('T'));
        assert!(value.ends_with('Z'));
    }
    #[test]
    fn confirmation_before_any_side_effect() {
        assert_eq!(confirm("1.1.0", "1.2.0", "1.0.0"), Err("install_failed"));
        assert_eq!(confirm("0.9.0", "0.9.0", "1.0.0"), Err("downgrade_refused"));
        assert_eq!(confirm("1.1.0", "1.1.0", "1.0.0"), Ok(()));
    }
    #[test]
    fn signature_failure_is_distinct_from_transport() {
        assert_eq!(
            download_error(&tauri_plugin_updater::Error::MissingSignedVersion),
            "signature_invalid"
        );
        assert_eq!(
            download_error(&tauri_plugin_updater::Error::SignatureUtf8(
                "fixture".into()
            )),
            "signature_invalid"
        );
    }
    struct MockBackend {
        calls: Mutex<Vec<&'static str>>,
        activity: Mutex<std::collections::VecDeque<Result<bool, &'static str>>>,
        reserve: Result<(), &'static str>,
        backup: Result<String, &'static str>,
    }
    impl GuardBackend for MockBackend {
        async fn activity(&self) -> Result<bool, &'static str> {
            self.calls.lock().unwrap().push("activity");
            self.activity.lock().unwrap().pop_front().unwrap()
        }
        async fn reserve(&self) -> Result<(), &'static str> {
            self.calls.lock().unwrap().push("reserve");
            self.reserve
        }
        async fn backup(&self) -> Result<String, &'static str> {
            self.calls.lock().unwrap().push("backup");
            self.backup.clone()
        }
    }
    fn mock(
        activity: Vec<Result<bool, &'static str>>,
        reserve: Result<(), &'static str>,
        backup: Result<String, &'static str>,
    ) -> MockBackend {
        MockBackend {
            calls: Mutex::new(vec![]),
            activity: Mutex::new(activity.into()),
            reserve,
            backup,
        }
    }
    #[test]
    fn backend_guard_order_and_each_failure_stop_before_download() {
        tauri::async_runtime::block_on(async {
            for (mock, expected, acquired, trace) in [
                (
                    mock(vec![Err("backend_unavailable")], Ok(()), Ok("zip".into())),
                    Err("backend_unavailable"),
                    false,
                    vec!["activity"],
                ),
                (
                    mock(vec![Ok(true)], Ok(()), Ok("zip".into())),
                    Err("job_running"),
                    false,
                    vec!["activity"],
                ),
                (
                    mock(vec![Ok(false)], Err("job_running"), Ok("zip".into())),
                    Err("job_running"),
                    false,
                    vec!["activity", "reserve"],
                ),
                (
                    mock(vec![Ok(false), Ok(true)], Ok(()), Ok("zip".into())),
                    Err("job_running"),
                    true,
                    vec!["activity", "reserve", "activity"],
                ),
                (
                    mock(
                        vec![Ok(false), Err("backend_unavailable")],
                        Ok(()),
                        Ok("zip".into()),
                    ),
                    Err("backend_unavailable"),
                    true,
                    vec!["activity", "reserve", "activity"],
                ),
                (
                    mock(vec![Ok(false), Ok(false)], Ok(()), Err("backup_failed")),
                    Err("backup_failed"),
                    true,
                    vec!["activity", "reserve", "activity", "backup"],
                ),
                (
                    mock(vec![Ok(false), Ok(false)], Ok(()), Ok("zip".into())),
                    Ok("zip".into()),
                    true,
                    vec!["activity", "reserve", "activity", "backup"],
                ),
            ] {
                let mut reserved = false;
                assert_eq!(prepare(&mock, &mut reserved).await, expected);
                assert_eq!(reserved, acquired);
                assert_eq!(*mock.calls.lock().unwrap(), trace);
            }
        });
    }
    struct MockPayload {
        calls: Mutex<Vec<&'static str>>,
        fail: Option<&'static str>,
    }
    impl MockPayload {
        fn step(&self, name: &'static str) -> Result<(), &'static str> {
            self.calls.lock().unwrap().push(name);
            if self.fail == Some(name) {
                Err(if name == "download" {
                    "signature_invalid"
                } else {
                    "install_failed"
                })
            } else {
                Ok(())
            }
        }
    }
    impl PayloadBackend for MockPayload {
        async fn download(&self) -> Result<Vec<u8>, &'static str> {
            self.step("download")?;
            Ok(vec![1])
        }
        fn cache(&self, _: &[u8]) -> Result<(), &'static str> {
            self.step("cache")
        }
        async fn shutdown(&self) -> Result<(), &'static str> {
            self.step("shutdown")
        }
        fn install(&self, _: &[u8]) -> Result<(), &'static str> {
            self.step("install")
        }
    }
    #[test]
    fn signature_cache_and_shutdown_fail_closed_before_install() {
        tauri::async_runtime::block_on(async {
            let stages = ["download", "cache", "shutdown", "install"];
            for fail in [
                Some("download"),
                Some("cache"),
                Some("shutdown"),
                Some("install"),
                None,
            ] {
                let mock = MockPayload {
                    calls: Mutex::new(vec![]),
                    fail,
                };
                let result = finish_update(&mock).await;
                assert_eq!(result.is_ok(), fail.is_none());
                let length = fail.map_or(4, |name| {
                    stages.iter().position(|s| *s == name).unwrap() + 1
                });
                assert_eq!(*mock.calls.lock().unwrap(), stages[..length]);
            }
        });
    }
}

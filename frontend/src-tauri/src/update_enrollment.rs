//! Credencial por instalação no mesmo cofre nativo usado pelo keyring Python.
//! A API nunca devolve o segredo à UI e falhas não carregam mensagens do SO.
pub trait Vault {
    fn read(&self) -> Result<Option<String>, &'static str>;
    fn write(&self, token: &str) -> Result<(), &'static str>;
    fn clear(&self) -> Result<(), &'static str>;
}

pub fn valid_token(token: &str) -> bool {
    (token.len() == 64 && token.bytes().all(|b| b.is_ascii_hexdigit()))
        || (token.len() == 43
            && token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'))
}

pub fn enroll(vault: &impl Vault, token: &str) -> Result<(), &'static str> {
    if !valid_token(token) {
        return Err("enrollment_invalid");
    }
    vault.write(token)?;
    if vault.read()?.as_deref() != Some(token) {
        return Err("credential_unavailable");
    }
    Ok(())
}

pub struct InstallationVault(pub String);

#[cfg(windows)]
impl InstallationVault {
    fn target(&self) -> Vec<u16> {
        self.0.encode_utf16().chain(Some(0)).collect()
    }
}

#[cfg(windows)]
impl Vault for InstallationVault {
    fn read(&self) -> Result<Option<String>, &'static str> {
        use windows_sys::Win32::{
            Foundation::{GetLastError, ERROR_NOT_FOUND},
            Security::Credentials::*,
        };
        let target = self.target();
        let mut credential = std::ptr::null_mut();
        // SAFETY: target termina em NUL; CredReadW aloca e CredFree libera o bloco.
        unsafe {
            if CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) == 0 {
                return if GetLastError() == ERROR_NOT_FOUND {
                    Ok(None)
                } else {
                    Err("credential_unavailable")
                };
            }
            let value = &*credential;
            let result = if value.CredentialBlobSize == 0 || value.CredentialBlob.is_null() {
                Err("credential_unavailable")
            } else {
                String::from_utf8(
                    std::slice::from_raw_parts(
                        value.CredentialBlob,
                        value.CredentialBlobSize as usize,
                    )
                    .to_vec(),
                )
                .map(Some)
                .map_err(|_| "credential_unavailable")
            };
            CredFree(credential.cast());
            result
        }
    }
    fn write(&self, token: &str) -> Result<(), &'static str> {
        use windows_sys::Win32::Security::Credentials::*;
        let mut target = self.target();
        let credential = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: target.as_mut_ptr(),
            CredentialBlobSize: token.len() as u32,
            CredentialBlob: token.as_ptr() as *mut u8,
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            ..Default::default()
        };
        // SAFETY: CredWriteW copia os buffers válidos por toda a chamada.
        if unsafe { CredWriteW(&credential, 0) } == 0 {
            Err("credential_unavailable")
        } else {
            Ok(())
        }
    }
    fn clear(&self) -> Result<(), &'static str> {
        use windows_sys::Win32::{
            Foundation::{GetLastError, ERROR_NOT_FOUND},
            Security::Credentials::*,
        };
        let target = self.target();
        // SAFETY: target termina em NUL e permanece vivo durante a chamada.
        if unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) } != 0
            || unsafe { GetLastError() } == ERROR_NOT_FOUND
        {
            Ok(())
        } else {
            Err("credential_unavailable")
        }
    }
}

// O suporte atual é Windows: falhar fechado, sem fallback em arquivo.
#[cfg(not(windows))]
impl Vault for InstallationVault {
    fn read(&self) -> Result<Option<String>, &'static str> {
        Err("credential_unavailable")
    }
    fn write(&self, _: &str) -> Result<(), &'static str> {
        Err("credential_unavailable")
    }
    fn clear(&self) -> Result<(), &'static str> {
        Err("credential_unavailable")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    #[derive(Default)]
    struct MockVault(RefCell<Option<String>>);
    impl Vault for MockVault {
        fn read(&self) -> Result<Option<String>, &'static str> {
            Ok(self.0.borrow().clone())
        }
        fn write(&self, token: &str) -> Result<(), &'static str> {
            *self.0.borrow_mut() = Some(token.into());
            Ok(())
        }
        fn clear(&self) -> Result<(), &'static str> {
            self.0.borrow_mut().take();
            Ok(())
        }
    }
    #[test]
    fn enrollment_roundtrip_and_clear_use_only_mock_vault() {
        let vault = MockVault::default();
        let synthetic = "a".repeat(64);
        assert_eq!(enroll(&vault, &synthetic), Ok(()));
        assert_eq!(vault.read().unwrap().as_deref(), Some(synthetic.as_str()));
        vault.clear().unwrap();
        vault.clear().unwrap();
        assert_eq!(vault.read().unwrap(), None);
        for invalid in ["short", &"g".repeat(64), &format!("{}\r\n", "a".repeat(64))] {
            assert_eq!(enroll(&vault, invalid), Err("enrollment_invalid"));
        }
        assert_eq!(vault.read().unwrap(), None);
    }
    #[test]
    fn vault_failure_is_stable_and_never_falls_back_to_disk() {
        struct Unavailable;
        impl Vault for Unavailable {
            fn read(&self) -> Result<Option<String>, &'static str> {
                Err("credential_unavailable")
            }
            fn write(&self, _: &str) -> Result<(), &'static str> {
                Err("credential_unavailable")
            }
            fn clear(&self) -> Result<(), &'static str> {
                Err("credential_unavailable")
            }
        }
        assert_eq!(
            enroll(&Unavailable, &"a".repeat(64)),
            Err("credential_unavailable")
        );
    }
}

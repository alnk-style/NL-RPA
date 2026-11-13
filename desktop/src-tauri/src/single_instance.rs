use std::ffi::c_void;

const ERROR_ALREADY_EXISTS: u32 = 183;

#[link(name = "kernel32")]
extern "system" {
    fn CreateMutexW(
        attributes: *const c_void,
        initial_owner: i32,
        name: *const u16,
    ) -> *mut c_void;
    fn GetLastError() -> u32;
    fn CloseHandle(handle: *mut c_void) -> i32;
}

pub struct InstanceGuard(*mut c_void);

impl Drop for InstanceGuard {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

pub fn acquire(name: &str) -> Option<InstanceGuard> {
    let wide_name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
    let handle = unsafe {
        CreateMutexW(
            std::ptr::null(),
            0,
            wide_name.as_ptr(),
        )
    };
    if handle.is_null() {
        return Some(InstanceGuard(handle));
    }
    if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
        unsafe {
            CloseHandle(handle);
        }
        return None;
    }
    Some(InstanceGuard(handle))
}

#[cfg(test)]
mod tests {
    use super::acquire;

    #[test]
    fn second_instance_is_rejected_until_first_guard_drops() {
        let name = "Local\\com.nlrpa.desktop.test";
        let first = acquire(name).expect("first instance");
        assert!(acquire(name).is_none());
        drop(first);
        assert!(acquire(name).is_some());
    }
}
